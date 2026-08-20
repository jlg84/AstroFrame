# AstroFrame

AstroFrame is a cross-platform astrophotography framing and planning desktop application. Give it a reference image and it helps answer the practical question: **can I reproduce this framing with my own equipment, and what should I send to NINA or ASIAIR?**

## Current status

**AstroFrame 1.0 RC1 — private release-candidate testing**

AstroFrame is built with Python/Qt and is intended to remain platform-independent. The macOS application has passed the RC1 smoke test from a clean GitHub build. The current release work is focused on installation/distribution testing with a small group of outside testers before wider release. Windows packaging and testing remain part of the 1.0 release work.

The original browser prototype in this repository is historical and no longer represents the current application.

## What AstroFrame does

- Loads reference astrophotography images, including JPEG, PNG and TIFF, and reuses cached solutions for identical images
- Plate-solves images locally with ASTAP, with subject hints and Astrometry.net fallback
- Recognises common target names and aliases from filenames and user hints
- Displays solved field geometry and catalogue objects on the reference image
- Imports user catalogue collections and preserves source-coordinate precision
- Canonicalises duplicate catalogue identities and selects the best available object position
- Stores multiple observing sites and evaluates target observability
- Searches beyond the immediate observing window when necessary
- Stores multiple telescope/camera rigs and compares their fields of view
- Recommends a better-matching rig when the selected field is unsuitable
- Supports reframing, rotation and mosaic planning
- Exports framing information for use in NINA and ASIAIR

## macOS RC1 build and package

Build the application from source with:

```bash
chmod +x build_mac_app.command
./build_mac_app.command
```

After the resulting `dist/AstroFrame.app` has passed its smoke test, create the private-test disk image with:

```bash
chmod +x package_mac_release.command
./package_mac_release.command
```

The packaging script creates a versioned macOS DMG and SHA-256 checksum under `release/`. It does not Developer ID sign or notarize the private RC1 package.

See [macOS installation](docs/MAC_INSTALL.md) and the [RC1 tester guide](docs/RC1_TESTER_GUIDE.md).

The Windows package has passed its RC1 smoke test. Build/package instructions are provided below, and installation requirements are covered in the Windows installation guide.

## Windows RC1 build and package

Build the Windows application from a Windows Python environment with:

```powershell
pyinstaller --clean --noconfirm AstroFrame-Windows.spec
```

After `dist\AstroFrame\AstroFrame.exe` has passed its smoke test, create the private-test ZIP and SHA-256 checksum with:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File package_windows_release.ps1
```

The packaging script creates a versioned Windows x64 ZIP and checksum under `release/`, and includes `READ ME FIRST.md` in the package.

ASTAP and an ASTAP star database are required for normal local plate solving and are not bundled with AstroFrame.

See [Windows installation](docs/WINDOWS_INSTALL.md) and the [RC1 tester guide](docs/RC1_TESTER_GUIDE.md).

## Core workflow

1. Load a reference image.
2. Let AstroFrame identify/plate-solve it, or confirm/provide the subject when asked.
3. Choose an observing site and imaging rig.
4. Inspect object markers, observability and equipment advice.
5. Adjust framing/rotation or build a mosaic if needed.
6. Export the result for NINA or ASIAIR.

## Documentation

- [Roadmap](ROADMAP.md)
- [Changelog](CHANGELOG.md)
- [Equipment notes](docs/EQUIPMENT.md)
- [Observing sites](docs/OBSERVING_SITES.md)
- [Using AstroFrame with NINA](docs/USING_WITH_NINA.md)
- [Using AstroFrame with ASIAIR](docs/USING_WITH_ASIAIR.md)
- [macOS installation](docs/MAC_INSTALL.md)
- [Windows installation](docs/WINDOWS_INSTALL.md)
- [RC1 tester guide](docs/RC1_TESTER_GUIDE.md)
- [Project vision](VISION.md)

## Development note

AstroFrame 1.0 RC1 is feature-frozen while installation and first-use behaviour are tested outside the development machines. Release-blocking bugs discovered during this phase may still be fixed; new feature ideas are deferred beyond RC1 unless they prevent normal use.

## Repository owner

Created for and tested by [jlg84](https://github.com/jlg84).
