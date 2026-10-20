# Changelog

This changelog records major public-development milestones. The many intermediate release-candidate builds were iterative development snapshots rather than separate public releases.

## 1.1.0 — In preparation

### AstroFrame Annotate

- Export annotated astrophotographs using plate-solved image coordinates.
- Add galaxy labels, outlines and detailed galaxy identification using HyperLEDA.
- Overlay celestial coordinate grids.
- Annotate nebulae, planetary nebulae, open and globular clusters using OpenNGC.
- Identify Barnard dark nebulae and Yale bright stars.
- Include Bica Magellanic Cloud catalogue objects.
- Display objects from imported observing collections.
- Add subject titles and configurable annotation styles.
- Save separate Atlas and Presentation annotation presets.
- Improve annotation placement, label collision avoidance and catalogue-position validation.
- Allow annotation exports to continue without galaxies when HyperLEDA is unavailable.

### Reliability

- Improve solved-image cache identification and legacy cache migration.
- Refine matching of extended catalogue objects to small image fields.
- Expand and stabilise the automated regression tests.

### Catalogue requirements

- HyperLEDA is required for galaxy annotations and is not automatically downloaded.
- OpenNGC, Barnard, Yale and Bica catalogue data are downloaded and cached when needed.
- Internet access may be required when using these catalogues for the first time.

## 1.0.0 — 2026-08-17

First public desktop release.

### Major capabilities

- Python/Qt desktop application replacing the original browser prototype
- Local ASTAP plate solving with Astrometry.net fallback
- Subject hints, target aliases and persistent solved-image cache
- User-imported catalogue collections and flexible source handling
- Full-precision catalogue coordinates, canonical object identities and marker placement
- Multiple observing sites, adjustable horizon and extended observability searches
- Multiple telescope/camera rigs with accurate FOV and rotation overlays
- Equipment Advisor with alternative-rig recommendations
- Interactive reframing and automatic mosaic planning
- NINA and ASIAIR framing handoff
- Regression tests for key solving, knowledge and observing behaviour
- Packaged macOS application

### RC1 acceptance fixes

- Preserve precise resolved coordinates from confirmed solver clues so catalogue markers do not fall back to coarser imported coordinates.
- Redirect macOS two-finger/wheel scrolling over Mosaic controls to the left sidebar, preventing accidental Mosaic changes while scrolling.
- Make visible Yes/No keyboard mnemonics respond to plain Y/N keys on macOS.
- Retain the flexible catalogue-import and first-run/onboarding fixes validated during RC1 testing.

## 0.1.0 — 2026-08-04

Initial working browser prototype.

### Added

- Local image loading
- ASI1600MM Pro + 442 mm profile
- ASI533MC Pro + 1448 mm profile
- Calculated FOV overlays
- Drag and rotation controls
- PNG overlay export
- Basic NINA value copying

The original 0.1 browser application is preserved under `legacy/browser-prototype/` for project history.
