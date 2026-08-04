# AstroFrame 0.3.0 — Astrometry.net plate solving

## What is new

- Plate-solve a loaded JPEG, PNG or TIFF through Astrometry.net.
- The first solve asks for your Astrometry.net API key and stores it in local macOS application settings.
- Successful solves change the reference status from **Estimated** to **Verified**.
- The solved angular width automatically calibrates the equipment overlays.
- The solved centre, pixel scale, dimensions and orientation are displayed.
- Solutions are cached by image contents and reused instantly when the same image is reopened.
- A verified solution can be cleared to return to manual estimated scaling.

## Run

```bash
cd /Users/jamesglucksman/Projects/AstroFrame
python3 main.py
```

## First plate solve

1. Open a reference image.
2. Click **Plate Solve with Astrometry.net**.
3. Paste your Astrometry.net API key when prompted.
4. Wait for the status to change to **Verified**.

The online service can take from a few seconds to several minutes depending on its queue and the image.
