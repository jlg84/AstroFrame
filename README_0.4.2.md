# AstroFrame 0.4.2 — Auto Field + Solver Log

## Changes

- ASTAP now uses **Field = Auto** for the first local attempt.
- Target-assisted solving tries:
  1. Auto field + target coordinates.
  2. Estimated field + target coordinates.
  3. Astrometry.net fallback.
- Blind solving tries ASTAP Auto field before Astrometry.net.
- Added a live **Solver Log** showing:
  - each attempt;
  - exact ASTAP command;
  - field mode;
  - target coordinates;
  - ASTAP exit code;
  - stdout/stderr;
  - elapsed time;
  - fallback and final result.
- Added **Copy Solver Log** and **Clear Log** buttons.

## Install and run

```bash
cd /Users/jamesglucksman/Projects/AstroFrame
unzip -o ~/Downloads/AstroFrame_0.4.2_auto_field_solver_log_full.zip
python3 main.py
```

## First test

Open a new uncached image and click **Plate Solve**. The log should show
`Field setting: Auto`. For Tarantula, use **Target-assisted solve…** and enter
`NGC 2070` or `5.6453, -69.1`.
