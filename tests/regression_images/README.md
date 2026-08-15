# AstroFrame Solver Regression Images

The actual image files are stored outside the Git repository because of their size.

Use these images after changes to the plate-solving code to check that previously working behaviour has not regressed.

| Image | Target type | Expected local result | Notes |
|---|---|---|---|
| NGC6357 SHO Combi2.tif | Emission nebula | ASTAP target-assisted success | Solved in about 5.5 seconds |
| NGC1313 Combi-LargeJPEG.jpg | Galaxy | ASTAP target-assisted success | Solved in about 7.2 seconds |
| NGC104 Combi-LargeJPEG.jpg | Tight globular cluster | ASTAP may fail; Astrometry.net fallback | Very crowded field |
| NGC55 close-up | Galaxy | ASTAP may fail; Astrometry.net fallback | Tight crop |
| Unknown reference image | Unknown | Blind ASTAP or Astrometry.net | Original AstroFrame use case |

## Test procedure

For each image:

1. Open the image in AstroFrame.
2. Record the selected solver strategy.
3. Try a blind solve.
4. Where the target is known, try again with a Target Hint.
5. Record:
   - solver used;
   - success or failure;
   - solve time;
   - any unexpected fallback;
   - any error shown in the Solver Log.

## Pass criteria

- Images previously solved by ASTAP should continue to solve locally.
- Genuine ASTAP failures should fall back cleanly when the strategy permits it.
- A failed Target Hint lookup must not prevent the solve from continuing.
- Successful ASTAP solutions must produce a verified WCS result in AstroFrame.