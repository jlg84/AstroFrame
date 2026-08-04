from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QGraphicsPixmapItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsSimpleTextItem,
    QGraphicsView,
    QFrame,
)

from .equipment import Rig


class OverlayItem(QGraphicsRectItem):
    """Movable field-of-view rectangle with an attached rig label."""

    def __init__(self, rig: Rig) -> None:
        super().__init__()
        self.rig = rig
        self.setFlags(
            QGraphicsRectItem.GraphicsItemFlag.ItemIsMovable
            | QGraphicsRectItem.GraphicsItemFlag.ItemIsSelectable
        )
        self.setBrush(QBrush(Qt.BrushStyle.NoBrush))
        self.setPen(QPen(QColor(rig.colour), 3))
        self.setZValue(10)

        self.label = QGraphicsSimpleTextItem(rig.name, self)
        self.label.setBrush(QBrush(QColor(rig.colour)))
        label_font = QFont()
        label_font.setPointSize(10)
        label_font.setBold(True)
        self.label.setFont(label_font)
        self.label.setFlag(QGraphicsSimpleTextItem.GraphicsItemFlag.ItemIgnoresTransformations)

    def update_label_position(self) -> None:
        rect = self.rect()
        # The label remains just inside the upper-left corner of the frame.
        self.label.setPos(rect.left() + 8, rect.top() + 8)


class ImageViewer(QGraphicsView):
    image_loaded = Signal(str, int, int)
    overlay_changed = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setRenderHints(
            QPainter.RenderHint.Antialiasing
            | QPainter.RenderHint.SmoothPixmapTransform
        )
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.setFrameShape(QFrame.Shape.NoFrame)

        self.scene = QGraphicsScene(self)
        self.setScene(self.scene)
        self.pixmap_item: QGraphicsPixmapItem | None = None
        self.overlays: dict[str, OverlayItem] = {}
        self.reference_width_deg = 3.0
        self.current_rotation_deg = 0.0
        self.setBackgroundBrush(QBrush(QColor("#090B0F")))

    @property
    def has_image(self) -> bool:
        return self.pixmap_item is not None

    def load_image(self, path: str) -> None:
        pixmap = QPixmap(path)
        if pixmap.isNull():
            raise ValueError("The selected image could not be opened.")

        visible_rigs = [item.rig for item in self.overlays.values() if item.isVisible()]
        self.scene.clear()
        self.overlays.clear()

        self.pixmap_item = self.scene.addPixmap(pixmap)
        self.pixmap_item.setZValue(0)
        self.scene.setSceneRect(self.pixmap_item.boundingRect())
        self.reset_view()

        for rig in visible_rigs:
            self.set_rig_visible(rig, True)

        self.image_loaded.emit(path, pixmap.width(), pixmap.height())

    def wheelEvent(self, event) -> None:
        if self.pixmap_item is None:
            return
        factor = 1.18 if event.angleDelta().y() > 0 else 1 / 1.18
        self.scale(factor, factor)

    def set_reference_width(self, degrees: float) -> None:
        self.reference_width_deg = max(0.01, degrees)
        self._refresh_overlay_sizes()

    def set_rig_visible(self, rig: Rig, visible: bool) -> None:
        if self.pixmap_item is None:
            return
        if rig.key not in self.overlays:
            item = OverlayItem(rig)
            self.scene.addItem(item)
            self.overlays[rig.key] = item
            item.setPos(self.scene.sceneRect().center())
            item.setRotation(self.current_rotation_deg)
        self.overlays[rig.key].setVisible(visible)
        self._refresh_overlay_sizes()

    def set_rotation(self, degrees: float) -> None:
        self.current_rotation_deg = degrees
        for item in self.overlays.values():
            item.setRotation(degrees)
        self.overlay_changed.emit()

    def centre_overlays(self) -> None:
        if self.pixmap_item is None:
            return
        centre = self.scene.sceneRect().center()
        for item in self.overlays.values():
            item.setPos(centre)
        self.overlay_changed.emit()

    def reset_view(self) -> None:
        """Restore fit-to-window zoom and centred panning without changing framing."""
        if self.pixmap_item is None:
            return
        self.resetTransform()
        self.fitInView(self.scene.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)
        self.centerOn(self.scene.sceneRect().center())

    def reset_framing(self, reference_width_deg: float = 3.0) -> None:
        """Restore manual image scale, rotation and overlay positions."""
        self.set_reference_width(reference_width_deg)
        self.set_rotation(0.0)
        self.centre_overlays()

    def _refresh_overlay_sizes(self) -> None:
        if self.pixmap_item is None:
            return
        image_rect = self.pixmap_item.boundingRect()
        image_width_px = image_rect.width()
        image_height_deg = self.reference_width_deg * image_rect.height() / image_rect.width()

        for item in self.overlays.values():
            rig = item.rig
            width_px = image_width_px * rig.fov_width_deg / self.reference_width_deg
            height_px = image_rect.height() * rig.fov_height_deg / image_height_deg
            item.setRect(-width_px / 2, -height_px / 2, width_px, height_px)
            item.update_label_position()
