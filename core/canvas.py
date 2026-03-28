from PyQt6.QtWidgets import QGraphicsView, QGraphicsScene
from PyQt6.QtGui import QColor, QPen, QPainter, QBrush
from PyQt6.QtCore import Qt, QPointF

class MesaCanvas(QGraphicsView):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.scene = QGraphicsScene()
        self.setScene(self.scene)
        
        # Explicitly define an "infinite" canvas so the hidden scrollbars have room to pan
        self.setSceneRect(-5000, -5000, 10000, 10000)
        
        # Performance & Aesthetics
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        self.setDragMode(QGraphicsView.DragMode.RubberBandDrag)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setBackgroundBrush(QBrush(QColor("#151b21")))
        
        # Panning state
        self._is_panning = False
        self._last_mouse_pos = QPointF()

    def drawBackground(self, painter, rect):
        """The blueprint dot-grid logic."""
        painter.fillRect(rect, QColor("#151b21"))
        pen = QPen(QColor("#2c3e50"), 2)
        painter.setPen(pen)
        grid_size = 20
        left = int(rect.left()) - (int(rect.left()) % grid_size)
        top = int(rect.top()) - (int(rect.top()) % grid_size)
        for x in range(left, int(rect.right()) + grid_size, grid_size):
            for y in range(top, int(rect.bottom()) + grid_size, grid_size):
                painter.drawPoint(x, y)

    def wheelEvent(self, event):
        """Unreal Engine style zooming without scroll bars interfering."""
        if event.angleDelta().y() == 0:
            event.ignore()
            return
            
        zoom_in_factor = 1.15
        zoom_out_factor = 1.0 / zoom_in_factor
        
        factor = zoom_in_factor if event.angleDelta().y() > 0 else zoom_out_factor
        self.scale(factor, factor)
        event.accept()

    # Middle-Click Panning Logic
    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.MiddleButton:
            self._is_panning = True
            self._last_mouse_pos = event.position()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._is_panning:
            # Panning bug failsafe: If the release event was consumed by a component, force exit
            if not (event.buttons() & Qt.MouseButton.MiddleButton):
                self._is_panning = False
                self.setCursor(Qt.CursorShape.ArrowCursor)
                super().mouseMoveEvent(event)
                return
                
            delta = event.position() - self._last_mouse_pos
            self._last_mouse_pos = event.position()
            self.horizontalScrollBar().setValue(int(self.horizontalScrollBar().value() - delta.x()))
            self.verticalScrollBar().setValue(int(self.verticalScrollBar().value() - delta.y()))
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.MiddleButton:
            self._is_panning = False
            self.setCursor(Qt.CursorShape.ArrowCursor)
            event.accept()
        else:
            super().mouseReleaseEvent(event)