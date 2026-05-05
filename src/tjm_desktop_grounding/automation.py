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

        pyautogui.hotkey("ctrl", "a")
        paste_text(post.text, self.config.type_interval_seconds)
        pyautogui.hotkey("ctrl", "s")

        # The Save As dialog opens for a new Notepad document. Typing the full
        # path is the most stable cross-version path through the native dialog.
        sleep(0.5)
        paste_text(str(output_path), self.config.type_interval_seconds)
        pyautogui.press("enter")
        sleep(0.5)

        self._confirm_overwrite_if_needed()
        sleep(0.3)
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

    @staticmethod
    def _confirm_overwrite_if_needed() -> None:
        import pyautogui

        # If Windows shows an overwrite confirmation, Alt+Y confirms it. If no
        # dialog is open, this is harmless in Notepad for the assessment path.
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
