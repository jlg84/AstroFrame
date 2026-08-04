# AstroFrame 0.3.3

Fixes a macOS crash when an Astrometry.net solve fails.

## Cause

The failure callback could run on the plate-solving worker thread and attempt
to open a Qt warning dialog there. macOS requires all windows and dialogs to
be created on the main GUI thread, so AppKit aborted Python.

## Fixed

- All solve progress, success, and failure callbacks are explicitly marshalled
  onto Qt's main GUI thread.
- Failed solves remain visible in the Reference Image panel.
- No modal warning dialog is created by the worker path.
- Successful solves and cached solutions are unchanged.

## Install

Copy the contents into the AstroFrame repository, replacing the existing
application files, then run:

```bash
cd /Users/jamesglucksman/Projects/AstroFrame
python3 main.py
```
