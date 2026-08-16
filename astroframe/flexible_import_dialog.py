from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QEvent
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from . import collection_import as _collection_import
from .collection_import import (
    FLEXIBLE_FIELDS,
    _read_flexible_table,
    count_flexible_rows,
    discover_flexible_source,
    flexible_preview_rows,
    import_flexible_collection,
)


class _WheelSafeComboBox(QComboBox):
    """Combo box that cannot be changed accidentally by trackpad scrolling.

    Wheel events are ignored unless the popup is open, allowing the surrounding
    QScrollArea to continue scrolling naturally when the pointer crosses a
    mapping selector.
    """

    def wheelEvent(self, event) -> None:
        view = self.view()
        if view is not None and view.isVisible():
            super().wheelEvent(event)
            return
        event.ignore()


def run_flexible_collection_import_dialog(self, path: str):
    """Small-screen-safe flexible catalogue mapper.

    The mapping/preview body scrolls vertically while Import/Cancel remain fixed
    at the bottom of the dialog, so the workflow remains usable on laptop-sized
    displays without changing display resolution.
    """
    try:
        table = discover_flexible_source(path)
    except Exception as exc:
        QMessageBox.warning(self, "Flexible catalogue import", str(exc))
        return None

    dialog = QDialog(self)
    dialog.setWindowTitle("Flexible Catalogue Import")
    dialog.resize(760, 720)
    dialog.setMinimumSize(620, 500)

    outer = QVBoxLayout(dialog)

    scroll = QScrollArea(dialog)
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.Shape.NoFrame)
    body = QWidget()
    layout = QVBoxLayout(body)
    scroll.setWidget(body)
    outer.addWidget(scroll, 1)

    intro = QLabel(
        "AstroFrame does not recognise this catalogue format, but it found a likely target table. "
        "Check the column mapping below, preview the interpreted targets, then import."
    )
    intro.setWordWrap(True)
    intro.setObjectName("helpText")
    layout.addWidget(intro)

    source_form = QFormLayout()
    sheet_combo = _WheelSafeComboBox()
    sheet_combo.addItems(table.get("sheet_names") or [table["sheet"]])
    sheet_combo.setCurrentText(table["sheet"])
    source_form.addRow("Worksheet", sheet_combo)
    header_spin = QSpinBox()
    header_spin.setRange(1, 9999)
    header_spin.setValue(int(table["header_row"]))
    source_form.addRow("Header row", header_spin)
    collection_name = QLineEdit(Path(path).stem)
    source_form.addRow("Collection name", collection_name)
    author_edit = QLineEdit()
    source_form.addRow("Author (optional)", author_edit)
    size_units = _WheelSafeComboBox()
    size_units.addItem("Arcminutes (common for DSO catalogues)", "arcmin")
    size_units.addItem("Degrees", "degrees")
    size_units.addItem("Arcseconds", "arcsec")
    source_form.addRow("Numeric size units", size_units)
    layout.addLayout(source_form)

    mapping_title = QLabel("COLUMN MAPPING")
    mapping_title.setObjectName("sectionHeading")
    layout.addWidget(mapping_title)
    mapping_form = QFormLayout()
    mapping_boxes: dict[str, QComboBox] = {}
    for key, label, required in FLEXIBLE_FIELDS:
        combo = _WheelSafeComboBox()
        mapping_boxes[key] = combo
        mapping_form.addRow(label + (" *" if required else ""), combo)
    layout.addLayout(mapping_form)

    preview_label = QLabel("PREVIEW")
    preview_label.setObjectName("sectionHeading")
    layout.addWidget(preview_label)
    preview = QPlainTextEdit()
    preview.setReadOnly(True)
    preview.setMaximumHeight(190)
    layout.addWidget(preview)
    status = QLabel()
    status.setWordWrap(True)
    status.setObjectName("helpText")
    layout.addWidget(status)
    layout.addStretch(1)

    buttons = QDialogButtonBox(
        QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Ok
    )
    buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Import")
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    outer.addWidget(buttons)

    current_table = {"value": table}
    rebuilding = {"value": False}

    def mapping_now() -> dict[str, int | None]:
        return {key: combo.currentData() for key, combo in mapping_boxes.items()}

    def refresh_preview(*_args) -> None:
        if rebuilding["value"]:
            return
        mapping = mapping_now()
        tbl = current_table["value"]
        rows = flexible_preview_rows(
            tbl, mapping, size_unit=str(size_units.currentData()), limit=5
        )
        count = count_flexible_rows(tbl, mapping)
        lines = []
        for item in rows:
            common = f" — {item['common_name']}" if item.get("common_name") else ""
            kind = f" · {item['type']}" if item.get("type") else ""
            con = f" · {item['constellation']}" if item.get("constellation") else ""
            lines.append(
                f"{item['name']}{common}{kind}{con}\n"
                f"  RA {item['ra_deg']:.5f}°   Dec {item['dec_deg']:+.5f}°"
            )
        preview.setPlainText(
            "\n".join(lines) if lines else "No usable target rows with the current mapping."
        )
        status.setText(
            f"{count} target{'s' if count != 1 else ''} currently have a usable name, RA and Dec. "
            "Only those rows will be imported. Unmapped source columns are still preserved as source metadata."
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(
            mapping.get("name") is not None and count > 0
        )

    def refill_mapping(*_args, preserve: bool = False) -> None:
        rebuilding["value"] = True
        tbl = current_table["value"]
        headers = tbl["headers"]
        # Resolve inference through the module at call time. RC1 startup patches
        # the generic inference rules for newly discovered real-world catalogue
        # edge cases; importing the function by value here would retain a stale
        # pre-patch reference for the lifetime of this module.
        inferred = _collection_import.infer_flexible_mapping(headers)
        for key, combo in mapping_boxes.items():
            previous_header = combo.currentData() if preserve else None
            combo.blockSignals(True)
            combo.clear()
            combo.addItem("— not mapped —", None)
            for i, header in enumerate(headers):
                combo.addItem(header, i)
            wanted = previous_header if previous_header is not None else inferred.get(key)
            idx = combo.findData(wanted)
            combo.setCurrentIndex(idx if idx >= 0 else 0)
            combo.blockSignals(False)
        rebuilding["value"] = False
        refresh_preview()

    def reload_source(*_args) -> None:
        try:
            tbl = _read_flexible_table(
                path,
                sheet_name=sheet_combo.currentText(),
                header_row=header_spin.value(),
            )
        except Exception as exc:
            status.setText(str(exc))
            buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(False)
            return
        current_table["value"] = tbl
        refill_mapping()

    sheet_combo.currentTextChanged.connect(reload_source)
    header_spin.valueChanged.connect(reload_source)
    size_units.currentIndexChanged.connect(refresh_preview)
    for combo in mapping_boxes.values():
        combo.currentIndexChanged.connect(refresh_preview)
    refill_mapping()

    if dialog.exec() != QDialog.DialogCode.Accepted:
        return None

    try:
        return import_flexible_collection(
            path,
            self.knowledge_store,
            table=current_table["value"],
            mapping=mapping_now(),
            collection_name=collection_name.text().strip() or Path(path).stem,
            author=author_edit.text().strip() or None,
            size_unit=str(size_units.currentData()),
        )
    except Exception as exc:
        QMessageBox.warning(self, "Flexible catalogue import", str(exc))
        return None
