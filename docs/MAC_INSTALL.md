# Installing AstroFrame 1.0 RC1 on macOS

AstroFrame 1.0 RC1 is currently being distributed to a small group of private testers before a wider release.

## Install

1. Open the AstroFrame DMG.
2. Drag **AstroFrame.app** to the **Applications** shortcut in the DMG.
3. Eject the AstroFrame disk image.
4. Open AstroFrame from your Applications folder.

## First-launch security notice for the private RC1

This private RC1 package is not yet Developer ID signed and notarized for public distribution. macOS may therefore refuse the first launch even when the package came directly from the AstroFrame project owner.

Only override macOS security if you expected this private test build and received it from a source you trust. After attempting to open AstroFrame, macOS may offer **Open Anyway** under **System Settings → Privacy & Security**. Once approved, subsequent launches should behave normally.

The DMG is accompanied by a SHA-256 checksum file. Private testers can use it to confirm that the downloaded package is byte-for-byte identical to the package produced for testing.

## Plate solving

AstroFrame can use ASTAP for local plate solving. ASTAP is an external application and is not bundled inside AstroFrame. Install it separately if you want local solving. AstroFrame can also use Astrometry.net as a configured fallback.

## First run

On first launch:

1. Personalise AstroFrame.
2. Add your observing site.
3. Add one or more telescope/camera rigs.
4. Load a reference image and let AstroFrame identify or solve it.
5. Optionally import one or more target collections.

User settings, observing sites, rigs, cached solutions and imported knowledge are stored outside the application bundle, so replacing AstroFrame.app with a newer build should not remove them.

## Private RC1 status

The macOS application itself has passed the project's RC1 smoke tests. This installation package is the next test: the aim is to find any assumptions that only worked on the development machines before wider distribution.
