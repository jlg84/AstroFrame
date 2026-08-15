from __future__ import annotations

from PySide6.QtCore import Qt, Signal, QPointF, QEvent, QTimer
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPen, QPixmap, QPolygonF, QCursor
from PySide6.QtWidgets import (
    QGraphicsEllipseItem,
    QGraphicsItem,
    QGraphicsLineItem,
    QGraphicsPixmapItem,
    QGraphicsPolygonItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsSimpleTextItem,
    QGraphicsView,
    QFrame,
    QWidget,
)

from .equipment import Rig


class CatalogueDotItem(QGraphicsEllipseItem):
    """Catalogue centre with a generous invisible mouse target."""
    def __init__(self, target_id: str, x: float, y: float, callback) -> None:
        # The item itself is deliberately much larger than the painted dot.  This
        # makes catalogue objects easy to discover with a mouse/trackpad without
        # making the photograph visually busier.
        self.hit_radius = 15.0
        self.dot_radius = 5.0
        # Keep the item's geometry local to its own origin, then position the item
        # at the catalogue coordinate.  The previous dev9t build mixed scene
        # coordinates into the parent rect and local coordinates into its child
        # dot, which displaced the painted dots away from their catalogue centres.
        super().__init__(-self.hit_radius, -self.hit_radius, self.hit_radius*2, self.hit_radius*2)
        self.setPos(x, y)
        self.target_id = target_id
        self.callback = callback
        self.setPen(QPen(Qt.PenStyle.NoPen))
        self.setBrush(QBrush(Qt.BrushStyle.NoBrush))
        self.setZValue(24)
        self.setToolTip("Click to identify this catalogue object")
        self.setAcceptedMouseButtons(Qt.MouseButton.LeftButton)
        self.setAcceptHoverEvents(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.dot = QGraphicsEllipseItem(
            -self.dot_radius, -self.dot_radius,
            self.dot_radius*2, self.dot_radius*2, self
        )
        self.dot.setPen(QPen(QColor("#F3D34A"), 1.5))
        self.dot.setBrush(QBrush(QColor("#F3D34A")))
        self.dot.setAcceptedMouseButtons(Qt.MouseButton.NoButton)

    def hoverEnterEvent(self, event) -> None:
        r = 7.0
        self.dot.setRect(-r, -r, r*2, r*2)
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event) -> None:
        r = self.dot_radius
        self.dot.setRect(-r, -r, r*2, r*2)
        super().hoverLeaveEvent(event)

    def mousePressEvent(self, event) -> None:
        if self.callback is not None:
            self.callback(self.target_id)
        event.accept()


class RigLabelItem(QGraphicsSimpleTextItem):
    """Clickable rig name without making the whole frame a selection target."""
    def __init__(self, text: str, rig_key: str, callback, parent=None) -> None:
        super().__init__(text, parent)
        self.rig_key = rig_key
        self.callback = callback
        self.setAcceptedMouseButtons(Qt.MouseButton.LeftButton)
        self.setAcceptHoverEvents(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mousePressEvent(self, event) -> None:
        if self.callback is not None:
            self.callback(self.rig_key)
        event.accept()


class OverlayItem(QGraphicsPolygonItem):
    """Field-of-view polygon with explicit scene geometry.

    dev11y deliberately avoids QGraphicsItem positioning and rotation transforms.
    The four sensor corners are recomputed directly in scene coordinates whenever
    the centre, size, or rotation changes.  This makes the painted geometry itself
    the source of truth, which is more robust on macOS/Qt than relying on setPos()
    or an item transform during an active pointer gesture.
    """

    def __init__(self, rig: Rig, moved_callback=None, label_clicked_callback=None) -> None:
        super().__init__()
        self.rig = rig
        self.moved_callback = moved_callback
        self.label_clicked_callback = label_clicked_callback
        self._centre = QPointF(0.0, 0.0)
        self._width = 1.0
        self._height = 1.0
        self._rotation_deg = 0.0
        self.setFlags(
            QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
            | QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges
        )
        self.setBrush(QBrush(QColor(0, 0, 0, 0)))
        self.setPen(QPen(QColor(rig.colour), 3))
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.setZValue(10)

        self.label = RigLabelItem(rig.name, rig.key, self.label_clicked_callback, self)
        self.label.setBrush(QBrush(QColor(rig.colour)))
        label_font = QFont()
        label_font.setPointSize(10)
        label_font.setBold(True)
        self.label.setFont(label_font)
        self.label.setFlag(QGraphicsSimpleTextItem.GraphicsItemFlag.ItemIgnoresTransformations)
        self._rebuild_polygon()

    def _rebuild_polygon(self) -> None:
        import math
        cx, cy = self._centre.x(), self._centre.y()
        hw, hh = self._width / 2.0, self._height / 2.0
        theta = math.radians(self._rotation_deg)
        c, sn = math.cos(theta), math.sin(theta)
        corners = []
        for dx, dy in ((-hw, -hh), (hw, -hh), (hw, hh), (-hw, hh)):
            x = cx + c * dx - sn * dy
            y = cy + sn * dx + c * dy
            corners.append(QPointF(x, y))
        self.setPolygon(QPolygonF(corners))
        self.update_label_position()
        self.update()

    def update_label_position(self) -> None:
        bounds = self.polygon().boundingRect()
        self.label.setPos(bounds.left() + 8.0, bounds.top() + 8.0)

    def scene_center(self) -> QPointF:
        return QPointF(self._centre)

    def scene_corners(self) -> list[QPointF]:
        # Polygon geometry is stored directly in scene coordinates in dev11y.
        return [QPointF(p) for p in self.polygon()]

    def set_scene_center(self, x: float | QPointF, y: float | None = None) -> None:
        if isinstance(x, QPointF):
            self._centre = QPointF(x)
        else:
            self._centre = QPointF(float(x), float(y if y is not None else 0.0))
        self._rebuild_polygon()
        if self.moved_callback is not None:
            self.moved_callback()

    def set_overlay_size(self, width: float, height: float) -> None:
        self._width = max(0.0, float(width))
        self._height = max(0.0, float(height))
        self._rebuild_polygon()

    def setRotation(self, angle: float) -> None:
        # Intentionally do NOT invoke QGraphicsItem.setRotation().  The rotated
        # corner positions are baked directly into the polygon geometry.
        self._rotation_deg = float(angle)
        self._rebuild_polygon()

    def rotation(self) -> float:
        return float(self._rotation_deg)

    def mousePressEvent(self, event) -> None:
        self.setCursor(Qt.CursorShape.ClosedHandCursor)
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        super().mouseReleaseEvent(event)
        self.setCursor(Qt.CursorShape.OpenHandCursor)


class DragGhostWidget(QWidget):
    """Viewport child used solely for live rig-frame dragging.

    This intentionally lives outside QGraphicsScene.  It is a transparent child
    widget over the viewport, so its repaint/movement path is independent of Qt's
    graphics-item scene cache and scene paint cycle.
    """
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._points = []
        self._colour = QColor("#FFFFFF")
        self._name = ""
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.hide()

    def set_frame(self, points, colour: str, name: str) -> None:
        self._points = [QPointF(p) for p in points]
        self._colour = QColor(colour)
        self._name = str(name)
        self.update()

    def paintEvent(self, event) -> None:
        if len(self._points) != 4:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(QPen(self._colour, 3))
        painter.setBrush(QBrush(Qt.BrushStyle.NoBrush))
        painter.drawPolygon(QPolygonF(self._points))
        anchor = self._points[0]
        font = QFont()
        font.setPointSize(10)
        font.setBold(True)
        painter.setFont(font)
        painter.drawText(int(anchor.x() + 8), int(anchor.y() + 18), self._name)
        painter.end()


class ImageViewer(QGraphicsView):
    image_loaded = Signal(str, int, int)
    overlay_changed = Signal()
    catalogue_marker_clicked = Signal(str)
    drag_debug = Signal(str)
    placement_clicked = Signal(float, float)
    rig_label_clicked = Signal(str)

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
        self._placement_mode = False
        # RC22i: only the explicitly selected Active Rig for Framing may be dragged.
        self._active_drag_rig_key: str | None = None

        self.scene = QGraphicsScene(self)
        self.setScene(self.scene)
        self.pixmap_item: QGraphicsPixmapItem | None = None
        self.overlays: dict[str, OverlayItem] = {}
        self.target_marker_items: list = []
        self.target_marker_id: str | None = None
        self.catalogue_dot_items: list = []
        self.rig_rotation_offsets: dict[str, float] = {}
        self.reference_width_deg = 3.0
        self.current_rotation_deg = 0.0
        self.setBackgroundBrush(QBrush(QColor("#090B0F")))

        self.minimum_zoom = 0.005
        self.maximum_zoom = 20.0

        # dev11s: while a rig frame is being dragged, poll the global cursor
        # position at ~60 Hz.  macOS/Qt can acknowledge the press (hence the
        # closed-hand cursor) yet fail to deliver the corresponding move events
        # through QGraphicsView/viewport on some trackpad configurations.  Cursor
        # polling makes the drag independent of that event-delivery path.
        self._overlay_drag_timer = QTimer(self)
        self._overlay_drag_timer.setInterval(16)
        self._overlay_drag_timer.timeout.connect(self._poll_overlay_drag)
        self._overlay_drag_tick = 0
        # dev11z: dedicated child widget for the live drag outline.  This is
        # deliberately outside QGraphicsScene after repeated macOS/Qt failures
        # to repaint scene-based drag geometry during a grabbed gesture.
        self._drag_ghost = DragGhostWidget(self.viewport())
        self._drag_ghost.setGeometry(self.viewport().rect())

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
        self.target_marker_items.clear()
        self.target_marker_id = None
        self.catalogue_dot_items.clear()
        # Per-setup rotations are a temporary framing experiment for the
        # current reference image, so reset them when a new image is loaded.
        self.rig_rotation_offsets.clear()

        self.pixmap_item = self.scene.addPixmap(pixmap)
        self.pixmap_item.setZValue(0)
        self.scene.setSceneRect(self.pixmap_item.boundingRect())
        self.reset_view()

        for rig in visible_rigs:
            self.set_rig_visible(rig, True)

        self.image_loaded.emit(path, pixmap.width(), pixmap.height())



    def clear_catalogue_markers(self) -> None:
        for item in self.catalogue_dot_items:
            try:
                self.scene.removeItem(item)
            except RuntimeError:
                pass
        self.catalogue_dot_items.clear()

    def show_catalogue_markers(self, objects: list[tuple[str, float, float]]) -> None:
        """Show small clickable dots for catalogue centres inside the image."""
        self.clear_catalogue_markers()
        if self.pixmap_item is None:
            return
        rect = self.pixmap_item.boundingRect()
        for target_id, x, y in objects:
            if not rect.contains(QPointF(x, y)):
                continue
            dot = CatalogueDotItem(
                target_id, x, y,
                lambda tid: self.catalogue_marker_clicked.emit(tid),
            )
            self.scene.addItem(dot)
            self.catalogue_dot_items.append(dot)

    def clear_target_marker(self) -> None:
        """Remove the currently displayed catalogue-object marker."""
        for item in self.target_marker_items:
            try:
                self.scene.removeItem(item)
            except RuntimeError:
                pass
        self.target_marker_items.clear()
        self.target_marker_id = None

    def show_target_marker(self, target_id: str, label: str, x: float, y: float) -> None:
        """Toggle a subtle, screen-readable marker at an image pixel position."""
        if self.pixmap_item is None:
            return
        if self.target_marker_id == target_id:
            self.clear_target_marker()
            return
        self.clear_target_marker()

        rect = self.pixmap_item.boundingRect()
        if not rect.contains(QPointF(x, y)):
            return

        radius = 12.0
        pen = QPen(QColor("#F3D34A"), 2)
        ellipse = QGraphicsEllipseItem(x - radius, y - radius, radius * 2, radius * 2)
        ellipse.setPen(pen)
        ellipse.setBrush(QBrush(Qt.BrushStyle.NoBrush))
        ellipse.setZValue(30)
        self.scene.addItem(ellipse)

        arm = 7.0
        lines = [
            QGraphicsLineItem(x - radius - arm, y, x - radius + 2, y),
            QGraphicsLineItem(x + radius - 2, y, x + radius + arm, y),
            QGraphicsLineItem(x, y - radius - arm, x, y - radius + 2),
            QGraphicsLineItem(x, y + radius - 2, x, y + radius + arm),
        ]
        for line in lines:
            line.setPen(pen)
            line.setZValue(30)
            self.scene.addItem(line)

        text = QGraphicsSimpleTextItem(label)
        text.setBrush(QBrush(QColor("#F6E58D")))
        font = QFont()
        font.setPointSize(11)
        font.setBold(True)
        text.setFont(font)
        text.setFlag(QGraphicsSimpleTextItem.GraphicsItemFlag.ItemIgnoresTransformations)
        text.setPos(x + radius + arm + 5, y - radius - 4)
        text.setZValue(31)
        self.scene.addItem(text)

        self.target_marker_items = [ellipse, *lines, text]
        self.target_marker_id = target_id
        self.centerOn(x, y)

    def show_edge_target_marker(self, target_id: str, label: str, x: float, y: float) -> None:
        """Toggle an inward-facing edge label for a catalogue centre outside the image.

        ``x``/``y`` are the object's projected image pixel coordinates.  The marker
        is clipped to the image edge so the user can see which way the off-frame
        catalogue centre lies without panning away from the photograph.
        """
        if self.pixmap_item is None:
            return
        if self.target_marker_id == target_id:
            self.clear_target_marker()
            return
        self.clear_target_marker()

        rect = self.pixmap_item.boundingRect()
        cx, cy = rect.center().x(), rect.center().y()
        vx, vy = float(x) - cx, float(y) - cy
        if abs(vx) < 1e-9 and abs(vy) < 1e-9:
            return

        # Intersect the ray from image centre to the projected catalogue centre
        # with a slightly inset image rectangle.  This keeps the arrow and label
        # fully visible even when the object centre itself is off-frame.
        inset = 18.0
        left, right = rect.left() + inset, rect.right() - inset
        top, bottom = rect.top() + inset, rect.bottom() - inset
        candidates = []
        if vx > 0:
            candidates.append((right - cx) / vx)
        elif vx < 0:
            candidates.append((left - cx) / vx)
        if vy > 0:
            candidates.append((bottom - cy) / vy)
        elif vy < 0:
            candidates.append((top - cy) / vy)
        positive = [t for t in candidates if t > 0]
        if not positive:
            return
        t = min(positive)
        ex, ey = cx + vx * t, cy + vy * t

        mag = max((vx * vx + vy * vy) ** 0.5, 1e-9)
        ux, uy = vx / mag, vy / mag
        px, py = -uy, ux

        pen = QPen(QColor("#F3D34A"), 2)
        brush = QBrush(QColor("#F3D34A"))
        tip = QPointF(ex, ey)
        base_x, base_y = ex - ux * 15.0, ey - uy * 15.0
        wing = 7.0
        polygon = QPolygonF([
            tip,
            QPointF(base_x + px * wing, base_y + py * wing),
            QPointF(base_x - px * wing, base_y - py * wing),
        ])
        arrow = QGraphicsPolygonItem(polygon)
        arrow.setPen(pen)
        arrow.setBrush(brush)
        arrow.setZValue(30)
        self.scene.addItem(arrow)

        text = QGraphicsSimpleTextItem(label)
        text.setBrush(QBrush(QColor("#F6E58D")))
        font = QFont()
        font.setPointSize(11)
        font.setBold(True)
        text.setFont(font)
        text.setFlag(QGraphicsSimpleTextItem.GraphicsItemFlag.ItemIgnoresTransformations)
        # Put the label just inside the image, on the opposite side of the arrow
        # from the off-frame target.  Clamp its anchor away from the raster edge.
        tx = ex - ux * 24.0 + px * 8.0
        ty = ey - uy * 24.0 + py * 8.0
        tx = min(max(tx, rect.left() + 8.0), rect.right() - 120.0)
        ty = min(max(ty, rect.top() + 8.0), rect.bottom() - 28.0)
        text.setPos(tx, ty)
        text.setZValue(31)
        self.scene.addItem(text)

        self.target_marker_items = [arrow, text]
        self.target_marker_id = target_id

    def set_active_drag_rig(self, rig_key: str | None) -> None:
        """Restrict direct frame dragging to the explicit Active Rig for Framing."""
        self._active_drag_rig_key = str(rig_key) if rig_key else None

    def _active_overlay_at_view_pos(self, pos) -> OverlayItem | None:
        """Return the active rig overlay when the pointer is inside it.

        This deliberately ignores graphics-item z-order, so a small active frame
        remains draggable even when it is wholly enclosed by a larger frame.
        """
        key = self._active_drag_rig_key
        if not key:
            return None
        overlay = self.overlays.get(key)
        if overlay is None or not overlay.isVisible() or overlay.opacity() <= 0.0:
            return None
        scene_pos = self.mapToScene(pos)
        try:
            if overlay.contains(overlay.mapFromScene(scene_pos)):
                return overlay
        except Exception:
            return None
        return None

    def _overlay_at_view_pos(self, pos) -> OverlayItem | None:
        item = self.itemAt(pos)
        while item is not None:
            if isinstance(item, OverlayItem):
                return item
            item = item.parentItem()
        return None

    def _begin_overlay_drag(self, overlay: OverlayItem, pos) -> None:
        self._dragging_overlay = overlay
        self._overlay_drag_start_scene = self.mapToScene(pos)
        self._overlay_drag_start_pos = QPointF(overlay.scene_center())
        self._overlay_previous_drag_mode = self.dragMode()
        self.setDragMode(QGraphicsView.DragMode.NoDrag)
        overlay.setCursor(Qt.CursorShape.ClosedHandCursor)
        self._overlay_drag_was_visible = overlay.isVisible()
        overlay.setVisible(False)
        self.viewport().setCursor(Qt.CursorShape.ClosedHandCursor)
        self._drag_ghost.setGeometry(self.viewport().rect())
        self._update_drag_ghost(overlay)
        self._drag_ghost.show()
        self._drag_ghost.raise_()
        self._drag_ghost.update()
        self.viewport().update()
        try:
            self.viewport().grabMouse()
        except Exception:
            pass
        # Do not rely on Qt delivering mouse-move events after the press.
        # Polling QCursor gives us the actual pointer position on macOS even
        # when the viewport event stream goes quiet.
        self._overlay_drag_tick = 0
        self._overlay_drag_timer.start()

    def _update_drag_ghost(self, overlay: OverlayItem) -> None:
        points = [QPointF(self.mapFromScene(p)) for p in overlay.scene_corners()]
        self._drag_ghost.setGeometry(self.viewport().rect())
        self._drag_ghost.set_frame(points, overlay.rig.colour, overlay.rig.name)
        self._drag_ghost.raise_()

    def _poll_overlay_drag(self) -> None:
        overlay = getattr(self, "_dragging_overlay", None)
        if overlay is None:
            self._overlay_drag_timer.stop()
            return
        try:
            self._overlay_drag_tick += 1
            global_pos = QCursor.pos()
            viewport_pos = self.viewport().mapFromGlobal(global_pos)
            scene_pos = self.mapToScene(viewport_pos)
            before = QPointF(overlay.scene_center())
            self._move_overlay_drag(viewport_pos)
            after = QPointF(overlay.scene_center())
        except Exception as exc:
            pass

    def _move_overlay_drag(self, pos) -> None:
        overlay = getattr(self, "_dragging_overlay", None)
        if overlay is None:
            return
        scene_pos = self.mapToScene(pos)
        start_scene = getattr(self, "_overlay_drag_start_scene", scene_pos)
        start_pos = getattr(self, "_overlay_drag_start_pos", overlay.scene_center())
        new_center = start_pos + (scene_pos - start_scene)
        overlay.set_scene_center(new_center)
        # Qt/macOS can defer repainting a moved QGraphicsItem while the viewport
        # owns an active mouse gesture.  The position really changes (as the
        # dev11t diagnostics proved), but the old pixels can remain on screen.
        # Explicitly dirty both the scene item and viewport so the FOV rectangle
        # is repainted at its new scene position on every drag update.
        # The scene item stays hidden while dragging; paint the moving outline
        # through the independent viewport-child widget instead.
        self._update_drag_ghost(overlay)
        self._drag_ghost.update()

    def _end_overlay_drag(self) -> None:
        self._overlay_drag_timer.stop()
        self._drag_ghost.hide()
        self._drag_ghost.clearFocus()
        overlay = getattr(self, "_dragging_overlay", None)
        if overlay is None:
            return
        # Commit by replacing the scene item entirely.  This avoids relying on
        # Qt to notice that an existing graphics item's geometry changed during
        # a grabbed mouse gesture.
        rig = overlay.rig
        centre = overlay.scene_center()
        width = float(getattr(overlay, "_width", 1.0))
        height = float(getattr(overlay, "_height", 1.0))
        rotation = overlay.rotation()
        was_visible = bool(getattr(self, "_overlay_drag_was_visible", True))
        try:
            self.scene.removeItem(overlay)
        except Exception:
            pass
        replacement = OverlayItem(
            rig,
            moved_callback=lambda: self.overlay_changed.emit(),
            label_clicked_callback=lambda key: self.rig_label_clicked.emit(key),
        )
        self.scene.addItem(replacement)
        replacement.set_overlay_size(width, height)
        replacement.setRotation(rotation)
        replacement.set_scene_center(centre)
        replacement.setVisible(was_visible)
        self.overlays[rig.key] = replacement

        self.viewport().unsetCursor()
        try:
            self.viewport().releaseMouse()
        except Exception:
            pass
        previous_mode = getattr(
            self, "_overlay_previous_drag_mode", QGraphicsView.DragMode.ScrollHandDrag
        )
        self.setDragMode(previous_mode)
        self._dragging_overlay = None
        self._overlay_drag_start_scene = None
        self._overlay_drag_start_pos = None
        self._overlay_previous_drag_mode = None
        self._overlay_drag_was_visible = None
        self.viewport().update()
        self.overlay_changed.emit()

    def set_placement_mode(self, enabled: bool) -> None:
        """When enabled, a left click on the image reports a scene position.

        This is the reliable manual-framing fallback for platforms where Qt's
        drag gesture remains unreliable.  It intentionally bypasses graphics
        item movement entirely.
        """
        self._placement_mode = bool(enabled)
        if self._placement_mode:
            self.viewport().setCursor(Qt.CursorShape.CrossCursor)
        elif getattr(self, "_dragging_overlay", None) is None:
            self.viewport().unsetCursor()

    def viewportEvent(self, event) -> bool:
        """Handle rig-frame dragging at the viewport-event level.

        QGraphicsView delivers pointer events through its viewport.  On macOS,
        especially with a trackpad, the view-level mouseMoveEvent path can be
        bypassed once the viewport owns the gesture.  Intercepting the viewport
        event stream directly makes the press/move/release sequence deterministic.
        """
        et = event.type()
        if et == QEvent.Type.MouseButtonPress and self._placement_mode:
            try:
                button = event.button()
                pos = event.position().toPoint()
            except Exception:
                return super().viewportEvent(event)
            if button == Qt.MouseButton.LeftButton and self.pixmap_item is not None:
                scene_pos = self.mapToScene(pos)
                rect = self.pixmap_item.boundingRect()
                x = min(max(scene_pos.x(), rect.left()), rect.right())
                y = min(max(scene_pos.y(), rect.top()), rect.bottom())
                self.placement_clicked.emit(float(x), float(y))
                event.accept()
                return True
        if et == QEvent.Type.MouseButtonPress:
            try:
                button = event.button()
                pos = event.position().toPoint()
            except Exception:
                return super().viewportEvent(event)
            if button == Qt.MouseButton.LeftButton:
                # RC22i ARDD: z-order no longer decides which rig moves.
                # Only the selected Active Rig for Framing can begin a drag.
                overlay = self._active_overlay_at_view_pos(pos)
                if overlay is not None:
                    self._begin_overlay_drag(overlay, pos)
                    event.accept()
                    return True
        elif et == QEvent.Type.MouseMove:
            if getattr(self, "_dragging_overlay", None) is not None:
                # dev11v: movement is intentionally TIMER-ONLY.  Earlier builds
                # moved here *and* from the global-cursor timer.  On macOS the
                # viewport can deliver lagging/stale move events, which then
                # overwrite the timer's newer position and make the frame look
                # nailed in place even though diagnostics show setPos changing.
                event.accept()
                return True
        elif et == QEvent.Type.MouseButtonRelease:
            if getattr(self, "_dragging_overlay", None) is not None:
                try:
                    button = event.button()
                except Exception:
                    button = Qt.MouseButton.LeftButton
                if button == Qt.MouseButton.LeftButton:
                    self._end_overlay_drag()
                    event.accept()
                    return True
        return super().viewportEvent(event)

    def mousePressEvent(self, event) -> None:
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        super().mouseReleaseEvent(event)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if hasattr(self, "_drag_ghost"):
            self._drag_ghost.setGeometry(self.viewport().rect())

    def paintEvent(self, event) -> None:
        # Live drag painting is handled by DragGhostWidget (dev11z), not by
        # QGraphicsView's scene paint cycle.
        super().paintEvent(event)

    def wheelEvent(self, event) -> None:
        """Smooth, bounded and fully reversible zoom."""
        if self.pixmap_item is None:
            event.ignore()
            return

        delta = event.pixelDelta().y()
        if delta == 0:
            delta = event.angleDelta().y()
        if delta == 0:
            event.ignore()
            return

        # Small trackpad deltas should feel smooth; a normal mouse-wheel
        # notch still gives a useful but not excessive zoom step.
        steps = delta / 120.0 if abs(delta) >= 120 else delta / 60.0
        factor = 1.15 ** steps

        current = abs(self.transform().m11())
        if current <= 0:
            current = 1.0

        desired = min(
            self.maximum_zoom,
            max(self.minimum_zoom, current * factor),
        )
        actual_factor = desired / current

        if abs(actual_factor - 1.0) > 1e-6:
            self.scale(actual_factor, actual_factor)

        event.accept()


    def _zoom_by_factor(self, factor: float) -> None:
        """Programmatic counterpart to wheel/trackpad zoom."""
        if self.pixmap_item is None or factor <= 0:
            return
        current = abs(self.transform().m11())
        if current <= 0:
            current = 1.0
        desired = min(
            self.maximum_zoom,
            max(self.minimum_zoom, current * factor),
        )
        actual_factor = desired / current
        if abs(actual_factor - 1.0) > 1e-6:
            self.scale(actual_factor, actual_factor)

    def zoom_in(self) -> None:
        self._zoom_by_factor(1.25)

    def zoom_out(self) -> None:
        self._zoom_by_factor(1.0 / 1.25)

    def set_reference_width(self, degrees: float) -> None:
        self.reference_width_deg = max(0.01, degrees)
        self._refresh_overlay_sizes()

    def refresh_rig_definitions(self, rigs) -> None:
        """Replace saved rig optics in existing overlays without losing framing state.

        Equipment records keep stable user_N keys while being edited.  Existing
        OverlayItems used to retain the old Rig object, so focal-length/sensor
        edits did not change the displayed FOV until the overlay was recreated.
        """
        by_key = {rig.key: rig for rig in rigs}
        for key, item in list(self.overlays.items()):
            rig = by_key.get(key)
            if rig is None:
                # A saved setup was removed.
                try:
                    self.scene.removeItem(item)
                except RuntimeError:
                    pass
                self.overlays.pop(key, None)
                self.rig_rotation_offsets.pop(key, None)
                continue
            item.rig = rig
            item.setPen(QPen(QColor(rig.colour), 3))
            item.label.setText(rig.name)
            item.label.rig_key = rig.key
            item.label.setBrush(QBrush(QColor(rig.colour)))
        self._refresh_overlay_sizes()

    def set_rig_visible(self, rig: Rig, visible: bool) -> None:
        if self.pixmap_item is None:
            return
        if rig.key not in self.overlays:
            item = OverlayItem(
                rig,
                moved_callback=lambda: self.overlay_changed.emit(),
                label_clicked_callback=lambda key: self.rig_label_clicked.emit(key),
            )
            self.scene.addItem(item)
            self.overlays[rig.key] = item
            item.set_scene_center(self.scene.sceneRect().center())
            item.setRotation(
                self.current_rotation_deg
                + self.rig_rotation_offsets.get(rig.key, 0.0)
            )
        self.overlays[rig.key].setVisible(visible)
        self._refresh_overlay_sizes()

    def set_rotation(self, degrees: float) -> None:
        self.current_rotation_deg = degrees
        for key, item in self.overlays.items():
            item.setRotation(
                degrees + self.rig_rotation_offsets.get(key, 0.0)
            )
        self.overlay_changed.emit()

    def set_rig_rotation(self, rig_key: str, degrees: float) -> None:
        """Rotate one setup relative to the global reference rotation."""
        self.rig_rotation_offsets[rig_key] = degrees % 180.0
        item = self.overlays.get(rig_key)
        if item is not None:
            item.setRotation(
                self.current_rotation_deg
                + self.rig_rotation_offsets[rig_key]
            )
        self.overlay_changed.emit()

    def rig_rotation(self, rig_key: str) -> float:
        return self.rig_rotation_offsets.get(rig_key, 0.0)

    def clear_rig_rotations(self) -> None:
        self.rig_rotation_offsets.clear()
        for item in self.overlays.values():
            item.setRotation(self.current_rotation_deg)
        self.overlay_changed.emit()

    def centre_overlays(self) -> None:
        if self.pixmap_item is None:
            return
        centre = self.scene.sceneRect().center()
        for item in self.overlays.values():
            item.set_scene_center(centre)
        self.overlay_changed.emit()

    def set_rig_center(self, rig_key: str, x: float, y: float) -> None:
        """Move one rig overlay so its optical centre sits on an image pixel."""
        item = self.overlays.get(rig_key)
        if item is None or self.pixmap_item is None:
            return
        item.set_scene_center(float(x), float(y))
        self.overlay_changed.emit()

    def fit_image(self) -> None:
        """Fit the whole reference image in the viewer."""
        if self.pixmap_item is None:
            return
        self.resetTransform()
        self.fitInView(
            self.scene.sceneRect(),
            Qt.AspectRatioMode.KeepAspectRatio,
        )
        self.centerOn(self.scene.sceneRect().center())

    def actual_pixels(self) -> None:
        """Show the reference image at native pixel scale."""
        if self.pixmap_item is None:
            return
        self.resetTransform()
        self.centerOn(self.scene.sceneRect().center())

    def reset_view(self) -> None:
        """Backward-compatible alias for Fit Image."""
        self.fit_image()

    def reset_framing(self, reference_width_deg: float = 3.0) -> None:
        """Restore manual image scale, rotation and overlay positions."""
        self.set_reference_width(reference_width_deg)
        self.clear_rig_rotations()
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
            item.set_overlay_size(width_px, height_px)
