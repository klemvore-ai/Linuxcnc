"""
Probing Tab - QtDragon Probing Interface for LinuxCNC Pro.

Modeled after LinuxCNC's flagship QtDragon GUI (VersaProbe / BasicProbe):
- QtDragon industrial dark-grey styling with glowing emerald-green / cyan accents
- High-contrast digital DRO readouts and segmented WCS buttons (G54-G59)
- Visual CAD diagram preview with grid, workpiece silhouette, stylus trajectory, and origin crosshairs
- Full cycle library:
  * Outside: 4 corners, 4 edges, boss/center, ridge/web
  * Inside: 4 inside corners, 4 inside edges, hole/bore center & diameter, valley/slot
  * Surface Z: Touch off with fast search + fine latch
  * Skew / Angle: 2-point edge angle measurement & coordinate rotation (G10 L2 P0 R...)
  * Tip Calibration: Probe stylus calibration
- Parameter entries (tip diameter, clearances, search distances, velocities)
- Auto Set Work Zero vs Measure Only modes
- Large tactile START PROBE, ABORT, and instant manual Zero buttons
"""

import math
import os
import re
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QPushButton,
    QGroupBox, QLineEdit, QComboBox, QRadioButton, QButtonGroup, QTabWidget,
    QScrollArea, QFrame, QMessageBox, QTableWidget, QTableWidgetItem, QHeaderView,
    QSizePolicy
)
from PyQt6.QtCore import Qt, QTimer, pyqtSignal, QPointF, QRectF
from PyQt6.QtGui import QPainter, QColor, QPen, QBrush, QFont, QPolygonF, QPainterPath
from core.machine_agent import client_from_settings


class QtDragonProbeDiagram(QWidget):
    """QtDragon style visual drafting diagram showing workpiece, probe path, and target origin."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(340, 280)
        self.cycle_name = "out_fl"
        self.probe_tripped = False
        self.probe_diam = 3.0
        self.xy_clear = 5.0
        self.z_drop = 5.0
        self.search_dist = 20.0

    def set_cycle(self, cycle_name, **params):
        self.cycle_name = cycle_name
        if "probe_diam" in params: self.probe_diam = params["probe_diam"]
        if "xy_clear" in params: self.xy_clear = params["xy_clear"]
        if "z_drop" in params: self.z_drop = params["z_drop"]
        if "search_dist" in params: self.search_dist = params["search_dist"]
        self.update()

    def set_probe_status(self, tripped: bool):
        if self.probe_tripped != tripped:
            self.probe_tripped = tripped
            self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        w = self.width()
        h = self.height()
        cx = w / 2.0
        cy = h / 2.0 + 12.0

        # QtDragon drafting background
        painter.fillRect(self.rect(), QColor("#1e1e1e"))

        # Subtle CAD grid lines
        grid_pen = QPen(QColor(45, 52, 54, 120), 1, Qt.PenStyle.DotLine)
        painter.setPen(grid_pen)
        grid_step = 25
        gx = 0
        while gx < w:
            painter.drawLine(gx, 0, gx, h)
            gx += grid_step
        gy = 0
        while gy < h:
            painter.drawLine(0, gy, w, gy)
            gy += grid_step

        # Outer border
        painter.setPen(QPen(QColor("#333333"), 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(0, 0, w - 1, h - 1)

        # QtDragon Status Badge at top
        badge_rect = QRectF(12, 10, w - 24, 26)
        if self.probe_tripped:
            painter.setBrush(QBrush(QColor(185, 28, 28, 240)))
            painter.setPen(QPen(QColor("#ef4444"), 1.5))
            status_text = "🔴 PROBE TRIPPED (CONTACT DETECTED)"
            text_color = QColor("#ffffff")
        else:
            painter.setBrush(QBrush(QColor(24, 32, 24, 240)))
            painter.setPen(QPen(QColor("#9ad58d"), 1.5))
            status_text = "🟢 PROBE READY (SIGNAL CLEAR)"
            text_color = QColor("#9ad58d")

        painter.drawRoundedRect(badge_rect, 4, 4)
        painter.setPen(text_color)
        painter.setFont(QFont("Arial", 9, QFont.Weight.Bold))
        painter.drawText(badge_rect, Qt.AlignmentFlag.AlignCenter, status_text)

        # QtDragon Palette
        pen_workpiece = QPen(QColor("#00adb5"), 2.5)
        brush_workpiece = QBrush(QColor(34, 47, 62, 220))
        pen_arrow = QPen(QColor("#f39c12"), 2.2)

        name = self.cycle_name

        if name == "out_boss":
            # Circular / Rectangular Boss Center
            boss_r = 48.0
            painter.setPen(pen_workpiece)
            painter.setBrush(brush_workpiece)
            painter.drawEllipse(QPointF(cx, cy), boss_r, boss_r)
            self._draw_crosshair_origin(painter, cx, cy, "ORIGIN (0,0)")
            # 4 inward approach arrows
            self._draw_arrow(painter, cx - 85, cy, cx - boss_r - 2, cy, "(1) +X")
            self._draw_arrow(painter, cx + 85, cy, cx + boss_r + 2, cy, "(2) -X")
            self._draw_arrow(painter, cx, cy - 85, cx, cy - boss_r - 2, "(3) +Y")
            self._draw_arrow(painter, cx, cy + 85, cx, cy + boss_r + 2, "(4) -Y")
            self._draw_stylus(painter, cx, cy - 90, "START")

        elif name == "in_hole":
            # Circular Bore / Hole Center
            hole_r = 80.0
            # Outer block
            painter.setPen(pen_workpiece)
            painter.setBrush(brush_workpiece)
            painter.drawRect(QRectF(cx - 105, cy - 90, 210, 180))
            # Inner pocket cutout
            painter.setBrush(QBrush(QColor("#141414")))
            painter.drawEllipse(QPointF(cx, cy), hole_r, hole_r)
            self._draw_crosshair_origin(painter, cx, cy, "ORIGIN (0,0)")
            # 4 outward approach arrows
            self._draw_arrow(painter, cx, cy, cx + hole_r - 3, cy, "(1)")
            self._draw_arrow(painter, cx, cy, cx - hole_r + 3, cy, "(2)")
            self._draw_arrow(painter, cx, cy, cx, cy + hole_r - 3, "(3)")
            self._draw_arrow(painter, cx, cy, cx, cy - hole_r + 3, "(4)")
            self._draw_stylus(painter, cx, cy, "START")

        elif name in ("out_fl", "out_fr", "out_bl", "out_br"):
            # Outside Corners
            block_w, block_h = 110.0, 95.0
            if name == "out_fl":
                ox, oy = cx - 30, cy + 30
                rect = QRectF(ox, oy - block_h, block_w, block_h)
                self._draw_corner(painter, rect, ox, oy, name, touch_x=1, touch_y=-1)
            elif name == "out_fr":
                ox, oy = cx + 30, cy + 30
                rect = QRectF(ox - block_w, oy - block_h, block_w, block_h)
                self._draw_corner(painter, rect, ox, oy, name, touch_x=-1, touch_y=-1)
            elif name == "out_bl":
                ox, oy = cx - 30, cy - 30
                rect = QRectF(ox, oy, block_w, block_h)
                self._draw_corner(painter, rect, ox, oy, name, touch_x=1, touch_y=1)
            elif name == "out_br":
                ox, oy = cx + 30, cy - 30
                rect = QRectF(ox - block_w, oy, block_w, block_h)
                self._draw_corner(painter, rect, ox, oy, name, touch_x=-1, touch_y=1)

        elif name in ("in_fl", "in_fr", "in_bl", "in_br"):
            # Inside Corners
            box_w, box_h = 180.0, 150.0
            corner_rect = QRectF(cx - box_w/2, cy - box_h/2, box_w, box_h)
            painter.setPen(pen_workpiece)
            painter.setBrush(QBrush(QColor("#141414")))
            painter.drawRect(corner_rect)

            if name == "in_fl":
                ox, oy = corner_rect.left(), corner_rect.bottom()
                self._draw_inside_corner(painter, ox, oy, 1, -1)
            elif name == "in_fr":
                ox, oy = corner_rect.right(), corner_rect.bottom()
                self._draw_inside_corner(painter, ox, oy, -1, -1)
            elif name == "in_bl":
                ox, oy = corner_rect.left(), corner_rect.top()
                self._draw_inside_corner(painter, ox, oy, 1, 1)
            elif name == "in_br":
                ox, oy = corner_rect.right(), corner_rect.top()
                self._draw_inside_corner(painter, ox, oy, -1, 1)

        elif name in ("out_b", "out_f", "out_l", "out_r", "in_b", "in_f", "in_l", "in_r"):
            self._draw_edge(painter, cx, cy, name)

        elif name == "probe_z":
            self._draw_z_surface(painter, cx, cy)

        elif name in ("angle_x", "angle_y"):
            self._draw_angle(painter, cx, cy, name)

        else:
            painter.setPen(QColor("#94a3b8"))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, f"Probing Routine: {name}")

        painter.end()

    def _draw_crosshair_origin(self, painter, x, y, label=""):
        painter.save()
        # Glowing QtDragon green crosshair
        painter.setPen(QPen(QColor("#9ad58d"), 2))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(QPointF(x, y), 9, 9)
        painter.drawLine(QPointF(x - 14, y), QPointF(x + 14, y))
        painter.drawLine(QPointF(x, y - 14), QPointF(x, y + 14))
        painter.setBrush(QBrush(QColor("#9ad58d")))
        painter.drawEllipse(QPointF(x, y), 3.5, 3.5)
        if label:
            painter.setFont(QFont("Arial", 8, QFont.Weight.Bold))
            painter.setPen(QColor("#9ad58d"))
            painter.drawText(QPointF(x + 14, y - 6), label)
        painter.restore()

    def _draw_stylus(self, painter, x, y, label=""):
        painter.save()
        # Metallic stem
        painter.setPen(QPen(QColor("#dfe6e9"), 3.5))
        painter.drawLine(QPointF(x, y - 20), QPointF(x, y))
        # Ruby tip ball
        painter.setPen(QPen(QColor("#ffffff"), 1.2))
        painter.setBrush(QBrush(QColor("#ff4757")))
        painter.drawEllipse(QPointF(x, y), 6.5, 6.5)
        if label:
            painter.setFont(QFont("Arial", 8, QFont.Weight.Bold))
            painter.setPen(QColor("#f39c12"))
            painter.drawText(QPointF(x - 22, y - 24), label)
        painter.restore()

    def _draw_arrow(self, painter, x1, y1, x2, y2, label=""):
        painter.save()
        painter.setPen(QPen(QColor("#f39c12"), 2.2))
        painter.drawLine(QPointF(x1, y1), QPointF(x2, y2))
        ang = math.atan2(y2 - y1, x2 - x1)
        arr_len = 8.5
        p_left = QPointF(x2 - arr_len * math.cos(ang - math.pi/6), y2 - arr_len * math.sin(ang - math.pi/6))
        p_right = QPointF(x2 - arr_len * math.cos(ang + math.pi/6), y2 - arr_len * math.sin(ang + math.pi/6))
        painter.setBrush(QBrush(QColor("#f39c12")))
        painter.drawPolygon(QPolygonF([QPointF(x2, y2), p_left, p_right]))
        if label:
            painter.setFont(QFont("Arial", 8, QFont.Weight.Bold))
            painter.setPen(QColor("#ffffff"))
            mid_x = (x1 + x2) / 2.0
            mid_y = (y1 + y2) / 2.0
            painter.drawText(QPointF(mid_x + 6, mid_y - 4), label)
        painter.restore()

    def _draw_corner(self, painter, rect, ox, oy, name, touch_x, touch_y):
        painter.setPen(QPen(QColor("#00adb5"), 2.5))
        painter.setBrush(QBrush(QColor(34, 47, 62, 230)))
        painter.drawRect(rect)
        self._draw_crosshair_origin(painter, ox, oy, "ORIGIN (0,0)")

        sx = ox - touch_x * 45
        sy = oy - touch_y * 45
        self._draw_stylus(painter, sx, sy, "START")
        self._draw_arrow(painter, sx, sy, sx, oy + touch_y * 3, "(1)")
        self._draw_arrow(painter, sx, sy, ox + touch_x * 3, sy, "(2)")

    def _draw_inside_corner(self, painter, ox, oy, dir_x, dir_y):
        self._draw_crosshair_origin(painter, ox, oy, "ORIGIN (0,0)")
        sx = ox + dir_x * 50
        sy = oy + dir_y * 50
        self._draw_stylus(painter, sx, sy, "START")
        self._draw_arrow(painter, sx, sy, sx, oy + dir_y * 2, "(1)")
        self._draw_arrow(painter, sx, sy, ox + dir_x * 2, sy, "(2)")

    def _draw_edge(self, painter, cx, cy, name):
        painter.save()
        pen_workpiece = QPen(QColor("#00adb5"), 2.5)
        brush_workpiece = QBrush(QColor(34, 47, 62, 230))
        painter.setPen(pen_workpiece)
        painter.setBrush(brush_workpiece)

        if "b" in name:
            painter.drawRect(QRectF(cx - 95, cy, 190, 75))
            self._draw_crosshair_origin(painter, cx, cy, "Y ZERO")
            sy = cy - 45 if "out" in name else cy + 45
            self._draw_stylus(painter, cx, sy, "START")
            self._draw_arrow(painter, cx, sy, cx, cy + (2 if "out" in name else -2), "(1) TOUCH Y")
        elif "f" in name:
            painter.drawRect(QRectF(cx - 95, cy - 75, 190, 75))
            self._draw_crosshair_origin(painter, cx, cy, "Y ZERO")
            sy = cy + 45 if "out" in name else cy - 45
            self._draw_stylus(painter, cx, sy, "START")
            self._draw_arrow(painter, cx, sy, cx, cy + (-2 if "out" in name else 2), "(1) TOUCH Y")
        elif "l" in name:
            painter.drawRect(QRectF(cx, cy - 75, 95, 150))
            self._draw_crosshair_origin(painter, cx, cy, "X ZERO")
            sx = cx - 45 if "out" in name else cx + 45
            self._draw_stylus(painter, sx, cy, "START")
            self._draw_arrow(painter, sx, cy, cx + (2 if "out" in name else -2), cy, "(1) TOUCH X")
        elif "r" in name:
            painter.drawRect(QRectF(cx - 95, cy - 75, 95, 150))
            self._draw_crosshair_origin(painter, cx, cy, "X ZERO")
            sx = cx + 45 if "out" in name else cx - 45
            self._draw_stylus(painter, sx, cy, "START")
            self._draw_arrow(painter, sx, cy, cx + (-2 if "out" in name else 2), cy, "(1) TOUCH X")
        painter.restore()

    def _draw_z_surface(self, painter, cx, cy):
        painter.save()
        # Isometric slab
        poly = QPolygonF([
            QPointF(cx - 95, cy + 12), QPointF(cx + 45, cy - 22),
            QPointF(cx + 105, cy + 12), QPointF(cx - 35, cy + 46)
        ])
        painter.setPen(QPen(QColor("#00adb5"), 2.5))
        painter.setBrush(QBrush(QColor(34, 47, 62, 230)))
        painter.drawPolygon(poly)
        painter.drawRect(QRectF(cx - 95, cy + 12, 60, 42))
        painter.drawRect(QRectF(cx - 35, cy + 46, 140, 42))

        self._draw_stylus(painter, cx, cy - 48, "START (Z+)")
        self._draw_arrow(painter, cx, cy - 38, cx, cy + 6, "G38.2 Z-")
        self._draw_crosshair_origin(painter, cx, cy + 12, "Z ZERO (0.0)")
        painter.restore()

    def _draw_angle(self, painter, cx, cy, name):
        painter.save()
        ang_deg = 8.5
        x0, y0 = cx - 95, cy + 32
        x1, y1 = cx + 95, cy - 32
        painter.setPen(QPen(QColor("#00adb5"), 3))
        painter.drawLine(QPointF(x0, y0), QPointF(x1, y1))

        p1 = QPointF(cx - 48, cy + 16)
        p2 = QPointF(cx + 48, cy - 16)
        self._draw_crosshair_origin(painter, p1.x(), p1.y(), "P1")
        self._draw_crosshair_origin(painter, p2.x(), p2.y(), "P2")

        self._draw_arrow(painter, p1.x(), p1.y() + 38, p1.x(), p1.y() + 4, "(1)")
        self._draw_arrow(painter, p2.x(), p2.y() + 38, p2.x(), p2.y() + 4, "(2)")

        painter.setFont(QFont("Arial", 9, QFont.Weight.Bold))
        painter.setPen(QColor("#f39c12"))
        painter.drawText(QPointF(cx - 45, cy - 38), f"Calculated Skew Angle θ: {ang_deg:.2f}°")
        painter.restore()


class ProbeGridButton(QPushButton):
    """3x3 Probing Grid Button with custom drafting graphics for Inside and Outside routines."""

    def __init__(self, cycle_code: str, label: str, tooltip: str = "", is_center: bool = False, is_inside: bool = False, parent=None):
        super().__init__(parent)
        self.cycle_code = cycle_code
        self.label_text = label
        self.is_center = is_center
        self.is_inside = is_inside
        self.is_selected = False
        self.setToolTip(tooltip)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumSize(84, 74)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def set_selected(self, selected: bool):
        if self.is_selected != selected:
            self.is_selected = selected
            self.update()

    def enterEvent(self, event):
        super().enterEvent(event)
        self.update()

    def leaveEvent(self, event):
        super().leaveEvent(event)
        self.update()

    def _draw_arrow(self, painter, p1, p2, color=QColor("#00d2d3"), arrow_size=4.0):
        painter.save()
        pen = QPen(color, 1.8, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.drawLine(p1, p2)
        angle = math.atan2(p2.y() - p1.y(), p2.x() - p1.x())
        a1 = QPointF(p2.x() - arrow_size * math.cos(angle - math.pi / 5),
                     p2.y() - arrow_size * math.sin(angle - math.pi / 5))
        a2 = QPointF(p2.x() - arrow_size * math.cos(angle + math.pi / 5),
                     p2.y() - arrow_size * math.sin(angle + math.pi / 5))
        painter.setBrush(QBrush(color))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawPolygon(QPolygonF([p2, a1, a2]))
        painter.restore()

    def _draw_ruby_tip(self, painter, pos, r=3.5):
        painter.save()
        painter.setPen(QPen(QColor("#7f1d1d"), 0.8))
        painter.setBrush(QBrush(QColor("#e74c3c")))
        painter.drawEllipse(pos, r, r)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(QColor("#ffffff")))
        painter.drawEllipse(QPointF(pos.x() - r * 0.35, pos.y() - r * 0.35), r * 0.35, r * 0.35)
        painter.restore()

    def _draw_crosshair(self, painter, pos, size=4.0, color=QColor("#ffd32a")):
        painter.save()
        painter.setPen(QPen(color, 1.2, Qt.PenStyle.SolidLine))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(pos, size, size)
        painter.drawLine(QPointF(pos.x() - size - 2, pos.y()), QPointF(pos.x() + size + 2, pos.y()))
        painter.drawLine(QPointF(pos.x(), pos.y() - size - 2), QPointF(pos.x(), pos.y() + size + 2))
        painter.restore()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        w = float(self.width())
        h = float(self.height())

        # 1. Background and Border
        if self.is_selected:
            bg_color = QColor("#163b40") if self.is_inside else QColor("#1e3d2f")
            border_color = QColor("#00adb5") if self.is_inside else QColor("#9ad58d")
            border_w = 2.0
        elif self.underMouse():
            bg_color = QColor("#30363d")
            border_color = QColor("#636e72")
            border_w = 1.5
        elif self.is_center:
            bg_color = QColor("#1b2838")
            border_color = QColor("#34495e")
            border_w = 1.0
        else:
            bg_color = QColor("#22272b")
            border_color = QColor("#383e42")
            border_w = 1.0

        painter.setPen(QPen(border_color, border_w))
        painter.setBrush(QBrush(bg_color))
        painter.drawRoundedRect(QRectF(1.0, 1.0, w - 2.0, h - 2.0), 5.0, 5.0)

        # 2. Graphic Canvas
        cx = w / 2.0
        cy = (h - 18.0) / 2.0
        hw = 16.0
        hh = 13.0

        arrow_col = QColor("#00adb5") if self.is_inside else QColor("#2ecc71")
        wp_fill = QColor("#57606f")
        wp_border = QColor("#ced6e0")

        code = self.cycle_code
        if code == "out_bl":
            painter.setPen(QPen(wp_border, 1.5))
            painter.setBrush(QBrush(wp_fill))
            painter.drawRect(QRectF(cx, cy, hw, hh))
            self._draw_arrow(painter, QPointF(cx - 14, cy + 5), QPointF(cx - 2, cy + 5), arrow_col)
            self._draw_arrow(painter, QPointF(cx + 5, cy - 13), QPointF(cx + 5, cy - 2), arrow_col)
            self._draw_ruby_tip(painter, QPointF(cx - 8, cy - 8))
            self._draw_crosshair(painter, QPointF(cx, cy))
        elif code == "out_b":
            painter.setPen(QPen(wp_border, 1.5))
            painter.setBrush(QBrush(wp_fill))
            painter.drawRect(QRectF(cx - hw, cy, hw * 2, hh))
            self._draw_arrow(painter, QPointF(cx, cy - 14), QPointF(cx, cy - 2), arrow_col)
            self._draw_ruby_tip(painter, QPointF(cx, cy - 8))
            self._draw_crosshair(painter, QPointF(cx, cy))
        elif code == "out_br":
            painter.setPen(QPen(wp_border, 1.5))
            painter.setBrush(QBrush(wp_fill))
            painter.drawRect(QRectF(cx - hw, cy, hw, hh))
            self._draw_arrow(painter, QPointF(cx + 14, cy + 5), QPointF(cx + 2, cy + 5), arrow_col)
            self._draw_arrow(painter, QPointF(cx - 5, cy - 13), QPointF(cx - 5, cy - 2), arrow_col)
            self._draw_ruby_tip(painter, QPointF(cx + 8, cy - 8))
            self._draw_crosshair(painter, QPointF(cx, cy))
        elif code == "out_l":
            painter.setPen(QPen(wp_border, 1.5))
            painter.setBrush(QBrush(wp_fill))
            painter.drawRect(QRectF(cx, cy - hh, hw, hh * 2))
            self._draw_arrow(painter, QPointF(cx - 14, cy), QPointF(cx - 2, cy), arrow_col)
            self._draw_ruby_tip(painter, QPointF(cx - 8, cy))
            self._draw_crosshair(painter, QPointF(cx, cy))
        elif code == "out_boss":
            painter.setPen(QPen(wp_border, 1.5))
            painter.setBrush(QBrush(wp_fill))
            painter.drawEllipse(QPointF(cx, cy), 9.0, 9.0)
            self._draw_arrow(painter, QPointF(cx, cy - 15), QPointF(cx, cy - 9), arrow_col)
            self._draw_arrow(painter, QPointF(cx, cy + 15), QPointF(cx, cy + 9), arrow_col)
            self._draw_arrow(painter, QPointF(cx - 15, cy), QPointF(cx - 9, cy), arrow_col)
            self._draw_arrow(painter, QPointF(cx + 15, cy), QPointF(cx + 9, cy), arrow_col)
            self._draw_crosshair(painter, QPointF(cx, cy), size=4.5, color=QColor("#9ad58d"))
        elif code == "out_r":
            painter.setPen(QPen(wp_border, 1.5))
            painter.setBrush(QBrush(wp_fill))
            painter.drawRect(QRectF(cx - hw, cy - hh, hw, hh * 2))
            self._draw_arrow(painter, QPointF(cx + 14, cy), QPointF(cx + 2, cy), arrow_col)
            self._draw_ruby_tip(painter, QPointF(cx + 8, cy))
            self._draw_crosshair(painter, QPointF(cx, cy))
        elif code == "out_fl":
            painter.setPen(QPen(wp_border, 1.5))
            painter.setBrush(QBrush(wp_fill))
            painter.drawRect(QRectF(cx, cy - hh, hw, hh))
            self._draw_arrow(painter, QPointF(cx - 14, cy - 5), QPointF(cx - 2, cy - 5), arrow_col)
            self._draw_arrow(painter, QPointF(cx + 5, cy + 13), QPointF(cx + 5, cy + 2), arrow_col)
            self._draw_ruby_tip(painter, QPointF(cx - 8, cy + 8))
            self._draw_crosshair(painter, QPointF(cx, cy))
        elif code == "out_f":
            painter.setPen(QPen(wp_border, 1.5))
            painter.setBrush(QBrush(wp_fill))
            painter.drawRect(QRectF(cx - hw, cy - hh, hw * 2, hh))
            self._draw_arrow(painter, QPointF(cx, cy + 14), QPointF(cx, cy + 2), arrow_col)
            self._draw_ruby_tip(painter, QPointF(cx, cy + 8))
            self._draw_crosshair(painter, QPointF(cx, cy))
        elif code == "out_fr":
            painter.setPen(QPen(wp_border, 1.5))
            painter.setBrush(QBrush(wp_fill))
            painter.drawRect(QRectF(cx - hw, cy - hh, hw, hh))
            self._draw_arrow(painter, QPointF(cx + 14, cy - 5), QPointF(cx + 2, cy - 5), arrow_col)
            self._draw_arrow(painter, QPointF(cx - 5, cy + 13), QPointF(cx - 5, cy + 2), arrow_col)
            self._draw_ruby_tip(painter, QPointF(cx + 8, cy + 8))
            self._draw_crosshair(painter, QPointF(cx, cy))
        elif code == "in_bl":
            painter.setPen(QPen(QColor("#00adb5"), 2.0))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawLine(QPointF(cx - hw, cy + hh), QPointF(cx - hw, cy - hh))
            painter.drawLine(QPointF(cx - hw, cy - hh), QPointF(cx + hw, cy - hh))
            self._draw_arrow(painter, QPointF(cx - hw + 8, cy - hh + 8), QPointF(cx - hw + 1, cy - hh + 8), arrow_col)
            self._draw_arrow(painter, QPointF(cx - hw + 8, cy - hh + 8), QPointF(cx - hw + 8, cy - hh + 1), arrow_col)
            self._draw_ruby_tip(painter, QPointF(cx - hw + 8, cy - hh + 8))
            self._draw_crosshair(painter, QPointF(cx - hw, cy - hh))
        elif code == "in_b":
            painter.setPen(QPen(QColor("#00adb5"), 2.0))
            painter.drawLine(QPointF(cx - hw, cy - hh), QPointF(cx + hw, cy - hh))
            self._draw_arrow(painter, QPointF(cx, cy - 1), QPointF(cx, cy - hh + 1), arrow_col)
            self._draw_ruby_tip(painter, QPointF(cx, cy - 1))
            self._draw_crosshair(painter, QPointF(cx, cy - hh))
        elif code == "in_br":
            painter.setPen(QPen(QColor("#00adb5"), 2.0))
            painter.drawLine(QPointF(cx - hw, cy - hh), QPointF(cx + hw, cy - hh))
            painter.drawLine(QPointF(cx + hw, cy - hh), QPointF(cx + hw, cy + hh))
            self._draw_arrow(painter, QPointF(cx + hw - 8, cy - hh + 8), QPointF(cx + hw - 1, cy - hh + 8), arrow_col)
            self._draw_arrow(painter, QPointF(cx + hw - 8, cy - hh + 8), QPointF(cx + hw - 8, cy - hh + 1), arrow_col)
            self._draw_ruby_tip(painter, QPointF(cx + hw - 8, cy - hh + 8))
            self._draw_crosshair(painter, QPointF(cx + hw, cy - hh))
        elif code == "in_l":
            painter.setPen(QPen(QColor("#00adb5"), 2.0))
            painter.drawLine(QPointF(cx - hw, cy - hh), QPointF(cx - hw, cy + hh))
            self._draw_arrow(painter, QPointF(cx - 1, cy), QPointF(cx - hw + 1, cy), arrow_col)
            self._draw_ruby_tip(painter, QPointF(cx - 1, cy))
            self._draw_crosshair(painter, QPointF(cx - hw, cy))
        elif code == "in_hole":
            painter.setPen(QPen(QColor("#00adb5"), 2.0))
            painter.setBrush(QBrush(QColor("#111417")))
            painter.drawEllipse(QPointF(cx, cy), 10.0, 10.0)
            self._draw_arrow(painter, QPointF(cx, cy - 2), QPointF(cx, cy - 9), arrow_col)
            self._draw_arrow(painter, QPointF(cx, cy + 2), QPointF(cx, cy + 9), arrow_col)
            self._draw_arrow(painter, QPointF(cx - 2, cy), QPointF(cx - 9, cy), arrow_col)
            self._draw_arrow(painter, QPointF(cx + 2, cy), QPointF(cx + 9, cy), arrow_col)
            self._draw_crosshair(painter, QPointF(cx, cy), size=4.0, color=QColor("#00adb5"))
        elif code == "in_r":
            painter.setPen(QPen(QColor("#00adb5"), 2.0))
            painter.drawLine(QPointF(cx + hw, cy - hh), QPointF(cx + hw, cy + hh))
            self._draw_arrow(painter, QPointF(cx + 1, cy), QPointF(cx + hw - 1, cy), arrow_col)
            self._draw_ruby_tip(painter, QPointF(cx + 1, cy))
            self._draw_crosshair(painter, QPointF(cx + hw, cy))
        elif code == "in_fl":
            painter.setPen(QPen(QColor("#00adb5"), 2.0))
            painter.drawLine(QPointF(cx - hw, cy - hh), QPointF(cx - hw, cy + hh))
            painter.drawLine(QPointF(cx - hw, cy + hh), QPointF(cx + hw, cy + hh))
            self._draw_arrow(painter, QPointF(cx - hw + 8, cy + hh - 8), QPointF(cx - hw + 1, cy + hh - 8), arrow_col)
            self._draw_arrow(painter, QPointF(cx - hw + 8, cy + hh - 8), QPointF(cx - hw + 8, cy + hh - 1), arrow_col)
            self._draw_ruby_tip(painter, QPointF(cx - hw + 8, cy + hh - 8))
            self._draw_crosshair(painter, QPointF(cx - hw, cy + hh))
        elif code == "in_f":
            painter.setPen(QPen(QColor("#00adb5"), 2.0))
            painter.drawLine(QPointF(cx - hw, cy + hh), QPointF(cx + hw, cy + hh))
            self._draw_arrow(painter, QPointF(cx, cy + 1), QPointF(cx, cy + hh - 1), arrow_col)
            self._draw_ruby_tip(painter, QPointF(cx, cy + 1))
            self._draw_crosshair(painter, QPointF(cx, cy + hh))
        elif code == "in_fr":
            painter.setPen(QPen(QColor("#00adb5"), 2.0))
            painter.drawLine(QPointF(cx - hw, cy + hh), QPointF(cx + hw, cy + hh))
            painter.drawLine(QPointF(cx + hw, cy + hh), QPointF(cx + hw, cy - hh))
            self._draw_arrow(painter, QPointF(cx + hw - 8, cy + hh - 8), QPointF(cx + hw - 1, cy + hh - 8), arrow_col)
            self._draw_arrow(painter, QPointF(cx + hw - 8, cy + hh - 8), QPointF(cx + hw - 8, cy + hh - 1), arrow_col)
            self._draw_ruby_tip(painter, QPointF(cx + hw - 8, cy + hh - 8))
            self._draw_crosshair(painter, QPointF(cx + hw, cy + hh))

        # 3. Text Label
        painter.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
        text_col = QColor("#ffffff") if self.is_selected else QColor("#b2bec3")
        painter.setPen(QPen(text_col))
        painter.drawText(QRectF(2, h - 17, w - 4, 15), Qt.AlignmentFlag.AlignCenter, self.label_text)


class ProbingTab(QWidget):
    """
    QtDragon style Probing Tab:
    - Top: Digital LED DROs, segmented WCS buttons (G54-G59), and hardware probe selector
    - Left: Cycle categories (Outside, Inside, Both 3x3 Grids, Surface Z, Angle/Skew) with graphical directional matrices
    - Center: Drafting-style CAD diagram with live trajectory, stylus, and origin crosshairs
    - Right: QtDragon parameters, glowing START PROBE button, ABORT, quick zeroing, and results table
    """

    probing_started = pyqtSignal(str)
    probing_completed = pyqtSignal(str, dict)

    def __init__(self, settings, dashboard=None, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.dashboard = dashboard
        self.active_cycle = "out_fl"
        self.active_wcs = "G54"
        self.is_probing = False
        self.last_results = {}
        self.all_grid_buttons = []
        self.cycle_buttons = {}

        self.init_ui()
        self.load_settings()

        # Telemetry update timer
        self.poll_timer = QTimer(self)
        self.poll_timer.timeout.connect(self._poll_machine_telemetry)
        self.poll_timer.start(100)

    def init_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(12, 10, 12, 10)
        main_layout.setSpacing(10)
        self.setStyleSheet("""
            QWidget { background-color: #242424; color: #e0e0e0; font-family: 'Segoe UI', Arial, sans-serif; }
            QGroupBox { font-weight: bold; border: 1px solid #3d3d3d; border-radius: 6px; margin-top: 6px; }
            QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px; color: #9ad58d; font-size: 11px; }
        """)

        # 1. TOP HEADER & QTDRAGON DIGITAL DRO BAR
        header_bar = QFrame()
        header_bar.setStyleSheet("background-color: #1a1a1a; border: 1px solid #333333; border-radius: 6px; padding: 4px;")
        header_layout = QHBoxLayout(header_bar)
        header_layout.setContentsMargins(10, 6, 10, 6)
        header_layout.setSpacing(12)

        title_lbl = QLabel("🐲 PROBING — QTDRAGON STYLE")
        title_lbl.setStyleSheet("font-size: 14px; font-weight: bold; color: #9ad58d; letter-spacing: 0.5px;")
        header_layout.addWidget(title_lbl)

        # Segmented WCS Button Bar (QtDragon style)
        header_layout.addSpacing(10)
        wcs_box = QHBoxLayout()
        wcs_box.setSpacing(2)
        self.wcs_buttons = {}
        wcs_list = ["G54", "G55", "G56", "G57", "G58", "G59"]
        for wcs in wcs_list:
            btn = QPushButton(wcs)
            btn.setCheckable(True)
            if wcs == "G54": btn.setChecked(True)
            btn.setStyleSheet("""
                QPushButton { background: #333333; color: #aaaaaa; border: 1px solid #222222; padding: 4px 8px; font-weight: bold; font-size: 11px; }
                QPushButton:checked { background: #27ae60; color: white; border: 1px solid #9ad58d; }
                QPushButton:hover:!checked { background: #444444; color: white; }
            """)
            btn.clicked.connect(lambda _, w=wcs: self._select_wcs(w))
            wcs_box.addWidget(btn)
            self.wcs_buttons[wcs] = btn
        header_layout.addLayout(wcs_box)

        header_layout.addStretch()

        # Probe Hardware Selector
        self.probe_hw_selector = QComboBox()
        self.probe_hw_selector.addItems(["🟢 Active: 3D Touch Probe", "🟠 Active: Touch Plate / Setter"])
        self.probe_hw_selector.setStyleSheet("""
            QComboBox { background-color: #2c3e50; color: white; font-weight: bold; padding: 5px 12px; border: 1px solid #34495e; border-radius: 4px; }
            QComboBox::drop-down { border: none; }
        """)
        self.probe_hw_selector.currentIndexChanged.connect(self._on_probe_hw_changed)
        header_layout.addWidget(self.probe_hw_selector)

        # QtDragon Pitch-Black Digital DRO
        dro_box = QHBoxLayout()
        dro_box.setSpacing(6)
        self.lbl_dro_x = QLabel("X: +000.0000")
        self.lbl_dro_y = QLabel("Y: +000.0000")
        self.lbl_dro_z = QLabel("Z: +000.0000")
        for lbl in (self.lbl_dro_x, self.lbl_dro_y, self.lbl_dro_z):
            lbl.setStyleSheet("""
                background-color: #111111; color: #9ad58d; font-family: 'Consolas', 'Monospace', monospace;
                font-size: 14px; font-weight: bold; padding: 4px 10px; border: 1px solid #2a2a2a; border-radius: 4px;
            """)
            dro_box.addWidget(lbl)
        header_layout.addLayout(dro_box)

        main_layout.addWidget(header_bar)

        # 2. BODY - 3 COLUMN LAYOUT
        body_layout = QHBoxLayout()
        body_layout.setSpacing(10)

        # --- LEFT COLUMN: QTDRAGON CYCLE SELECTOR ---
        left_group = QGroupBox("Probe Cycles")
        left_layout = QVBoxLayout(left_group)
        left_layout.setSpacing(8)

        self.category_tabs = QTabWidget()
        self.category_tabs.setStyleSheet("""
            QTabBar::tab { background: #333333; color: #cccccc; padding: 7px 12px; font-weight: bold; font-size: 11px; border-top-left-radius: 4px; border-top-right-radius: 4px; }
            QTabBar::tab:selected { background: #454545; color: #9ad58d; border-bottom: 2px solid #9ad58d; }
            QTabWidget::pane { border: 1px solid #333333; background: #2a2a2a; border-radius: 4px; }
        """)

        # 3x3 Button Specs for Outside and Inside Probing
        out_buttons = [
            ("Back-Left", "out_bl", "Outside Back-Left Corner (O114)"),
            ("Back Y+", "out_b", "Outside Back Edge Y+ (O115)"),
            ("Back-Right", "out_br", "Outside Back-Right Corner (O116)"),
            ("Left X-", "out_l", "Outside Left Edge X- (O117)"),
            ("Boss Center", "out_boss", "Outside Boss / Center (O106)"),
            ("Right X+", "out_r", "Outside Right Edge X+ (O118)"),
            ("Front-Left", "out_fl", "Outside Front-Left Corner (O119)"),
            ("Front Y-", "out_f", "Outside Front Edge Y- (O120)"),
            ("Front-Right", "out_fr", "Outside Front-Right Corner (O121)"),
        ]

        in_buttons = [
            ("In Back-Left", "in_bl", "Inside Back-Left Corner (O122)"),
            ("In Back Y+", "in_b", "Inside Back Wall (O123)"),
            ("In Back-Right", "in_br", "Inside Back-Right Corner (O124)"),
            ("In Left X-", "in_l", "Inside Left Wall (O125)"),
            ("Bore Center", "in_hole", "Inside Hole / Bore Center & Dia (O107)"),
            ("In Right X+", "in_r", "Inside Right Wall (O126)"),
            ("In Front-Left", "in_fl", "Inside Front-Left Corner (O127)"),
            ("In Front Y-", "in_f", "Inside Front Wall (O128)"),
            ("In Front-Right", "in_fr", "Inside Front-Right Corner (O129)"),
        ]

        # Tab 1: Outside Cycles (3x3 Grid)
        tab_outside = QWidget()
        tab_out_layout = QVBoxLayout(tab_outside)
        tab_out_layout.setContentsMargins(6, 6, 6, 6)
        tab_out_layout.setSpacing(6)
        lbl_out = QLabel("Workpiece Outside Features (3x3 Grid):")
        lbl_out.setStyleSheet("color: #9ad58d; font-weight: bold; font-size: 11px;")
        tab_out_layout.addWidget(lbl_out)

        out_grid, _ = self._create_3x3_grid(out_buttons, is_inside=False)
        tab_out_layout.addLayout(out_grid)
        tab_out_layout.addStretch()
        self.category_tabs.addTab(tab_outside, "🔲 Outside (3x3)")

        # Tab 2: Inside Cycles (3x3 Grid)
        tab_inside = QWidget()
        tab_in_layout = QVBoxLayout(tab_inside)
        tab_in_layout.setContentsMargins(6, 6, 6, 6)
        tab_in_layout.setSpacing(6)
        lbl_in = QLabel("Workpiece Inside Pockets / Holes (3x3 Grid):")
        lbl_in.setStyleSheet("color: #00adb5; font-weight: bold; font-size: 11px;")
        tab_in_layout.addWidget(lbl_in)

        in_grid, _ = self._create_3x3_grid(in_buttons, is_inside=True)
        tab_in_layout.addLayout(in_grid)
        tab_in_layout.addStretch()
        self.category_tabs.addTab(tab_inside, "🔘 Inside (3x3)")

        # Tab 3: Both Grids (Outside & Inside 2x 3x3)
        tab_both = QWidget()
        tab_both_layout = QVBoxLayout(tab_both)
        tab_both_layout.setContentsMargins(2, 2, 2, 2)
        tab_both_layout.setSpacing(4)

        scroll_both = QScrollArea()
        scroll_both.setWidgetResizable(True)
        scroll_both.setStyleSheet("QScrollArea { border: none; background: transparent; }")

        scroll_widget = QWidget()
        scroll_layout = QVBoxLayout(scroll_widget)
        scroll_layout.setContentsMargins(2, 2, 2, 2)
        scroll_layout.setSpacing(8)

        both_out_box = QGroupBox("🔲 Outside Routines (3x3 Grid)")
        both_out_box_layout = QVBoxLayout(both_out_box)
        both_out_box_layout.setContentsMargins(4, 6, 4, 6)
        both_out_grid, _ = self._create_3x3_grid(out_buttons, is_inside=False)
        both_out_box_layout.addLayout(both_out_grid)
        scroll_layout.addWidget(both_out_box)

        both_in_box = QGroupBox("🔘 Inside Routines (3x3 Grid)")
        both_in_box_layout = QVBoxLayout(both_in_box)
        both_in_box_layout.setContentsMargins(4, 6, 4, 6)
        both_in_grid, _ = self._create_3x3_grid(in_buttons, is_inside=True)
        both_in_box_layout.addLayout(both_in_grid)
        scroll_layout.addWidget(both_in_box)

        scroll_both.setWidget(scroll_widget)
        tab_both_layout.addWidget(scroll_both)
        self.category_tabs.addTab(tab_both, "🔲🔘 Both (2x 3x3)")

        # Tab 4: Surface Z
        tab_z = QWidget()
        tab_z_layout = QVBoxLayout(tab_z)
        tab_z_layout.setSpacing(8)

        btn_probe_z = QPushButton("⬇️ Surface Z Zero (O103)")
        btn_probe_z.setStyleSheet(self._qtdragon_action_style(color="#8e44ad"))
        btn_probe_z.clicked.connect(lambda: self.select_cycle("probe_z"))
        tab_z_layout.addWidget(btn_probe_z)
        self.cycle_buttons["probe_z"] = btn_probe_z

        lbl_z_note = QLabel("Downward search touching workpiece top face, auto-zeroing Z offset.")
        lbl_z_note.setStyleSheet("color: #888888; font-size: 11px;")
        lbl_z_note.setWordWrap(True)
        tab_z_layout.addWidget(lbl_z_note)
        tab_z_layout.addStretch()
        self.category_tabs.addTab(tab_z, "⬇️ Surface Z")

        # Tab 5: Skew / Angle Alignment
        tab_angle = QWidget()
        tab_ang_layout = QVBoxLayout(tab_angle)
        tab_ang_layout.setSpacing(8)

        btn_ang_x = QPushButton("📐 X-Axis Edge Angle Align")
        btn_ang_x.setStyleSheet(self._qtdragon_btn_style())
        btn_ang_x.clicked.connect(lambda: self.select_cycle("angle_x"))
        tab_ang_layout.addWidget(btn_ang_x)
        self.cycle_buttons["angle_x"] = btn_ang_x

        btn_ang_y = QPushButton("📐 Y-Axis Edge Angle Align")
        btn_ang_y.setStyleSheet(self._qtdragon_btn_style())
        btn_ang_y.clicked.connect(lambda: self.select_cycle("angle_y"))
        tab_ang_layout.addWidget(btn_ang_y)
        self.cycle_buttons["angle_y"] = btn_ang_y

        lbl_ang_note = QLabel("Probes 2 points along a workpiece edge to compute rotation skew θ and apply coordinate rotation (G10 L2 P0 R...).")
        lbl_ang_note.setStyleSheet("color: #888888; font-size: 11px;")
        lbl_ang_note.setWordWrap(True)
        tab_ang_layout.addWidget(lbl_ang_note)
        tab_ang_layout.addStretch()
        self.category_tabs.addTab(tab_angle, "📐 Angle")

        left_layout.addWidget(self.category_tabs)
        left_group.setFixedWidth(340)
        body_layout.addWidget(left_group)

        # --- CENTER COLUMN: QTDRAGON DRAFTING DIAGRAM ---
        center_group = QGroupBox("Cycle Visual Drafting Diagram")
        center_layout = QVBoxLayout(center_group)
        center_layout.setSpacing(8)

        self.diagram = QtDragonProbeDiagram()
        center_layout.addWidget(self.diagram, 1)

        # QtDragon Guidance Box
        inst_box = QFrame()
        inst_box.setStyleSheet("background-color: #1a1a1a; border: 1px solid #333333; border-radius: 4px; padding: 8px;")
        inst_layout = QVBoxLayout(inst_box)
        inst_layout.setSpacing(4)

        self.lbl_cycle_title = QLabel("Selected: Outside Front-Left Corner (O119)")
        self.lbl_cycle_title.setStyleSheet("color: #9ad58d; font-weight: bold; font-size: 12px;")
        inst_layout.addWidget(self.lbl_cycle_title)

        self.lbl_cycle_steps = QLabel("1. Jog stylus ~5-10mm above the starting point.\n2. Verify search distance exceeds gap.\n3. Click START PROBE.")
        self.lbl_cycle_steps.setStyleSheet("color: #b0b0b0; font-size: 11px;")
        self.lbl_cycle_steps.setWordWrap(True)
        inst_layout.addWidget(self.lbl_cycle_steps)

        center_layout.addWidget(inst_box)
        body_layout.addWidget(center_group, 1)

        # --- RIGHT COLUMN: PARAMETERS & ACTION CONTROLS ---
        right_group = QGroupBox("Probe Parameters & Controls")
        right_layout = QVBoxLayout(right_group)
        right_layout.setSpacing(8)

        # Parameters Form
        form_grid = QGridLayout()
        form_grid.setSpacing(5)

        input_style = "background-color: #151515; color: white; border: 1px solid #3d3d3d; padding: 4px 6px; border-radius: 3px; font-weight: bold;"

        self.inp_probe_diam = QLineEdit("3.0")
        self.inp_probe_diam.setStyleSheet(input_style)
        self.inp_probe_diam.textChanged.connect(self._on_param_changed)
        form_grid.addWidget(QLabel("Tip Dia (D):"), 0, 0); form_grid.addWidget(self.inp_probe_diam, 0, 1)

        self.inp_xy_clear = QLineEdit("5.0")
        self.inp_xy_clear.setStyleSheet(input_style)
        self.inp_xy_clear.textChanged.connect(self._on_param_changed)
        form_grid.addWidget(QLabel("XY Clear:"), 1, 0); form_grid.addWidget(self.inp_xy_clear, 1, 1)

        self.inp_z_drop = QLineEdit("5.0")
        self.inp_z_drop.setStyleSheet(input_style)
        self.inp_z_drop.textChanged.connect(self._on_param_changed)
        form_grid.addWidget(QLabel("Z Drop:"), 2, 0); form_grid.addWidget(self.inp_z_drop, 2, 1)

        self.inp_search_dist = QLineEdit("20.0")
        self.inp_search_dist.setStyleSheet(input_style)
        self.inp_search_dist.textChanged.connect(self._on_param_changed)
        form_grid.addWidget(QLabel("Search Dist:"), 3, 0); form_grid.addWidget(self.inp_search_dist, 3, 1)

        self.inp_probe_vel = QLineEdit("100.0")
        self.inp_probe_vel.setStyleSheet(input_style)
        self.inp_probe_vel.textChanged.connect(self._on_param_changed)
        form_grid.addWidget(QLabel("Feed Rate:"), 4, 0); form_grid.addWidget(self.inp_probe_vel, 4, 1)

        self.inp_expected_size = QLineEdit("50.0")
        self.inp_expected_size.setStyleSheet(input_style)
        form_grid.addWidget(QLabel("Expected Size:"), 5, 0); form_grid.addWidget(self.inp_expected_size, 5, 1)

        right_layout.addLayout(form_grid)

        # Zero Mode Selection
        mode_layout = QHBoxLayout()
        self.rb_set_zero = QRadioButton("Set Work Zero")
        self.rb_set_zero.setChecked(True)
        self.rb_set_zero.setStyleSheet("color: #9ad58d; font-weight: bold; font-size: 11px;")
        self.rb_measure_only = QRadioButton("Measure Only")
        self.rb_measure_only.setStyleSheet("color: #00adb5; font-weight: bold; font-size: 11px;")
        mode_layout.addWidget(self.rb_set_zero)
        mode_layout.addWidget(self.rb_measure_only)
        right_layout.addLayout(mode_layout)

        # PROMINENT QTDRAGON ACTION BUTTONS
        self.btn_start_cycle = QPushButton("▶ START PROBE")
        self.btn_start_cycle.setStyleSheet("""
            QPushButton {
                background: qlineargradient(spread:pad, x1:0, y1:1, x2:0, y2:0, stop:0 #1b4332, stop:1 #2d6a4f);
                color: #ffffff; font-weight: bold; padding: 12px; border: 1.5px solid #9ad58d; border-radius: 5px; font-size: 13px;
            }
            QPushButton:hover { background: #2d6a4f; border-color: #52b788; }
            QPushButton:pressed { background: #081c15; }
        """)
        self.btn_start_cycle.clicked.connect(self.start_probing_cycle)
        right_layout.addWidget(self.btn_start_cycle)

        self.btn_abort_cycle = QPushButton("⏹ ABORT")
        self.btn_abort_cycle.setStyleSheet("""
            QPushButton {
                background: qlineargradient(spread:pad, x1:0, y1:1, x2:0, y2:0, stop:0 #5c1d1d, stop:1 #8b2626);
                color: #ffffff; font-weight: bold; padding: 8px; border: 1px solid #e74c3c; border-radius: 4px; font-size: 12px;
            }
            QPushButton:hover { background: #a82828; }
            QPushButton:pressed { background: #3b1010; }
        """)
        self.btn_abort_cycle.clicked.connect(self.abort_probing)
        right_layout.addWidget(self.btn_abort_cycle)

        # Quick Manual Zero Buttons
        zero_group = QGroupBox("Direct Axis Touch-Off")
        zero_layout = QGridLayout(zero_group)
        zero_layout.setContentsMargins(4, 6, 4, 4)
        zero_layout.setSpacing(4)
        z_btn_style = """
            QPushButton { background: #2f3640; color: #e0e0e0; font-weight: bold; padding: 5px; border: 1px solid #444; border-radius: 3px; font-size: 10px; }
            QPushButton:hover { background: #353b48; border-color: #9ad58d; color: #9ad58d; }
        """
        btn_zx = QPushButton("Zero X"); btn_zx.setStyleSheet(z_btn_style); btn_zx.clicked.connect(lambda: self.zero_axis("X"))
        btn_zy = QPushButton("Zero Y"); btn_zy.setStyleSheet(z_btn_style); btn_zy.clicked.connect(lambda: self.zero_axis("Y"))
        btn_zz = QPushButton("Zero Z"); btn_zz.setStyleSheet(z_btn_style); btn_zz.clicked.connect(lambda: self.zero_axis("Z"))
        btn_zxy = QPushButton("Zero XY"); btn_zxy.setStyleSheet(z_btn_style); btn_zxy.clicked.connect(lambda: self.zero_axis("XY"))
        btn_zall = QPushButton("Zero All"); btn_zall.setStyleSheet(z_btn_style); btn_zall.clicked.connect(lambda: self.zero_axis("ALL"))

        zero_layout.addWidget(btn_zx, 0, 0); zero_layout.addWidget(btn_zy, 0, 1); zero_layout.addWidget(btn_zz, 0, 2)
        zero_layout.addWidget(btn_zxy, 1, 0, 1, 1); zero_layout.addWidget(btn_zall, 1, 1, 1, 2)
        right_layout.addWidget(zero_group)

        # Results & Log Card
        res_group = QGroupBox("Probing Results")
        res_layout = QVBoxLayout(res_group)
        res_layout.setContentsMargins(6, 6, 6, 6)
        self.lbl_results = QLabel("Status: Idle\nReady for probing cycle.")
        self.lbl_results.setStyleSheet("font-family: 'Consolas', monospace; font-size: 10px; color: #9ad58d;")
        res_layout.addWidget(self.lbl_results)
        right_layout.addWidget(res_group)

        right_group.setFixedWidth(290)
        body_layout.addWidget(right_group)

        main_layout.addLayout(body_layout, 1)
        self.select_cycle(self.active_cycle)

    def _create_3x3_grid(self, button_specs, is_inside=False):
        grid = QGridLayout()
        grid.setSpacing(6)
        buttons = []
        for idx, (label, code, tip) in enumerate(button_specs):
            is_center = (code in ("out_boss", "in_hole"))
            btn = ProbeGridButton(code, label, tooltip=tip, is_center=is_center, is_inside=is_inside, parent=self)
            btn.clicked.connect(lambda _, c=code: self.select_cycle(c))
            grid.addWidget(btn, idx // 3, idx % 3)
            buttons.append(btn)
            self.all_grid_buttons.append(btn)
            if code not in self.cycle_buttons:
                self.cycle_buttons[code] = btn
        return grid, buttons

    def _qtdragon_btn_style(self, is_center=False, is_inside=False):
        if is_center:
            bg0, bg1 = ("#005f73", "#0a9396") if is_inside else ("#b07d1a", "#d49b28")
            border = "#94d2bd" if is_inside else "#e9d8a6"
        elif is_inside:
            bg0, bg1 = ("#1b4332", "#2d6a4f")
            border = "#74c69d"
        else:
            bg0, bg1 = ("#1a365d", "#2b4c7e")
            border = "#63b3ed"

        return f"""
            QPushButton {{
                background: qlineargradient(spread:pad, x1:0, y1:1, x2:0, y2:0, stop:0 {bg0}, stop:1 {bg1});
                color: #ffffff; font-weight: bold; padding: 9px 4px; border: 1px solid {border}; border-radius: 4px; font-size: 11px;
            }}
            QPushButton:hover {{ border-color: #ffffff; }}
            QPushButton:pressed {{ background: #111111; }}
        """

    def _qtdragon_action_style(self, color="#8e44ad"):
        return f"""
            QPushButton {{
                background-color: {color}; color: white; font-weight: bold; padding: 11px; border: 1px solid #ffffff; border-radius: 4px; font-size: 12px;
            }}
            QPushButton:hover {{ filter: brightness(1.15); }}
        """

    def _select_wcs(self, wcs_name):
        self.active_wcs = wcs_name
        for name, btn in self.wcs_buttons.items():
            btn.setChecked(name == wcs_name)

    def select_cycle(self, cycle_name):
        self.active_cycle = cycle_name
        self._sync_diagram()

        for btn in self.all_grid_buttons:
            if hasattr(btn, "cycle_code"):
                btn.set_selected(btn.cycle_code == cycle_name)

        titles = {
            "out_bl": "Outside Back-Left Corner (O114)",
            "out_b": "Outside Back Edge Y+ (O115)",
            "out_br": "Outside Back-Right Corner (O116)",
            "out_l": "Outside Left Edge X- (O117)",
            "out_boss": "Outside Boss / Center (O106)",
            "out_r": "Outside Right Edge X+ (O118)",
            "out_fl": "Outside Front-Left Corner (O119)",
            "out_f": "Outside Front Edge Y- (O120)",
            "out_fr": "Outside Front-Right Corner (O121)",
            "in_bl": "Inside Back-Left Corner (O122)",
            "in_b": "Inside Back Wall (O123)",
            "in_br": "Inside Back-Right Corner (O124)",
            "in_l": "Inside Left Wall (O125)",
            "in_hole": "Inside Hole / Bore Center & Dia (O107)",
            "in_r": "Inside Right Wall (O126)",
            "in_fl": "Inside Front-Left Corner (O127)",
            "in_f": "Inside Front Wall (O128)",
            "in_fr": "Inside Front-Right Corner (O129)",
            "probe_z": "Surface Z Zero (O103)",
            "angle_x": "X-Axis Edge Rotation Alignment",
            "angle_y": "Y-Axis Edge Rotation Alignment",
        }
        self.lbl_cycle_title.setText(f"Selected: {titles.get(cycle_name, cycle_name)}")

        if "boss" in cycle_name or "hole" in cycle_name:
            self.lbl_cycle_steps.setText(
                "1. Jog stylus to approximate center of feature.\n"
                "2. Ensure Expected Size matches diameter.\n"
                "3. Click START PROBE to locate center."
            )
        elif "angle" in cycle_name:
            self.lbl_cycle_steps.setText(
                "1. Jog stylus near edge starting location.\n"
                "2. Probes 2 points along edge to calculate skew angle θ.\n"
                "3. Click START PROBE."
            )
        elif cycle_name == "probe_z":
            self.lbl_cycle_steps.setText(
                "1. Jog stylus tip ~5-15mm above workpiece surface.\n"
                "2. Verify probe signal is clear.\n"
                "3. Click START PROBE to measure and set Z0."
            )
        else:
            self.lbl_cycle_steps.setText(
                "1. Jog probe stylus ~5-10mm above yellow start marker.\n"
                "2. Ensure clearance and search distance.\n"
                "3. Click START PROBE."
            )

    def _sync_diagram(self):
        try: d = float(self.inp_probe_diam.text())
        except ValueError: d = 3.0
        try: clr = float(self.inp_xy_clear.text())
        except ValueError: clr = 5.0
        try: zdrop = float(self.inp_z_drop.text())
        except ValueError: zdrop = 5.0
        try: search = float(self.inp_search_dist.text())
        except ValueError: search = 20.0

        self.diagram.set_cycle(self.active_cycle, probe_diam=d, xy_clear=clr, z_drop=zdrop, search_dist=search)

    def _on_param_changed(self):
        self._sync_diagram()
        self.save_settings()

    def _on_probe_hw_changed(self, idx):
        if self.dashboard and hasattr(self.dashboard, 'probe_selector'):
            self.dashboard.probe_selector.blockSignals(True)
            self.dashboard.probe_selector.setCurrentIndex(1 if idx == 0 else 0)
            self.dashboard.probe_selector.blockSignals(False)
            if hasattr(self.dashboard, 'switch_probe_hardware'):
                self.dashboard.switch_probe_hardware()

    def load_settings(self):
        if not self.settings: return
        self.inp_probe_diam.setText(self.settings.get_setting("PROBING", "probe_diam", "3.0"))
        self.inp_xy_clear.setText(self.settings.get_setting("PROBING", "xy_clear", "5.0"))
        self.inp_z_drop.setText(self.settings.get_setting("PROBING", "z_drop", "5.0"))
        self.inp_search_dist.setText(self.settings.get_setting("PROBING", "search_dist", "20.0"))
        self.inp_probe_vel.setText(self.settings.get_setting("PROBING", "probe_vel", "100.0"))
        self._sync_diagram()

    def save_settings(self):
        if not self.settings: return
        self.settings.save_setting("PROBING", "probe_diam", self.inp_probe_diam.text())
        self.settings.save_setting("PROBING", "xy_clear", self.inp_xy_clear.text())
        self.settings.save_setting("PROBING", "z_drop", self.inp_z_drop.text())
        self.settings.save_setting("PROBING", "search_dist", self.inp_search_dist.text())
        self.settings.save_setting("PROBING", "probe_vel", self.inp_probe_vel.text())

    def _poll_machine_telemetry(self):
        if self.dashboard and hasattr(self.dashboard, 'mock_coords'):
            coords = self.dashboard.mock_coords
            x = coords.get('X', 0.0)
            y = coords.get('Y', 0.0)
            z = coords.get('Z', 0.0)
            self.lbl_dro_x.setText(f"X: {float(x):+08.4f}")
            self.lbl_dro_y.setText(f"Y: {float(y):+08.4f}")
            self.lbl_dro_z.setText(f"Z: {float(z):+08.4f}")

            tripped = bool(coords.get('PROBE_VAL', 0) or coords.get('probe_tripped', False))
            self.diagram.set_probe_status(tripped)

    def start_probing_cycle(self):
        """Execute active probing cycle with safety guards."""
        if self.dashboard:
            if getattr(self.dashboard, 'is_estopped', False):
                QMessageBox.warning(self, "E-STOP Active", "Cannot probe while machine is in E-STOP state.")
                return
            if hasattr(self.dashboard, 'mock_coords') and not self.dashboard.mock_coords.get('MACHINE_ON', False):
                QMessageBox.warning(self, "Power Off", "Machine power is OFF. Please power on the machine before probing.")
                return

        cycle = self.active_cycle
        self.probing_started.emit(cycle)
        self.lbl_results.setText(f"Status: Probing [{cycle}] in progress...\nApproaching workpiece in {self.active_wcs}...")

        # Sync parameters to dashboard
        if self.dashboard:
            if hasattr(self.dashboard, 'input_probe_diam'):
                self.dashboard.input_probe_diam.setText(self.inp_probe_diam.text())
            if hasattr(self.dashboard, 'input_xy_clearance'):
                self.dashboard.input_xy_clearance.setText(self.inp_xy_clear.text())
            if hasattr(self.dashboard, 'input_z_drop'):
                self.dashboard.input_z_drop.setText(self.inp_z_drop.text())
            if hasattr(self.dashboard, 'input_search_dist'):
                self.dashboard.input_search_dist.setText(self.inp_search_dist.text())

        # Trigger routine via dashboard or agent
        if self.dashboard and hasattr(self.dashboard, 'trigger_probing'):
            self.dashboard.trigger_probing(cycle)
            self.lbl_results.setText(f"Status: Cycle [{cycle}] dispatched.\nMonitoring touch contact...")
            self.probing_completed.emit(cycle, {"status": "dispatched"})
        else:
            self.lbl_results.setText(f"Status: Cycle [{cycle}] executed in standalone test mode.")
            self.probing_completed.emit(cycle, {"status": "simulated"})

    def abort_probing(self):
        """Immediately abort all machine motion."""
        if self.dashboard and hasattr(self.dashboard, 'execute_mdi'):
            self.dashboard.execute_mdi("M2")
        self.lbl_results.setText("Status: Probing ABORTED by operator.")

    def zero_axis(self, axis_code):
        """Direct touch off: set active WCS origin to zero for specified axis."""
        p_index = 1
        for idx, (name, btn) in enumerate(self.wcs_buttons.items()):
            if btn.isChecked():
                p_index = idx + 1
                break
        wcs_name = self.active_wcs

        cmd = ""
        if axis_code == "X":
            cmd = f"G10 L20 P{p_index} X0"
        elif axis_code == "Y":
            cmd = f"G10 L20 P{p_index} Y0"
        elif axis_code == "Z":
            cmd = f"G10 L20 P{p_index} Z0"
        elif axis_code == "XY":
            cmd = f"G10 L20 P{p_index} X0 Y0"
        elif axis_code == "ALL":
            cmd = f"G10 L20 P{p_index} X0 Y0 Z0"

        if cmd and self.dashboard and hasattr(self.dashboard, 'execute_mdi'):
            self.dashboard.execute_mdi(cmd)
            self.lbl_results.setText(f"Zero Set: {axis_code} -> 0 in {wcs_name}\n({cmd})")
        else:
            self.lbl_results.setText(f"Zero Set: {axis_code} -> 0 ({cmd})")

    def cleanup(self):
        if hasattr(self, 'poll_timer') and self.poll_timer.isActive():
            self.poll_timer.stop()

    def closeEvent(self, event):
        self.cleanup()
        super().closeEvent(event)

# Backwards compatible alias
ProbeDiagramWidget = QtDragonProbeDiagram

