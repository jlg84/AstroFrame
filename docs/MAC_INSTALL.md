# Installing AstroFrame 1.1.1 on macOS

AstroFrame 1.1.1 is distributed as a macOS disk image (DMG).

## Install

1. Open the AstroFrame DMG.
2. Drag **AstroFrame.app** to the **Applications** shortcut in the DMG.
3. Eject the AstroFrame disk image.
4. Open AstroFrame from your Applications folder.

## First-launch security notice

AstroFrame 1.1.1 is not yet Developer ID signed and notarized. macOS may therefore refuse the first launch even when the package came directly from the AstroFrame project owner.

Only override macOS security if you expected this AstroFrame build and received it from a source you trust. After attempting to open AstroFrame, macOS may offer **Open Anyway** under **System Settings → Privacy & Security**. Once approved, subsequent launches should behave normally.

The DMG is accompanied by a SHA-256 checksum file. You can use it to confirm that the downloaded package is byte-for-byte identical to the published release artifact.

## Plate solving

AstroFrame can use ASTAP for local plate solving. ASTAP is an external application and is not bundled inside AstroFrame. Install it separately if you want local solving. AstroFrame can also use Astrometry.net as a configured fallback.

## Annotation catalogues

AstroFrame 1.1 introduces **AstroFrame Annotate**, which exports labelled astronomical photographs using plate-solved image coordinates.

- **Galaxies:** Galaxy labels and outlines require the HyperLEDA catalogue. AstroFrame looks for a local `hyperleda.csv`, including in supported ASTAP catalogue locations. HyperLEDA is not automatically downloaded. If it is unavailable, AstroFrame offers to continue with the other selected annotations.
- **Other astronomical objects:** OpenNGC, Barnard dark nebulae, Yale bright stars and Bica catalogue data are downloaded and cached when needed. Internet access may be required the first time these features are used.
- **Coordinate grids:** These use the image's plate-solving information and do not require HyperLEDA.

ASTAP's star database, such as D50, is separate from HyperLEDA. Installing a star database does not by itself provide galaxy annotations.

## First run

On first launch:

1. Personalise AstroFrame.
2. Add your observing site.
3. Add one or more telescope/camera rigs.
4. Load a reference image and let AstroFrame identify or solve it.
5. Optionally import one or more target collections.

User settings, observing sites, rigs, cached solutions and imported knowledge are stored outside the application bundle, so replacing AstroFrame.app with a newer build should not remove them.
