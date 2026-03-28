from PyQt6.QtWidgets import (QGraphicsRectItem, QGraphicsTextItem, QGraphicsItem, 
                             QGraphicsPathItem, QGraphicsEllipseItem, QGraphicsDropShadowEffect)
from PyQt6.QtGui import QColor, QPen, QBrush, QFont, QPainterPath, QLinearGradient
from PyQt6.QtCore import Qt, QPointF, QRectF

class Wire(QGraphicsPathItem):
    def __init__(self, start_pin, end_pin, color="#f39c12"):
        super().__init__()
        self.start_pin = start_pin
        self.end_pin = end_pin
        
        self.base_color = QColor(color)
        self.setPen(QPen(self.base_color, 3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        self.setZValue(-1) # Rest behind components
        
        # Enable interaction for hover and right-click
        self.setFlags(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)
        self.setAcceptHoverEvents(True)
        
        self.start_pin.wires.append(self)
        self.end_pin.wires.append(self)
        self.update_path()

    def update_path(self):
        """Draws sweeping organic curves that strictly push away from the pins"""
        try:
            path = QPainterPath()
            p1 = self.start_pin.scenePos()
            p2 = self.end_pin.scenePos()
            path.moveTo(p1)
            
            # Dynamic bezier distance
            dx = max(abs(p2.x() - p1.x()) * 0.6, 60.0)
            cp1_x = p1.x() + dx if self.start_pin.is_out else p1.x() - dx
            cp2_x = p2.x() - dx if not self.end_pin.is_out else p2.x() + dx
            
            path.cubicTo(cp1_x, p1.y(), cp2_x, p2.y(), p2.x(), p2.y())
            self.setPath(path)
        except RuntimeError:
            pass # Failsafe: The underlying C++ object is currently being deleted

    def hoverEnterEvent(self, event):
        pen = self.pen()
        pen.setWidth(5)
        pen.setColor(self.base_color.lighter(150)) # Brighten dynamically on hover
        self.setPen(pen)
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event):
        pen = self.pen()
        pen.setWidth(3)
        pen.setColor(self.base_color) # Restore original color
        self.setPen(pen)
        super().hoverLeaveEvent(event)

    def set_color(self, hex_color):
        self.base_color = QColor(hex_color)
        pen = self.pen()
        pen.setColor(self.base_color)
        self.setPen(pen)

class Pin(QGraphicsEllipseItem):
    def __init__(self, label, is_out, hal, color, parent):
        super().__init__(-6, -6, 12, 12, parent)
        
        # Match your library color schema
        color_map = {
            "Power (Red)": "#e74c3c",
            "Signal (Blue)": "#3498db",
            "Clock (Yellow)": "#f1c40f",
            "Ground (Black)": "#95a5a6",
            "Default": "#2ecc71"
        }
        self.setBrush(QBrush(QColor(color_map.get(color, "#2ecc71"))))
        self.setPen(QPen(QColor(0, 0, 0), 1.5))
        self.is_out = is_out
        self.hal_name = hal
        self.label = label
        self.wires = []
        
        lbl = QGraphicsTextItem(label, self)
        lbl.setFont(QFont("Segoe UI", 9))
        lbl.setDefaultTextColor(QColor("#ecf0f1"))
        
        if is_out:
            lbl.setPos(-lbl.boundingRect().width() - 15, -12)
        else:
            lbl.setPos(15, -12)

class MesaComponent(QGraphicsRectItem):
    def __init__(self, name, x, y, pins_data, comp_id):
        super().__init__()
        self.name = name
        self.comp_id = comp_id
        self.width = 260 # Slightly wider for comfortable text
        self.spacing = 28
        self.header_height = 40
        
        # Pro Drop Shadow
        shadow = QGraphicsDropShadowEffect()
        shadow.setBlurRadius(20)
        shadow.setOffset(5, 5)
        shadow.setColor(QColor(0, 0, 0, 180))
        self.setGraphicsEffect(shadow)
        
        self.setPen(QPen(Qt.PenStyle.NoPen))
        self.setBrush(QBrush(Qt.BrushStyle.NoBrush))
        
        self.title = QGraphicsTextItem(name, self)
        self.title.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
        self.title.setDefaultTextColor(Qt.GlobalColor.white)
        self.title.setPos(10, 8)

        self.pins = []
        for i, p in enumerate(pins_data):
            color_val = p[3] if len(p) > 3 else "Default"
            pin = Pin(p[0], p[1], p[2], color_val, self)
            pin.setPos(self.width if p[1] else 0, self.header_height + 20 + (i * self.spacing))
            self.pins.append(pin)

        height = self.header_height + 40 + (len(pins_data) * self.spacing)
        self.setRect(0, 0, self.width, height)
        self.setPos(x, y)

        self.setFlags(QGraphicsItem.GraphicsItemFlag.ItemIsMovable | 
                      QGraphicsItem.GraphicsItemFlag.ItemIsSelectable |
                      QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges)

    def paint(self, painter, option, widget=None):
        """Custom Paint override to draw the advanced Blueprint graphic"""
        rect = self.boundingRect()
        
        # Body background (Dark Translucent)
        painter.setBrush(QBrush(QColor(25, 25, 25, 235)))
        
        if self.isSelected():
            painter.setPen(QPen(QColor("#3498db"), 3)) # Bright blue outline when selected!
        else:
            painter.setPen(QPen(QColor(40, 40, 40), 2))
            
        painter.drawRoundedRect(rect, 8, 8)
        
        # Top Header (Blue Gradient with curved top corners and sharp bottom corners)
        header_path = QPainterPath()
        header_path.setFillRule(Qt.FillRule.WindingFill)
        header_path.addRoundedRect(QRectF(rect.x(), rect.y(), rect.width(), self.header_height), 8, 8)
        header_path.addRect(QRectF(rect.x(), rect.y() + self.header_height - 8, rect.width(), 8))
        
        grad = QLinearGradient(0, 0, 0, self.header_height)
        grad.setColorAt(0.0, QColor("#2980b9"))
        grad.setColorAt(1.0, QColor("#1a5276"))
        
        painter.setBrush(QBrush(grad))
        painter.setPen(QPen(Qt.PenStyle.NoPen))
        painter.drawPath(header_path.simplified())
        
        # Divider Line
        painter.setPen(QPen(QColor(15, 15, 15), 2))
        painter.drawLine(int(rect.left()), int(rect.top() + self.header_height), int(rect.right()), int(rect.top() + self.header_height))

    def itemChange(self, change, value):
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged:
            for pin in self.childItems():
                if isinstance(pin, Pin):
                    for wire in pin.wires: wire.update_path()
        return super().itemChange(change, value)

class ComponentZone(QGraphicsRectItem):
    def __init__(self, rect, title="Network Zone"):
        super().__init__(rect)
        self.setZValue(-2) # Strictly behind wires (-1) and components (0)
        self.setPen(QPen(QColor("#34495e"), 2, Qt.PenStyle.DashLine))
        self.setBrush(QBrush(QColor(52, 73, 94, 30))) # Translucent slate overlay
        
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, False)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, False)
        
        self.title = QGraphicsTextItem(title, self)
        self.title.setFont(QFont("Segoe UI", 12, QFont.Weight.Bold))
        self.title.setDefaultTextColor(QColor("#95a5a6"))
        self.title.setPos(rect.topLeft() + QPointF(10, 5))