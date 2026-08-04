# AstroFrame 0.4.0 — ASTAP First

## New

- **Plate Solve** now tries local ASTAP first.
- Detects either of the ASTAP executable locations found on James's Mac:
  - `/Applications/ASTAP.app/Contents/MacOS/astap`
  - `/Applications/ASTAP.app/Contents/MacOS/astap.app/Contents/MacOS/astap`
- Reads ASTAP's WCS solution and updates the existing Verified display.
- Uses the stored Astrometry.net API key as an automatic fallback if ASTAP
  cannot solve the image.
- Cached solutions continue to work regardless of which solver produced them.

## Test

1. Open an image that does not already have a cached solution.
2. Click **Plate Solve**.
3. The status should first say **Solving locally with ASTAP…**
4. On success, it should say **Verified — ASTAP**.
5. Test the Betta Fish image. If ASTAP fails and an Astrometry.net key is
   already stored, AstroFrame should proceed to the online fallback.

## Run

```bash
cd /Users/jamesglucksman/Projects/AstroFrame
python3 main.py
```
