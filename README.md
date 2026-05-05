# Vision-Based Desktop Automation with Dynamic Icon Grounding

This project is a take-home submission for the TJM Labs desktop automation task.
It launches Notepad from a visually detected desktop icon, writes the first 10
posts from JSONPlaceholder, saves each post to `Desktop/tjm-project`, closes
Notepad, and repeats the workflow from a fresh screenshot.

The goal is intentionally not to launch Notepad the easy way. The point is to
demonstrate a scalable visual grounding approach that can survive icon movement.

## Setup

Target environment:

- Windows 10/11
- 1920x1080 display
- Notepad shortcut created on the desktop
- Python 3.11+
- `uv`

If `uv` is not installed:

```bash
pip install uv
```

Install:

```bash
uv sync
```

Recommended OCR support:

```bash
uv sync --extra ocr
```

The OCR path uses the Python `pytesseract` wrapper. On Windows, install the
Tesseract binary separately and make sure `tesseract.exe` is available on PATH.

Run:

```bash
uv run tjm-ground
```

Dry-run grounding only:

```bash
uv run tjm-ground --dry-run --annotate screenshots/notepad_detected.png
```

Check the active capture/click resolution:

```bash
uv run tjm-ground --screen-info
```

For the assessment environment, the screenshot size and PyAutoGUI coordinate
size should both be `1920x1080`. If they differ, set Windows **Display
resolution** to `1920 x 1080` and **Scale** to `100%` in Settings > System >
Display, then rerun the diagnostic command.

Use a visual template fallback:

```bash
uv run tjm-ground --template assets/notepad_template.png
```

## What The App Does

For each of the first 10 posts:

1. Shows the desktop.
2. Captures a fresh screenshot.
3. Grounds the Notepad desktop icon.
4. Double-clicks the grounded center coordinate.
5. Waits for a Notepad window to appear.
6. Types:

   ```text
   Title: {title}

   {body}
   ```

7. Saves the file as `Desktop/tjm-project/post_{id}.txt`.
8. Closes Notepad.

Existing files are overwritten by default. This keeps the assessment repeatable.

## Grounding Design

The implementation uses a cascade:

1. **OCR label grounding**
   - Reads visible desktop text.
   - Looks for a label matching the requested target, default `Notepad`.
   - Infers the icon region above the matched label and returns the estimated
     icon center for clicking.

2. **OpenCV candidate detection**
   - Finds icon-like visual regions from edges and contours.
   - Runs localized OCR around each candidate so tiny desktop labels are read
     from smaller, enhanced crops instead of only from the full screenshot.

3. **Template matching fallback**
   - Optional `--template` image can be used when OCR is unavailable.
   - This is deliberately a fallback because exact icon templates are brittle
     across Windows themes, scaling, and icon sizes.

4. **Windows UI Automation fallback**
   - If visual OCR/template matching fail, reads the desktop item rectangle from
     Windows UI Automation and still returns a grounded screen coordinate.
   - This is a last resort for noisy wallpapers or low-contrast labels; the
     visual path remains first.

5. **Explorer desktop-list fallback**
   - As a final recovery path, reads the desktop `SysListView32` icon rectangle
     from Explorer. This prevents a total failure when OCR cannot read a label
     on a very busy wallpaper.

The structure is inspired by modern high-resolution GUI grounding work such as
ScreenSpot-Pro / ScreenSeekeR: reduce the search space first, then do finer
matching on candidate regions instead of depending on fixed coordinates.

Reference: https://arxiv.org/abs/2504.07981

## Error Handling

- Retries grounding up to 3 times.
- Uses `Win+D` before screenshots to reduce obstruction by windows.
- Handles multiple matches by choosing the highest confidence result.
- Validates that Notepad launched with a window-title check.
- Uses fallback posts if the API is unavailable.
- Creates `Desktop/tjm-project` if it does not exist.

## Annotated Screenshot Deliverables

Create these by moving the Notepad icon and running dry-run mode:

```bash
uv run tjm-ground --dry-run --annotate screenshots/top_left_annotated.png
uv run tjm-ground --dry-run --annotate screenshots/bottom_right_annotated.png
uv run tjm-ground --dry-run --annotate screenshots/center_annotated.png
```

The generated images draw the detected bounding box, center point, and confidence.

## Known Limitations

- OCR requires the desktop label to be visible.
- If another window fully covers the icon, detection can fail after retries.
- Template matching depends on the provided template matching the current icon
  scale/theme closely enough.
- For arbitrary icons without visible text, the next step would be a semantic
  image-text model such as CLIP or a GUI grounding model, evaluated on detected
  candidate regions.

## Interview Discussion Notes

Why this approach:

- Fixed coordinates fail as soon as the icon moves.
- Pure template matching is fragile across icon sizes, themes, and scaling.
- OCR label grounding is practical on Windows desktops because shortcut labels
  are visible and move with the icon.
- Candidate detection gives a path toward model-based semantic grounding.

Failure cases:

- Hidden desktop icon.
- Label renamed to something unrelated.
- OCR binary missing or unable to read the label.
- Very busy backgrounds with low-contrast icon text.

Improvements with more time:

- Add CLIP/OWL-ViT scoring over icon candidates for text-free semantic grounding.
- Support multiple monitors and arbitrary resolutions.
- Add a calibration step that records current icon size and text contrast.
