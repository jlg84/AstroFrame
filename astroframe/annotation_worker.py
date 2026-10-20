from __future__ import annotations

from pathlib import Path
from threading import Event
from tempfile import TemporaryDirectory

from PySide6.QtCore import QObject, Signal, Slot

from .annotations import annotate_galaxies, annotate_openngc_objects, annotate_barnard_objects
from .annotations import annotate_bright_stars
from .coordinate_grid import render_coordinate_grid


class AnnotationWorker(QObject):
    progress = Signal(str)
    succeeded = Signal(int, str)
    failed = Signal(str)
    finished = Signal()
    cancelled = Signal()

    def cancel(self) -> None:
        self._cancel_event.set()

    def _check_cancelled(self) -> None:
        if self._cancel_event.is_set():
            raise InterruptedError("Annotation cancelled")

    def __init__(
        self,
        image_path: str | Path,
        wcs_path: str | Path,
        catalogue_path: str | Path,
        output_path: str | Path,
        *,
        include_galaxies: bool = True,
        galaxy_detail: str = "featured",
        include_grid: bool = False,
        include_nebulae: bool = False,
        include_clusters: bool = False,
        include_planetary_nebulae: bool = False,
        include_open_clusters: bool = False,
        include_globular_clusters: bool = False,
        include_dark_nebulae: bool = False,
        include_bright_stars: bool = False,
        star_magnitude_limit: float = 6.0,
        output_max_edge: int = 0,
        annotation_style: str = "automatic",
        show_outlines: bool = True,
        include_imported_collections: bool = False,
        include_bica: bool = False,
        bica_detail: str = "featured",
        primary_subject: dict | None = None,
        show_subject_title: bool = True,
    ) -> None:
        super().__init__()
        self._cancel_event = Event()
        self.image_path = Path(image_path)
        self.wcs_path = Path(wcs_path)
        self.catalogue_path = Path(catalogue_path) if catalogue_path is not None else None
        self.output_path = Path(output_path)
        self.include_galaxies = include_galaxies
        self.galaxy_detail = (
            galaxy_detail if galaxy_detail in ("featured", "detailed")
            else "featured"
        )
        self.include_grid = include_grid
        self.include_nebulae = include_nebulae
        self.include_clusters = include_clusters
        self.include_planetary_nebulae = include_planetary_nebulae
        self.include_open_clusters = include_open_clusters
        self.include_globular_clusters = include_globular_clusters
        self.include_dark_nebulae = include_dark_nebulae
        self.include_bright_stars = include_bright_stars
        self.star_magnitude_limit = float(star_magnitude_limit)
        self.output_max_edge = int(output_max_edge)
        self.annotation_style = annotation_style
        self.show_outlines = show_outlines
        self.include_imported_collections = include_imported_collections
        self.include_bica = include_bica
        self.bica_detail = (
            bica_detail if bica_detail in ("featured", "detailed")
            else "featured"
        )
        self.primary_subject = primary_subject
        self.show_subject_title = show_subject_title

    def _collect_imported_annotations(self):
        """Collect marker-only annotations from imported target collections."""
        from .knowledge import KnowledgeStore
        from .annotations import Annotation, image_dimensions, sky_to_pixel
        from astropy.coordinates import SkyCoord
        import astropy.units as u

        store = KnowledgeStore()
        width, height = image_dimensions(self.image_path)

        from astropy.wcs import WCS
        from astropy.io import fits

        with fits.open(self.wcs_path) as hdul:
            wcs = WCS(hdul[0].header).celestial

        centre_x = (width - 1) / 2
        centre_y = (height - 1) / 2
        centre_ra, centre_dec = wcs.all_pix2world(
            [[centre_x, height - 1 - centre_y]], 0
        )[0]

        # Broad candidate search; exact inclusion is checked in pixels.
        matches = store.entries_near_position(
            float(centre_ra), float(centre_dec), 5.0
        )

        results = []
        seen = set()
        for target, collection, entry, distance in matches:
            self._check_cancelled()
            if target.id in seen:
                continue
            seen.add(target.id)

            try:
                x, y = sky_to_pixel(
                    self.wcs_path,
                    target.ra_deg,
                    target.dec_deg,
                    image_height=height,
                )
            except (ValueError, KeyError, ZeroDivisionError):
                continue

            if not (0 <= x < width and 0 <= y < height):
                continue

            results.append(Annotation(
                name=target.canonical_name,
                x=x,
                y=y,
                colour="#E2C17A",
                prefer_marker=True,
            ))

        self.progress.emit(
            "Imported collection objects inside image: "
            + (", ".join(item.name for item in results) if results else "none")
        )
        return results

    def _collect_bica_annotations(self, existing_annotations):
        """Select prominent, well-separated Bica objects inside the image."""
        from math import hypot
        from .annotations import (
            Annotation, image_dimensions, image_sky_coverage, sky_to_pixel
        )
        from .reference_catalog import query_bica, bica_cache_ready

        width, height = image_dimensions(self.image_path)
        centre_ra, centre_dec, radius = image_sky_coverage(
            self.wcs_path, width, height
        )

        # Bica covers the Magellanic Clouds, not the entire sky.
        near_lmc = hypot(
            (centre_ra - 80.9) * 0.35, centre_dec + 69.8
        ) < radius + 9
        near_smc = hypot(
            (centre_ra - 13.2) * 0.4, centre_dec + 72.8
        ) < radius + 7

        if not (near_lmc or near_smc):
            self.progress.emit("Bica: outside Magellanic Cloud region.")
            return []

        if not bica_cache_ready("featured"):
            self.progress.emit("Bica: local catalogue cache unavailable.")
            return []

        objects = query_bica(
            centre_ra, centre_dec,
            2 * radius, 2 * radius,
            "featured",
        )

        occupied = [
            (float(item.x), float(item.y))
            for item in existing_annotations
        ]

        if self.primary_subject:
            ra = self.primary_subject.get("subject_ra_deg")
            dec = self.primary_subject.get("subject_dec_deg")
            if ra is not None and dec is not None:
                x, y = sky_to_pixel(
                    self.wcs_path, float(ra), float(dec),
                    image_height=height,
                )
                if 0 <= x < width and 0 <= y < height:
                    occupied.append((x, y))

        selected = []
        detailed = self.bica_detail == "detailed"
        limit = 100 if detailed else 30
        spacing = 45 if detailed else 70

        # Featured retains the original score-first selection.
        # Detailed makes an initial pass across image regions.
        ranked = sorted(
            objects, key=lambda item: item.feature_score, reverse=True
        )

        if detailed:
            cells = {}
            for obj in ranked:
                self._check_cancelled()
                x, y = sky_to_pixel(
                    self.wcs_path, obj.ra_deg, obj.dec_deg,
                    image_height=height,
                )
                if not (0 <= x < width and 0 <= y < height):
                    continue
                col = min(5, int(x * 6 / width))
                row = min(3, int(y * 4 / height))
                cells.setdefault((row, col), []).append((obj, x, y))

            # Round-robin selection gives every populated region
            # opportunities before any one region dominates.
            ordered = []
            maximum = max((len(v) for v in cells.values()), default=0)
            for index in range(maximum):
                for cell in sorted(cells):
                    if index < len(cells[cell]):
                        ordered.append(cells[cell][index])
        else:
            ordered = []
            for obj in ranked:
                self._check_cancelled()
                x, y = sky_to_pixel(
                    self.wcs_path, obj.ra_deg, obj.dec_deg,
                    image_height=height,
                )
                if 0 <= x < width and 0 <= y < height:
                    ordered.append((obj, x, y))

        for obj, x, y in ordered:
            self._check_cancelled()

            if any(
                hypot(x - px, y - py) < spacing
                for px, py in occupied
            ):
                continue

            selected.append(Annotation(
                name=obj.name,
                x=x,
                y=y,
                colour="#D8B878",
                prefer_marker=True,
            ))
            occupied.append((x, y))

            if len(selected) == limit:
                break

        self.progress.emit(
            f"Bica: selected {len(selected)} {self.bica_detail} objects "
            f"from {len(objects)} candidates."
        )
        return selected

    @Slot()
    def run(self) -> None:
        try:
            from .annotations import render_annotated_image
            from .annotations import image_dimensions, image_sky_coverage
            from .annotations import sky_to_pixel, galaxy_ellipse_pixels
            from .annotations import Annotation

            self._check_cancelled()
            selected_modes = []
            if self.include_nebulae:
                selected_modes.append("nebulae")
            if self.include_clusters:
                selected_modes.append("clusters")
            if self.include_planetary_nebulae:
                selected_modes.append("planetary_nebulae")
            if self.include_open_clusters:
                selected_modes.append("open_clusters")
            if self.include_globular_clusters:
                selected_modes.append("globular_clusters")

            needs_galaxies = self.include_galaxies
            needs_grid = self.include_grid
            count = 0

            annotations = []
            if needs_galaxies:
                self.progress.emit("Collecting galaxy annotations…")
                galaxy_annotations = annotate_galaxies(
                    self.image_path, self.wcs_path, self.catalogue_path,
                    self.output_path, progress=self.progress.emit,
                    collect_only=True,
                    min_major_arcmin=(
                        0.3 if self.galaxy_detail == "detailed" else 1.0
                    ),
                )

                if self.galaxy_detail == "detailed":
                    width, height = image_dimensions(self.image_path)
                    cells = {}

                    # Distribute galaxies across the image, prioritising
                    # larger objects within each region.
                    for item in galaxy_annotations:
                        col = min(5, max(0, int(item.x * 6 / width)))
                        row = min(3, max(0, int(item.y * 4 / height)))
                        cells.setdefault((row, col), []).append(item)

                    for group in cells.values():
                        group.sort(
                            key=lambda item: item.major_arcmin,
                            reverse=True,
                        )

                    selected = []
                    maximum = max(
                        (len(group) for group in cells.values()),
                        default=0,
                    )

                    for index in range(maximum):
                        self._check_cancelled()
                        for cell in sorted(cells):
                            if index < len(cells[cell]):
                                selected.append(cells[cell][index])
                                if len(selected) >= 100:
                                    break
                        if len(selected) >= 100:
                            break

                    self.progress.emit(
                        f"Detailed galaxies: selected {len(selected)} "
                        f"of {len(galaxy_annotations)} candidates."
                    )
                    # Tiny galaxies are clearer as identification markers.
                    from dataclasses import replace

                    galaxy_annotations = [
                        replace(item, prefer_marker=True)
                        if item.major_radius_px < 4.0
                        else item
                        for item in selected
                    ]

                annotations.extend(galaxy_annotations)

            for mode in selected_modes:
                self.progress.emit(f"Collecting OpenNGC {mode}…")
                annotations.extend(annotate_openngc_objects(
                    self.image_path, self.wcs_path, self.output_path,
                    mode=mode, progress=self.progress.emit,
                    collect_only=True,
                ))

            if self.include_dark_nebulae:
                self.progress.emit("Collecting Barnard dark nebulae…")
                annotations.extend(annotate_barnard_objects(
                    self.image_path, self.wcs_path, self.output_path,
                    progress=self.progress.emit,
                    collect_only=True,
                ))

            if self.include_bright_stars:
                self.progress.emit("Collecting Yale bright stars…")
                annotations.extend(annotate_bright_stars(
                    self.image_path, self.wcs_path, self.output_path,
                    magnitude_limit=self.star_magnitude_limit,
                    progress=self.progress.emit,
                    collect_only=True,
                ))

            self._check_cancelled()

            if self.include_imported_collections:
                import re

                def object_key(name):
                    text = str(name or "").upper().strip()
                    match = re.search(
                        r"(?<![A-Z0-9])(NGC|IC|HR|M)\s*0*(\d+)(?!\d)",
                        text,
                    )
                    if match:
                        return f"{match.group(1)}{int(match.group(2))}"
                    return re.sub(r"[^A-Z0-9]", "", text)

                existing = {object_key(item.name) for item in annotations}
                if self.primary_subject:
                    existing.add(object_key(self.primary_subject.get("name")))

                self.progress.emit("Collecting imported collection objects…")
                imported_added = 0
                for item in self._collect_imported_annotations():
                    key = object_key(item.name)
                    if key and key not in existing:
                        annotations.append(item)
                        existing.add(key)
                        imported_added += 1
                self.progress.emit(
                    f"Imported collections added {imported_added} new annotations."
                )

            # Avoid duplicating the titled primary subject in the catalogue
            # annotations. Normalize spacing and leading zeros in IDs.
            if self.primary_subject and self.primary_subject.get("name"):
                import re

                def normalized_identifier(value):
                    text = str(value or "").strip().upper()
                    match = re.fullmatch(
                        r"(NGC|IC|M)\s*0*(\d+)", text
                    )
                    if not match:
                        # Also recognise identifiers in descriptive labels,
                        # such as "Carina Nebula (NGC 3372)".
                        match = re.search(
                            r"(?<![A-Z0-9])(NGC|IC|M)\s*0*(\d+)(?!\d)",
                            text,
                        )
                    if match:
                        return f"{match.group(1)}{int(match.group(2))}"
                    return " ".join(text.split())

                primary_id = normalized_identifier(
                    self.primary_subject["name"]
                )
                annotations = [
                    item for item in annotations
                    if normalized_identifier(item.name) != primary_id
                ]

            if self.include_bica:
                self.progress.emit("Collecting featured Bica objects…")
                annotations.extend(
                    self._collect_bica_annotations(annotations)
                )

            count = len(annotations)
            if annotations:
                self.progress.emit(f"Rendering {count} annotations together…")
                if needs_grid:
                    with TemporaryDirectory() as temp_dir:
                        annotated_path = Path(temp_dir) / "annotated.png"
                        render_annotated_image(
                            self.image_path, annotations, annotated_path,
                            annotation_style=self.annotation_style,
                            show_outlines=self.show_outlines,
                        )
                        self.progress.emit("Adding coordinate grid…")
                        render_coordinate_grid(
                            annotated_path, self.wcs_path, self.output_path,
                        )
                else:
                    render_annotated_image(
                        self.image_path, annotations, self.output_path,
                        annotation_style=self.annotation_style,
                        show_outlines=self.show_outlines,
                    )
            elif needs_grid:
                self.progress.emit("Rendering coordinate grid…")
                render_coordinate_grid(
                    self.image_path, self.wcs_path, self.output_path,
                )
            else:
                import shutil
                shutil.copy2(self.image_path, self.output_path)

            # Add the primary-subject title last, above annotations and grid.
            if (
                self.show_subject_title
                and self.primary_subject
                and self.primary_subject.get("name")
            ):
                from .annotations import render_primary_subject_title
                self._check_cancelled()
                self.progress.emit("Adding primary subject title…")
                render_primary_subject_title(
                    self.output_path,
                    self.primary_subject,
                    self.output_path,
                )

            # Export the final opaque annotation as RGB PNG, without alpha.
            if self.output_path.suffix.lower() == ".png":
                from PySide6.QtGui import QImage
                final_image = QImage(str(self.output_path))
                if final_image.isNull():
                    raise OSError("Could not read completed annotated PNG")
                if self.output_max_edge > 0:
                    from PySide6.QtCore import Qt
                    long_edge = max(final_image.width(), final_image.height())
                    if long_edge > self.output_max_edge:
                        self.progress.emit("Resizing annotated image…")
                        final_image = final_image.scaled(
                            self.output_max_edge,
                            self.output_max_edge,
                            Qt.AspectRatioMode.KeepAspectRatio,
                            Qt.TransformationMode.SmoothTransformation,
                        )
                rgb = final_image.convertToFormat(QImage.Format.Format_RGB888)
                if not rgb.save(str(self.output_path), "PNG"):
                    raise OSError("Could not save RGB annotated PNG")

            self._check_cancelled()
            self.succeeded.emit(count, str(self.output_path))
        except InterruptedError:
            self.output_path.unlink(missing_ok=True)
            self.cancelled.emit()
        except Exception as exc:
            self.failed.emit(str(exc))
        finally:
            self.finished.emit()
