# AstroFrame 0.3.2

Fixes reference-image state when switching images.

## Fixed

- Opening a new image immediately returns the reference status to **Estimated**.
- The plate-solve button returns to **Plate Solve with Astrometry.net**.
- Results or progress messages from a solve started for an earlier image are ignored.
- A previous image's verified solution cannot be applied to the newly opened image.
- Cached solutions still load automatically for the correct image.

## Install

Copy the contents of this package into the AstroFrame repository, replacing
the existing application files, then run:

```bash
cd /Users/jamesglucksman/Projects/AstroFrame
python3 main.py
```
