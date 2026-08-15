# AstroFrame

AstroFrame is a macOS astrophotography framing and planning companion. Give it a reference image and it helps answer the practical question: **can I reproduce this framing with my own equipment, and what should I send to NINA?**

## Current status

**AstroFrame 1.0 — release-candidate development (current tested baseline: RC22x)**

AstroFrame is now a packaged Python/Qt desktop application for macOS. The original browser prototype in this repository is historical and no longer represents the current application.

## What AstroFrame does

- Loads reference astrophotography images, including JPEG, PNG and TIFF, and reuses cached solutions for identical images
- Plate-solves images locally with ASTAP, with subject hints and Astrometry.net fallback
- Recognises common target names and aliases from filenames and user hints
- Displays solved field geometry and catalogue objects on the reference image
- Imports user catalogue collections and preserves source-coordinate precision
- Canonicalises duplicate catalogue identities and selects the best available object position
- Stores multiple observing sites and evaluates target observability
- Searches beyond the immediate observing window when necessary
- Stores multiple telescope/camera rigs and compares their fields of view
- Recommends a better-matching rig when the selected field is unsuitable
- Supports reframing, rotation and mosaic planning
- Exports framing information for use in NINA

## macOS development build

The release-candidate packages include a build script. After unpacking a package in `~/Downloads`:

```bash
cd ~/Downloads/AstroFrame_1.0-RC22x
chmod +x build_mac_app.command
./build_mac_app.command
```

The script builds the macOS application from the supplied source. ASTAP is recommended for local plate solving; Astrometry.net can be used as a fallback where configured.

## Core workflow

1. Load a reference image.
2. Let AstroFrame identify/plate-solve it, or confirm/provide the subject when asked.
3. Choose an observing site and imaging rig.
4. Inspect object markers, observability and equipment advice.
5. Adjust framing/rotation or build a mosaic if needed.
6. Export the result for NINA.

## Documentation

- [Roadmap](ROADMAP.md)
- [Changelog](CHANGELOG.md)
- [Equipment notes](docs/EQUIPMENT.md)
- [Observing sites](docs/OBSERVING_SITES.md)
- [Using AstroFrame with NINA](docs/USING_WITH_NINA.md)
- [Project vision](VISION.md)

## Development note

AstroFrame is under active private development. RC22x is the current stabilisation baseline after extensive testing of plate solving, catalogue imports, coordinate precision, object-marker canonicalisation, observability, equipment framing, mosaics and NINA handoff.

## Repository owner

Created for and tested by [jlg84](https://github.com/jlg84).
