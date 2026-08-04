# AstroFrame 0.4.2a

Hotfix for the plate-solve startup crash in 0.4.2.

## Fixed

The Solver Log attempted to display undefined variables named `width` and
`height`. It now uses the already available `width_px` and `height_px`
values, allowing the solver worker to start normally.

## Install

```bash
cd /Users/jamesglucksman/Projects/AstroFrame
unzip -o ~/Downloads/AstroFrame_0.4.2a_hotfix_full.zip
python3 main.py
```

## Check

After clicking **Plate Solve**, the Solver Log should continue beyond the
image name and pixel dimensions, then show the ASTAP attempt and exact command.
