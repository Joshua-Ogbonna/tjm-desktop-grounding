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

        pyautogui.doubleClick(*result.center, interval=0.08)
        self.wait_for_notepad()

    def wait_for_notepad(self) -> None:
        deadline = monotonic() + self.config.launch_timeout_seconds
        while monotonic() < deadline:
            if self._notepad_window_exists():
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
        return output_path

    def close_notepad(self) -> None:
        import pyautogui

        pyautogui.hotkey("alt", "f4")
        sleep(0.5)
        self._dismiss_save_prompt_if_present()

    def _notepad_window_exists(self) -> bool:
        try:
            from pywinauto import Desktop
        except ImportError:
            return True

        try:
            windows = Desktop(backend="uia").windows()
        except Exception:
            return False

        for window in windows:
            try:
                title = window.window_text().casefold()
            except Exception:
                continue
            if "notepad" in title:
                try:
                    window.set_focus()
                except Exception:
                    pass
                return True
        return False

    def _focus_notepad(self) -> None:
        try:
            from pywinauto import Desktop
        except ImportError:
            return

        try:
            windows = Desktop(backend="uia").windows()
        except Exception:
            return

        for window in windows:
            try:
                title = window.window_text().casefold()
            except Exception:
                continue
            if "notepad" not in title:
                continue
            try:
                window.set_focus()
                return
            except Exception:
                return

    def _save_as(self, output_path: Path) -> None:
        import pyautogui

        pyautogui.hotkey("ctrl", "s")
        dialog = self._wait_for_save_as_dialog()

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

    def _wait_for_save_as_dialog(self):
        try:
            from pywinauto import Desktop
        except ImportError as exc:
            raise AutomationError(
                "pywinauto is required to verify the Save As dialog before typing a path."
            ) from exc

        deadline = monotonic() + self.config.launch_timeout_seconds
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
                if "save as" in title:
                    try:
                        window.set_focus()
                    except Exception:
                        pass
                    return window
            sleep(0.2)

        raise AutomationError(
            "Timed out waiting for the Save As dialog. "
            "The filename was not typed into Notepad."
        )

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
                if "save as" in window.window_text().casefold():
                    return True
            except Exception:
                continue
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
