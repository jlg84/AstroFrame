# AstroFrame

AstroFrame is a lightweight astrophotography framing companion for comparing a reference image with the field of view of a user's own telescope and camera combinations.

## Current status

**Version 0.1.0 — working browser prototype**

The current build runs locally on macOS in Safari, Chrome, or another modern browser. Nothing is uploaded anywhere.

### Working features

- Open a local JPEG or PNG reference image
- Switch between saved imaging rigs
- Display the calculated field-of-view rectangle
- Drag the frame over the image
- Rotate the frame
- Save an overlay PNG
- Copy RA, Dec, and rotation values for NINA

### Included equipment profiles

- ASI1600MM Pro + 442 mm focal length
- ASI533MC Pro + 1448 mm focal length

### Not yet implemented

- Automatic plate solving
- AstroBin URL import
- Accurate RA/Dec updates when the frame is dragged
- Side-by-side rig comparison
- Equipment profile editor
- Direct NINA export

## Run on a Mac

1. Download or clone the repository.
2. Open the `app` folder.
3. Double-click `AstroFrame.html`.

## Project direction

The immediate goal is to make it quick to answer:

1. Can I reproduce this image with my equipment?
2. Which rig is the best match?
3. What centre coordinates and camera rotation should I use in NINA?

See [ROADMAP.md](ROADMAP.md) for the planned milestones.

## Repository owner

Created for and tested by [jlg84](https://github.com/jlg84).
