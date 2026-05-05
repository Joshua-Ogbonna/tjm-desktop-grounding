from __future__ import annotations

import argparse
from pathlib import Path

from tjm_desktop_grounding.config import AppConfig


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Launch Notepad by visually grounding its desktop icon."
    )
    parser.add_argument("--target", default="Notepad", help="Desktop icon label to ground.")
    parser.add_argument(
        "--limit",
        type=int,
        default=10,
        help="Number of JSONPlaceholder posts to write.",
    )
    parser.add_argument(
        "--save-dir",
        type=Path,
        default=Path.home() / "Desktop" / "tjm-project",
        help="Directory where post files are saved.",
    )
    parser.add_argument(
        "--template",
        type=Path,
        default=None,
        help="Optional template image fallback for visual matching.",
    )
    parser.add_argument(
        "--annotate",
        type=Path,
        default=None,
        help="Optional path for an annotated detection screenshot.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Only capture, ground, and optionally annotate. Do not click or type.",
    )
    parser.add_argument(
        "--screen-info",
        action="store_true",
        help="Print screenshot and coordinate-system diagnostics, then exit.",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()

    from tjm_desktop_grounding.api import fetch_posts
    from tjm_desktop_grounding.automation import AutomationError, NotepadAutomation
    from tjm_desktop_grounding.grounding import GroundingError

    config = AppConfig(target_label=args.target, save_directory=args.save_dir)
    automation = NotepadAutomation(config)

    try:
        if args.screen_info:
            show_screen_info()
            return

        if not args.dry_run:
            posts = fetch_posts(limit=args.limit)
            if not posts:
                raise AutomationError("No posts available from API or fallback data.")
        else:
            posts = []

        if args.dry_run:
            automation.show_desktop()
            result = ground_icon(args, config)
            show_grounding_result(result)
            print("Dry run complete.")
            return

        written_files: list[Path] = []
        for index, post in enumerate(posts, start=1):
            print(f"Post {index}/{len(posts)}: grounding {args.target!r}")
            automation.show_desktop()
            result = ground_icon(args, config)
            show_grounding_result(result)
            automation.launch_from_grounding(result)
            written_files.append(automation.write_and_save_post(post))
            automation.close_notepad()

        show_written_files(written_files)

    except (GroundingError, AutomationError, KeyboardInterrupt) as exc:
        print(f"Automation failed: {exc}")
        raise SystemExit(1) from exc


def ground_icon(args: argparse.Namespace, config: AppConfig):
    from tjm_desktop_grounding.grounding import DesktopGrounder, GroundingOptions

    options = GroundingOptions(
        target=config.target_label,
        attempts=config.retry_attempts,
        retry_delay_seconds=config.retry_delay_seconds,
        template_path=args.template,
        annotate_path=args.annotate,
    )
    return DesktopGrounder(options).locate()


def show_grounding_result(result) -> None:
    print(
        "Grounding Result: "
        f"target={result.target!r}, "
        f"method={result.method}, "
        f"confidence={result.confidence:.2f}, "
        f"center={result.center}, "
        f"screenshot={result.screenshot_path or ''}"
    )
    if result.note:
        print(result.note)


def show_written_files(paths: list[Path]) -> None:
    print("Saved Files:")
    for path in paths:
        print(f"- {path}")


def show_screen_info() -> None:
    from tjm_desktop_grounding.screenshot import capture_screen, get_screen_diagnostics

    image = capture_screen()
    diagnostics = get_screen_diagnostics()
    print(f"Screenshot size: {image.width}x{image.height}")

    pyautogui_size = diagnostics.get("pyautogui_size")
    if pyautogui_size:
        print(
            "PyAutoGUI coordinate size: "
            f"{pyautogui_size['width']}x{pyautogui_size['height']}"
        )
        if (image.width, image.height) != (
            pyautogui_size["width"],
            pyautogui_size["height"],
        ):
            print(
                "Warning: screenshot pixels and click coordinates differ. "
                "Set Windows display Scale to 100% for this assessment."
            )

    for monitor in diagnostics.get("mss_monitors", []):
        print(
            "MSS monitor "
            f"{monitor['index']}: "
            f"{monitor['width']}x{monitor['height']} "
            f"at ({monitor['left']}, {monitor['top']})"
        )


if __name__ == "__main__":
    main()
