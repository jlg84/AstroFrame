# AstroFrame 0.4.1 — Assisted ASTAP solving

## New

- Blind ASTAP solves now use an all-sky radius (`-r 180`) and automatic downsampling (`-z 0`).
- New **Target-assisted solve…** button.
- Enter a target name such as `NGC 2070` or `Omega Centauri`.
- Alternatively enter decimal RA hours and Dec degrees, e.g. `5.6453, -69.1`.
- Assisted ASTAP retries use the target centre, estimated field height, and a 10° search radius.
- Verified details now identify the solver, solve mode, and elapsed time.
- Astrometry.net remains the automatic fallback.

## Test

1. Open `Tarantula.tif`.
2. Click **Target-assisted solve…**.
3. Enter `NGC 2070` or `5.6453, -69.1`.
4. Expected result: **Verified — ASTAP**, usually within a few seconds.
