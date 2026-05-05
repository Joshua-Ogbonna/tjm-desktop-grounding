from __future__ import annotations

from pathlib import Path
from time import monotonic, sleep

from tjm_desktop_grounding.config import AppConfig
from tjm_desktop_grounding.models import BlogPost, GroundingResult


class AutomationError(RuntimeError):
    """Raised when the desktop automation workflow cannot continue."""


class NotepadAutomation:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.active_notepad_handle: int | None = None

    def show_desktop(self) -> None:
        import pyautogui

        try:
            import win32com.client

            win32com.client.Dispatch("Shell.Application").MinimizeAll()
        except Exception:
            pyautogui.hotkey("win", "m")
        sleep(1.2)

    def launch_from_grounding(self, result: GroundingResult) -> None:
        import pyautogui

        existing_handles = self._notepad_window_handles()
        pyautogui.doubleClick(*result.center, interval=0.08)
        self.wait_for_notepad(existing_handles=existing_handles)

    def wait_for_notepad(self, existing_handles: set[int] | None = None) -> None:
        existing_handles = existing_handles or set()
        deadline = monotonic() + self.config.launch_timeout_seconds
        while monotonic() < deadline:
            if self._focus_notepad(prefer_newer_than=existing_handles, allow_fallback=not existing_handles):
                sleep(0.4)
                return
            sleep(0.2)
        raise AutomationError("Timed out waiting for Notepad to launch.")

    def write_and_save_post(self, post: BlogPost) -> Path:
        import pyautogui

        self.config.save_directory.mkdir(parents=True, exist_ok=True)
        output_path = self.config.save_directory / f"post_{post.id}.txt"

        self._focus_notepad()
        pyautogui.hotkey("ctrl", "a")
        paste_text(post.text, self.config.type_interval_seconds)
        sleep(0.2)

        self._save_as(output_path)
        self._verify_saved_post(output_path, post.text)
        return output_path

    def close_notepad(self) -> None:
        import pyautogui

        closed_with_uia = self._close_focused_notepad_with_uia()
        if not closed_with_uia:
            self._focus_notepad()
            pyautogui.hotkey("alt", "f4")
        sleep(0.5)
        self._dismiss_save_prompt_if_present()

    def _notepad_window_exists(self) -> bool:
        return self._focus_notepad()

    def _notepad_window_handles(self) -> set[int]:
        try:
            from pywinauto import Desktop
        except ImportError:
            return set()

        try:
            windows = Desktop(backend="uia").windows()
        except Exception:
            return set()

        handles: set[int] = set()
        for window in windows:
            try:
                title = window.window_text().casefold()
                handle = int(window.handle)
            except Exception:
                continue
            if "notepad" in title:
                handles.add(handle)
        return handles

    def _focus_notepad(
        self,
        prefer_newer_than: set[int] | None = None,
        allow_fallback: bool = True,
    ) -> bool:
        prefer_newer_than = prefer_newer_than or set()
        try:
            from pywinauto import Desktop
        except ImportError:
            return True

        try:
            windows = Desktop(backend="uia").windows()
        except Exception:
            return False

        fallback_window = None

        for window in windows:
            try:
                title = window.window_text().casefold()
                handle = int(window.handle)
            except Exception:
                continue
            if "notepad" not in title:
                continue

            if self.active_notepad_handle is not None and handle == self.active_notepad_handle:
                return self._focus_window(window, handle)

            if fallback_window is None:
                fallback_window = (window, handle)

            if handle not in prefer_newer_than:
                return self._focus_window(window, handle)

        if fallback_window is None or not allow_fallback:
            return False

        window, handle = fallback_window
        return self._focus_window(window, handle)

    def _focus_window(self, window, handle: int) -> bool:
        try:
            window.set_focus()
        except Exception:
            return False
        self.active_notepad_handle = handle
        return True

    def _save_as(self, output_path: Path) -> None:
        import pyautogui

        pyautogui.hotkey("ctrl", "s")
        dialog = self._wait_for_file_save_dialog(required=False, timeout_seconds=1.5)
        if dialog is None:
            self._open_save_as_from_notepad_menu()
            dialog = self._wait_for_file_save_dialog(required=True)

        if dialog is not None and self._fill_save_as_dialog(dialog, output_path):
            self._wait_for_save_as_to_close()
            return

        # Fallback for environments where pywinauto can see windows but cannot
        # manipulate the native dialog controls.
        paste_text(str(output_path), self.config.type_interval_seconds)
        pyautogui.press("enter")
        sleep(0.6)
        self._confirm_overwrite_if_needed()
        self._wait_for_save_as_to_close()

    @staticmethod
    def _open_save_as_from_notepad_menu() -> None:
        import pyautogui

        pyautogui.hotkey("ctrl", "shift", "s")
        sleep(0.8)
        if NotepadAutomation._save_as_dialog_exists():
            return

        pyautogui.hotkey("alt", "f")
        sleep(0.2)
        pyautogui.press("a")
        sleep(0.8)

    def _wait_for_file_save_dialog(
        self,
        required: bool = True,
        timeout_seconds: float | None = None,
    ):
        try:
            from pywinauto import Desktop
        except ImportError as exc:
            raise AutomationError(
                "pywinauto is required to verify the Save As dialog before typing a path."
            ) from exc

        timeout_seconds = timeout_seconds or self.config.launch_timeout_seconds
        seen_windows: list[str] = []
        deadline = monotonic() + timeout_seconds
        while monotonic() < deadline:
            try:
                windows = Desktop(backend="uia").windows()
            except Exception:
                windows = []

            for window in windows:
                try:
                    title = window.window_text().casefold()
                    class_name = window.class_name()
                except Exception:
                    continue
                if title:
                    seen_windows.append(f"{title} [{class_name}]")
                if self._is_file_save_dialog(window, title):
                    try:
                        window.set_focus()
                    except Exception:
                        pass
                    return window
            sleep(0.2)

        if required:
            seen = ", ".join(dict.fromkeys(seen_windows[-12:])) or "no readable windows"
            raise AutomationError(
                "Timed out waiting for the Save As dialog. "
                "The filename was not typed into Notepad. "
                f"Visible windows: {seen}"
            )
        return None

    @staticmethod
    def _is_file_save_dialog(window, title: str | None = None) -> bool:
        title = title if title is not None else ""
        normalized_title = title.casefold()
        if "save" in normalized_title and "notepad" not in normalized_title:
            return True

        if "notepad" in normalized_title:
            return False

        try:
            class_name = window.class_name()
        except Exception:
            class_name = ""

        has_native_dialog_shape = class_name == "#32770" or "dialog" in class_name.casefold()
        if not has_native_dialog_shape:
            return False

        try:
            buttons = [
                button.window_text().casefold()
                for button in window.descendants(control_type="Button")
            ]
            edits = window.descendants(control_type="Edit")
        except Exception:
            return False

        return bool(edits) and any(text in {"save", "&save"} for text in buttons)

    def _fill_save_as_dialog(self, dialog, output_path: Path) -> bool:
        import pyautogui

        try:
            dialog.set_focus()
        except Exception:
            pass

        filename = str(output_path)
        if self._set_filename_with_uia(dialog, filename):
            return self._press_save_button(dialog)

        pyautogui.hotkey("alt", "n")
        paste_text(filename, self.config.type_interval_seconds)
        pyautogui.press("enter")
        sleep(0.6)
        self._confirm_overwrite_if_needed()
        return True

    @staticmethod
    def _set_filename_with_uia(dialog, filename: str) -> bool:
        try:
            edits = dialog.descendants(control_type="Edit")
        except Exception:
            return False

        if not edits:
            return False

        filename_edit = None
        for edit in edits:
            try:
                if edit.automation_id() == "1001":
                    filename_edit = edit
                    break
            except Exception:
                continue

        if filename_edit is None:
            filename_edit = edits[0]

        try:
            filename_edit.set_focus()
            filename_edit.set_edit_text(filename)
            return True
        except Exception:
            return False

    @staticmethod
    def _press_save_button(dialog) -> bool:
        try:
            save_button = dialog.child_window(title="Save", control_type="Button")
            save_button.click_input()
        except Exception:
            import pyautogui

            pyautogui.press("enter")

        sleep(0.6)
        NotepadAutomation._confirm_overwrite_if_needed()
        return True

    def _wait_for_save_as_to_close(self) -> None:
        deadline = monotonic() + self.config.launch_timeout_seconds
        while monotonic() < deadline:
            if not self._save_as_dialog_exists():
                sleep(0.3)
                return
            sleep(0.2)
        raise AutomationError("Timed out waiting for the Save As dialog to close.")

    @staticmethod
    def _save_as_dialog_exists() -> bool:
        try:
            from pywinauto import Desktop
        except ImportError:
            return False

        try:
            windows = Desktop(backend="uia").windows()
        except Exception:
            return False

        for window in windows:
            try:
                title = window.window_text().casefold()
                if NotepadAutomation._is_file_save_dialog(window, title):
                    return True
            except Exception:
                continue
        return False

    @staticmethod
    def _verify_saved_post(output_path: Path, expected_text: str) -> None:
        if not output_path.exists():
            raise AutomationError(f"Notepad did not create the expected file: {output_path}")

        try:
            saved_text = output_path.read_text(encoding="utf-8-sig")
        except UnicodeDecodeError:
            saved_text = output_path.read_text()

        if saved_text.strip() != expected_text.strip():
            raise AutomationError(
                f"Saved file content did not match the expected post: {output_path}"
            )

    def _close_focused_notepad_with_uia(self) -> bool:
        try:
            from pywinauto import Desktop
        except ImportError:
            return False

        try:
            windows = Desktop(backend="uia").windows()
        except Exception:
            return False

        notepad_windows = []
        for window in windows:
            try:
                title = window.window_text().casefold()
                handle = int(window.handle)
            except Exception:
                continue
            if "notepad" not in title:
                continue
            if self.active_notepad_handle is not None and handle == self.active_notepad_handle:
                return self._close_notepad_window(window)
            notepad_windows.append(window)

        if not notepad_windows:
            return False

        return self._close_notepad_window(notepad_windows[0])

    def _close_notepad_window(self, window) -> bool:
        try:
            window.set_focus()
            window.close()
            self.active_notepad_handle = None
            return True
        except Exception:
            return False

    @staticmethod
    def _confirm_overwrite_if_needed() -> None:
        import pyautogui

        try:
            from pywinauto import Desktop
        except ImportError:
            pyautogui.hotkey("alt", "y")
            return

        deadline = monotonic() + 1.5
        while monotonic() < deadline:
            try:
                windows = Desktop(backend="uia").windows()
            except Exception:
                windows = []

            for window in windows:
                try:
                    title = window.window_text().casefold()
                except Exception:
                    continue
                if "confirm save as" not in title:
                    continue
                try:
                    window.child_window(title="Yes", control_type="Button").click_input()
                    return
                except Exception:
                    pyautogui.hotkey("alt", "y")
                    return
            sleep(0.1)

        # If no dialog is open, this is harmless in Notepad for the assessment path.
        pyautogui.hotkey("alt", "y")

    @staticmethod
    def _dismiss_save_prompt_if_present() -> None:
        import pyautogui

        # In case Notepad thinks there are unsaved changes after saving, choose
        # Don't Save. The files were already written through Ctrl+S.
        pyautogui.hotkey("alt", "n")


def paste_text(text: str, fallback_interval_seconds: float) -> None:
    import pyautogui

    try:
        import pyperclip

        pyperclip.copy(text)
        pyautogui.hotkey("ctrl", "v")
    except Exception:
        pyautogui.write(text, interval=fallback_interval_seconds)
