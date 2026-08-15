# Using AstroFrame with ASIAIR

AstroFrame can prepare framing information for use with ASIAIR as well as NINA.

## Typical workflow

1. Load and solve the reference image in AstroFrame.
2. Select the observing site and the telescope/camera rig you intend to use.
3. Use AstroFrame to evaluate the field of view, rotation and framing.
4. Reframe the field or create a mosaic where required.
5. Export the ASIAIR handoff information from AstroFrame.
6. Use the exported target/framing information in ASIAIR to reproduce the planned field.

## What to verify

Before beginning an imaging run, confirm that the target coordinates, camera rotation and intended framing in ASIAIR agree with the AstroFrame plan. For mosaics, also verify the panel layout before starting the sequence.

This document will be expanded as the ASIAIR handoff workflow is further tested and refined during the AstroFrame 1.0 release-candidate cycle.
