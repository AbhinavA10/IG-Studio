# ig-speed — IG Studio

A local editor that turns folders of DSLR exports into Instagram carousel slides.
It's a Python backend (Pillow) that reads your photos straight from disk, with a browser UI for arranging them.

## Run

Double-click **`IG Studio.bat`**. It starts the server with the `igstudio` conda environment and opens
http://localhost:5178/. Close the console window to stop it.

Or run it manually from any Python with Pillow installed:
```
conda activate igstudio
python ig_studio.py
```

## Workflow

1. Export your photos into a trip folder, with one subfolder per post:
   ```
   Portugal/
     1 Lisbon/   *.jpg
     2 Sintra/   *.jpg
   ```
2. Click **Browse…** (a native folder dialog) or paste the path, then press **Open**. Recent folders are remembered.
3. Pick a ratio and style, press **🎲 Regenerate** (or R) until you like the result, then fine-tune.
4. Click **Export post** or **Export all**. Slides are written to `Portugal\_instagram\<post>\01.jpg, 02.jpg…`. Click 📂 to open that folder.

Edits are saved automatically to `_instagram\<post>\layout.json`, so reopening the folder picks up where you left off.
Previews are cached in your temp folder, so a post only needs its previews generated once.

## Post settings

- **Ratio:** 4:5, 3:4, 1:1 or 3:2. Every slide in a post uses the same ratio, which Instagram requires.
- **Style:** you choose it; the number of photos never decides it.
  - *Smart mix:* photos that suit the frame get their own slide; the rest are paired to fit.
  - *One per slide:* every photo on its own slide.
  - *Pairs:* mostly two photos per slide.
  - *Collage:* 2–4 photos per slide.
- **Max slides** (default 10). A spread counts as 2 slides.
- **Gap** (default 0 = no borders) and gap colour.
- **Keep shooting order:** when off, Regenerate shuffles everything except the first photo.
- **Allow spreads across 2 slides:** lets Regenerate stretch a landscape across two consecutive slides.

Name a file `cover…` or `!…` to make it the first photo.

## Editing

| Action | How |
|---|---|
| Reposition a photo | Drag it |
| Zoom | Scroll wheel over the photo |
| Reset crop | Double-click the photo |
| Resize panes | Drag the divider between photos (double-click it to reset) |
| Swap photos, within or between slides | Shift + drag a photo onto another photo in the editor or the strip, or drag directly between thumbnails in the strip |
| Spread a slide over 2–3 slides | *Spread over* dropdown |
| Change layout | *Template* dropdown, or **🎲 Re-roll slide** (random template and pane sizes) |
| Reorder | **◀ Move / Move ▶**, **Make cover** |
| Split / combine | **Split**, **Merge next** |
| Drop a photo | Click it, then **Remove photo** (or Delete). It moves to *Unused photos*; click it there to add it back |
| Undo / redo | Ctrl+Z / Ctrl+Y |
| Prev / next slide | ← / → |

Supported files: JPEG, PNG, TIFF, WebP. Export is 1080 px wide by default (1440 px optional), sRGB JPEG at quality 95.
