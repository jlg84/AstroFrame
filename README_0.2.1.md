# AstroFrame 0.2.1

This update replaces the default Qt appearance with a clearer dark inspector and improves the handling of unsolved reference images.

## Run

```bash
cd /Users/jamesglucksman/Projects/AstroFrame
python3 main.py
```

## Changes to test

1. Open a reference image.
2. Turn both equipment frames on.
3. Confirm that each frame has a visible label.
4. Change **Image angular width** and confirm that only the frames rescale against the unchanged image.
5. Use **Reset view** after zooming and panning.
6. Use **Reset framing** after changing image width, rotation, and frame positions.
7. Close and reopen AstroFrame and check that window size, selected rigs, and image angular width are remembered.

## Meaning of Image angular width

It is the angular width of the entire reference image, not the FOV of the selected rig. Until an image is plate-solved, this is an estimate used to scale the fixed equipment fields against the background.
