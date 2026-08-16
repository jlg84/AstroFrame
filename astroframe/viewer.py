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
        self.hit_radius = 15.0
        self.dot_radius = 6.0
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

        # A dark outer ring keeps the yellow marker legible over bright stars,
        # nebulosity and warm-toned image detail without making the overlay huge.
        self.halo = QGraphicsEllipseItem(-8.0, -8.0, 16.0, 16.0, self)
        self.halo.setPen(QPen(QColor(10, 10, 10, 210), 2.5))
        self.halo.setBrush(QBrush(Qt.BrushStyle.NoBrush))
        self.halo.setAcceptedMouseButtons(Qt.MouseButton.NoButton)

        self.dot = QGraphicsEllipseItem(
            -self.dot_radius, -self.dot_radius,
            self.dot_radius*2, self.dot_radius*2, self
        )
        self.dot.setPen(QPen(QColor("#FFF176"), 1.5))
        self.dot.setBrush(QBrush(QColor("#F3D34A")))
        self.dot.setAcceptedMouseButtons(Qt.MouseButton.NoButton)

    def hoverEnterEvent(self, event) -> None:
        r = 8.0
        self.dot.setRect(-r, -r, r*2, r*2)
        self.halo.setRect(-10.0, -10.0, 20.0, 20.0)
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event) -> None:
        r = self.dot_radius
        self.dot.setRect(-r, -r, r*2, r*2)
        self.halo.setRect(-8.0, -8.0, 16.0, 16.0)
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
    """Field-of-view polygon with explicit scene geometry."""
    def __init__(self, rig: Rig, moved_callback=None, label_clicked_callback=None) -> None:
        super().__init__()
        self.rig = rig
        self.moved_callback = moved_callback
        self.label_clicked_callback = label_clicked_callback
        self._centre = QPointF(0.0, 0.0)
        self._width = 1.0
        self._height = 1.0
        self._rotation_deg = 0.0
        self.setFlags(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable | QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges)
        self.setBrush(QBrush(QColor(0, 0, 0, 0)))
        self.setPen(QPen(QColor(rig.colour), 3))
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.setZValue(10)
        self.label = RigLabelItem(rig.name, rig.key, self.label_clicked_callback, self)
        self.label.setBrush(QBrush(QColor(rig.colour)))
        label_font = QFont(); label_font.setPointSize(10); label_font.setBold(True)
        self.label.setFont(label_font)
        self.label.setFlag(QGraphicsSimpleTextItem.GraphicsItemFlag.ItemIgnoresTransformations)
        self._rebuild_polygon()

    def _rebuild_polygon(self) -> None:
        import math
        cx, cy = self._centre.x(), self._centre.y(); hw, hh = self._width/2.0, self._height/2.0
        theta = math.radians(self._rotation_deg); c, sn = math.cos(theta), math.sin(theta)
        corners = [QPointF(cx+c*dx-sn*dy, cy+sn*dx+c*dy) for dx,dy in ((-hw,-hh),(hw,-hh),(hw,hh),(-hw,hh))]
        self.setPolygon(QPolygonF(corners)); self.update_label_position(); self.update()
    def update_label_position(self) -> None:
        b=self.polygon().boundingRect(); self.label.setPos(b.left()+8.0,b.top()+8.0)
    def scene_center(self): return QPointF(self._centre)
    def scene_corners(self): return [QPointF(p) for p in self.polygon()]
    def set_scene_center(self,x,y=None):
        self._centre=QPointF(x) if isinstance(x,QPointF) else QPointF(float(x),float(y if y is not None else 0.0)); self._rebuild_polygon()
        if self.moved_callback is not None: self.moved_callback()
    def set_overlay_size(self,w,h): self._width=max(0.0,float(w)); self._height=max(0.0,float(h)); self._rebuild_polygon()
    def setRotation(self,a): self._rotation_deg=float(a); self._rebuild_polygon()
    def rotation(self): return float(self._rotation_deg)
    def mousePressEvent(self,event): self.setCursor(Qt.CursorShape.ClosedHandCursor); super().mousePressEvent(event)
    def mouseReleaseEvent(self,event): super().mouseReleaseEvent(event); self.setCursor(Qt.CursorShape.OpenHandCursor)


class DragGhostWidget(QWidget):
    def __init__(self,parent=None):
        super().__init__(parent); self._points=[]; self._colour=QColor("#FFFFFF"); self._name=""
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents,True); self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground,True); self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground,True); self.hide()
    def set_frame(self,points,colour,name): self._points=[QPointF(p) for p in points]; self._colour=QColor(colour); self._name=str(name); self.update()
    def paintEvent(self,event):
        if len(self._points)!=4:return
        p=QPainter(self); p.setRenderHint(QPainter.RenderHint.Antialiasing,True); p.setPen(QPen(self._colour,3)); p.setBrush(QBrush(Qt.BrushStyle.NoBrush)); p.drawPolygon(QPolygonF(self._points)); a=self._points[0]; f=QFont(); f.setPointSize(10); f.setBold(True); p.setFont(f); p.drawText(int(a.x()+8),int(a.y()+18),self._name); p.end()


class ImageViewer(QGraphicsView):
    image_loaded=Signal(str,int,int); overlay_changed=Signal(); catalogue_marker_clicked=Signal(str); drag_debug=Signal(str); placement_clicked=Signal(float,float); rig_label_clicked=Signal(str)
    def __init__(self,parent=None):
        super().__init__(parent); self.setRenderHints(QPainter.RenderHint.Antialiasing|QPainter.RenderHint.SmoothPixmapTransform); self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag); self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse); self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter); self.setFrameShape(QFrame.Shape.NoFrame); self._placement_mode=False; self._active_drag_rig_key=None
        self.scene=QGraphicsScene(self); self.setScene(self.scene); self.pixmap_item=None; self.overlays={}; self.target_marker_items=[]; self.target_marker_id=None; self.catalogue_dot_items=[]; self.rig_rotation_offsets={}; self.reference_width_deg=3.0; self.current_rotation_deg=0.0; self.setBackgroundBrush(QBrush(QColor("#090B0F"))); self.minimum_zoom=0.005; self.maximum_zoom=20.0
        self._overlay_drag_timer=QTimer(self); self._overlay_drag_timer.setInterval(16); self._overlay_drag_timer.timeout.connect(self._poll_overlay_drag); self._overlay_drag_tick=0; self._drag_ghost=DragGhostWidget(self.viewport()); self._drag_ghost.setGeometry(self.viewport().rect())
    @property
    def has_image(self): return self.pixmap_item is not None
    def load_image(self,path):
        pixmap=QPixmap(path)
        if pixmap.isNull(): raise ValueError("The selected image could not be opened.")
        visible=[i.rig for i in self.overlays.values() if i.isVisible()]; self.scene.clear(); self.overlays.clear(); self.target_marker_items.clear(); self.target_marker_id=None; self.catalogue_dot_items.clear(); self.rig_rotation_offsets.clear(); self.pixmap_item=self.scene.addPixmap(pixmap); self.pixmap_item.setZValue(0); self.scene.setSceneRect(self.pixmap_item.boundingRect()); self.reset_view()
        for rig in visible:self.set_rig_visible(rig,True)
        self.image_loaded.emit(path,pixmap.width(),pixmap.height())
    def clear_catalogue_markers(self):
        for item in self.catalogue_dot_items:
            try:self.scene.removeItem(item)
            except RuntimeError:pass
        self.catalogue_dot_items.clear()
    def show_catalogue_markers(self,objects):
        self.clear_catalogue_markers()
        if self.pixmap_item is None:return
        rect=self.pixmap_item.boundingRect()
        for target_id,x,y in objects:
            if rect.contains(QPointF(x,y)):
                dot=CatalogueDotItem(target_id,x,y,lambda tid:self.catalogue_marker_clicked.emit(tid)); self.scene.addItem(dot); self.catalogue_dot_items.append(dot)
    def clear_target_marker(self):
        for item in self.target_marker_items:
            try:self.scene.removeItem(item)
            except RuntimeError:pass
        self.target_marker_items.clear(); self.target_marker_id=None
    def show_target_marker(self,target_id,label,x,y):
        if self.pixmap_item is None:return
        if self.target_marker_id==target_id:self.clear_target_marker();return
        self.clear_target_marker(); rect=self.pixmap_item.boundingRect()
        if not rect.contains(QPointF(x,y)):return
        radius=12.0; pen=QPen(QColor("#F3D34A"),2); ellipse=QGraphicsEllipseItem(x-radius,y-radius,radius*2,radius*2); ellipse.setPen(pen); ellipse.setBrush(QBrush(Qt.BrushStyle.NoBrush)); ellipse.setZValue(30); self.scene.addItem(ellipse); arm=7.0
        lines=[QGraphicsLineItem(x-radius-arm,y,x-radius+2,y),QGraphicsLineItem(x+radius-2,y,x+radius+arm,y),QGraphicsLineItem(x,y-radius-arm,x,y-radius+2),QGraphicsLineItem(x,y+radius-2,x,y+radius+arm)]
        for line in lines:line.setPen(pen);line.setZValue(30);self.scene.addItem(line)
        text=QGraphicsSimpleTextItem(label);text.setBrush(QBrush(QColor("#F6E58D")));font=QFont();font.setPointSize(11);font.setBold(True);text.setFont(font);text.setFlag(QGraphicsSimpleTextItem.GraphicsItemFlag.ItemIgnoresTransformations);text.setPos(x+radius+arm+5,y-radius-4);text.setZValue(31);self.scene.addItem(text);self.target_marker_items=[ellipse,*lines,text];self.target_marker_id=target_id;self.centerOn(x,y)
    def show_edge_target_marker(self,target_id,label,x,y):
        if self.pixmap_item is None:return
        if self.target_marker_id==target_id:self.clear_target_marker();return
        self.clear_target_marker();rect=self.pixmap_item.boundingRect();cx,cy=rect.center().x(),rect.center().y();vx,vy=float(x)-cx,float(y)-cy
        if abs(vx)<1e-9 and abs(vy)<1e-9:return
        inset=18.0;left,right=rect.left()+inset,rect.right()-inset;top,bottom=rect.top()+inset,rect.bottom()-inset;c=[]
        if vx>0:c.append((right-cx)/vx)
        elif vx<0:c.append((left-cx)/vx)
        if vy>0:c.append((bottom-cy)/vy)
        elif vy<0:c.append((top-cy)/vy)
        positive=[t for t in c if t>0]
        if not positive:return
        t=min(positive);ex,ey=cx+vx*t,cy+vy*t;mag=max((vx*vx+vy*vy)**0.5,1e-9);ux,uy=vx/mag,vy/mag;px,py=-uy,ux;pen=QPen(QColor("#F3D34A"),2);brush=QBrush(QColor("#F3D34A"));tip=QPointF(ex,ey);bx,by=ex-ux*15.0,ey-uy*15.0;wing=7.0;polygon=QPolygonF([tip,QPointF(bx+px*wing,by+py*wing),QPointF(bx-px*wing,by-py*wing)]);arrow=QGraphicsPolygonItem(polygon);arrow.setPen(pen);arrow.setBrush(brush);arrow.setZValue(30);self.scene.addItem(arrow);text=QGraphicsSimpleTextItem(label);text.setBrush(QBrush(QColor("#F6E58D")));font=QFont();font.setPointSize(11);font.setBold(True);text.setFont(font);text.setFlag(QGraphicsSimpleTextItem.GraphicsItemFlag.ItemIgnoresTransformations);tx=ex-ux*24.0+px*8.0;ty=ey-uy*24.0+py*8.0;tx=min(max(tx,rect.left()+8.0),rect.right()-120.0);ty=min(max(ty,rect.top()+8.0),rect.bottom()-28.0);text.setPos(tx,ty);text.setZValue(31);self.scene.addItem(text);self.target_marker_items=[arrow,text];self.target_marker_id=target_id
    def set_active_drag_rig(self,rig_key):self._active_drag_rig_key=str(rig_key) if rig_key else None
    def _active_overlay_at_view_pos(self,pos):
        key=self._active_drag_rig_key
        if not key:return None
        overlay=self.overlays.get(key)
        if overlay is None or not overlay.isVisible() or overlay.opacity()<=0:return None
        try:
            if overlay.contains(overlay.mapFromScene(self.mapToScene(pos))):return overlay
        except Exception:return None
        return None
    def _begin_overlay_drag(self,overlay,pos):
        self._dragging_overlay=overlay;self._overlay_drag_start_scene=self.mapToScene(pos);self._overlay_drag_start_pos=QPointF(overlay.scene_center());self._overlay_previous_drag_mode=self.dragMode();self.setDragMode(QGraphicsView.DragMode.NoDrag);overlay.setCursor(Qt.CursorShape.ClosedHandCursor);self._overlay_drag_was_visible=overlay.isVisible();overlay.setVisible(False);self.viewport().setCursor(Qt.CursorShape.ClosedHandCursor);self._drag_ghost.setGeometry(self.viewport().rect());self._update_drag_ghost(overlay);self._drag_ghost.show();self._drag_ghost.raise_();self._drag_ghost.update();self.viewport().update()
        try:self.viewport().grabMouse()
        except Exception:pass
        self._overlay_drag_tick=0;self._overlay_drag_timer.start()
    def _update_drag_ghost(self,overlay):self._drag_ghost.setGeometry(self.viewport().rect());self._drag_ghost.set_frame([QPointF(self.mapFromScene(p)) for p in overlay.scene_corners()],overlay.rig.colour,overlay.rig.name);self._drag_ghost.raise_()
    def _poll_overlay_drag(self):
        overlay=getattr(self,"_dragging_overlay",None)
        if overlay is None:self._overlay_drag_timer.stop();return
        try:self._overlay_drag_tick+=1;self._move_overlay_drag(self.viewport().mapFromGlobal(QCursor.pos()))
        except Exception:pass
    def _move_overlay_drag(self,pos):
        overlay=getattr(self,"_dragging_overlay",None)
        if overlay is None:return
        scene_pos=self.mapToScene(pos);start_scene=getattr(self,"_overlay_drag_start_scene",scene_pos);start_pos=getattr(self,"_overlay_drag_start_pos",overlay.scene_center());overlay.set_scene_center(start_pos+(scene_pos-start_scene));self._update_drag_ghost(overlay);self._drag_ghost.update()
    def _end_overlay_drag(self):
        self._overlay_drag_timer.stop();self._drag_ghost.hide();self._drag_ghost.clearFocus();overlay=getattr(self,"_dragging_overlay",None)
        if overlay is None:return
        rig=overlay.rig;centre=overlay.scene_center();width=float(getattr(overlay,"_width",1.0));height=float(getattr(overlay,"_height",1.0));rotation=overlay.rotation();was_visible=bool(getattr(self,"_overlay_drag_was_visible",True))
        try:self.scene.removeItem(overlay)
        except Exception:pass
        replacement=OverlayItem(rig,moved_callback=lambda:self.overlay_changed.emit(),label_clicked_callback=lambda key:self.rig_label_clicked.emit(key));self.scene.addItem(replacement);replacement.set_overlay_size(width,height);replacement.setRotation(rotation);replacement.set_scene_center(centre);replacement.setVisible(was_visible);self.overlays[rig.key]=replacement;self.viewport().unsetCursor()
        try:self.viewport().releaseMouse()
        except Exception:pass
        self.setDragMode(getattr(self,"_overlay_previous_drag_mode",QGraphicsView.DragMode.ScrollHandDrag));self._dragging_overlay=None;self._overlay_drag_start_scene=None;self._overlay_drag_start_pos=None;self._overlay_previous_drag_mode=None;self._overlay_drag_was_visible=None;self.viewport().update();self.overlay_changed.emit()
    def set_placement_mode(self,enabled):
        self._placement_mode=bool(enabled)
        if self._placement_mode:self.viewport().setCursor(Qt.CursorShape.CrossCursor)
        elif getattr(self,"_dragging_overlay",None) is None:self.viewport().unsetCursor()
    def viewportEvent(self,event):
        et=event.type()
        if et==QEvent.Type.MouseButtonPress and self._placement_mode:
            try:button=event.button();pos=event.position().toPoint()
            except Exception:return super().viewportEvent(event)
            if button==Qt.MouseButton.LeftButton and self.pixmap_item is not None:
                scene_pos=self.mapToScene(pos);rect=self.pixmap_item.boundingRect();self.placement_clicked.emit(float(min(max(scene_pos.x(),rect.left()),rect.right())),float(min(max(scene_pos.y(),rect.top()),rect.bottom())));event.accept();return True
        if et==QEvent.Type.MouseButtonPress:
            try:button=event.button();pos=event.position().toPoint()
            except Exception:return super().viewportEvent(event)
            if button==Qt.MouseButton.LeftButton:
                overlay=self._active_overlay_at_view_pos(pos)
                if overlay is not None:self._begin_overlay_drag(overlay,pos);event.accept();return True
        elif et==QEvent.Type.MouseMove and getattr(self,"_dragging_overlay",None) is not None:event.accept();return True
        elif et==QEvent.Type.MouseButtonRelease and getattr(self,"_dragging_overlay",None) is not None:
            try:button=event.button()
            except Exception:button=Qt.MouseButton.LeftButton
            if button==Qt.MouseButton.LeftButton:self._end_overlay_drag();event.accept();return True
        return super().viewportEvent(event)
    def mousePressEvent(self,event):super().mousePressEvent(event)
    def mouseMoveEvent(self,event):super().mouseMoveEvent(event)
    def mouseReleaseEvent(self,event):super().mouseReleaseEvent(event)
    def resizeEvent(self,event):
        super().resizeEvent(event)
        if hasattr(self,"_drag_ghost"):self._drag_ghost.setGeometry(self.viewport().rect())
    def paintEvent(self,event):super().paintEvent(event)
    def wheelEvent(self,event):
        if self.pixmap_item is None:event.ignore();return
        delta=event.pixelDelta().y()
        if delta==0:delta=event.angleDelta().y()
        if delta==0:event.ignore();return
        steps=delta/120.0 if abs(delta)>=120 else delta/60.0;factor=1.15**steps;current=abs(self.transform().m11()) or 1.0;desired=min(self.maximum_zoom,max(self.minimum_zoom,current*factor));actual=desired/current
        if abs(actual-1.0)>1e-6:self.scale(actual,actual)
        event.accept()
    def _zoom_by_factor(self,factor):
        if self.pixmap_item is None or factor<=0:return
        current=abs(self.transform().m11()) or 1.0;desired=min(self.maximum_zoom,max(self.minimum_zoom,current*factor));actual=desired/current
        if abs(actual-1.0)>1e-6:self.scale(actual,actual)
    def zoom_in(self):self._zoom_by_factor(1.25)
    def zoom_out(self):self._zoom_by_factor(1.0/1.25)
    def set_reference_width(self,degrees):self.reference_width_deg=max(0.01,degrees);self._refresh_overlay_sizes()
    def refresh_rig_definitions(self,rigs):
        by_key={rig.key:rig for rig in rigs}
        for key,item in list(self.overlays.items()):
            rig=by_key.get(key)
            if rig is None:
                try:self.scene.removeItem(item)
                except RuntimeError:pass
                self.overlays.pop(key,None);self.rig_rotation_offsets.pop(key,None);continue
            item.rig=rig;item.setPen(QPen(QColor(rig.colour),3));item.label.setText(rig.name);item.label.rig_key=rig.key;item.label.setBrush(QBrush(QColor(rig.colour)))
        self._refresh_overlay_sizes()
    def set_rig_visible(self,rig,visible):
        if self.pixmap_item is None:return
        if rig.key not in self.overlays:
            item=OverlayItem(rig,moved_callback=lambda:self.overlay_changed.emit(),label_clicked_callback=lambda key:self.rig_label_clicked.emit(key));self.scene.addItem(item);self.overlays[rig.key]=item;item.set_scene_center(self.scene.sceneRect().center());item.setRotation(self.current_rotation_deg+self.rig_rotation_offsets.get(rig.key,0.0))
        self.overlays[rig.key].setVisible(visible);self._refresh_overlay_sizes()
    def set_rotation(self,degrees):
        self.current_rotation_deg=degrees
        for key,item in self.overlays.items():item.setRotation(degrees+self.rig_rotation_offsets.get(key,0.0))
        self.overlay_changed.emit()
    def set_rig_rotation(self,rig_key,degrees):
        self.rig_rotation_offsets[rig_key]=degrees%180.0;item=self.overlays.get(rig_key)
        if item is not None:item.setRotation(self.current_rotation_deg+self.rig_rotation_offsets[rig_key])
        self.overlay_changed.emit()
    def rig_rotation(self,rig_key):return self.rig_rotation_offsets.get(rig_key,0.0)
    def clear_rig_rotations(self):
        self.rig_rotation_offsets.clear()
        for item in self.overlays.values():item.setRotation(self.current_rotation_deg)
        self.overlay_changed.emit()
    def centre_overlays(self):
        if self.pixmap_item is None:return
        centre=self.scene.sceneRect().center()
        for item in self.overlays.values():item.set_scene_center(centre)
        self.overlay_changed.emit()
    def set_rig_center(self,rig_key,x,y):
        item=self.overlays.get(rig_key)
        if item is None or self.pixmap_item is None:return
        item.set_scene_center(float(x),float(y));self.overlay_changed.emit()
    def fit_image(self):
        if self.pixmap_item is None:return
        self.resetTransform();self.fitInView(self.scene.sceneRect(),Qt.AspectRatioMode.KeepAspectRatio);self.centerOn(self.scene.sceneRect().center())
    def actual_pixels(self):
        if self.pixmap_item is None:return
        self.resetTransform();self.centerOn(self.scene.sceneRect().center())
    def reset_view(self):self.fit_image()
    def reset_framing(self,reference_width_deg=3.0):self.set_reference_width(reference_width_deg);self.clear_rig_rotations();self.set_rotation(0.0);self.centre_overlays()
    def _refresh_overlay_sizes(self):
        if self.pixmap_item is None:return
        image_rect=self.pixmap_item.boundingRect();image_width_px=image_rect.width();image_height_deg=self.reference_width_deg*image_rect.height()/image_rect.width()
        for item in self.overlays.values():
            rig=item.rig;item.set_overlay_size(image_width_px*rig.fov_width_deg/self.reference_width_deg,image_rect.height()*rig.fov_height_deg/image_height_deg)
