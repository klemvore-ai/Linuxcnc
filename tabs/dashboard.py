from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, 
                             QLabel, QPushButton, QGroupBox, QSlider, QLineEdit, 
                             QSizePolicy, QScrollArea, QMessageBox, QStackedWidget, QFileDialog, QTextEdit, QInputDialog, QGraphicsDropShadowEffect, QTabWidget, QGraphicsView, QGraphicsScene, QComboBox, QGraphicsItem, QProgressBar, QAbstractButton, QApplication)
from PyQt6.QtCore import Qt, QTimer, QProcess, QThread, pyqtSignal, QRectF, QRect, QSize, QFileSystemWatcher, QPointF
from PyQt6.QtGui import QColor, QPainterPath, QPen, QPainter, QTextCursor, QTextFormat, QBrush, QFont, QSyntaxHighlighter, QTextCharFormat, QPolygonF
from core.linuxcnc_api import LocalLinuxCNCAPI
import platform
import threading
import subprocess
import base64
import json
import os
import shutil
import sys
import tempfile
import math
import queue
import time
import socket
import re
import collections
from core.machine_agent import client_from_settings

LOCAL_IPS = ['localhost', '127.0.0.1']
try:
    _out = subprocess.check_output("hostname -I", shell=True, text=True, stderr=subprocess.DEVNULL)
    LOCAL_IPS.extend(_out.split())
except:
    pass

def is_local_ip(ip):
    return ip in LOCAL_IPS


def has_local_linuxcnc():
    return platform.system() == "Linux" and shutil.which("linuxcnc") is not None


def is_local_linuxcnc_running():
    if not has_local_linuxcnc():
        return False
    try:
        result = subprocess.run(
            ["pgrep", "-x", "linuxcnc"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    except OSError:
        return False
    return result.returncode == 0


def is_local_controller(ip):
    return is_local_ip(ip) or (not ip and has_local_linuxcnc())


def linuxcnc_status_snapshot(linuxcnc_module, status, error_channel):
    status.poll()
    error_messages = []
    while True:
        error = error_channel.poll()
        if not error:
            break
        error_messages.append(str(error[1]))

    joints = int(getattr(status, "joints", getattr(status, "axes", 3)))
    z_joint = 3 if joints >= 4 else 2
    actual_position = getattr(status, "actual_position", ())

    def work_position(axis_index, joint_index):
        try:
            if status.homed[joint_index]:
                offsets = (
                    getattr(status, "g5x_offset", (0.0,) * 9)[axis_index]
                    + getattr(status, "g92_offset", (0.0,) * 9)[axis_index]
                    + getattr(status, "tool_offset", (0.0,) * 9)[axis_index]
                )
                return actual_position[axis_index] - offsets
            return status.joint[joint_index]["input"]
        except (AttributeError, IndexError, KeyError, TypeError):
            return actual_position[axis_index] if len(actual_position) > axis_index else 0.0

    spindle_rpm = getattr(status, "spindle_speed", 0.0)
    try:
        spindle = status.spindle[0]
        if isinstance(spindle, dict):
            spindle_rpm = spindle.get("commanded_speed", spindle.get("speed", spindle_rpm))
    except (AttributeError, IndexError, TypeError):
        pass

    interp_state = getattr(status, "interp_state", 1)
    if interp_state == getattr(linuxcnc_module, "INTERP_IDLE", 1):
        machine_status = "IDLE"
        is_paused = False
    elif (
        interp_state == getattr(linuxcnc_module, "INTERP_PAUSED", 2)
        or getattr(status, "paused", False)
        or getattr(status, "task_paused", False)
    ):
        machine_status = "PAUSED"
        is_paused = True
    else:
        machine_status = "RUNNING"
        is_paused = False

    task_mode = getattr(status, "task_mode", 0)
    if task_mode == linuxcnc_module.MODE_MANUAL:
        mode = "MANUAL"
    elif task_mode == linuxcnc_module.MODE_AUTO:
        mode = "AUTO"
    elif task_mode == linuxcnc_module.MODE_MDI:
        mode = "MDI"
    else:
        mode = "UNKNOWN"

    snapshot = {
        "X": work_position(0, 0),
        "Y": work_position(1, 1),
        "Z": work_position(2, z_joint),
        "FEED": getattr(status, "current_vel", 0.0) * 60.0,
        "RPM": abs(float(spindle_rpm)),
        "FEED_OVR": getattr(status, "feedrate", 1.0),
        "SPINDLE_OVR": getattr(status, "spindle_scale", 1.0),
        "HOMED_COUNT": sum(1 for homed in getattr(status, "homed", ()) if homed),
        "JOINTS": joints,
        "MACHINE_ON": status.task_state == linuxcnc_module.STATE_ON,
        "ESTOP_ACTIVATED": status.task_state == linuxcnc_module.STATE_ESTOP,
        "STATUS": machine_status,
        "IS_PAUSED": is_paused,
        "MODE": mode,
        "LINE": getattr(status, "motion_line", 0),
        "AXIS_MASK": int(getattr(status, "axis_mask", 7)),
        "UNITS": "inch" if status.linear_units == 1.0 else "mm",
        "TOOL_IN_SPINDLE": int(getattr(status, "tool_in_spindle", 0)),
        "POCKET_PREPPED": int(getattr(status, "pocket_prepped", -1)),
        "TASK_BLOCKED": getattr(status, "exec_state", 2) not in (1, 2),
        "PROBE_VAL": getattr(status, "probe_val", 0),
        "probe_tripped": bool(getattr(status, "probe_tripped", False)),
    }
    dtg = getattr(status, "dtg", ())
    for axis_index, axis in enumerate(("X", "Y", "Z")):
        snapshot[f"ABSOLUTE_{axis}"] = actual_position[axis_index] if len(actual_position) > axis_index else 0.0
        snapshot[f"DTG_{axis}"] = dtg[axis_index] if len(dtg) > axis_index else 0.0
    if error_messages:
        snapshot["ERROR"] = "\n".join(error_messages)
    active_limits = []
    for joint_index, joint in enumerate(getattr(status, "joint", ())):
        if joint.get("min_hard_limit"):
            active_limits.append(f"joint {joint_index} negative")
        if joint.get("max_hard_limit"):
            active_limits.append(f"joint {joint_index} positive")
    snapshot["ACTIVE_LIMITS"] = active_limits
    snapshot["LIMITS_ACTIVE"] = bool(active_limits)
    return snapshot


class MDILineEdit(QLineEdit):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.history = []
        self.history_idx = 0

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Up:
            if self.history:
                self.history_idx = max(0, self.history_idx - 1)
                self.setText(self.history[self.history_idx])
        elif event.key() == Qt.Key.Key_Down:
            if self.history:
                if self.history_idx < len(self.history) - 1:
                    self.history_idx += 1
                    self.setText(self.history[self.history_idx])
                else:
                    self.history_idx = len(self.history)
                    self.clear()
        else:
            super().keyPressEvent(event)

    def add_to_history(self, cmd):
        if cmd and (not self.history or self.history[-1] != cmd):
            self.history.append(cmd)
        self.history_idx = len(self.history)

from PyQt6.QtWidgets import QPlainTextEdit

class LineNumberArea(QWidget):
    def __init__(self, editor):
        super().__init__(editor)
        self.codeEditor = editor

    def sizeHint(self):
        return QSize(self.codeEditor.lineNumberAreaWidth(), 0)

    def paintEvent(self, event):
        self.codeEditor.lineNumberAreaPaintEvent(event)

class GCodeHighlighter(QSyntaxHighlighter):
    def __init__(self, document):
        super().__init__(document)
        self.rules = []

        # G Codes (Blue)
        g_fmt = QTextCharFormat()
        g_fmt.setForeground(QColor("#3498db"))
        g_fmt.setFontWeight(QFont.Weight.Bold)
        self.rules.append((re.compile(r"[Gg]\s*\d+(\.\d+)?"), g_fmt))

        # M Codes (Red)
        m_fmt = QTextCharFormat()
        m_fmt.setForeground(QColor("#e74c3c"))
        m_fmt.setFontWeight(QFont.Weight.Bold)
        self.rules.append((re.compile(r"[Mm]\s*\d+(\.\d+)?"), m_fmt))

        # Coordinates (Green)
        coord_fmt = QTextCharFormat()
        coord_fmt.setForeground(QColor("#2ecc71"))
        self.rules.append((re.compile(r"[XxYyZzIiJjKkAabBcC]\s*-?\d+(\.\d+)?"), coord_fmt))

        # Feed & Speed (Yellow)
        fs_fmt = QTextCharFormat()
        fs_fmt.setForeground(QColor("#f1c40f"))
        self.rules.append((re.compile(r"[FfSs]\s*\d+(\.\d+)?"), fs_fmt))

        # Other parameters: Subroutines, Tools, Offsets (Purple)
        other_fmt = QTextCharFormat()
        other_fmt.setForeground(QColor("#9b59b6"))
        self.rules.append((re.compile(r"[OoTtHhDdQqPp]\s*-?\d+(\.\d+)?"), other_fmt))

        # Comments (Dimmed Gray, Italic) - Appended last so it overrides other formatting
        comment_fmt = QTextCharFormat()
        comment_fmt.setForeground(QColor("#7f8c8d"))
        comment_fmt.setFontItalic(True)
        self.rules.append((re.compile(r";.*"), comment_fmt))
        self.rules.append((re.compile(r"\(.*?\)"), comment_fmt))

    def highlightBlock(self, text):
        for pattern, fmt in self.rules:
            for match in pattern.finditer(text):
                self.setFormat(match.start(), match.end() - match.start(), fmt)

class GCodeEditor(QPlainTextEdit):
    line_double_clicked = pyqtSignal(int)
    def __init__(self, parent=None):
        super().__init__(parent)
        self.lineNumberArea = LineNumberArea(self)
        self.blockCountChanged.connect(self.updateLineNumberAreaWidth)
        self.updateRequest.connect(self.updateLineNumberArea)
        self.updateLineNumberAreaWidth(0)
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.highlighter = GCodeHighlighter(self.document())

    def lineNumberAreaWidth(self):
        digits = 1
        max_b = max(1, self.blockCount())
        while max_b >= 10:
            max_b /= 10
            digits += 1
        space = 5 + self.fontMetrics().horizontalAdvance('9') * digits
        return space

    def updateLineNumberAreaWidth(self, _):
        self.setViewportMargins(self.lineNumberAreaWidth(), 0, 0, 0)

    def updateLineNumberArea(self, rect, dy):
        if dy:
            self.lineNumberArea.scroll(0, dy)
        else:
            self.lineNumberArea.update(0, rect.y(), self.lineNumberArea.width(), rect.height())
        if rect.contains(self.viewport().rect()):
            self.updateLineNumberAreaWidth(0)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        cr = self.contentsRect()
        self.lineNumberArea.setGeometry(QRect(cr.left(), cr.top(), self.lineNumberAreaWidth(), cr.height()))

    def lineNumberAreaPaintEvent(self, event):
        painter = QPainter(self.lineNumberArea)
        painter.fillRect(event.rect(), QColor("#1a1a1a"))
        
        block = self.firstVisibleBlock()
        blockNumber = block.blockNumber()
        top = int(self.blockBoundingGeometry(block).translated(self.contentOffset()).top())
        bottom = top + int(self.blockBoundingRect(block).height())
        
        painter.setPen(QColor("#777777"))
        font = self.font()
        painter.setFont(font)
        
        while block.isValid() and top <= event.rect().bottom():
            if block.isVisible() and bottom >= event.rect().top():
                number = str(blockNumber + 1)
                painter.drawText(0, top, self.lineNumberArea.width() - 3, self.fontMetrics().height(),
                                 Qt.AlignmentFlag.AlignRight, number)
            
            block = block.next()
            top = bottom
            bottom = top + int(self.blockBoundingRect(block).height())
            blockNumber += 1

    def mouseDoubleClickEvent(self, event):
        super().mouseDoubleClickEvent(event)
        cursor = self.cursorForPosition(event.pos())
        self.line_double_clicked.emit(cursor.blockNumber())

class _CompatItem:
    def __init__(self, line_idx=None, is_rapid=False):
        self._data = {0: line_idx}
        self.is_rapid = is_rapid
        self.pen = None
        self.z_value = 0
    def data(self, key):
        return self._data.get(key)
    def setData(self, key, val):
        self._data[key] = val
    def setPen(self, pen):
        self.pen = pen
    def setZValue(self, z):
        self.z_value = z

class _CompatScene:
    def __init__(self, viewer):
        self._viewer = viewer
        self._extra_items = []
        
    def items(self, *args, **kwargs):
        res = [v['item'] for v in self._viewer.segment_items.values() if isinstance(v, dict) and 'item' in v]
        if not res and self._viewer.segments:
            res = [1] * len(self._viewer.segments)
        return res + self._extra_items
        
    def addText(self, text):
        class _TextItem:
            def __init__(self, text):
                self.text = text
                self.color = None
                self.pos = (0, 0)
            def setDefaultTextColor(self, color): self.color = color
            def setPos(self, x, y): self.pos = (x, y)
        t = _TextItem(text)
        self._extra_items.append(t)
        return t
        
    def addEllipse(self, *args, **kwargs):
        class _EllipseItem:
            def __init__(self):
                self.z = 0
                self.pos = (0, 0)
                self.rect = (0, 0, 0, 0)
            def setZValue(self, z): self.z = z
            def setPos(self, x, y): self.pos = (x, y)
            def setRect(self, *rect): self.rect = rect
        item = _EllipseItem()
        self._extra_items.append(item)
        return item
        
    def removeItem(self, item):
        if item in self._extra_items:
            self._extra_items.remove(item)
            
    def clear(self):
        self._extra_items.clear()
        
    def itemsBoundingRect(self):
        return QRectF(0, 0, 100, 100)

class ToolpathViewer(QWidget):
    segment_double_clicked = pyqtSignal(int)
    MAX_PREVIEW_LINES = 100000
    MAX_PREVIEW_SEGMENTS = 25000

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet("background-color: #111111; border: 2px solid #444444; border-radius: 4px;")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.machine_units = "mm"
        self.display_units = "mm"
        self.preview_enabled = True
        self.last_gcode_text = ""
        self.last_file_path = None

        # 3D Camera State
        self.azimuth = -45.0
        self.elevation = 30.0
        self.zoom = 1.0
        self.pan_x = 0.0
        self.pan_y = 0.0
        self.target_center = [0.0, 0.0, 0.0]

        # Machine Limits
        self.machine_limits = {
            'min_x': -5.0, 'max_x': 1275.0,
            'min_y': -5.0, 'max_y': 1275.0,
            'min_z': -134.0, 'max_z': 5.0
        }
        self.tool_pos = (0.0, 0.0, 0.0)
        self.active_line = -1
        self.preview_note = None

        self.segments = []
        self.segment_items = {}

        self._drag_mode = None
        self._last_mouse_pos = None
        self._mouse_press_pos = None

        self.scene = _CompatScene(self)
        self.tool_marker = self.scene.addEllipse(-1, -1, 2, 2)

    def _update_tool_marker_size(self, bounds=None):
        pass

    def set_machine_limits(self, min_x, max_x, min_y, max_y, min_z, max_z):
        self.machine_limits = {
            'min_x': float(min_x), 'max_x': float(max_x),
            'min_y': float(min_y), 'max_y': float(max_y),
            'min_z': float(min_z), 'max_z': float(max_z)
        }
        self.update()

    def get_camera_basis(self):
        a = math.radians(self.azimuth)
        b = math.radians(self.elevation)
        cos_a, sin_a = math.cos(a), math.sin(a)
        cos_b, sin_b = math.cos(b), math.sin(b)
        rx = cos_a
        ry = sin_a
        rz = 0.0
        ux = -sin_a * sin_b
        uy = cos_a * sin_b
        uz = cos_b
        return (rx, ry, rz), (ux, uy, uz)

    def project_3d(self, x, y, z):
        (rx, ry, rz), (ux, uy, uz) = self.get_camera_basis()
        dx = x - self.target_center[0]
        dy = y - self.target_center[1]
        dz = z - self.target_center[2]
        sx = dx * rx + dy * ry + dz * rz
        sy = dx * ux + dy * uy + dz * uz
        w = max(100, self.width())
        h = max(100, self.height())
        px = w / 2.0 + self.pan_x + sx * self.zoom
        py = h / 2.0 + self.pan_y - sy * self.zoom
        return px, py

    def to_iso(self, px, py, pz):
        cos_a = math.cos(math.radians(30))
        sin_a = math.sin(math.radians(30))
        sx = (px - py) * cos_a
        sy = -(px + py) * sin_a - pz
        return sx, sy

    def drawForeground(self, painter=None, rect=None):
        pass

    def set_view_iso(self):
        self.azimuth = -45.0
        self.elevation = 30.0
        self.fit_view()

    def set_view_top(self):
        self.azimuth = 0.0
        self.elevation = 90.0
        self.fit_view()

    def set_view_front(self):
        self.azimuth = 0.0
        self.elevation = 0.0
        self.fit_view()

    def set_view_side(self):
        self.azimuth = 90.0
        self.elevation = 0.0
        self.fit_view()

    def fit_view(self):
        if self.segments:
            min_x = min(min(s['p0'][0], s['p1'][0]) for s in self.segments)
            max_x = max(max(s['p0'][0], s['p1'][0]) for s in self.segments)
            min_y = min(min(s['p0'][1], s['p1'][1]) for s in self.segments)
            max_y = max(max(s['p0'][1], s['p1'][1]) for s in self.segments)
            min_z = min(min(s['p0'][2], s['p1'][2]) for s in self.segments)
            max_z = max(max(s['p0'][2], s['p1'][2]) for s in self.segments)
        else:
            min_x, max_x = self.machine_limits['min_x'], self.machine_limits['max_x']
            min_y, max_y = self.machine_limits['min_y'], self.machine_limits['max_y']
            min_z, max_z = self.machine_limits['min_z'], self.machine_limits['max_z']

        self.target_center = [(min_x + max_x) / 2.0, (min_y + max_y) / 2.0, (min_z + max_z) / 2.0]
        self.pan_x = 0.0
        self.pan_y = 0.0

        corners = [
            (min_x, min_y, min_z), (max_x, min_y, min_z),
            (max_x, max_y, min_z), (min_x, max_y, min_z),
            (min_x, min_y, max_z), (max_x, min_y, max_z),
            (max_x, max_y, max_z), (min_x, max_y, max_z),
        ]
        (rx, ry, rz), (ux, uy, uz) = self.get_camera_basis()
        proj_x = [(c[0]-self.target_center[0])*rx + (c[1]-self.target_center[1])*ry + (c[2]-self.target_center[2])*rz for c in corners]
        proj_y = [(c[0]-self.target_center[0])*ux + (c[1]-self.target_center[1])*uy + (c[2]-self.target_center[2])*uz for c in corners]
        span_x = max(max(proj_x) - min(proj_x), 10.0)
        span_y = max(max(proj_y) - min(proj_y), 10.0)

        w = max(100, self.width())
        h = max(100, self.height())
        self.zoom = min((w * 0.8) / span_x, (h * 0.8) / span_y)
        self.update()

    def fitInView(self, rect=None, mode=None):
        self.fit_view()

    def scale(self, sx, sy=None):
        self.zoom *= sx
        self.update()

    def _clear_preview_scene(self):
        self.segments.clear()
        self.segment_items.clear()
        self.active_line = -1
        self.preview_note = None
        self.scene.clear()

    def set_preview_enabled(self, enabled: bool, gcode_text=None, file_path=None):
        self.preview_enabled = bool(enabled)
        if self.preview_enabled:
            target_path = file_path or getattr(self, 'last_file_path', None)
            text = gcode_text if gcode_text is not None else getattr(self, 'last_gcode_text', '')
            if target_path or text:
                self.update_path(text, file_path=target_path)
            else:
                self._clear_preview_scene()
                self.update()
        else:
            if gcode_text is not None:
                self.last_gcode_text = gcode_text
            if file_path is not None:
                self.last_file_path = file_path
            self._clear_preview_scene()
            self.scene.addText("3D Toolpath Preview is disabled.\nClick 'Enable Preview' to parse and display.")
            self.update()

    def update_path(self, gcode_text, file_path=None):
        self.last_gcode_text = gcode_text if gcode_text else ""
        self.last_file_path = file_path
        self._clear_preview_scene()

        if not getattr(self, 'preview_enabled', True):
            self.scene.addText("3D Toolpath Preview is disabled.\nClick 'Enable Preview' to parse and display.")
            self.update()
            return

        preview_lines = []
        if file_path and os.path.exists(file_path):
            try:
                file_size = os.path.getsize(file_path)
                if file_size > 2 * 1024 * 1024:
                    with open(file_path, 'rb') as bf:
                        line_count = sum(chunk.count(b'\n') for chunk in iter(lambda: bf.read(1024*1024), b''))
                    sample_step = max(1, line_count // self.MAX_PREVIEW_SEGMENTS)
                    with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                        for idx, line in enumerate(f):
                            if idx % sample_step == 0:
                                preview_lines.append((idx, line))
                    self.preview_note = f"Preview simplified: {line_count:,} lines sampled to {len(preview_lines):,} moves"
                    self.scene.addText(self.preview_note)
                else:
                    with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                        lines = f.readlines()
                    line_count = len(lines)
                    if line_count > self.MAX_PREVIEW_LINES:
                        sample_step = max(1, line_count // self.MAX_PREVIEW_SEGMENTS)
                        preview_lines = [(idx, line) for idx, line in enumerate(lines) if idx % sample_step == 0]
                        self.preview_note = f"Preview simplified: {line_count:,} lines sampled to {len(preview_lines):,} moves"
                        self.scene.addText(self.preview_note)
                    else:
                        preview_lines = list(enumerate(lines))
            except Exception:
                preview_lines = []
        elif gcode_text and str(gcode_text).strip():
            text = str(gcode_text)
            lines = text.splitlines()
            line_count = len(lines)
            if line_count > self.MAX_PREVIEW_LINES:
                sample_step = max(1, line_count // self.MAX_PREVIEW_SEGMENTS)
                preview_lines = [(idx, line) for idx, line in enumerate(lines) if idx % sample_step == 0]
                self.preview_note = f"Preview simplified: {line_count:,} lines sampled to {len(preview_lines):,} moves"
                self.scene.addText(self.preview_note)
            else:
                preview_lines = list(enumerate(lines))
        else:
            self.update()
            return

        cx, cy, cz = 0.0, 0.0, 0.0
        mode = 'G1'
        distance_mode = 'G90'
        plane = 'G17'
        unit_scale = 1.0

        for line_idx, raw_line in preview_lines:
            clean = raw_line.split(';')[0].split('(')[0].strip().upper()
            if not clean:
                continue

            tokens = re.findall(r'[A-Z][-+]?[^A-Z\s]*', clean)
            for t in tokens:
                if t in ('G0', 'G00'): mode = 'G0'
                elif t in ('G1', 'G01'): mode = 'G1'
                elif t in ('G2', 'G02'): mode = 'G2'
                elif t in ('G3', 'G03'): mode = 'G3'
                elif t in ('G90', 'G90.0'): distance_mode = 'G90'
                elif t in ('G91', 'G91.0'): distance_mode = 'G91'
                elif t in ('G17', 'G17.0'): plane = 'G17'
                elif t in ('G18', 'G18.0'): plane = 'G18'
                elif t in ('G19', 'G19.0'): plane = 'G19'
                elif t in ('G20', 'G20.0'): unit_scale = 25.4
                elif t in ('G21', 'G21.0'): unit_scale = 1.0

            mv = {}
            i, j, k, r_val = 0.0, 0.0, 0.0, None
            has_move = False
            for t in tokens:
                code = t[0]
                val_str = t[1:]
                try:
                    v = float(val_str)
                    if code == 'X': mv['X'] = v * unit_scale; has_move = True
                    elif code == 'Y': mv['Y'] = v * unit_scale; has_move = True
                    elif code == 'Z': mv['Z'] = v * unit_scale; has_move = True
                    elif code == 'I': i = v * unit_scale
                    elif code == 'J': j = v * unit_scale
                    elif code == 'K': k = v * unit_scale
                    elif code == 'R': r_val = v * unit_scale
                except ValueError:
                    pass

            if has_move:
                if distance_mode == 'G91':
                    nx = cx + mv.get('X', 0.0)
                    ny = cy + mv.get('Y', 0.0)
                    nz = cz + mv.get('Z', 0.0)
                else:
                    nx = mv.get('X', cx)
                    ny = mv.get('Y', cy)
                    nz = mv.get('Z', cz)

                is_rapid = (mode == 'G0')
                compat_item = _CompatItem(line_idx, is_rapid=is_rapid)

                if mode in ('G2', 'G3'):
                    arc_pts = self._interpolate_arc((cx, cy, cz), (nx, ny, nz), mode, plane, i, j, k, r_val)
                    p_curr = (cx, cy, cz)
                    for pt in arc_pts:
                        seg = {'type': mode, 'p0': p_curr, 'p1': pt, 'line_no': line_idx}
                        self.segments.append(seg)
                        p_curr = pt
                else:
                    seg = {'type': mode, 'p0': (cx, cy, cz), 'p1': (nx, ny, nz), 'line_no': line_idx}
                    self.segments.append(seg)

                self.segment_items[line_idx] = {'item': compat_item, 'is_rapid': is_rapid}
                cx, cy, cz = nx, ny, nz

        self.fit_view()

    def _interpolate_arc(self, p0, p1, mode, plane, i, j, k, r):
        x0, y0, z0 = p0
        x1, y1, z1 = p1
        if plane == 'G17':
            u0, v0, u1, v1 = x0, y0, x1, y1
            du, dv = i, j
        elif plane == 'G18':
            u0, v0, u1, v1 = z0, x0, z1, x1
            du, dv = k, i
        else:
            u0, v0, u1, v1 = y0, z0, y1, z1
            du, dv = j, k

        if r is not None and abs(r) > 1e-6:
            dx = (u1 - u0) / 2.0
            dy = (v1 - v0) / 2.0
            d_sq = dx*dx + dy*dy
            r_sq = r*r
            if d_sq > r_sq:
                cu, cv = (u0 + u1)/2.0, (v0 + v1)/2.0
            else:
                h = math.sqrt(max(0.0, r_sq - d_sq))
                d = math.sqrt(d_sq)
                sign = (1 if r > 0 else -1) if mode == 'G2' else (-1 if r > 0 else 1)
                cu = (u0 + u1)/2.0 - sign * h * dy / d
                cv = (v0 + v1)/2.0 + sign * h * dx / d
        else:
            cu, cv = u0 + du, v0 + dv

        ru0, rv0 = u0 - cu, v0 - cv
        ru1, rv1 = u1 - cu, v1 - cv
        radius = math.hypot(ru0, rv0)
        if radius < 1e-4:
            return [p1]

        start_ang = math.atan2(rv0, ru0)
        end_ang = math.atan2(rv1, ru1)
        sweep = end_ang - start_ang
        if mode == 'G2':
            if sweep >= 0: sweep -= 2 * math.pi
        else:
            if sweep <= 0: sweep += 2 * math.pi

        if abs(u0 - u1) < 1e-4 and abs(v0 - v1) < 1e-4:
            sweep = -2 * math.pi if mode == 'G2' else 2 * math.pi

        steps = max(3, min(72, int(abs(math.degrees(sweep)) / 5.0)))
        pts = []
        for s in range(1, steps + 1):
            t = s / steps
            ang = start_ang + sweep * t
            u = cu + radius * math.cos(ang)
            v = cv + radius * math.sin(ang)
            if plane == 'G17':
                z = z0 + (z1 - z0) * t
                pts.append((u, v, z))
            elif plane == 'G18':
                y = y0 + (y1 - y0) * t
                pts.append((v, y, u))
            else:
                x = x0 + (x1 - x0) * t
                pts.append((x, u, v))
        return pts

    def highlight_line(self, line_idx):
        if not hasattr(self, 'segment_items') or not getattr(self, 'preview_enabled', True):
            return
        if self.active_line in self.segment_items:
            data = self.segment_items[self.active_line]
            if 'item' in data and hasattr(data['item'], 'setPen'):
                pen = QPen(QColor(150, 150, 150, 100), 1, Qt.PenStyle.DashLine) if data.get('is_rapid') else QPen(QColor("#39FF14"), 1.5)
                pen.setCosmetic(True)
                data['item'].setPen(pen)
                data['item'].setZValue(0)

        self.active_line = int(line_idx)
        if line_idx in self.segment_items:
            data = self.segment_items[line_idx]
            if 'item' in data and hasattr(data['item'], 'setPen'):
                hl_pen = QPen(QColor("#ff00ff"), 3)
                hl_pen.setCosmetic(True)
                data['item'].setPen(hl_pen)
                data['item'].setZValue(1)
        self.update()

    def update_tool_position(self, x, y, z=0.0):
        scale = 1.0
        if getattr(self, 'display_units', 'mm') == "inch":
            scale = 25.4
        try:
            self.tool_pos = (float(x) * scale, float(y) * scale, float(z) * scale)
        except (ValueError, TypeError):
            return
        if hasattr(self, 'tool_marker') and hasattr(self.tool_marker, 'setPos'):
            self.tool_marker.setPos(self.tool_pos[0], self.tool_pos[1])
        self.update()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_mode = 'orbit'
            self._last_mouse_pos = event.position()
            self._mouse_press_pos = event.position()
        elif event.button() in (Qt.MouseButton.RightButton, Qt.MouseButton.MiddleButton):
            self._drag_mode = 'pan'
            self._last_mouse_pos = event.position()

    def mouseMoveEvent(self, event):
        if not self._drag_mode or not self._last_mouse_pos:
            return
        dx = event.position().x() - self._last_mouse_pos.x()
        dy = event.position().y() - self._last_mouse_pos.y()
        self._last_mouse_pos = event.position()

        if self._drag_mode == 'orbit':
            self.azimuth = (self.azimuth + dx * 0.5) % 360.0
            self.elevation = max(-89.9, min(89.9, self.elevation - dy * 0.5))
            self.update()
        elif self._drag_mode == 'pan':
            self.pan_x += dx
            self.pan_y += dy
            self.update()

    def mouseReleaseEvent(self, event):
        self._drag_mode = None

    def wheelEvent(self, event):
        if event.angleDelta().y() == 0:
            event.ignore()
            return
        factor = 1.15 if event.angleDelta().y() > 0 else 1.0 / 1.15
        mx = event.position().x()
        my = event.position().y()
        cx = self.width() / 2.0
        cy = self.height() / 2.0
        self.pan_x = mx - cx - (mx - cx - self.pan_x) * factor
        self.pan_y = my - cy - (my - cy - self.pan_y) * factor
        self.zoom = max(0.0001, min(10000.0, self.zoom * factor))
        self.update()
        event.accept()

    def mouseDoubleClickEvent(self, event):
        mx = event.position().x()
        my = event.position().y()
        best_dist = 12.0
        best_line = None

        for seg in self.segments:
            p0 = self.project_3d(*seg['p0'])
            p1 = self.project_3d(*seg['p1'])
            dx = p1[0] - p0[0]
            dy = p1[1] - p0[1]
            l2 = dx*dx + dy*dy
            if l2 < 1e-4:
                d = math.hypot(mx - p0[0], my - p0[1])
            else:
                t = max(0.0, min(1.0, ((mx - p0[0])*dx + (my - p0[1])*dy) / l2))
                px = p0[0] + t * dx
                py = p0[1] + t * dy
                d = math.hypot(mx - px, my - py)
            if d < best_dist:
                best_dist = d
                best_line = seg['line_no']

        if best_line is not None:
            self.segment_double_clicked.emit(best_line)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor("#111111"))

        if not self.preview_enabled:
            painter.setPen(QColor("#888888"))
            font = QFont("Arial", 11)
            painter.setFont(font)
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "3D Toolpath Preview is disabled.\nClick 'Enable Preview' to parse and display.")
            painter.end()
            return

        # 1. Machine limits wireframe box
        ml = self.machine_limits
        corners = [
            (ml['min_x'], ml['min_y'], ml['min_z']), (ml['max_x'], ml['min_y'], ml['min_z']),
            (ml['max_x'], ml['max_y'], ml['min_z']), (ml['min_x'], ml['max_y'], ml['min_z']),
            (ml['min_x'], ml['min_y'], ml['max_z']), (ml['max_x'], ml['min_y'], ml['max_z']),
            (ml['max_x'], ml['max_y'], ml['max_z']), (ml['min_x'], ml['max_y'], ml['max_z']),
        ]
        edges = [
            (0, 1), (1, 2), (2, 3), (3, 0),
            (4, 5), (5, 6), (6, 7), (7, 4),
            (0, 4), (1, 5), (2, 6), (3, 7),
        ]
        pen_box = QPen(QColor(52, 73, 94, 160), 1, Qt.PenStyle.DashLine)
        painter.setPen(pen_box)
        for i0, i1 in edges:
            p0 = self.project_3d(*corners[i0])
            p1 = self.project_3d(*corners[i1])
            painter.drawLine(QPointF(*p0), QPointF(*p1))

        # 2. Table grid on Z=0
        pen_grid = QPen(QColor(40, 50, 60, 90), 1, Qt.PenStyle.DotLine)
        painter.setPen(pen_grid)
        step = 200.0
        gx = ml['min_x']
        while gx <= ml['max_x']:
            p0 = self.project_3d(gx, ml['min_y'], 0.0)
            p1 = self.project_3d(gx, ml['max_y'], 0.0)
            painter.drawLine(QPointF(*p0), QPointF(*p1))
            gx += step
        gy = ml['min_y']
        while gy <= ml['max_y']:
            p0 = self.project_3d(ml['min_x'], gy, 0.0)
            p1 = self.project_3d(ml['max_x'], gy, 0.0)
            painter.drawLine(QPointF(*p0), QPointF(*p1))
            gy += step

        # 3. Origin marker (0,0,0)
        op = self.project_3d(0.0, 0.0, 0.0)
        ax_len = 25.0
        ox_pt = self.project_3d(ax_len, 0.0, 0.0)
        oy_pt = self.project_3d(0.0, ax_len, 0.0)
        oz_pt = self.project_3d(0.0, 0.0, ax_len)
        painter.setPen(QPen(QColor("#e74c3c"), 2))
        painter.drawLine(QPointF(*op), QPointF(*ox_pt))
        painter.setPen(QPen(QColor("#2ecc71"), 2))
        painter.drawLine(QPointF(*op), QPointF(*oy_pt))
        painter.setPen(QPen(QColor("#3498db"), 2))
        painter.drawLine(QPointF(*op), QPointF(*oz_pt))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(QColor(255, 255, 255, 220)))
        painter.drawEllipse(QPointF(*op), 3, 3)

        # 4. Toolpath segments
        pen_rapid = QPen(QColor(150, 150, 150, 90), 1, Qt.PenStyle.DashLine)
        pen_feed = QPen(QColor("#39FF14"), 1.5)
        pen_active = QPen(QColor("#FF00FF"), 3.0)

        for seg in self.segments:
            p0 = self.project_3d(*seg['p0'])
            p1 = self.project_3d(*seg['p1'])
            if seg['line_no'] == self.active_line:
                painter.setPen(pen_active)
            elif seg['type'] == 'G0':
                painter.setPen(pen_rapid)
            else:
                painter.setPen(pen_feed)
            painter.drawLine(QPointF(*p0), QPointF(*p1))

        # 5. Live Tool Cone
        tx, ty, tz = self.tool_pos
        tip = self.project_3d(tx, ty, tz)
        cone_h = 25.0
        cone_r = 6.0

        # Guide line to table
        base_pt = self.project_3d(tx, ty, 0.0)
        painter.setPen(QPen(QColor(243, 156, 18, 80), 1, Qt.PenStyle.DotLine))
        painter.drawLine(QPointF(*tip), QPointF(*base_pt))

        # 3D Cone
        poly = QPolygonF()
        poly.append(QPointF(*tip))
        for step_idx in range(13):
            th = (step_idx / 12.0) * 2.0 * math.pi
            bx = tx + cone_r * math.cos(th)
            by = ty + cone_r * math.sin(th)
            bz = tz + cone_h
            pt = self.project_3d(bx, by, bz)
            poly.append(QPointF(*pt))

        painter.setPen(QPen(QColor("#f39c12"), 1.5))
        painter.setBrush(QBrush(QColor(243, 156, 18, 170)))
        painter.drawPolygon(poly)

        # Tool tip dot
        painter.setPen(QPen(QColor("#ffffff"), 1))
        painter.setBrush(QBrush(QColor("#ff2222")))
        painter.drawEllipse(QPointF(*tip), 3.0, 3.0)

        # 6. Orientation Triad (bottom-left)
        ox, oy = 50.0, self.height() - 50.0
        triad_len = 35.0
        (rx, ry, rz), (ux, uy, uz) = self.get_camera_basis()
        xx, xy = ox + rx * triad_len, oy - ux * triad_len
        painter.setPen(QPen(QColor("#e74c3c"), 2.5))
        painter.drawLine(QPointF(ox, oy), QPointF(xx, xy))
        painter.drawText(QPointF(xx + 3, xy + 4), "X")
        yx, yy = ox + ry * triad_len, oy - uy * triad_len
        painter.setPen(QPen(QColor("#2ecc71"), 2.5))
        painter.drawLine(QPointF(ox, oy), QPointF(yx, yy))
        painter.drawText(QPointF(yx + 3, yy + 4), "Y")
        zx, zy = ox + rz * triad_len, oy - uz * triad_len
        painter.setPen(QPen(QColor("#3498db"), 2.5))
        painter.drawLine(QPointF(ox, oy), QPointF(zx, zy))
        painter.drawText(QPointF(zx + 3, zy + 4), "Z")
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(255, 255, 255, 200))
        painter.drawEllipse(QPointF(ox, oy), 3, 3)

        # 7. Status HUD Overlay
        painter.setPen(QColor("#aaaaaa"))
        font_hud = QFont("Monospace", 9)
        painter.setFont(font_hud)
        painter.drawText(12, 20, f"View: Azimuth {self.azimuth:.0f}° | Elevation {self.elevation:.0f}° | Zoom {self.zoom:.2f}x")
        if self.preview_note:
            painter.setPen(QColor("#ffb347"))
            painter.drawText(12, 36, self.preview_note)

        painter.setPen(QColor("#666666"))
        painter.drawText(self.width() - 280, self.height() - 15, "Left-drag: Orbit | Right-drag: Pan | Wheel: Zoom")
        painter.end()

class PositionFetcherThread(QThread):
    position_fetched = pyqtSignal(dict)
    error = pyqtSignal(str)

    def __init__(self, ip, user, password, agent_client=None):
        super().__init__()
        self.ip = ip
        self.user = user
        self.password = password
        self.agent_client = agent_client
        self.running = False
        
        # Streaming Payload: Stays alive remotely and squirts JSON updates at 20Hz!
        py_code = """import json, time, sys, signal
try:
    import linuxcnc
    c = linuxcnc.command()
    e = linuxcnc.error_channel()
except Exception:
    sys.exit(0)

s = None
last_ok = time.time()
last_file = None
total_lines = 0

def cleanup_and_exit(*args):
    sys.exit(0)

signal.signal(signal.SIGHUP, cleanup_and_exit)
signal.signal(signal.SIGTERM, cleanup_and_exit)
try:
    signal.signal(signal.SIGPIPE, signal.SIG_IGN)
except: pass

while True:
    try:
        if s is None:
            try:
                s = linuxcnc.stat()
            except:
                time.sleep(1)
                continue
                
        s.poll()
        out = {}
        
        err_list = []
        while True:
            err = e.poll()
            if not err: break
            err_list.append(str(err[1]))
        if err_list:
            out['ERROR'] = "\\n".join(err_list)
        
        current_file = getattr(s, 'file', None)
        if current_file != last_file:
            last_file = current_file
            total_lines = 0
            if current_file:
                try:
                    with open(current_file, 'r', encoding='utf-8', errors='ignore') as f:
                        total_lines = sum(1 for _ in f)
                except: pass
        out['TOTAL_LINES'] = total_lines
        
        joints = getattr(s, 'joints', getattr(s, 'axes', 3))
        is_gantry = (joints >= 4)
        z_joint = 3 if is_gantry else 2
        
        def get_pos(axis_idx, joint_idx):
            try:
                if s.homed[joint_idx]:
                    abs_pos = s.actual_position[axis_idx]
                    g5x = getattr(s, 'g5x_offset', (0.0,)*9)[axis_idx]
                    g92 = getattr(s, 'g92_offset', (0.0,)*9)[axis_idx]
                    tool = getattr(s, 'tool_offset', (0.0,)*9)[axis_idx]
                    return abs_pos - (g5x + g92 + tool)
                else:
                    return s.joint[joint_idx]['input']
            except Exception:
                try: return s.actual_position[axis_idx]
                except Exception: return 0.0

        out['X'] = get_pos(0, 0)
        out['Y'] = get_pos(1, 1)
        out['Z'] = get_pos(2, z_joint)
        
        out['FEED'] = getattr(s, 'current_vel', 0.0) * 60.0
        # This analog spindle has no encoder: display commanded RPM.
        out['RPM'] = 0.0
        try:
            if hasattr(s, 'spindle') and len(s.spindle) > 0 and isinstance(s.spindle[0], dict):
                commanded = s.spindle[0].get('commanded_speed')
                if commanded is None:
                    commanded = s.spindle[0].get('speed', 0.0)
                out['RPM'] = abs(float(commanded))
            else:
                out['RPM'] = abs(float(getattr(s, 'spindle_speed', 0.0)))
        except Exception:
            pass
        
        out['FEED_OVR'] = getattr(s, 'feedrate', 1.0)
        out['SPINDLE_OVR'] = getattr(s, 'spindle_scale', 1.0)
        
        out['HOMED_COUNT'] = sum(1 for h in getattr(s, 'homed', []) if h)
        out['MACHINE_ON'] = (s.task_state == linuxcnc.STATE_ON)
        out['ESTOP_ACTIVATED'] = (s.task_state == linuxcnc.STATE_ESTOP)
        
        istate = getattr(s, 'interp_state', 1)
        if istate == linuxcnc.INTERP_IDLE: out['STATUS'] = 'IDLE'
        elif istate == linuxcnc.INTERP_PAUSED: out['STATUS'] = 'PAUSED'
        else: out['STATUS'] = 'RUNNING'
        
        mode = getattr(s, 'task_mode', 0)
        if mode == linuxcnc.MODE_MANUAL: out['MODE'] = 'MANUAL'
        elif mode == linuxcnc.MODE_AUTO: out['MODE'] = 'AUTO'
        elif mode == linuxcnc.MODE_MDI: out['MODE'] = 'MDI'
        else: out['MODE'] = 'UNKNOWN'
        
        out['LINE'] = getattr(s, 'motion_line', 0)
        out['JOINTS'] = int(getattr(s, 'joints', getattr(s, 'axes', 3)))
        out['AXIS_MASK'] = int(getattr(s, 'axis_mask', 7))
        out['UNITS'] = "inch" if s.linear_units == 1.0 else "mm"
        out['TOOL_IN_SPINDLE'] = int(getattr(s, 'tool_in_spindle', 0))
        out['POCKET_PREPPED'] = int(getattr(s, 'pocket_prepped', -1))
        out['TASK_BLOCKED'] = getattr(s, 'exec_state', 2) not in (1, 2)
        out['PROBE_VAL'] = getattr(s, 'probe_val', 0)
        out['probe_tripped'] = bool(getattr(s, 'probe_tripped', False))
        
        try:
            out['ABSOLUTE_X'] = s.actual_position[0]
            out['ABSOLUTE_Y'] = s.actual_position[1]
            out['ABSOLUTE_Z'] = s.actual_position[2]
            out['DTG_X'] = s.dtg[0]
            out['DTG_Y'] = s.dtg[1]
            out['DTG_Z'] = s.dtg[2]
        except Exception:
            out['ABSOLUTE_X'] = 0.0; out['ABSOLUTE_Y'] = 0.0; out['ABSOLUTE_Z'] = 0.0
            out['DTG_X'] = 0.0; out['DTG_Y'] = 0.0; out['DTG_Z'] = 0.0
        
        print(json.dumps(out))
        sys.stdout.flush()
        last_ok = time.time()
    except Exception:
        if time.time() - last_ok > 5.0:
            cleanup_and_exit()
    time.sleep(0.1)"""
        b64 = base64.b64encode(py_code.encode()).decode()
        self.cmd = f"python3 -u -c \"import base64; exec(base64.b64decode('{b64}').decode())\""

    def run(self):
        if self.agent_client is not None:
            self.running = True
            while self.running:
                try:
                    self.position_fetched.emit(self.agent_client.telemetry())
                except Exception as error:
                    if self.running:
                        self.error.emit(f"Machine-agent telemetry failed: {error}")
                    self.msleep(1000)
                    continue
                self.msleep(100)
            return

        if is_local_controller(self.ip):
            self.running = True
            if sys.platform.startswith("linux"):
                system_site_packages = "/usr/lib/python3/dist-packages"
                if os.path.isdir(system_site_packages) and system_site_packages not in sys.path:
                    sys.path.append(system_site_packages)
            try:
                import linuxcnc
                status = linuxcnc.stat()
                error_channel = linuxcnc.error_channel()
            except Exception as error:
                self.error.emit(f"Local LinuxCNC API unavailable: {error}")
                self.running = False
                return

            while self.running:
                try:
                    if status is None:
                        status = linuxcnc.stat()
                    if error_channel is None:
                        error_channel = linuxcnc.error_channel()
                    self.position_fetched.emit(
                        linuxcnc_status_snapshot(linuxcnc, status, error_channel)
                    )
                except Exception as error:
                    status = None
                    error_channel = None
                    if self.running:
                        self.error.emit(f"Local LinuxCNC status failed: {error}")
                    self.msleep(1000)
                    continue
                self.msleep(100)
            return

        if not self.ip or not self.user:
            self.error.emit("Remote credentials are required for live monitoring.")
            return
            
        self.running = True
        while self.running:
            try:
                proc = subprocess.Popen(
                    ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", f"{self.user}@{self.ip}", self.cmd],
                    stdout=subprocess.PIPE,
                    text=True,
                )
                self.proc = proc
                while self.running and proc.poll() is None:
                    try:
                        line = proc.stdout.readline()
                        if not line: break
                        line = line.strip()
                        if line.startswith('{') and line.endswith('}'):
                            coords = json.loads(line)
                            self.position_fetched.emit(coords)
                    except json.JSONDecodeError:
                        continue
                    except Exception as e:
                        if self.running:
                            self.error.emit(f"Position fetcher error: {e}")
                        break
                proc.terminate()
            except Exception as e:
                if self.running:
                    self.error.emit(f"SSH connection failed: {e}")
            
            # Reconnect loop
            for _ in range(20):
                if not self.running: break
                self.msleep(100)

    def stop(self):
        self.running = False
        if hasattr(self, 'proc') and self.proc and self.proc.poll() is None:
            try: self.proc.terminate()
            except: pass


class AgentCapabilityFetcherThread(QThread):
    """Fetch supported agent capabilities without blocking Dashboard startup."""

    capabilities_fetched = pyqtSignal(list)

    def __init__(self, agent_client):
        super().__init__()
        self.agent_client = agent_client

    def run(self):
        try:
            status = self.agent_client.agent_status()
            capabilities = status.get("capabilities", [])
            if isinstance(capabilities, list) and all(isinstance(item, str) for item in capabilities):
                self.capabilities_fetched.emit(capabilities)
        except Exception:
            return

class CalibrationWorker(QThread):
    """
    Runs the remote tool setter calibration script in a background thread
    to avoid freezing the UI, and returns the result via a signal.
    """
    calibration_complete = pyqtSignal(str)
    calibration_failed = pyqtSignal(str)

    def __init__(self, dashboard_instance, py_code):
        super().__init__()
        self.dashboard = dashboard_instance
        self.py_code = py_code

    def run(self):
        ip, user, password = self.dashboard.get_ssh_creds()
        b64_code = base64.b64encode(self.py_code.encode()).decode()
        cmd = f"python3 -u -c \"import base64; exec(base64.b64decode('{b64_code}').decode())\""

        if is_local_controller(ip):
            try:
                proc = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=300)
                result = proc.stdout.strip()
                error = proc.stderr.strip()
                if "ERROR" in result or (error and "Traceback" not in error): self.calibration_failed.emit(result or error)
                elif "USER_CANCEL" in result: pass
                else: self.calibration_complete.emit(result)
            except Exception as e:
                self.calibration_failed.emit(str(e))
            return

        if not ip or not user:
            self.calibration_failed.emit("SSH credentials are not configured.")
            return

        try:
            res = subprocess.run(
                ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", f"{user}@{ip}", cmd],
                capture_output=True,
                text=True,
                timeout=300,
            )
            result = res.stdout.strip()
            error = res.stderr.strip()
            if "ERROR" in result or (error and "Traceback" not in error): self.calibration_failed.emit(result or error)
            elif "USER_CANCEL" in result: pass
            else: self.calibration_complete.emit(result)
        except Exception as e:
            self.calibration_failed.emit(str(e))


class AgentCalibrationWorker(QThread):
    """Run tool-setter calibration through the authenticated machine agent."""
    calibration_complete = pyqtSignal(str)
    calibration_failed = pyqtSignal(str)

    def __init__(self, agent_client, x, y, velocity, search_distance):
        super().__init__()
        self.agent_client = agent_client
        self.x = x
        self.y = y
        self.velocity = velocity
        self.search_distance = search_distance

    def run(self):
        try:
            result = self.agent_client.calibrate_tool_setter(
                self.x, self.y, self.velocity, self.search_distance
            )
            self.calibration_complete.emit(str(result["setter_height"]))
        except Exception as error:
            self.calibration_failed.emit(str(error))


class AgentHomeAllWorker(QThread):
    """Home the configured XYYZ joints through the named agent API."""
    completed = pyqtSignal()
    failed = pyqtSignal(str)
    progress = pyqtSignal(str)

    def __init__(self, agent_client):
        super().__init__()
        self.agent_client = agent_client

    @staticmethod
    def _required_joint_pins(axis):
        return {
            "X": ["joint.0.homed"],
            "Y": ["joint.1.homed", "joint.2.homed"],
            "Z": ["joint.3.homed"],
        }[axis]

    @staticmethod
    def _truthy_hal(value):
        return str(value).strip().upper() in {"1", "TRUE"}

    def _wait_for_axis_homed(self, axis, timeout=120.0):
        deadline = time.monotonic() + timeout
        pins = self._required_joint_pins(axis)
        while time.monotonic() < deadline:
            hal_values = self.agent_client.read_hal_values(pins).get("values", {})
            joints_homed = all(self._truthy_hal(hal_values.get(pin, "FALSE")) for pin in pins)
            if joints_homed:
                return
            self.msleep(500)
        raise RuntimeError(f"{axis} did not report homed before timeout.")

    def run(self):
        try:
            for axis in ("Z", "X", "Y"):
                self.progress.emit(f"Homing {axis}...")
                self.agent_client.home_axis(axis, timeout=130.0)
                self._wait_for_axis_homed(axis)
                self.progress.emit(f"Homing {axis} complete")
            self.completed.emit()
        except Exception as error:
            self.failed.emit(str(error))


class AgentUnhomeAllWorker(QThread):
    """Clear all homed joints through the authenticated machine agent."""
    completed = pyqtSignal()
    failed = pyqtSignal(str)

    def __init__(self, agent_client):
        super().__init__()
        self.agent_client = agent_client

    @staticmethod
    def _truthy_hal(value):
        return str(value).strip().upper() in {"1", "TRUE"}

    def _wait_for_all_unhomed(self, timeout=10.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            telemetry = self.agent_client.telemetry()
            if int(telemetry.get("HOMED_COUNT", 0)) == 0:
                return
            self.msleep(500)
        raise RuntimeError("LinuxCNC did not report all joints unhomed before timeout.")

    def run(self):
        try:
            self.agent_client.machine_command("unhome_all", timeout=10.0)
            self._wait_for_all_unhomed()
            self.completed.emit()
        except Exception as error:
            self.failed.emit(str(error))

class MachineDashboard(QWidget):
    error_signal = pyqtSignal(str)
    program_sync_state = pyqtSignal(bool)
    LARGE_FILE_THRESHOLD = 2 * 1024 * 1024

    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.error_signal.connect(self.show_error_message)
        self.program_sync_state.connect(self._set_program_sync_state)
        self.settings = settings
        self.local_api = LocalLinuxCNCAPI() if getattr(settings, "local_only", False) else None
        self.home_buttons = {}
        self.dro_labels = {}
        self.custom_dro_labels = {}
        self.custom_status_labels = {}
        self.custom_home_all_buttons = []
        self.jog_buttons = {}
        self.status_leds = {}
        self.display_units = "mm"
        self.mock_coords = {'X': 0.0, 'Y': 0.0, 'Z': 0.0, 'FEED': 0.0, 'RPM': 0.0}
        self.dro_mode = 'WORK'
        self.dro_mode_buttons = {}
        self.is_estopped = True
        self.machine_state = "ESTOP"
        self.active_jogs = set()
        self._last_update_state = {}
        self._program_sync_in_progress = False
        self._is_large_file_preview = False
        self.loaded_gcode_path = None
        self._tool_change_prompt_active = False
        self._tool_change_last_pocket = -1
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        
        self.ssh_connected = False
        self.ping_process = QProcess(self)
        self.ping_process.finished.connect(self.handle_ping_result)
        self.ping_timer = QTimer(self)
        self.ping_timer.timeout.connect(self.send_ping)
        
        self.pos_fetcher = None
        self.capability_fetcher = None

        self.ui_file_watcher = QFileSystemWatcher(self)
        self.ui_file_watcher.fileChanged.connect(self.on_ui_file_changed)
        self.current_ui_file = None

        self.ssh_client = None
        self.cmd_queue = queue.Queue()
        self.cmd_thread = threading.Thread(target=self._cmd_worker, daemon=True)
        self.cmd_thread.start()

        self.init_ui()
        QTimer.singleShot(0, self.refresh_agent_capabilities)
        QTimer.singleShot(0, self.refresh_macro_list)
        self.init_heartbeat()
        self.settings.reload()
        if client_from_settings(self.settings) is not None:
            self.start_position_fetcher()
        else:
            self.ping_timer.start(3000)  # Ping in the background every 3 seconds
            self.send_ping()
        self.setFocus()  # Ensure focus on startup

    def show_error_message(self, message):
        QMessageBox.warning(self, "LinuxCNC Command Error", f"The machine command failed with the following error:\n\n{message}")

    def refresh_agent_capabilities(self):
        """Discover optional secure controls after the Dashboard has rendered."""
        agent_client = client_from_settings(self.settings)
        if agent_client is None:
            return
        self.capability_fetcher = AgentCapabilityFetcherThread(agent_client)
        self.capability_fetcher.capabilities_fetched.connect(self.apply_agent_capabilities)
        self.capability_fetcher.start()

    def refresh_macro_list(self):
        """Populate the secure subroutine picker from files installed on the machine."""
        agent_client = client_from_settings(self.settings)
        if agent_client is None:
            return
        try:
            macros = agent_client.list_macros().get("macros", [])
        except Exception as error:
            self.combo_ocodes.clear()
            self.combo_ocodes.addItem("No managed macros found")
            self.btn_run_ocode.setEnabled(False)
            self.btn_run_ocode.setToolTip(f"Unable to list secure macros: {error}")
            return
        current = self.combo_ocodes.currentData()
        self.combo_ocodes.clear()
        labels = {
            "O100": "Warmup",
            "O101": "Tool Measure",
            "O102": "Park",
            "O103": "Touch Plate Z",
            "O104": "Outside Corner",
            "O105": "Inside Corner",
            "O106": "Boss Center",
            "O107": "Hole Center",
            "O108": "Probe X Edge",
            "O109": "Probe Y Edge",
            "O110": "Auto Tool Change",
            "O111": "Calibrate Setter",
            "O112": "Calibrate Spindle Nose",
            "O113": "Quick Tool Change",
            "O114": "Outside Back-Left Corner",
            "O115": "Outside Back Edge",
            "O116": "Outside Back-Right Corner",
            "O117": "Outside Left Edge",
            "O118": "Outside Right Edge",
            "O119": "Outside Front-Left Corner",
            "O120": "Outside Front Edge",
            "O121": "Outside Front-Right Corner",
            "O122": "Inside Back-Left Corner",
            "O123": "Inside Back Edge",
            "O124": "Inside Back-Right Corner",
            "O125": "Inside Left Edge",
            "O126": "Inside Right Edge",
            "O127": "Inside Front-Left Corner",
            "O128": "Inside Front Edge",
            "O129": "Inside Front-Right Corner",
        }
        for macro in macros:
            ocode = macro.get("ocode")
            filename = macro.get("filename")
            if ocode and filename:
                self.combo_ocodes.addItem(f"{ocode} ({labels.get(ocode, filename)})", ocode)
        if self.combo_ocodes.count() == 0:
            self.combo_ocodes.addItem("No managed macros found")
            self.btn_run_ocode.setEnabled(False)
            self.btn_run_ocode.setToolTip("Install O100.ngc, O101.ngc, or O102.ngc from the Macros page.")
            return
        index = self.combo_ocodes.findData(current)
        if index >= 0:
            self.combo_ocodes.setCurrentIndex(index)
        self.btn_run_ocode.setEnabled(True)
        self.btn_run_ocode.setToolTip("Run the selected managed O-code subroutine.")

    def apply_agent_capabilities(self, capabilities):
        """Disable typed controls unavailable on an older enrolled machine agent."""
        # A successful capability response proves the mTLS agent is reachable.
        # Do not let a transient telemetry/motion error flap this indicator.
        self.ssh_connected = True
        available = set(capabilities)
        capability_map = {
            "program_control": [self.btn_cycle_start, self.btn_cycle_pause],
            "mode_control": [self.btn_mode_manual, self.btn_mode_mdi, self.btn_mode_auto],
            "spindle_control": [self.btn_m3, self.btn_m4, self.btn_m5],
            "coolant_control": [self.btn_m7, self.btn_m8, self.btn_m9],
            "home_all": [self.btn_home_all],
            "tool_change": [self.btn_load_tool],
            "tool_setter_move": [self.btn_move_setter, self.btn_park],
            "raw_z_probe": [self.btn_raw_probe_z, self.input_raw_probe_distance],
            "touch_off_z": [self.btn_touch_off_z],
        }
        for capability, controls in capability_map.items():
            if capability in available:
                continue
            message = f"This machine agent does not support {capability.replace('_', ' ')}."
            for control in controls:
                control.setEnabled(False)
                control.setToolTip(message)

    def get_probe_plate_thickness(self):
        val = self.settings.get_setting('Calibration', 'probe_plate_thickness')
        try:
            return float(val) if val and str(val).strip() else 5.0
        except (ValueError, TypeError):
            return 5.0

    def update_jog_speed(self):
        if hasattr(self, 'lbl_jog_speed_val'):
            val = self.jog_speed_slider.value()
            self.lbl_jog_speed_val.setText(f"{val} %")
        
        # Add this line to give focus back to the main dashboard
        self.setFocus()

    def get_ssh_creds(self):
        ip = self.settings.get_setting("SSH", "ip_address", "")
        user = self.settings.get_setting("SSH", "username", "")
        password = self.settings.get_setting("SSH", "password", "")
        return ip, user, password

    def set_dro_mode(self, mode):
        self.dro_mode = mode
        self.update_status()

    def start_tool_setter_calibration(self, *args):
        reply = QMessageBox.question(self, "Tool Setter Calibration",
                                     "This will guide you through calibrating the tool setter height.\n\n"
                                     "You will need a reference tool of known length and a way to set your workpiece Z-zero (like a touch plate).\n\n"
                                     "Do you want to begin?",
                                     QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply != QMessageBox.StandardButton.Yes:
            return

        setter_x = float(self.settings.get_setting("PROBING", "tool_setter_x", 0.0))
        setter_y = float(self.settings.get_setting("PROBING", "tool_setter_y", 0.0))
        setter_vel = float(self.settings.get_setting("PROBING", "setter_vel", 150.0))
        setter_search_dist = float(self.settings.get_setting("PROBING", "setter_search_dist", 100.0))

        self.settings.reload()
        agent_client = client_from_settings(self.settings)
        if agent_client is None:
            configured_keys = {
                key: bool(self.settings.get_setting("AGENT", key, "").strip())
                for key in ("base_url", "ca_file", "client_cert", "client_key")
            }
            self.on_calibration_failed(
                "Secure machine-agent configuration is required for tool-setter calibration. "
                f"Configured fields: {configured_keys}"
            )
            return

        reply = QMessageBox.question(
            self,
            "Set Workpiece Zero",
            "Insert the reference tool and touch it off on the workpiece Z-zero surface.\n\n"
            "Click Yes when the workpiece zero is set.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        reply = QMessageBox.question(
            self,
            "Probe Tool Setter",
            "Confirm the spindle is clear and the tool setter is ready.\n\n"
            "Select 'Touch Plate / Setter' as the active probe input.\n"
            "The setter search can travel up to the configured setter search distance.\n\n"
            "Click Yes to begin the bounded calibration cycle.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        self.cal_worker = AgentCalibrationWorker(
            agent_client, setter_x, setter_y, setter_vel, setter_search_dist
        )
        self.cal_worker.calibration_complete.connect(self.on_calibration_complete)
        self.cal_worker.calibration_failed.connect(self.on_calibration_failed)
        self.cal_worker.start()
        return

        py_code = f"""
import linuxcnc, time, sys, os

def has_zenity():
    return os.system("which zenity > /dev/null") == 0

def prompt(msg_type, title, text):
    return os.system(f"export DISPLAY=:0; zenity --{{msg_type}} --title='{{title}}' --text='{{text}}' --width=400")

if not has_zenity():
    print("ERROR: Zenity is not installed. Please run 'sudo apt install zenity' on the LinuxCNC machine.")
    sys.exit(1)

if prompt('question', 'Step 1: Set Workpiece Zero', 'Please insert your reference tool (e.g., a gauge block of known height).\\n\\nManually jog and touch this tool off on your workpiece Z-zero surface (e.g., using a touch plate).\\n\\nClick YES when Z-zero is set correctly.') != 0:
    print("USER_CANCEL")
    sys.exit(0)

try:
    s = linuxcnc.stat(); c = linuxcnc.command(); s.poll()
    e = getattr(linuxcnc, 'error_channel', lambda: None)()
    def wait_idle(timeout=120):
        start_time = time.time()
        err = e.poll()
        if err:
            sys.stderr.write(str(err[1]))
            sys.exit(1)
        while time.time() - start_time < timeout:
            s.poll()
            if s.interp_state == linuxcnc.INTERP_IDLE: return
            err = e.poll()
            if err:
                sys.stderr.write(str(err[1]))
                sys.exit(1)
            if s.task_state == linuxcnc.STATE_ESTOP:
                sys.stderr.write("Machine is in E-STOP state.")
                sys.exit(1)
            time.sleep(0.05)
        sys.stderr.write("MDI command timed out.")
        sys.exit(1)
    def run_mdi(cmd):
        c.mdi(cmd); time.sleep(0.05); wait_idle()

    if prompt('question', 'Step 2: Probe Tool Setter', 'Move the spindle over your tool setter.\\n\\nClick YES to begin probing.') != 0:
        print("USER_CANCEL")
        sys.exit(0)

    if s.task_mode != linuxcnc.MODE_MDI: c.mode(linuxcnc.MODE_MDI); wait_idle()
    run_mdi("G90"); run_mdi("G53 G0 Z0"); run_mdi("G53 G0 X{setter_x} Y{setter_y}")
    
    s.poll()
    if getattr(s, 'probe_val', 0): run_mdi("G91"); run_mdi("G38.4 Z20.0 F{setter_vel}"); run_mdi("G90")
        
    z_min = -1000.0
    try: z_min = s.axis[2]['min_position_limit']
    except: pass
    
    z_offset = s.g5x_offset[2] + s.g92_offset[2] + s.tool_offset[2]
    z_target = z_min - z_offset + 0.1
    
    s.poll()
    current_z = s.position[2] - z_offset
    if z_target >= current_z:
        z_target = current_z - 200.0
        
    run_mdi(f"G38.2 Z{{z_target:.4f}} F{setter_vel * 4.0}")
    s.poll()
    if not s.probe_tripped:
        prompt('error', 'Probe Failed', 'The fast probe sequence failed to make contact.')
        sys.exit(1)
        
    run_mdi("G91"); run_mdi("G0 Z2.0"); run_mdi("G90")
    run_mdi(f"G38.2 Z{{z_target:.4f}} F{setter_vel}")
    s.poll()
    if not s.probe_tripped:
        prompt('error', 'Probe Failed', 'The slow probe sequence failed to make contact.')
        sys.exit(1)
        
    setter_height = s.probed_position[2]
    print(f"{{setter_height:.4f}}")
    run_mdi("G91"); run_mdi("G0 Z5.0"); run_mdi("G90")
    prompt('info', 'Calibration Complete!', f'Tool setter Reference Z calculated as: {{setter_height:.4f}}\\nThis value will be updated on your dashboard.')
except Exception as e:
    prompt('error', 'Error', f'An error occurred: {{e}}')
    sys.exit(1)
"""
        self.cal_worker = CalibrationWorker(self, py_code)
        self.cal_worker.calibration_complete.connect(self.on_calibration_complete)
        self.cal_worker.calibration_failed.connect(self.on_calibration_failed)
        self.cal_worker.start()

    def on_calibration_complete(self, result_str):
        try:
            val = float(result_str)
            self.input_tool_setter.setText(f"{val:.4f}")
            QMessageBox.information(
                self,
                "Tool Setter Calibration Complete",
                f"Setter reference Z measured as {val:.4f}.\n\n"
                "The value is saved in LinuxCNC settings. Use Settings -> "
                "Probing & Locations -> Sync Probing to Machine INI before "
                "running automatic tool probing.",
            )
        except ValueError:
            self.on_calibration_failed(f"Received invalid result from machine: {result_str}")

    def on_calibration_failed(self, error_str):
        QMessageBox.critical(self, "Calibration Failed", f"The tool setter calibration failed:\n\n{error_str}")

    def init_ui(self):
        # 1. KILL THE ZOMBIE LAYOUTS
        if self.layout() is not None:
            # We move the old layout to a 'dead' widget to clear the screen
            QWidget().setLayout(self.layout())

        self.main_layout = QVBoxLayout(self)
        
        # Stacked Widget to switch between Default and Custom UI
        self.stack = QStackedWidget()
        self.main_layout.addWidget(self.stack, 1)
        
        # Default UI Page
        self.default_ui_widget = QWidget()
        self.build_default_ui()
        self.stack.addWidget(self.default_ui_widget)
        
        # Custom UI Page
        self.custom_scroll = QScrollArea()
        self.custom_scroll.setWidgetResizable(True)
        self.custom_scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")
        
        self.custom_ui_widget = QWidget()
        self.custom_scroll.setWidget(self.custom_ui_widget)
        self.stack.addWidget(self.custom_scroll)

        # Fix for keyboard jogging: prevent buttons and sliders from stealing focus
        for control in self.findChildren((QAbstractButton, QSlider)):
            control.setFocusPolicy(Qt.FocusPolicy.NoFocus)

    def build_default_ui(self):
        # 2. DEFINE THE DICTIONARY IMMEDIATELY (Prevents KeyError)
        self.status_leds = {
            "CONNECTION": QLabel("MACHINE: OFFLINE"),
            "ESTOP": QLabel("ESTOP"),
            "POWER": QLabel("POWER"),
            "HOMED": QLabel("HOMED"),
            "READY": QLabel("READY")
        }

        master_layout = QVBoxLayout(self.default_ui_widget)

        # --- TOP ROW: LEDs ---
        led_layout = QHBoxLayout()
        for i, (name, label) in enumerate(self.status_leds.items()):
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            label.setStyleSheet("background-color: #555; color: white; padding: 8px; border-radius: 4px; font-weight: bold;")
            led_layout.addWidget(label)
        master_layout.addLayout(led_layout)
        master_layout.addSpacing(10)

        # --- MIDDLE ROW: G-Code & Preview ---
        gcode_preview_layout = QHBoxLayout()
        
        # G-Code Editor
        gcode_group = QGroupBox("G-Code Editor")
        gcode_group.setStyleSheet("QGroupBox { font-weight: bold; border: 1px solid #444; border-radius: 4px; margin-top: 10px; } QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 5px; }")
        gcode_vbox = QVBoxLayout()
        
        gcode_toolbar = QHBoxLayout()
        btn_load = QPushButton("📂 Load")
        btn_load.setStyleSheet("QPushButton { background-color: #2980b9; color: white; padding: 4px 10px; border-radius: 3px; font-weight: bold; } QPushButton:pressed { background-color: #1f618d; }")
        btn_merge = QPushButton("🔗 Merge")
        btn_merge.setStyleSheet("QPushButton { background-color: #34495e; color: white; padding: 4px 10px; border-radius: 3px; font-weight: bold; } QPushButton:pressed { background-color: #2c3e50; }")
        btn_save = QPushButton("💾 Save")
        btn_save.setStyleSheet("QPushButton { background-color: #27ae60; color: white; padding: 4px 10px; border-radius: 3px; font-weight: bold; } QPushButton:pressed { background-color: #229954; }")
        btn_sync = QPushButton("📤 Sync to Machine")
        btn_sync.setStyleSheet("QPushButton { background-color: #f39c12; color: black; padding: 4px 10px; border-radius: 3px; font-weight: bold; } QPushButton:pressed { background-color: #c87f0a; }")
        btn_clear = QPushButton("🗑️ Clear")
        btn_clear.setStyleSheet("QPushButton { background-color: #c0392b; color: white; padding: 4px 10px; border-radius: 3px; font-weight: bold; } QPushButton:pressed { background-color: #922b21; }")
        
        btn_optimize = QPushButton("✨ Fix Fusion Rapids")
        btn_optimize.setStyleSheet("QPushButton { background-color: #8e44ad; color: white; padding: 4px 10px; border-radius: 3px; font-weight: bold; } QPushButton:pressed { background-color: #732d91; }")
        
        btn_clean = QPushButton("🧹 Clean G-Code")
        btn_clean.setStyleSheet("QPushButton { background-color: #1abc9c; color: white; padding: 4px 10px; border-radius: 3px; font-weight: bold; } QPushButton:pressed { background-color: #16a085; }")
        
        gcode_toolbar.addWidget(btn_load)
        gcode_toolbar.addWidget(btn_merge)
        gcode_toolbar.addWidget(btn_save)
        gcode_toolbar.addWidget(btn_sync)
        gcode_toolbar.addWidget(btn_optimize)
        gcode_toolbar.addWidget(btn_clean)
        gcode_toolbar.addWidget(btn_clear)
        gcode_toolbar.addStretch()
        gcode_vbox.addLayout(gcode_toolbar)
        
        self.default_gcode_editor = GCodeEditor()
        self.default_gcode_editor.setPlaceholderText("(G-Code Program)\n\nLoad a file from LinuxCNC or paste G-Code here.")
        self.default_gcode_editor.setStyleSheet("background-color: #1e1e1e; color: #ecf0f1; font-family: 'Consolas', 'Courier New', monospace; border: 1px solid #555; border-radius: 4px;")
        gcode_vbox.addWidget(self.default_gcode_editor)
        
        mdi_layout = QHBoxLayout()
        self.mdi_input = MDILineEdit()
        self.mdi_input.setPlaceholderText("MDI Command (e.g. M3 S1000) + Enter")
        self.mdi_input.setStyleSheet("""
            QLineEdit {
                background-color: #111;
                color: #39FF14;
                font-family: 'Consolas', 'Courier New', monospace;
                font-size: 14px;
                border: 1px solid #555;
                border-radius: 3px;
                padding: 4px;
                selection-background-color: #39FF14;
                selection-color: #111;
            }
            QLineEdit:focus {
                border: 2px solid #39FF14;
            }
        """)
        
        self.btn_mdi_exec = QPushButton("Execute")
        self.btn_mdi_exec.setStyleSheet("QPushButton { background-color: #d35400; color: white; padding: 4px 10px; border-radius: 3px; font-weight: bold; } QPushButton:pressed { background-color: #a04000; }")
        mdi_layout.addWidget(self.mdi_input)
        mdi_layout.addWidget(self.btn_mdi_exec)
        gcode_vbox.addLayout(mdi_layout)
        
        btn_load.clicked.connect(lambda: self.load_gcode(self.default_gcode_editor))
        btn_merge.clicked.connect(lambda: self.merge_gcode_files(self.default_gcode_editor))
        btn_save.clicked.connect(lambda: self.save_gcode(self.default_gcode_editor))
        btn_sync.clicked.connect(lambda: self.sync_gcode_to_machine("" if self.__dict__.get('_is_large_file_preview', False) else self.default_gcode_editor.toPlainText()))
        btn_clear.clicked.connect(lambda: self.clear_gcode(self.default_gcode_editor))
        btn_clean.clicked.connect(self.clean_gcode)
        btn_optimize.clicked.connect(self.fix_fusion_rapids)
        self.default_gcode_editor.line_double_clicked.connect(self.run_from_line)
        
        self.btn_mdi_exec.clicked.connect(self.run_mdi_from_input)
        self.mdi_input.returnPressed.connect(self.run_mdi_from_input)
        if client_from_settings(self.settings) is not None:
            message = "Secure mode permits only named, approved machine operations."
            self.mdi_input.setEnabled(False)
            self.mdi_input.setToolTip(message)
            self.btn_mdi_exec.setEnabled(False)
            self.btn_mdi_exec.setToolTip(message)
        
        gcode_group.setLayout(gcode_vbox)
        gcode_preview_layout.addWidget(gcode_group, 1)
        
        # 3D Preview
        preview_group = QGroupBox("3D Toolpath Preview")
        preview_group.setStyleSheet("QGroupBox { font-weight: bold; border: 1px solid #444; border-radius: 4px; margin-top: 10px; } QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 5px; }")
        preview_vbox = QVBoxLayout()
        
        # View Preset Controls Toolbar
        view_toolbar = QHBoxLayout()
        view_toolbar.setSpacing(4)
        btn_style = (
            "QPushButton { background-color: #2b2b2b; color: #e0e0e0; border: 1px solid #555; "
            "border-radius: 3px; padding: 3px 8px; font-weight: bold; font-size: 11px; } "
            "QPushButton:hover { background-color: #3d3d3d; border-color: #00adb5; } "
            "QPushButton:pressed { background-color: #1a1a1a; }"
        )

        self.btn_view_iso = QPushButton("🔄 ISO")
        self.btn_view_iso.setStyleSheet(btn_style)
        self.btn_view_iso.setToolTip("Isometric View (Azimuth: -45°, Elevation: 30°)")
        view_toolbar.addWidget(self.btn_view_iso)

        self.btn_view_top = QPushButton("📐 TOP")
        self.btn_view_top.setStyleSheet(btn_style)
        self.btn_view_top.setToolTip("Top View (XY Plane)")
        view_toolbar.addWidget(self.btn_view_top)

        self.btn_view_front = QPushButton("⏹ FRONT")
        self.btn_view_front.setStyleSheet(btn_style)
        self.btn_view_front.setToolTip("Front View (XZ Plane)")
        view_toolbar.addWidget(self.btn_view_front)

        self.btn_view_side = QPushButton("⏹ SIDE")
        self.btn_view_side.setStyleSheet(btn_style)
        self.btn_view_side.setToolTip("Side View (YZ Plane)")
        view_toolbar.addWidget(self.btn_view_side)

        self.btn_view_fit = QPushButton("🔍 FIT")
        self.btn_view_fit.setStyleSheet(btn_style)
        self.btn_view_fit.setToolTip("Fit Toolpath and Machine Limits to Viewport")
        view_toolbar.addWidget(self.btn_view_fit)

        view_toolbar.addStretch()
        preview_vbox.addLayout(view_toolbar)

        self.toolpath_viewer = ToolpathViewer(parent=self)
        self.btn_view_iso.clicked.connect(self.toolpath_viewer.set_view_iso)
        self.btn_view_top.clicked.connect(self.toolpath_viewer.set_view_top)
        self.btn_view_front.clicked.connect(self.toolpath_viewer.set_view_front)
        self.btn_view_side.clicked.connect(self.toolpath_viewer.set_view_side)
        self.btn_view_fit.clicked.connect(self.toolpath_viewer.fit_view)

        if hasattr(self, 'settings') and self.settings is not None:
            try:
                min_x = float(self.settings.get_setting('AXIS_X', 'min_limit', '-5.0'))
                max_x = float(self.settings.get_setting('AXIS_X', 'max_limit', '1275.0'))
                min_y = float(self.settings.get_setting('AXIS_Y', 'min_limit', '-5.0'))
                max_y = float(self.settings.get_setting('AXIS_Y', 'max_limit', '1275.0'))
                min_z = float(self.settings.get_setting('AXIS_Z', 'min_limit', '-134.0'))
                max_z = float(self.settings.get_setting('AXIS_Z', 'max_limit', '5.0'))
                self.toolpath_viewer.set_machine_limits(min_x, max_x, min_y, max_y, min_z, max_z)
            except Exception:
                pass
        
        preview_pref = True
        if hasattr(self, 'settings') and self.settings is not None:
            preview_pref = self.settings.get_setting('UI_PREFERENCES', 'enable_toolpath_preview', 'True') == 'True'
        if not preview_pref:
            self.toolpath_viewer.set_preview_enabled(False)
            
        preview_vbox.addWidget(self.toolpath_viewer)
        
        preview_btn_layout = QHBoxLayout()
        
        self.btn_toggle_preview = QPushButton()
        self.update_preview_toggle_button()
        self.btn_toggle_preview.clicked.connect(self.toggle_toolpath_preview)
        preview_btn_layout.addWidget(self.btn_toggle_preview)
        
        btn_screenshot = QPushButton("📸 Save Screenshot")
        btn_screenshot.setStyleSheet("QPushButton { background-color: #2c3e50; color: white; padding: 4px 10px; border-radius: 3px; font-weight: bold; } QPushButton:pressed { background-color: #1a252f; }")
        btn_screenshot.clicked.connect(self.save_toolpath_screenshot)
        preview_btn_layout.addWidget(btn_screenshot)
        
        preview_vbox.addLayout(preview_btn_layout)
        preview_group.setLayout(preview_vbox)
        
        gcode_preview_layout.addWidget(preview_group, 2)
        
        # Wire Editor changes to Toolpath Viewer
        self.default_gcode_editor.textChanged.connect(self._on_gcode_text_changed)
        self.toolpath_viewer.segment_double_clicked.connect(self.highlight_gcode_line)
        
        master_layout.addLayout(gcode_preview_layout, 1) # Stretch to fill middle
        master_layout.addSpacing(10)

        # --- BOTTOM ROW: Controls ---
        controls_layout = QHBoxLayout()

        # --- LEFT COLUMN: Readouts & Motion ---
        left_col = QVBoxLayout()

        dro_probe_layout = QHBoxLayout()
        
        dro_group = QGroupBox("Digital Readout (DRO)")
        dro_group.setStyleSheet("QGroupBox { font-weight: bold; border: 1px solid #444; border-radius: 4px; margin-top: 10px; } QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 5px; }")
        dro_v_layout = QVBoxLayout(dro_group)

        # --- Global DRO Mode Buttons ---
        mode_group = QWidget()
        mode_layout = QHBoxLayout(mode_group)
        mode_layout.setContentsMargins(0, 0, 0, 0)
        mode_layout.setSpacing(5)
        
        btn_work = QPushButton("Work")
        btn_abs = QPushButton("Machine")
        btn_dtg = QPushButton("DTG")
        
        self.dro_mode_buttons = {'WORK': btn_work, 'MACHINE': btn_abs, 'DTG': btn_dtg}
        for mode, btn in self.dro_mode_buttons.items():
            btn.setStyleSheet(f"background-color: {'#2980b9' if mode == self.dro_mode else '#34495e'}; color: white; padding: 4px; border-radius: 3px; font-weight: bold;")
            btn.clicked.connect(lambda _, m=mode: self.set_dro_mode(m))
            mode_layout.addWidget(btn)
            
        dro_v_layout.addWidget(mode_group)

        for i, axis in enumerate(["X", "Y", "Z"]):
            row_layout = QHBoxLayout()
            # Create the label if it doesn't exist
            if axis not in self.dro_labels:
                self.dro_labels[axis] = QLabel("0.000")
            
            self.dro_labels[axis].setFixedWidth(200)
            self.dro_labels[axis].setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self.dro_labels[axis].setStyleSheet("font-size: 36px; color: #39FF14; background: #111; border: 2px solid #555; padding: 5px; font-family: 'Consolas', 'Courier New', monospace; border-radius: 4px;")
            lbl = QLabel(f"{axis}:")
            lbl.setStyleSheet("font-size: 36px; font-weight: bold; color: #ccc;")
                
            row_layout.addWidget(lbl)
            row_layout.addWidget(self.dro_labels[axis])

            btn_zero = QPushButton(f"Zero {axis}")
            btn_zero.setStyleSheet("QPushButton { background-color: #34495e; color: white; padding: 10px; font-weight: bold; border-radius: 4px; } QPushButton:pressed { background-color: #2c3e50; }")
            btn_zero.clicked.connect(lambda _, a=axis: self.zero_axis(a))
            row_layout.addWidget(btn_zero)
            dro_v_layout.addLayout(row_layout)

        dro_probe_layout.addWidget(dro_group)
        
        probe_group = QGroupBox("Offsets & Probing")
        probe_group.setStyleSheet("QGroupBox { font-weight: bold; border: 1px solid #444; border-radius: 4px; margin-top: 10px; } QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 5px; }")
        probe_layout = QVBoxLayout()
        
        self.probe_selector = QComboBox()
        self.probe_selector.addItems(["🟢 Active: Touch Plate / Setter", "🟠 Active: 3D Touch Probe"])
        self.probe_selector.setStyleSheet("background-color: #27ae60; color: white; font-weight: bold; padding: 6px; border-radius: 4px; margin-bottom: 5px;")
        self.probe_selector.currentIndexChanged.connect(self.switch_probe_hardware)
        probe_layout.addWidget(self.probe_selector)
        
        probe_tabs = QTabWidget()
        probe_tabs.setStyleSheet("QTabBar::tab { padding: 6px 10px; font-weight: bold; }")
        
        # --- PLATE TAB ---
        plate_tab = QWidget()
        plate_layout = QVBoxLayout(plate_tab)
        
        z_layout = QHBoxLayout()
        self.input_z_thickness = QLineEdit()
        self.input_z_thickness.setPlaceholderText("Plate Thickness (mm)")
        self.input_z_thickness.setText(self.settings.get_setting("PROBING", "plate_thickness", ""))
        self.input_z_thickness.textChanged.connect(lambda t: self.settings.save_setting("PROBING", "plate_thickness", t))
        z_layout.addWidget(self.input_z_thickness)
        
        btn_set_z = QPushButton("Set Z Zero")
        btn_set_z.clicked.connect(self.set_z_zero)
        btn_set_z.setStyleSheet("QPushButton { background-color: #8e44ad; color: white; font-weight: bold; padding: 6px; border-radius: 4px; } QPushButton:pressed { background-color: #732d91; }")
        z_layout.addWidget(btn_set_z)
        plate_layout.addLayout(z_layout)
        
        btn_fast_probe = QPushButton("Fast Z Probe")
        btn_fast_probe.clicked.connect(self.fast_probe)
        btn_fast_probe.setStyleSheet("QPushButton { background-color: #d35400; color: white; font-weight: bold; padding: 8px; border-radius: 4px; } QPushButton:pressed { background-color: #a04000; }")
        plate_layout.addWidget(btn_fast_probe)

        self.input_raw_probe_distance = QLineEdit("-10")
        self.input_raw_probe_distance.setPlaceholderText("Downward Z travel")
        self.input_raw_probe_distance.setToolTip("Negative distance only. This operation does not change work offsets.")
        self.btn_raw_probe_z = QPushButton("Raw Z Probe (No Offset)")
        self.btn_raw_probe_z.setStyleSheet("QPushButton { background-color: #34495e; color: white; font-weight: bold; padding: 8px; border-radius: 4px; } QPushButton:pressed { background-color: #2c3e50; }")
        self.btn_raw_probe_z.clicked.connect(self.run_raw_z_probe)
        raw_probe_layout = QHBoxLayout()
        raw_probe_layout.addWidget(self.input_raw_probe_distance)
        raw_probe_layout.addWidget(self.btn_raw_probe_z)
        plate_layout.addLayout(raw_probe_layout)

        self.btn_touch_off_z = QPushButton("Touch Plate Z Offset")
        self.btn_touch_off_z.setStyleSheet("QPushButton { background-color: #8e44ad; color: white; font-weight: bold; padding: 8px; border-radius: 4px; } QPushButton:pressed { background-color: #732d91; }")
        self.btn_touch_off_z.clicked.connect(self.run_touch_off_z)
        plate_layout.addWidget(self.btn_touch_off_z)
        plate_layout.addStretch()
        probe_tabs.addTab(plate_tab, "Plate")
        
        # --- TOUCH TAB (SHORTCUT TO PROBING TAB) ---
        touch_tab = QWidget()
        touch_layout = QVBoxLayout(touch_tab)
        touch_layout.setContentsMargins(10, 15, 10, 15)
        touch_layout.setSpacing(10)

        lbl_touch_info = QLabel("Workpiece probing has been upgraded to Probe Basic style in the dedicated Probing tab.")
        lbl_touch_info.setStyleSheet("color: #cbd5e1; font-size: 11px;")
        lbl_touch_info.setWordWrap(True)
        lbl_touch_info.setAlignment(Qt.AlignmentFlag.AlignCenter)
        touch_layout.addWidget(lbl_touch_info)

        btn_open_probing = QPushButton("🔍 Open Probing Tab (Probe Basic)")
        btn_open_probing.setStyleSheet("QPushButton { background-color: #0284c7; color: white; font-weight: bold; padding: 10px; border-radius: 4px; font-size: 12px; } QPushButton:hover { background-color: #0369a1; }")
        btn_open_probing.clicked.connect(self.open_probing_tab)
        touch_layout.addWidget(btn_open_probing)

        # Retain parameters for backwards compatibility
        self.input_probe_diam = QLineEdit(self.settings.get_setting("PROBING", "probe_diam", "3.0"))
        self.input_xy_clearance = QLineEdit(self.settings.get_setting("PROBING", "xy_clear", "5.0"))
        self.input_z_drop = QLineEdit(self.settings.get_setting("PROBING", "z_drop", "5.0"))
        self.input_search_dist = QLineEdit(self.settings.get_setting("PROBING", "search_dist", "20.0"))

        touch_layout.addStretch()
        probe_tabs.addTab(touch_tab, "Touch")
        
        # --- TOOL SETTER TAB ---
        tool_tab = QWidget()
        tool_layout = QVBoxLayout(tool_tab)
        
        tool_load_layout = QHBoxLayout()
        self.input_tool_num = QLineEdit()
        self.input_tool_num.setPlaceholderText("Tool #")
        self.input_tool_num.setStyleSheet("max-width: 60px;")
        self.btn_load_tool = QPushButton("Load Tool (M6)")
        self.btn_load_tool.setStyleSheet("QPushButton { background-color: #34495e; color: white; font-weight: bold; padding: 6px; border-radius: 4px; } QPushButton:pressed { background-color: #2c3e50; }")
        self.btn_load_tool.clicked.connect(self.load_tool)
        tool_load_layout.addWidget(QLabel("Tool:"))
        tool_load_layout.addWidget(self.input_tool_num)
        tool_load_layout.addWidget(self.btn_load_tool)
        tool_load_layout.addStretch()
        tool_layout.addLayout(tool_load_layout)
        
        self.input_tool_setter = QLineEdit()
        self.input_tool_setter.setPlaceholderText("Setter Ref Z (Machine)")
        self.input_tool_setter.setReadOnly(True)
        self.input_tool_setter.setStyleSheet("background-color: #111; color: #f39c12; font-weight: bold; border: 1px solid #555;")
        self.input_tool_setter.setText(self.settings.get_setting("PROBING", "setter_ref_z", ""))
        self.input_tool_setter.textChanged.connect(lambda t: self.settings.save_setting("PROBING", "setter_ref_z", t))
        tool_layout.addWidget(self.input_tool_setter)
        
        self.btn_move_setter = QPushButton("Move to Tool Change / Setter")
        self.btn_move_setter.setStyleSheet("QPushButton { background-color: #2980b9; color: white; font-weight: bold; padding: 8px; border-radius: 4px; } QPushButton:pressed { background-color: #1f618d; }")
        self.btn_move_setter.clicked.connect(self.move_to_tool_setter)
        tool_layout.addWidget(self.btn_move_setter)
        
        btn_tool_probe = QPushButton("Probe Tool Height")
        btn_tool_probe.setStyleSheet("QPushButton { background-color: #27ae60; color: white; font-weight: bold; padding: 8px; border-radius: 4px; } QPushButton:pressed { background-color: #229954; }")
        btn_tool_probe.clicked.connect(lambda: self.trigger_probing("tool_height"))
        
        btn_calibrate_setter = QPushButton("Calibrate Tool Setter")
        btn_calibrate_setter.setStyleSheet("QPushButton { background-color: #f39c12; color: black; font-weight: bold; padding: 8px; border-radius: 4px; } QPushButton:pressed { background-color: #c87f0a; }")
        btn_calibrate_setter.clicked.connect(self.start_tool_setter_calibration)
        tool_layout.addWidget(btn_calibrate_setter)
        tool_layout.addWidget(btn_tool_probe)
        tool_layout.addStretch()
        probe_tabs.addTab(tool_tab, "Tool Setter")

        if client_from_settings(self.settings) is not None:
            self.probe_selector.setToolTip("Select the verified probe source before a secure Z probe or setter operation.")

        
        # --- PARK TAB ---
        park_tab = QWidget()
        park_layout = QVBoxLayout(park_tab)
        self.btn_park = QPushButton("Safely Park Machine")
        self.btn_park.setStyleSheet("QPushButton { background-color: #3498db; color: white; font-weight: bold; padding: 8px; border-radius: 4px; } QPushButton:pressed { background-color: #2980b9; }")
        self.btn_park.clicked.connect(self.park_machine)
        park_layout.addWidget(self.btn_park)
        park_layout.addStretch()
        probe_tabs.addTab(park_tab, "Park")
        
        probe_layout.addWidget(probe_tabs)
        probe_group.setLayout(probe_layout)
        dro_probe_layout.addWidget(probe_group)
        
        macro_group = QGroupBox("Macros & Coolant")
        macro_group.setStyleSheet("QGroupBox { font-weight: bold; border: 1px solid #444; border-radius: 4px; margin-top: 10px; } QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 5px; }")
        macro_layout = QGridLayout()
        
        self.spindle_rpm_input = QLineEdit("1000")
        self.spindle_rpm_input.setPlaceholderText("RPM")
        self.spindle_rpm_input.setStyleSheet("background-color: #111; color: #39FF14; border: 1px solid #555; padding: 4px; border-radius: 4px; font-weight: bold; font-family: monospace; max-width: 60px;")
        
        rpm_lbl = QLabel("RPM:")
        rpm_lbl.setStyleSheet("color: #ccc; font-weight: bold;")
        
        rpm_layout = QHBoxLayout()
        rpm_layout.addWidget(rpm_lbl)
        rpm_layout.addWidget(self.spindle_rpm_input)
        rpm_layout.addStretch()
        
        self.btn_m3 = QPushButton("M3 (CW)")
        self.btn_m4 = QPushButton("M4 (CCW)")
        self.btn_m5 = QPushButton("M5 (Stop)")
        self.btn_m7 = QPushButton("M7 (Mist)")
        self.btn_m8 = QPushButton("M8 (Flood)")
        self.btn_m9 = QPushButton("M9 (Off)")
        
        for btn in [self.btn_m3, self.btn_m4, self.btn_m5, self.btn_m7, self.btn_m8, self.btn_m9]:
            btn.setStyleSheet("QPushButton { background-color: #2980b9; color: white; font-weight: bold; padding: 8px; border-radius: 4px; } QPushButton:pressed { background-color: #1f618d; }")
            
        self.btn_m5.setStyleSheet("QPushButton { background-color: #c0392b; color: white; font-weight: bold; padding: 8px; border-radius: 4px; } QPushButton:pressed { background-color: #922b21; }")
        self.btn_m9.setStyleSheet("QPushButton { background-color: #c0392b; color: white; font-weight: bold; padding: 8px; border-radius: 4px; } QPushButton:pressed { background-color: #922b21; }")
        
        self.btn_m3.clicked.connect(lambda: self.set_spindle_direction("forward"))
        self.btn_m4.clicked.connect(lambda: self.set_spindle_direction("reverse"))
        self.btn_m5.clicked.connect(lambda: self.set_spindle_direction("stop"))
        self.btn_m7.clicked.connect(lambda: self.set_coolant("mist_on"))
        self.btn_m8.clicked.connect(lambda: self.set_coolant("flood_on"))
        self.btn_m9.clicked.connect(lambda: self.set_coolant("off"))
        
        macro_layout.addLayout(rpm_layout, 0, 0, 1, 3)
        macro_layout.addWidget(self.btn_m3, 1, 0); macro_layout.addWidget(self.btn_m4, 1, 1); macro_layout.addWidget(self.btn_m5, 1, 2)
        macro_layout.addWidget(self.btn_m7, 2, 0); macro_layout.addWidget(self.btn_m8, 2, 1); macro_layout.addWidget(self.btn_m9, 2, 2)
        
        # Add Subroutine Options
        self.combo_ocodes = QComboBox()
        self.combo_ocodes.setStyleSheet("background-color: #2d2d30; color: white; border: 1px solid #555; padding: 4px; border-radius: 4px; font-weight: bold;")
        if client_from_settings(self.settings) is None:
            self.combo_ocodes.addItems(["O100 (Warmup)", "O101 (Tool Measure)", "O102 (Park)"])
        else:
            self.combo_ocodes.addItem("Loading managed macros...")
        
        self.btn_run_ocode = QPushButton("Run Subroutine")
        self.btn_run_ocode.setStyleSheet("QPushButton { background-color: #8e44ad; color: white; font-weight: bold; padding: 6px; border-radius: 4px; } QPushButton:pressed { background-color: #732d91; }")
        self.btn_run_ocode.clicked.connect(self.run_ocode)
        if client_from_settings(self.settings) is not None:
            self.btn_run_ocode.setEnabled(False)
        
        macro_layout.addWidget(self.combo_ocodes, 3, 0, 1, 2)
        macro_layout.addWidget(self.btn_run_ocode, 3, 2)
        
        macro_group.setLayout(macro_layout)
        dro_probe_layout.addWidget(macro_group)
        dro_probe_layout.addStretch()
        
        left_col.addLayout(dro_probe_layout)
        
        run_jog_home_layout = QHBoxLayout()
        
        run_group = QGroupBox("Program Control")
        run_group.setStyleSheet("QGroupBox { font-weight: bold; border: 1px solid #444; border-radius: 4px; margin-top: 10px; } QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 5px; }")
        run_layout = QVBoxLayout()
        
        self.progress_bar = QProgressBar()
        self.progress_bar.setStyleSheet("QProgressBar { border: 1px solid #555; border-radius: 4px; text-align: center; font-weight: bold; color: white; background-color: #111; } QProgressBar::chunk { background-color: #27ae60; }")
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat("No Program Loaded")
        self.progress_bar.setFixedHeight(25)
        run_layout.addWidget(self.progress_bar)
        
        mode_btn_layout = QHBoxLayout()
        self.btn_mode_manual = QPushButton("MANUAL")
        self.btn_mode_mdi = QPushButton("MDI")
        self.btn_mode_auto = QPushButton("AUTO")
        for btn in [self.btn_mode_manual, self.btn_mode_mdi, self.btn_mode_auto]:
            btn.setFixedHeight(36)
            btn.setStyleSheet("QPushButton { background-color: #34495e; color: white; font-weight: bold; border-radius: 4px; } QPushButton:pressed { background-color: #2c3e50; }")
            mode_btn_layout.addWidget(btn)
        run_layout.addLayout(mode_btn_layout)

        self.btn_cycle_start = QPushButton("▶ CYCLE START")
        self.btn_cycle_pause = QPushButton("⏸ PAUSE")
        self.btn_cycle_stop = QPushButton("⏹ STOP")

        for btn in [self.btn_cycle_start, self.btn_cycle_pause, self.btn_cycle_stop]:
            btn.setFixedHeight(40)
            btn.setStyleSheet("QPushButton { background-color: #34495e; color: white; font-weight: bold; border-radius: 4px; } QPushButton:pressed { background-color: #2c3e50; }")
            run_layout.addWidget(btn)

        self.btn_mode_manual.clicked.connect(lambda: self.select_machine_mode("manual"))
        self.btn_mode_mdi.clicked.connect(lambda: self.select_machine_mode("mdi"))
        self.btn_mode_auto.clicked.connect(lambda: self.select_machine_mode("auto"))
        self.btn_cycle_start.clicked.connect(self.start_cycle)
        self.btn_cycle_pause.clicked.connect(self.pause_or_resume_cycle)
        self.btn_cycle_stop.clicked.connect(self.stop_cycle)

        self.btn_cycle_start.setStyleSheet("QPushButton { background-color: #27ae60; color: white; font-weight: bold; border-radius: 4px; } QPushButton:pressed { background-color: #229954; }")
        self.btn_cycle_pause.setStyleSheet("QPushButton { background-color: #f39c12; color: black; font-weight: bold; border-radius: 4px; } QPushButton:pressed { background-color: #c87f0a; }")
        self.btn_cycle_stop.setStyleSheet("QPushButton { background-color: #c0392b; color: white; font-weight: bold; border-radius: 4px; } QPushButton:pressed { background-color: #922b21; }")
        
        run_group.setLayout(run_layout)
        run_jog_home_layout.addWidget(run_group)
        
        jog_group = QGroupBox("Jogging Controls")
        jog_group.setStyleSheet("QGroupBox { font-weight: bold; border: 1px solid #444; border-radius: 4px; margin-top: 10px; } QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 5px; }")
        jog_layout = QVBoxLayout()
        
        speed_layout = QHBoxLayout()
        speed_layout.addWidget(QLabel("Jog Speed:"))
        self.jog_speed_slider = QSlider(Qt.Orientation.Horizontal)
        self.jog_speed_slider.setRange(1, 100)
        self.jog_speed_slider.setValue(10)
        self.jog_speed_slider.valueChanged.connect(self.update_jog_speed)
        speed_layout.addWidget(self.jog_speed_slider)
        
        self.lbl_jog_speed_val = QLabel("10 %")
        speed_layout.addWidget(self.lbl_jog_speed_val)
        jog_layout.addLayout(speed_layout)
        
        btn_grid = QGridLayout()
        btn_y_pos = QPushButton("Y+")
        btn_y_neg = QPushButton("Y-")
        btn_x_pos = QPushButton("X+")
        btn_x_neg = QPushButton("X-")
        btn_z_pos = QPushButton("Z+")
        btn_z_neg = QPushButton("Z-")
        
        self.jog_buttons = {
            "Y+": btn_y_pos,
            "Y-": btn_y_neg,
            "X+": btn_x_pos,
            "X-": btn_x_neg,
            "Z+": btn_z_pos,
            "Z-": btn_z_neg,
        }
        for btn in self.jog_buttons.values():
            btn.setFixedSize(60, 60)
            btn.setStyleSheet("QPushButton { background-color: #2c3e50; color: white; font-weight: bold; font-size: 16px; border-radius: 5px; } QPushButton:pressed { background-color: #1a252f; padding-top: 2px; padding-left: 2px; }")
            
        btn_grid.addWidget(btn_y_pos, 0, 1)
        btn_grid.addWidget(btn_x_neg, 1, 0)
        btn_grid.addWidget(btn_x_pos, 1, 2)
        btn_grid.addWidget(btn_y_neg, 2, 1)
        
        btn_grid.addWidget(btn_z_pos, 0, 4)
        btn_grid.addWidget(btn_z_neg, 2, 4)

        btn_y_pos.pressed.connect(lambda: self.jog_button_pressed('Y', 1))
        btn_y_pos.released.connect(lambda: self.jog_button_released('Y'))
        btn_y_neg.pressed.connect(lambda: self.jog_button_pressed('Y', -1))
        btn_y_neg.released.connect(lambda: self.jog_button_released('Y'))
        
        btn_x_pos.pressed.connect(lambda: self.jog_button_pressed('X', 1))
        btn_x_pos.released.connect(lambda: self.jog_button_released('X'))
        btn_x_neg.pressed.connect(lambda: self.jog_button_pressed('X', -1))
        btn_x_neg.released.connect(lambda: self.jog_button_released('X'))
        
        btn_z_pos.pressed.connect(lambda: self.jog_button_pressed('Z', 1))
        btn_z_pos.released.connect(lambda: self.jog_button_released('Z'))
        btn_z_neg.pressed.connect(lambda: self.jog_button_pressed('Z', -1))
        btn_z_neg.released.connect(lambda: self.jog_button_released('Z'))
        
        btn_grid.setColumnStretch(3, 1)

        jog_layout.addLayout(btn_grid)
        jog_group.setLayout(jog_layout)
        run_jog_home_layout.addWidget(jog_group)

        home_group = QGroupBox("Homing")
        home_group.setStyleSheet("QGroupBox { font-weight: bold; border: 1px solid #444; border-radius: 4px; margin-top: 10px; } QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 5px; }")
        home_layout = QVBoxLayout()
        
        self.btn_home_all = QPushButton("HOME ALL")
        self.btn_home_all.setFixedHeight(50)
        self.btn_home_all.setStyleSheet("QPushButton { background-color: #27ae60; color: white; font-weight: bold; font-size: 16px; border-radius: 5px; } QPushButton:pressed { background-color: #229954; padding-top: 2px; padding-left: 2px; }")
        self.btn_home_all.clicked.connect(self.home_all_axes)
        home_layout.addWidget(self.btn_home_all)
        
        home_btn_layout = QHBoxLayout()
        for axis in ["X", "Y", "Z"]:
            btn = QPushButton(f"Home {axis}")
            btn.setFixedHeight(40)
            btn.setStyleSheet("QPushButton { background-color: #2980b9; color: white; font-weight: bold; border-radius: 4px; } QPushButton:pressed { background-color: #1f618d; padding-top: 2px; padding-left: 2px; }")
            btn.clicked.connect(lambda checked, a=axis: self.home_axis(a))
            self.home_buttons[axis] = btn
            home_btn_layout.addWidget(btn)
            
        home_layout.addLayout(home_btn_layout)
        home_group.setLayout(home_layout)
        run_jog_home_layout.addWidget(home_group)
        run_jog_home_layout.addStretch()

        left_col.addLayout(run_jog_home_layout)
        left_col.addStretch()

        # --- RIGHT COLUMN: State & Configuration ---
        right_col = QVBoxLayout()
        right_col.addStretch()
        
        override_group = QGroupBox("Overrides")
        override_group.setStyleSheet("QGroupBox { font-weight: bold; border: 1px solid #444; border-radius: 4px; margin-top: 10px; } QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 5px; }")
        override_layout = QVBoxLayout()
        
        self.dro_labels["FEED"] = QLabel("0.0")
        self.dro_labels["FEED"].setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.dro_labels["FEED"].setStyleSheet("font-size: 24px; color: #f39c12; background: #111; border: 2px solid #555; padding: 5px; font-family: 'Consolas', 'Courier New', monospace; border-radius: 4px;")
        feed_lbl = QLabel("FEED:")
        feed_lbl.setStyleSheet("font-size: 20px; font-weight: bold; color: #ccc;")
        feed_ro_layout = QHBoxLayout()
        feed_ro_layout.addWidget(feed_lbl)
        feed_ro_layout.addWidget(self.dro_labels["FEED"])
        
        feed_layout = QHBoxLayout()
        feed_layout.addWidget(QLabel("Feed Ovr:"))
        self.feed_slider = QSlider(Qt.Orientation.Horizontal)
        self.feed_slider.setRange(0, 200)
        self.feed_slider.setValue(100)
        self.lbl_feed_val = QLabel("100%")
        self.feed_slider.valueChanged.connect(lambda v: self.lbl_feed_val.setText(f"{v}%"))
        self.feed_slider.sliderReleased.connect(self.set_feed_override)
        feed_layout.addWidget(self.feed_slider)
        feed_layout.addWidget(self.lbl_feed_val)
        
        self.dro_labels["RPM"] = QLabel("0.0")
        self.dro_labels["RPM"].setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.dro_labels["RPM"].setStyleSheet("font-size: 24px; color: #f39c12; background: #111; border: 2px solid #555; padding: 5px; font-family: 'Consolas', 'Courier New', monospace; border-radius: 4px;")
        rpm_lbl = QLabel("RPM:")
        rpm_lbl.setStyleSheet("font-size: 20px; font-weight: bold; color: #ccc;")
        rpm_ro_layout = QHBoxLayout()
        rpm_ro_layout.addWidget(rpm_lbl)
        rpm_ro_layout.addWidget(self.dro_labels["RPM"])
        
        spindle_layout = QHBoxLayout()
        spindle_layout.addWidget(QLabel("Spindle Ovr:"))
        self.spindle_slider = QSlider(Qt.Orientation.Horizontal)
        self.spindle_slider.setRange(0, 200)
        self.spindle_slider.setValue(100)
        self.lbl_spindle_val = QLabel("100%")
        self.spindle_slider.valueChanged.connect(lambda v: self.lbl_spindle_val.setText(f"{v}%"))
        self.spindle_slider.sliderReleased.connect(self.set_spindle_override)
        spindle_layout.addWidget(self.spindle_slider)
        spindle_layout.addWidget(self.lbl_spindle_val)
        
        override_layout.addLayout(feed_ro_layout)
        override_layout.addLayout(feed_layout)
        override_layout.addSpacing(10)
        override_layout.addLayout(rpm_ro_layout)
        override_layout.addLayout(spindle_layout)
        
        btn_reset_ovr = QPushButton("Reset Overrides")
        btn_reset_ovr.setStyleSheet("QPushButton { background-color: #7f8c8d; color: white; font-weight: bold; padding: 4px; border-radius: 4px; } QPushButton:pressed { background-color: #616a6b; }")
        btn_reset_ovr.clicked.connect(self.reset_overrides)
        override_layout.addWidget(btn_reset_ovr)
        
        override_group.setLayout(override_layout)
        right_col.addWidget(override_group)
        
        state_group = QGroupBox("Machine Control")
        state_group.setStyleSheet("QGroupBox { font-weight: bold; border: 1px solid #444; border-radius: 4px; margin-top: 10px; } QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 5px; }")
        state_layout = QVBoxLayout()
        
        self.btn_master_toggle = QPushButton("RESET (E-STOP)")
        self.btn_master_toggle.setFixedHeight(80)
        self.btn_master_toggle.setStyleSheet("QPushButton { background-color: #c0392b; color: white; font-weight: bold; font-size: 20px; border-radius: 5px; } QPushButton:pressed { background-color: #922b21; padding-top: 2px; padding-left: 2px; }")
        self.btn_master_toggle.clicked.connect(self.toggle_estop)
        state_layout.addWidget(self.btn_master_toggle)
        
        self.btn_power = QPushButton("POWER ON")
        self.btn_power.setFixedHeight(60)
        self.btn_power.setStyleSheet("QPushButton { background-color: #7f8c8d; color: white; font-weight: bold; font-size: 16px; border-radius: 5px; } QPushButton:pressed { background-color: #616a6b; padding-top: 2px; padding-left: 2px; }")
        self.btn_power.clicked.connect(self.toggle_power)
        state_layout.addWidget(self.btn_power)
        
        self.btn_override_limits = QPushButton("OVERRIDE LIMITS")
        self.btn_override_limits.setFixedHeight(40)
        self.btn_override_limits.setStyleSheet("QPushButton { background-color: #d35400; color: white; font-weight: bold; font-size: 14px; border-radius: 5px; } QPushButton:pressed { background-color: #a04000; padding-top: 2px; padding-left: 2px; }")
        self.btn_override_limits.clicked.connect(self.override_limits)
        state_layout.addWidget(self.btn_override_limits)
        
        state_group.setLayout(state_layout)
        right_col.addWidget(state_group)
        
        controls_layout.addLayout(left_col, 3)
        controls_layout.addLayout(right_col, 1)
        
        master_layout.addLayout(controls_layout)

    def restore_default_ui(self):
        self.stack.setCurrentIndex(0)
        if self.ui_file_watcher.files():
            self.ui_file_watcher.removePaths(self.ui_file_watcher.files())
        self.current_ui_file = None

    def load_custom_ui(self, path=None):
        if not path:
            default_dir = os.path.join(os.getcwd(), "ui_layouts")
            last_dir = self.settings.get_setting("PATHS", "last_ui_dir", default_dir)
            if not os.path.exists(last_dir): last_dir = default_dir
            path, _ = QFileDialog.getOpenFileName(self, "Load Custom UI", last_dir, "LinuxCNC UI (*.linuxcncui);;All Files (*)")
            if not path: return
        
        try:
            with open(path, 'r') as f:
                content = f.read()
                if not content.strip(): return # File is empty, probably during a save
                layout_data = json.loads(content)
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to load UI layout:\n{e}")
            # If reload fails, remove from watcher to prevent loop
            if self.current_ui_file and self.current_ui_file in self.ui_file_watcher.files():
                self.ui_file_watcher.removePath(self.current_ui_file)
            self.current_ui_file = None
            return
            
        # Recreate custom_ui_widget to clear old data
        self.stack.removeWidget(self.custom_ui_widget)
        self.custom_ui_widget.deleteLater()
        self.custom_ui_widget = QWidget()
        
        max_w, max_h = 0, 0
        
        # Instantiate widgets based on data using Absolute Positioning
        for item in layout_data:
            w_type = item.get("type", "Button")
            title = item.get("title", "Widget")
            x = item.get("x", 0)
            y = item.get("y", 0)
            w = item.get("w", 150)
            h = item.get("h", 60)
            
            if x + w > max_w: max_w = x + w
            if y + h > max_h: max_h = y + h
            
            # Parse Visual Properties
            bg_color = item.get("bg_color", "#2980b9" if w_type == "Button" else ("#27ae60" if w_type == "DRO" else "#34495e"))
            text_color = item.get("text_color", "#ffffff")
            align = item.get("alignment", "Center")
            valign = item.get("valignment", "Center")
            
            font_family = item.get("font_family", "System Default")
            default_font_size = 24 if w_type == "DRO" else 10
            font_size = item.get("font_size", default_font_size)
            border_color = item.get("border_color", "#2c3e50")
            border_width = item.get("border_width", 2)
            border_radius = item.get("border_radius", 10 if w_type == "LED" else 5)
            has_shadow = item.get("has_shadow", False)
            
            img_path = item.get("image_path", "").replace("\\", "/")
            show_text = item.get("show_text", True)
            img_scaling = item.get("image_scaling", "Stretch")
            
            display_title = title if show_text else ""
            
            valign_css = "top" if valign == "Top" else ("bottom" if valign == "Bottom" else "center")
            align_css = "center" if align == "Center" else ("left" if align == "Left" else "right")
            
            q_align = Qt.AlignmentFlag.AlignHCenter if align == "Center" else (Qt.AlignmentFlag.AlignLeft if align == "Left" else Qt.AlignmentFlag.AlignRight)
            q_valign = Qt.AlignmentFlag.AlignTop if valign == "Top" else (Qt.AlignmentFlag.AlignBottom if valign == "Bottom" else Qt.AlignmentFlag.AlignVCenter)
            
            bg_css = f"background-color: {bg_color};"
            if img_path and os.path.exists(img_path):
                if img_scaling == "Stretch":
                    bg_css = f"border-image: url('{img_path}') 0 0 0 0 stretch stretch;"
                else:
                    bg_css = f"background-image: url('{img_path}'); background-position: {align_css} {valign_css}; background-repeat: no-repeat;"
                
            font_css = f"font-size: {font_size}pt;"
            if font_family != "System Default":
                font_css += f" font-family: '{font_family}';"
                
            common_style = f"{bg_css} color: {text_color}; text-align: {valign_css} {align_css}; {font_css}"
            
            widget_obj = None
            
            if w_type == "Button":
                btn = QPushButton(display_title, self.custom_ui_widget)
                btn.setGeometry(int(x), int(y), int(w), int(h))
                btn.setStyleSheet(f"QPushButton {{ {common_style} font-weight: bold; border: {border_width}px solid {border_color}; border-radius: {border_radius}px; }} QPushButton:pressed {{ background-color: {border_color}; padding-top: 2px; padding-left: 2px; }}")
                normalized_title = title.replace(" ", "_").upper()
                if normalized_title in {"HOME_ALL", "UNHOME_ALL"}:
                    self.custom_home_all_buttons.append(btn)
                    btn.clicked.connect(self.home_all_axes)
                elif normalized_title in {"HOME_X", "HOME_Y", "HOME_Z"}:
                    btn.clicked.connect(lambda _, axis=normalized_title[-1]: self.home_axis(axis))
                else:
                    safe_title = title.replace(' ', '_').lower()
                    out_pin = f"ui.{safe_title}.out"
                    btn.pressed.connect(lambda _=None, p=out_pin: self.send_halcmd_setp(p, "True"))
                    btn.released.connect(lambda _=None, p=out_pin: self.send_halcmd_setp(p, "False"))
                widget_obj = btn
            elif w_type == "Toggle":
                btn = QPushButton(display_title, self.custom_ui_widget)
                btn.setCheckable(True)
                btn.setGeometry(int(x), int(y), int(w), int(h))
                btn.setStyleSheet(f"QPushButton {{ {common_style} font-weight: bold; border: {border_width}px solid {border_color}; border-radius: {border_radius}px; }} QPushButton:checked {{ background-color: #27ae60; }} QPushButton:pressed {{ background-color: {border_color}; padding-top: 2px; padding-left: 2px; }}")
                
                # Phase 4: Wire Toggle to SSH HAL Command
                safe_title = title.replace(' ', '_').lower()
                state_pin = f"ui.{safe_title}.state"
                btn.toggled.connect(lambda checked, p=state_pin: self.send_halcmd_setp(p, "True" if checked else "False"))
                widget_obj = btn
            elif w_type == "DRO":
                grp = QGroupBox(display_title, self.custom_ui_widget)
                grp.setGeometry(int(x), int(y), int(w), int(h))
                grp.setStyleSheet(f"QGroupBox {{ {bg_css} border: 2px solid {bg_color}; border-radius: 5px; margin-top: 10px; color: {text_color}; font-weight: bold; }} QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 3px 0 3px; }}")
                lbl = QLabel("0.000", grp)
                lbl.setGeometry(10, 20, int(w)-20, int(h)-30)
                lbl.setAlignment(q_align | q_valign)
                lbl.setStyleSheet("font-size: 24px; color: #39FF14; background: black; padding: 5px;")
                dro_key = next((axis for axis in ("X", "Y", "Z", "FEED", "RPM") if axis in title.upper()), title)
                self.custom_dro_labels[dro_key] = lbl
                widget_obj = grp
            elif w_type == "LED":
                lbl = QLabel(display_title, self.custom_ui_widget)
                lbl.setGeometry(int(x), int(y), int(w), int(h))
                lbl.setAlignment(q_align | q_valign)
                lbl.setStyleSheet(f"QLabel {{ {common_style} border: {border_width}px solid {border_color}; border-radius: {border_radius}px; font-weight: bold; }}")
                self.custom_status_labels[title.upper()] = lbl
                widget_obj = lbl
            elif "G-Code" in w_type:
                container = QWidget(self.custom_ui_widget)
                container.setGeometry(int(x), int(y), int(w), int(h))
                
                vbox = QVBoxLayout(container)
                vbox.setContentsMargins(0, 0, 0, 0)
                vbox.setSpacing(2)
                
                toolbar = QHBoxLayout()
                lbl = QLabel(display_title)
                lbl.setStyleSheet("color: #8e44ad; font-weight: bold;")
                toolbar.addWidget(lbl)
                
                btn_load = QPushButton("📂 Load")
                btn_load.setStyleSheet("QPushButton { background-color: #2980b9; color: white; padding: 2px 6px; border-radius: 3px; } QPushButton:pressed { background-color: #1f618d; }")
                btn_save = QPushButton("💾 Save")
                btn_save.setStyleSheet("QPushButton { background-color: #27ae60; color: white; padding: 2px 6px; border-radius: 3px; } QPushButton:pressed { background-color: #229954; }")
                btn_sync = QPushButton("📤 Sync")
                btn_sync.setStyleSheet("QPushButton { background-color: #f39c12; color: black; padding: 2px 6px; border-radius: 3px; } QPushButton:pressed { background-color: #c87f0a; }")
                btn_clear = QPushButton("🗑️ Clear")
                btn_clear.setStyleSheet("QPushButton { background-color: #c0392b; color: white; padding: 2px 6px; border-radius: 3px; } QPushButton:pressed { background-color: #922b21; }")
                
                toolbar.addWidget(btn_load)
                toolbar.addWidget(btn_save)
                toolbar.addWidget(btn_sync)
                toolbar.addWidget(btn_clear)
                toolbar.addStretch()
                
                txt = GCodeEditor()
                txt.setPlaceholderText(f"(G-Code Editor: {display_title})\n\nLoad a file from LinuxCNC or paste G-Code here.")
                txt.setStyleSheet("background-color: #1e1e1e; color: #ecf0f1; font-family: monospace; border: 1px solid #8e44ad;")
                
                vbox.addLayout(toolbar)
                vbox.addWidget(txt)
                
                btn_load.clicked.connect(lambda _, t=txt: self.load_gcode(t))
                btn_save.clicked.connect(lambda _, t=txt: self.save_gcode(t))
                btn_sync.clicked.connect(lambda _, t=txt: self.sync_gcode_to_machine("" if self.__dict__.get('_is_large_file_preview', False) else t.toPlainText()))
                btn_clear.clicked.connect(lambda _, t=txt: self.clear_gcode(t))
                txt.line_double_clicked.connect(self.run_from_line)
                widget_obj = container
            elif "3D" in w_type:
                lbl = QLabel(f"{display_title}\n(3D Toolpath Viewer)" if show_text else "", self.custom_ui_widget)
                lbl.setGeometry(int(x), int(y), int(w), int(h))
                lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
                lbl.setStyleSheet(f"background-color: #111; color: #d35400; border: {border_width}px dashed {border_color}; border-radius: {border_radius}px; {font_css}")
                widget_obj = lbl
                
            if has_shadow and widget_obj:
                shadow = QGraphicsDropShadowEffect()
                shadow.setBlurRadius(15)
                shadow.setOffset(3, 3)
                shadow.setColor(QColor(0, 0, 0, 150))
                widget_obj.setGraphicsEffect(shadow)

        self.custom_ui_widget.setMinimumSize(int(max_w) + 20, int(max_h) + 20)
        self.stack.setCurrentWidget(self.custom_scroll)

        # Add watcher logic
        self.current_ui_file = path
        self._setup_ui_watcher()

        print("[dashboard.py] Custom UI loaded successfully.")

    def _setup_ui_watcher(self):
        # Remove old paths to avoid watching multiple files
        if self.ui_file_watcher.files():
            self.ui_file_watcher.removePaths(self.ui_file_watcher.files())
        if self.current_ui_file and os.path.exists(self.current_ui_file):
            self.ui_file_watcher.addPath(self.current_ui_file)

    def on_ui_file_changed(self, path):
        print(f"[dashboard.py] UI file changed: {path}. Reloading...")
        QTimer.singleShot(200, lambda: self.load_custom_ui(path))

    def send_halcmd_setp(self, pin_name, value_str):
        if client_from_settings(self.settings) is not None:
            self.error_signal.emit("Secure mode does not permit arbitrary HAL pin writes.")
            return

        # Translate virtual UI pins to their real-time command parameters
        if pin_name.startswith("ui."):
            clean_name = pin_name.replace("ui.", "").replace(".", "_").replace("-", "_")
            target = f"linuxcnc_ui.0.{clean_name}_cmd"
        else:
            target = pin_name

        if getattr(self.settings, "local_only", False):
            try:
                self.local_api.set_hal_value(target, value_str)
            except Exception as error:
                self.error_signal.emit(f"HAL write failed for {target}: {error}")
            return

        ip = self.settings.get_setting("SSH", "ip_address", "")
        user = self.settings.get_setting("SSH", "username", "")
        if not ip or not user:
            return
        self.cmd_queue.put(f"halcmd setp {target} {value_str}")

    def switch_probe_hardware(self, index):
        agent_client = client_from_settings(self.settings)
        if agent_client is not None:
            command = "probe_source_setter" if index == 0 else "probe_source_probe"
            try:
                agent_client.machine_command(command)
            except Exception as error:
                self.error_signal.emit(f"Probe source selection failed: {error}")
                self.probe_selector.blockSignals(True)
                self.probe_selector.setCurrentIndex(1 - index)
                self.probe_selector.blockSignals(False)
                return
        else:
            self.send_halcmd_setp("lut5.0.in-2", str(index))
        if index == 0:
            self.probe_selector.setStyleSheet("background-color: #27ae60; color: white; font-weight: bold; padding: 6px; border-radius: 4px; margin-bottom: 5px;")
        else:
            self.probe_selector.setStyleSheet("background-color: #d35400; color: white; font-weight: bold; padding: 6px; border-radius: 4px; margin-bottom: 5px;")

    def run_from_line(self, line_number):
        if self.__dict__.get('_is_large_file_preview', False):
            QMessageBox.warning(
                self,
                "Run From Line",
                "Run from line is disabled in Large File Preview Mode because the editor contains a sampled preview.",
            )
            return

        if client_from_settings(self.settings) is not None:
            QMessageBox.information(
                self,
                "Run From Line Not Available",
                "Secure mode does not permit arbitrary program-line execution.",
            )
            return

        if self.is_estopped:
            QMessageBox.warning(self, "E-STOP", "Machine is in E-STOP state. Release E-STOP before running.")
            return
            
        if not self.mock_coords.get('MACHINE_ON', False):
            QMessageBox.warning(self, "Power Off", "Machine power is OFF.\n\nPlease click the 'POWER ON' button before running programs.")
            return
            
        real_line = line_number + 1
        confirm = QMessageBox.question(self, "Run From Line", f"Are you sure you want to start the program from line {real_line}?", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if confirm != QMessageBox.StandardButton.Yes:
            return
            
        ip = self.settings.get_setting("SSH", "ip_address", "")
        user = self.settings.get_setting("SSH", "username", "")
        password = self.settings.get_setting("SSH", "password", "")
        
        if not ip or not user: return
            
        # Send Python script via SSH to switch to Auto mode and execute from the specific line!
        py_code = f"import linuxcnc, time\ntry:\n    c=linuxcnc.command()\n    c.mode(linuxcnc.MODE_AUTO)\n    time.sleep(0.1)\n    c.auto(linuxcnc.AUTO_RUN, {real_line})\nexcept: pass"

        def send_cmd():
            self._send_b64_py_cmd(py_code)
        threading.Thread(target=send_cmd, daemon=True).start()
        print(f"[dashboard.py] Sent remote command to start from line {real_line}")

    def confirm_and_execute_mdi(self, mdi_cmd, action_name):
        # We bypass the confirmation for stop commands for safety!
        if mdi_cmd in ["M5", "M9"]:
            self.execute_mdi(mdi_cmd)
            return
            
        confirm = QMessageBox.question(self, "Confirm Action", f"Are you sure you want to start {action_name} ({mdi_cmd})?", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if confirm == QMessageBox.StandardButton.Yes:
            self.execute_mdi(mdi_cmd)

    def zero_axis(self, axis):
        """Set one work-coordinate axis to zero through the secure agent when enrolled."""
        agent_client = client_from_settings(self.settings)
        if agent_client is not None:
            try:
                agent_client.touch_off_axis(axis, 0.0)
                self.update_real_coords(agent_client.telemetry())
                self.update_status()
            except Exception as error:
                self.error_signal.emit(f"Zero {axis} failed: {error}")
            return
        self.execute_mdi(f"G10 L20 P0 {axis}0")

    def set_coolant(self, mode):
        """Apply one named coolant action without accepting arbitrary MDI in secure mode."""
        agent_client = client_from_settings(self.settings)
        if agent_client is not None:
            commands = {
                "mist_on": "coolant_mist_on",
                "flood_on": "coolant_flood_on",
                "off": "coolant_off",
            }
            try:
                agent_client.machine_command(commands[mode])
            except Exception as error:
                self.error_signal.emit(f"Coolant control failed: {error}")
            return

        commands = {
            "mist_on": "import linuxcnc\nc=linuxcnc.command(); c.mist(1)",
            "flood_on": "import linuxcnc\nc=linuxcnc.command(); c.flood(1)",
            "off": "import linuxcnc\nc=linuxcnc.command(); c.mist(0); c.flood(0)",
        }
        self._send_b64_py_cmd(commands[mode])

    def set_spindle_direction(self, action):
        """Apply a bounded spindle direction command without arbitrary MDI in secure mode."""
        try:
            rpm = float(self.spindle_rpm_input.text()) if action != "stop" else 0.0
        except ValueError:
            QMessageBox.warning(self, "Invalid RPM", "Enter a numeric spindle RPM.")
            return
        agent_client = client_from_settings(self.settings)
        if agent_client is not None:
            try:
                agent_client.spindle_command(action, rpm)
            except Exception as error:
                self.error_signal.emit(f"Spindle control failed: {error}")
            return
        mdi = {"forward": f"M3 S{rpm:g}", "reverse": f"M4 S{rpm:g}", "stop": "M5"}
        self.confirm_and_execute_mdi(mdi[action], f"Spindle {action.title()}")

    def stop_cycle(self):
        """Abort the active program, clear feed-hold, and safely return to manual mode."""
        agent_client = client_from_settings(self.settings)
        if agent_client is not None:
            try:
                agent_client.machine_command("abort")
                try:
                    agent_client.spindle_command("stop", 0.0)
                except Exception:
                    pass
                try:
                    agent_client.machine_command("coolant_off")
                except Exception:
                    pass
                try:
                    agent_client.machine_command("mode_manual")
                except Exception:
                    pass
            except Exception as error:
                self.error_signal.emit(f"Cycle stop failed: {error}")
        elif getattr(self.settings, "local_only", False) and self.local_api:
            try:
                command = self.local_api.command()
                try:
                    command.set_feed_hold(0)
                except Exception:
                    pass
                command.abort()
                command.wait_complete(1.0)
                try:
                    command.spindle(self.local_api.linuxcnc.SPINDLE_OFF)
                except Exception:
                    pass
                try:
                    command.flood(0)
                    command.mist(0)
                except Exception:
                    pass
                try:
                    status = self.local_api.status()
                    for _ in range(20):
                        status.poll()
                        if getattr(status, "current_vel", 0.0) == 0.0 and getattr(status, "interp_state", 1) == self.local_api.linuxcnc.INTERP_IDLE:
                            break
                        time.sleep(0.05)
                except Exception:
                    pass
                command.mode(self.local_api.linuxcnc.MODE_MANUAL)
                command.wait_complete(1.0)
                try:
                    command.reset_interpreter()
                except Exception:
                    pass
                try:
                    err_chan = self.local_api.error_channel()
                    for _ in range(100):
                        if not err_chan.poll():
                            break
                except Exception:
                    pass
            except Exception as error:
                self.error_signal.emit(f"Cycle stop failed: {error}")
        else:
            self._send_b64_py_cmd(
                "import linuxcnc, time\nc=linuxcnc.command(); s=linuxcnc.stat()\n"
                "try: c.set_feed_hold(0)\nexcept: pass\n"
                "c.abort()\n"
                "try: c.spindle(linuxcnc.SPINDLE_OFF)\nexcept: pass\n"
                "try: c.flood(0); c.mist(0)\nexcept: pass\n"
                "for _ in range(20):\n"
                "    try:\n"
                "        s.poll()\n"
                "        if getattr(s, 'current_vel', 0.0) == 0.0 and getattr(s, 'interp_state', 1) == linuxcnc.INTERP_IDLE: break\n"
                "    except: pass\n"
                "    time.sleep(0.05)\n"
                "c.mode(linuxcnc.MODE_MANUAL)\nc.wait_complete(1.0)\n"
                "try: c.reset_interpreter()\nexcept: pass\n"
                "try:\n"
                "    e = linuxcnc.error_channel()\n"
                "    for _ in range(100):\n"
                "        if not e.poll(): break\n"
                "except: pass",
                urgent=True,
            )
        if hasattr(self, 'btn_cycle_pause'):
            self.btn_cycle_pause.setText("⏸ PAUSE")
            self.btn_cycle_pause.setStyleSheet(
                "QPushButton { background-color: #f39c12; color: black; font-weight: bold; border-radius: 4px; } QPushButton:pressed { background-color: #c87f0a; }"
            )
        if self.__dict__.get('active_jogs'):
            self.active_jogs.clear()
        self.mock_coords["MODE"] = "MANUAL"
        self.mock_coords["STATUS"] = "IDLE"
        self.mock_coords["IS_PAUSED"] = False
        self.update_status()

    def start_cycle(self):
        """Start the loaded program through a typed command when enrolled."""
        if self.__dict__.get('active_jogs'):
            self.jog_button_released()
            self.active_jogs.clear()

        agent_client = client_from_settings(self.settings)
        if agent_client is not None:
            if self._program_sync_in_progress:
                self.error_signal.emit("Cycle start is unavailable while the G-code program is still being uploaded.")
                return
            mock_coords = self.__dict__.get("mock_coords", {})
            if mock_coords.get("MODE") and mock_coords.get("MODE") != "AUTO":
                QMessageBox.warning(
                    self,
                    "Mode Error",
                    "Cannot start cycle: Machine must be in AUTO mode.\nPlease select AUTO mode first.",
                )
                return
            try:
                loaded_path = self.__dict__.get('loaded_gcode_path')
                target_prog = os.path.basename(loaded_path) if loaded_path else "active_program.ngc"
                agent_client.open_program(target_prog)
                agent_client.machine_command("program_start", timeout=15.0)
            except Exception as error:
                if self.__dict__.get('error_signal'):
                    self.error_signal.emit(f"Cycle start failed: {error}")
            return
        if getattr(self.settings, "local_only", False) and self.local_api:
            try:
                command = self.local_api.command()
                status = self.local_api.status()
                status.poll()
                current_file = getattr(status, 'file', '') or ''

                loaded_path = self.__dict__.get('loaded_gcode_path')
                has_program = False
                if loaded_path and os.path.exists(loaded_path):
                    has_program = True
                elif self.__dict__.get('default_gcode_editor') and not self.default_gcode_editor.document().isEmpty():
                    has_program = True
                elif current_file and os.path.exists(current_file):
                    has_program = True

                if not has_program:
                    QMessageBox.warning(self, "No Program", "Please load a G-code program before starting cycle.")
                    return

                if getattr(status, 'task_mode', None) != self.local_api.linuxcnc.MODE_AUTO:
                    QMessageBox.warning(
                        self,
                        "Mode Error",
                        "Cannot start cycle: Machine must be in AUTO mode.\nPlease select AUTO mode first.",
                    )
                    return

                if not current_file or not os.path.exists(current_file):
                    if loaded_path and os.path.exists(loaded_path):
                        command.program_open(loaded_path)
                        command.wait_complete(2.0)
                    else:
                        local_dir = self.get_nc_files_dir()
                        local_dest = os.path.join(local_dir, "active_program.ngc")
                        editor_content = self.default_gcode_editor.toPlainText().strip() if "default_gcode_editor" in self.__dict__ else ""
                        with open(local_dest, 'w', encoding='utf-8') as f:
                            f.write(editor_content)
                        command.program_open(local_dest)
                        command.wait_complete(2.0)

                try:
                    command.set_feed_hold(0)
                except Exception:
                    pass
                command.auto(self.local_api.linuxcnc.AUTO_RUN, 0)
            except Exception as error:
                self.error_signal.emit(f"Cycle start failed: {error}")
            return

        mock_coords = self.__dict__.get("mock_coords", {})
        if mock_coords.get("MODE") and mock_coords.get("MODE") != "AUTO":
            QMessageBox.warning(
                self,
                "Mode Error",
                "Cannot start cycle: Machine must be in AUTO mode.\nPlease select AUTO mode first.",
            )
            return

        self._send_b64_py_cmd(
            "import linuxcnc, sys\n"
            "c=linuxcnc.command(); s=linuxcnc.stat(); s.poll()\n"
            "if s.task_mode != linuxcnc.MODE_AUTO: sys.exit(0)\n"
            "try: c.set_feed_hold(0)\nexcept: pass\n"
            "c.auto(linuxcnc.AUTO_RUN, 0)"
        )

    def select_machine_mode(self, mode):
        """Select Manual, MDI, or Auto mode."""
        if self.__dict__.get('active_jogs'):
            self.jog_button_released()
            self.active_jogs.clear()

        # Reject mode changes while actively running or paused
        if self.mock_coords.get('STATUS') in ('RUNNING', 'PAUSED'):
            QMessageBox.warning(
                self,
                "Mode Switch Blocked",
                "Cannot change machine mode while a program is running or paused.\nPlease stop the program first.",
            )
            return

        mode_key = str(mode).strip().lower()
        if mode_key not in ("manual", "mdi", "auto"):
            self.error_signal.emit(f"Unknown mode: {mode}")
            return

        agent_client = client_from_settings(self.settings)
        if agent_client is not None:
            try:
                agent_client.machine_command(f"mode_{mode_key}")
                self.mock_coords["MODE"] = mode_key.upper()
                self.update_status()
            except Exception as error:
                self.error_signal.emit(f"Mode selection failed: {error}")
            return

        if getattr(self.settings, "local_only", False) and self.local_api:
            try:
                status = self.local_api.status()
                status.poll()
                istate = getattr(status, 'interp_state', 1)
                if isinstance(istate, int) and istate != getattr(self.local_api.linuxcnc, 'INTERP_IDLE', 1):
                    QMessageBox.warning(
                        self,
                        "Mode Switch Blocked",
                        "Cannot change machine mode while a program is running or paused.\nPlease stop the program first.",
                    )
                    return

                mode_map = {
                    "manual": self.local_api.linuxcnc.MODE_MANUAL,
                    "mdi": self.local_api.linuxcnc.MODE_MDI,
                    "auto": self.local_api.linuxcnc.MODE_AUTO,
                }
                target_mode = mode_map[mode_key]
                command = self.local_api.command()
                command.mode(target_mode)
                command.wait_complete(1.0)
                self.mock_coords["MODE"] = mode_key.upper()
                self.update_status()
            except Exception as error:
                self.error_signal.emit(f"Mode selection failed: {error}")
            return

        legacy_code = {
            "manual": "import linuxcnc\nc=linuxcnc.command(); c.mode(linuxcnc.MODE_MANUAL)",
            "mdi": "import linuxcnc\nc=linuxcnc.command(); c.mode(linuxcnc.MODE_MDI)",
            "auto": "import linuxcnc\nc=linuxcnc.command(); c.mode(linuxcnc.MODE_AUTO)",
        }
        self._send_b64_py_cmd(legacy_code[mode_key], urgent=True)
        self.mock_coords["MODE"] = mode_key.upper()
        self.update_status()

    def pause_or_resume_cycle(self):
        """Pause or resume the program with immediate feed-hold and interpreter control."""
        agent_client = client_from_settings(self.settings)
        if agent_client is not None:
            try:
                agent_client.machine_command("program_pause_resume", timeout=15.0)
                is_currently_paused = self.mock_coords.get("STATUS") == "PAUSED"
                self.mock_coords["STATUS"] = "RUNNING" if is_currently_paused else "PAUSED"
                self.mock_coords["IS_PAUSED"] = not is_currently_paused
                self.update_status()
            except Exception as error:
                self.error_signal.emit(f"Pause/resume failed: {error}")
            return
        if getattr(self.settings, "local_only", False) and self.local_api:
            try:
                status = self.local_api.status()
                status.poll()
                command = self.local_api.command()
                is_paused = bool(
                    getattr(status, 'paused', False)
                    or getattr(status, 'task_paused', False)
                    or getattr(status, 'interp_state', 1) == self.local_api.linuxcnc.INTERP_PAUSED
                    or self.mock_coords.get("STATUS") == "PAUSED"
                )
                if is_paused:
                    try:
                        command.set_feed_hold(0)
                    except Exception:
                        pass
                    command.auto(self.local_api.linuxcnc.AUTO_RESUME)
                    self.mock_coords["STATUS"] = "RUNNING"
                    self.mock_coords["IS_PAUSED"] = False
                    if hasattr(self, 'btn_cycle_pause'):
                        self.btn_cycle_pause.setText("⏸ PAUSE")
                        self.btn_cycle_pause.setStyleSheet(
                            "QPushButton { background-color: #f39c12; color: black; font-weight: bold; border-radius: 4px; } QPushButton:pressed { background-color: #c87f0a; }"
                        )
                else:
                    try:
                        command.set_feed_hold(1)
                    except Exception:
                        pass
                    command.auto(self.local_api.linuxcnc.AUTO_PAUSE)
                    self.mock_coords["STATUS"] = "PAUSED"
                    self.mock_coords["IS_PAUSED"] = True
                    if hasattr(self, 'btn_cycle_pause'):
                        self.btn_cycle_pause.setText("▶ RESUME")
                        self.btn_cycle_pause.setStyleSheet(
                            "QPushButton { background-color: #3498db; color: white; font-weight: bold; border-radius: 4px; } QPushButton:pressed { background-color: #2980b9; }"
                        )
                self.update_status()
            except Exception as error:
                self.error_signal.emit(f"Pause/resume failed: {error}")
            return
        self._send_b64_py_cmd(
            "import linuxcnc\n"
            "c=linuxcnc.command(); s=linuxcnc.stat(); s.poll()\n"
            "is_paused = bool(getattr(s, 'paused', False) or getattr(s, 'task_paused', False) or getattr(s, 'interp_state', 1) == linuxcnc.INTERP_PAUSED)\n"
            "if is_paused:\n"
            "    try: c.set_feed_hold(0)\n"
            "    except: pass\n"
            "    c.auto(linuxcnc.AUTO_RESUME)\n"
            "else:\n"
            "    try: c.set_feed_hold(1)\n"
            "    except: pass\n"
            "    c.auto(linuxcnc.AUTO_PAUSE)\n",
            urgent=True,
        )
        is_currently_paused = self.mock_coords.get("STATUS") == "PAUSED"
        self.mock_coords["STATUS"] = "RUNNING" if is_currently_paused else "PAUSED"
        self.mock_coords["IS_PAUSED"] = not is_currently_paused
        self.update_status()

    def execute_mdi(self, mdi_cmd):
        if client_from_settings(self.settings) is not None:
            QMessageBox.information(
                self,
                "MDI Not Available",
                "Secure mode does not permit arbitrary MDI commands. Use a named, approved machine operation instead.",
            )
            return

        if self.is_estopped:
            QMessageBox.warning(self, "E-STOP", "Machine is in E-STOP state. Release E-STOP before running.")
            return

        if not self.mock_coords.get('MACHINE_ON', False):
            QMessageBox.warning(self, "Power Off", "Machine power is OFF.\n\nPlease click the 'POWER ON' button before sending MDI commands.")
            return

        if self.mock_coords.get('STATUS') in ('RUNNING', 'PAUSED'):
            QMessageBox.warning(self, "Interpreter Busy", "Cannot execute MDI command while a program is running or paused.")
            return

        if getattr(self.settings, "local_only", False):
            try:
                linuxcnc = self.local_api.linuxcnc
                status = self.local_api.status()
                status.poll()
                if status.task_state == linuxcnc.STATE_ESTOP:
                    raise RuntimeError("E-stop is active.")
                if status.task_state != linuxcnc.STATE_ON:
                    raise RuntimeError("Machine power is off.")
                istate = getattr(status, 'interp_state', 1)
                if isinstance(istate, int) and istate != getattr(linuxcnc, 'INTERP_IDLE', 1):
                    raise RuntimeError("Cannot execute MDI command while a program is running or paused.")
                command = self.local_api.command()
                if status.task_mode != linuxcnc.MODE_MDI:
                    command.mode(linuxcnc.MODE_MDI)
                    command.wait_complete(1.0)
                command.mdi(mdi_cmd)
                command.wait_complete(1.0)
                self.mock_coords["MODE"] = "MDI"
                self.update_status()
            except Exception as error:
                self.error_signal.emit(f"MDI command failed: {error}")
                return
            print(f"[dashboard.py] Executed MDI: {mdi_cmd}")
            return

        ip = self.settings.get_setting("SSH", "ip_address", "")
        user = self.settings.get_setting("SSH", "username", "")

        escaped_mdi_cmd = mdi_cmd.replace("'", "\\'")
        py_code = f"""import linuxcnc, time, sys
try:
    c = linuxcnc.command()
    s = linuxcnc.stat()
    e = linuxcnc.error_channel()

    def wait_idle(timeout=10):
        start_time = time.time()
        # First, check for any existing errors
        err = e.poll()
        if err:
            sys.stderr.write(str(err[1]))
            sys.exit(1)

        while time.time() - start_time < timeout:
            s.poll()
            if s.interp_state == linuxcnc.INTERP_IDLE:
                return # Success
            
            # Check for errors while waiting
            err = e.poll()
            if err:
                sys.stderr.write(str(err[1]))
                sys.exit(1)
            
            if s.task_state == linuxcnc.STATE_ESTOP:
                sys.stderr.write("Machine is in E-STOP state.")
                sys.exit(1)

            time.sleep(0.05)
            
        sys.stderr.write("MDI command timed out while waiting for completion.")
        sys.exit(1)

    s.poll()
    if s.task_mode != linuxcnc.MODE_MDI:
        c.mode(linuxcnc.MODE_MDI)
        wait_idle()

    c.mdi('{escaped_mdi_cmd}')
    wait_idle()

except linuxcnc.error as e:
    sys.stderr.write(f"LinuxCNC Error: {{e}}")
    sys.exit(1)
except Exception as e:
    sys.stderr.write(f"An unexpected error occurred: {{e}}")
    sys.exit(1)
"""

        if ip and user:
            self._send_b64_py_cmd(py_code)
        else:
            self._send_b64_py_cmd(py_code, urgent=True)
        print(f"[dashboard.py] Executed MDI: {mdi_cmd}")

    def run_mdi_from_input(self):
        cmd = self.mdi_input.text().strip()
        if cmd:
            self.execute_mdi(cmd)
            self.mdi_input.add_to_history(cmd)
            self.mdi_input.clear() # Clear the box so it's ready for the next command

    def load_tool(self):
        tool_num = getattr(self, 'input_tool_num', QLineEdit()).text().strip()
        if tool_num.isdigit():
            agent_client = client_from_settings(self.settings)
            if agent_client is not None:
                try:
                    agent_client.change_tool(int(tool_num))
                except Exception as error:
                    self.error_signal.emit(f"Tool change failed: {error}")
                return
            self.execute_mdi(f"T{tool_num} M6 G43")
        else:
            QMessageBox.warning(self, "Invalid Tool", "Please enter a valid numeric tool number.")

    def trigger_probing(self, routine_name):
        try: probe_diam = float(getattr(self, 'input_probe_diam', QLineEdit()).text() or 0.0)
        except Exception: probe_diam = 0.0
        try: xy_clear = float(getattr(self, 'input_xy_clearance', QLineEdit()).text() or 0.0)
        except Exception: xy_clear = 0.0
        try: z_drop = float(getattr(self, 'input_z_drop', QLineEdit()).text() or 0.0)
        except Exception: z_drop = 0.0
        try: search_dist = float(getattr(self, 'input_search_dist', QLineEdit()).text() or 0.0)
        except Exception: search_dist = 0.0
        
        try: setter_ref_z = float(self.input_tool_setter.text() or 0.0)
        except ValueError: setter_ref_z = 0.0
        
        setter_x = float(self.settings.get_setting("PROBING", "tool_setter_x", 0.0))
        setter_y = float(self.settings.get_setting("PROBING", "tool_setter_y", 0.0))
        
        probe_vel = float(self.settings.get_setting("PROBING", "probe_vel", 100.0))
        setter_vel = float(self.settings.get_setting("PROBING", "setter_vel", 150.0))

        if routine_name == "probe_z":
            agent_client = client_from_settings(self.settings)
            if agent_client is not None:
                try:
                    target = float(self.input_raw_probe_distance.text())
                except (AttributeError, ValueError):
                    self.error_signal.emit("Probe Z requires a numeric downward travel distance.")
                    return
                if target >= 0:
                    self.error_signal.emit("Probe Z requires a negative downward travel distance.")
                    return
                confirm = QMessageBox.question(
                    self,
                    "Confirm Probe Z",
                    f"Probe downward {abs(target):g} units at {probe_vel:g} velocity, then retract 2 units?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                )
                if confirm != QMessageBox.StandardButton.Yes:
                    return
                try:
                    agent_client.probe_z(target, probe_vel)
                except Exception as error:
                    self.error_signal.emit(f"Probe Z failed: {error}")
                return

        if routine_name == "tool_height":
            self.probe_selector.setCurrentIndex(0)
            print("[dashboard.py] Triggering Tool Height Probe")
            agent_client = client_from_settings(self.settings)
            if agent_client is not None:
                try:
                    agent_client.move_tool_setter(setter_x, setter_y, setter_ref_z)
                except Exception as error:
                    self.error_signal.emit(f"Tool height probe failed: {error}")
                return
            py_code = f"""import linuxcnc, time, sys, os
try:
    hal.set_p("lut5.0.in-2", "0") #Force Touch Plate Mode
    time.sleep(0.2)
    c = linuxcnc.command(); s = linuxcnc.stat()
    e = getattr(linuxcnc, 'error_channel', lambda: None)()
    def wait_idle(timeout=120):
        start_time = time.time()
        err = e.poll()
        if err:
            sys.stderr.write(str(err[1]))
            sys.exit(1)
        while time.time() - start_time < timeout:
            s.poll()
            if s.interp_state == linuxcnc.INTERP_IDLE: return
            err = e.poll()
            if err:
                sys.stderr.write(str(err[1]))
                sys.exit(1)
            if s.task_state == linuxcnc.STATE_ESTOP:
                sys.stderr.write("Machine is in E-STOP state.")
                sys.exit(1)
            time.sleep(0.05)
        sys.stderr.write("MDI command timed out.")
        sys.exit(1)
    s.poll()
    if s.task_mode != linuxcnc.MODE_MDI: c.mode(linuxcnc.MODE_MDI); wait_idle()
    def run_mdi(cmd_str):
        c.mdi(cmd_str); time.sleep(0.05); wait_idle()
    
    run_mdi("G90")
    run_mdi("G53 G0 Z0")
    run_mdi(f"G53 G0 X{setter_x} Y{setter_y}")
    
    s.poll()
    if getattr(s, 'probe_val', 0):
        run_mdi("G91")
        run_mdi(f"G38.4 Z20.0 F{setter_vel}")
        run_mdi("G90")
        
    z_min = -1000.0
    try: z_min = s.axis[2]['min_position_limit']
    except: pass
    
    z_offset = s.g5x_offset[2] + s.g92_offset[2] + s.tool_offset[2]
    z_target = z_min - z_offset + 0.1
    
    s.poll()
    current_z = s.position[2] - z_offset
    if z_target >= current_z:
        z_target = current_z - 200.0
        
    run_mdi(f"G38.2 Z{{z_target:.4f}} F{setter_vel * 4.0}")
    s.poll()
    if not s.probe_tripped:
        report_error("Probe failed: fast search did not make contact.")
        sys.exit(1)
        
    run_mdi("G91"); run_mdi("G0 Z2.0"); run_mdi("G90")
    run_mdi(f"G38.2 Z{{z_target:.4f}} F{setter_vel}")
    s.poll()
    if s.probe_tripped:
        tool_num = s.tool_in_spindle
        if tool_num == 0:
            # Cannot set length for T0, prompt user to load a tool
            report_error("Cannot set tool length because no tool is loaded (T0). Load a tool and retry.")
            sys.exit(1)
            
        tool_length = s.probed_position[2] - {setter_ref_z}
        run_mdi(f"G10 L1 P{{tool_num}} Z{{tool_length:.4f}}")
        c.load_tool_table()
        time.sleep(0.1)
        c.mdi(f"G43 H{{tool_num}}")
        wait_idle()
        run_mdi("G90")
        run_mdi("G53 G0 Z0")
except Exception as e: pass"""
            self._send_b64_py_cmd(py_code, timeout=120)
            return

        self.probe_selector.setCurrentIndex(1)
        
        agent_client = client_from_settings(self.settings)
        if agent_client is not None:
            macro_map = {
                "out_boss": "O106",
                "in_hole": "O107",
                "out_bl": "O114", "out_b": "O115", "out_br": "O116",
                "out_l": "O117", "out_r": "O118",
                "out_fl": "O119", "out_f": "O120", "out_fr": "O121",
                "in_bl": "O122", "in_b": "O123", "in_br": "O124",
                "in_l": "O125", "in_r": "O126",
                "in_fl": "O127", "in_f": "O128", "in_fr": "O129",
            }
            ocode = macro_map.get(routine_name)
            if ocode:
                try:
                    agent_client.run_subroutine(ocode)
                except Exception as error:
                    self.error_signal.emit(f"Probing subroutine failed: {error}")
                return

        pr = abs(probe_diam / 2.0)  #Calculate Probe Radius
        vel = abs(probe_vel)  #Set Velocity
        search = abs(search_dist)
        clear = abs(xy_clear)
        zdrop = abs(z_drop)

        def probe_out(ax, clr_sgn, srch_sgn):
            return f"""
    run_mdi("G91")
    run_mdi(f"G0 {{'{ax}'}}{{clear * {clr_sgn}}}")
    run_mdi(f"G38.3 Z-{{zdrop}} F{{vel}}")
    s.poll()
    if s.probe_tripped:
        run_mdi("G90")
        run_mdi(f"G53 G0 Z{{start_z}}")
        run_mdi(f"G53 G0 X{{start_x}} Y{{start_y}}")
        sys.exit(0)
    run_mdi(f"G38.2 {{'{ax}'}}{{search * {srch_sgn}}} F{{vel}}")
    s.poll()
    if not s.probe_tripped:
        run_mdi("G90")
        run_mdi(f"G53 G0 Z{{start_z}}")
        run_mdi(f"G53 G0 X{{start_x}} Y{{start_y}}")
        sys.exit(0)
    run_mdi(f"G10 L20 P0 {{'{ax}'}}{{pr if {srch_sgn} < 0 else -pr}}")
    run_mdi(f"G0 {{'{ax}'}}{{-search/4.0 * {srch_sgn}}}")
    run_mdi("G90")
    run_mdi(f"G53 G0 Z{{start_z}}")
    run_mdi(f"G53 G0 X{{start_x}} Y{{start_y}}")
"""

        def probe_in(ax, srch_sgn):
            return f"""
    run_mdi("G91")
    run_mdi(f"G38.3 Z-{{zdrop}} F{{vel}}")
    s.poll()
    if s.probe_tripped:
        run_mdi("G90")
        run_mdi(f"G53 G0 X{{start_x}} Y{{start_y}}")
        run_mdi(f"G53 G0 Z{{start_z}}")
        sys.exit(0)
    run_mdi(f"G38.2 {{'{ax}'}}{{search * {srch_sgn}}} F{{vel}}")
    s.poll()
    if not s.probe_tripped:
        run_mdi("G90")
        run_mdi(f"G53 G0 X{{start_x}} Y{{start_y}}")
        run_mdi(f"G53 G0 Z{{start_z}}")
        sys.exit(0)
    run_mdi(f"G10 L20 P0 {{'{ax}'}}{{pr if {srch_sgn} < 0 else -pr}}")
    run_mdi("G90")
    run_mdi(f"G53 G0 X{{start_x}} Y{{start_y}}")
    run_mdi(f"G53 G0 Z{{start_z}}")
"""

        def probe_center(ctype):
            if ctype == "hole":
                return """
    run_mdi("G91")
    run_mdi(f"G38.3 Z-{zdrop} F{vel}")
    s.poll()
    if s.probe_tripped:
        run_mdi("G90")
        run_mdi(f"G53 G0 X{start_x} Y{start_y}")
        run_mdi(f"G53 G0 Z{start_z}")
        sys.exit(0)
    run_mdi(f"G38.2 X{search} F{vel}")
    s.poll()
    if not s.probe_tripped:
        run_mdi("G90")
        run_mdi(f"G53 G0 X{start_x} Y{start_y}")
        run_mdi(f"G53 G0 Z{start_z}")
        sys.exit(0)
    x1 = s.probed_position[0]
    run_mdi("G90")
    run_mdi(f"G53 G0 X{start_x}")
    run_mdi(f"G53 G0 Z{start_z}")
    
    run_mdi("G91")
    run_mdi(f"G38.3 Z-{zdrop} F{vel}")
    s.poll()
    if s.probe_tripped:
        run_mdi("G90")
        run_mdi(f"G53 G0 X{start_x} Y{start_y}")
        run_mdi(f"G53 G0 Z{start_z}")
        sys.exit(0)
    run_mdi(f"G38.2 X{-search} F{vel}")
    s.poll()
    if not s.probe_tripped:
        run_mdi("G90")
        run_mdi(f"G53 G0 X{start_x} Y{start_y}")
        run_mdi(f"G53 G0 Z{start_z}")
        sys.exit(0)
    x2 = s.probed_position[0]
    run_mdi("G90")
    run_mdi(f"G53 G0 X{start_x}")
    run_mdi(f"G53 G0 Z{start_z}")
    
    cx = (x1 + x2) / 2.0
    run_mdi(f"G53 G0 X{cx}")
    run_mdi("G10 L20 P0 X0")
    
    run_mdi("G91")
    run_mdi(f"G38.3 Z-{zdrop} F{vel}")
    s.poll()
    if s.probe_tripped:
        run_mdi("G90")
        run_mdi(f"G53 G0 X{cx} Y{start_y}")
        run_mdi(f"G53 G0 Z{start_z}")
        sys.exit(0)
    run_mdi(f"G38.2 Y{search} F{vel}")
    s.poll()
    if not s.probe_tripped:
        run_mdi("G90")
        run_mdi(f"G53 G0 X{cx} Y{start_y}")
        run_mdi(f"G53 G0 Z{start_z}")
        sys.exit(0)
    y1 = s.probed_position[1]
    run_mdi("G90")
    run_mdi(f"G53 G0 Y{start_y}")
    run_mdi(f"G53 G0 Z{start_z}")
    
    run_mdi("G91")
    run_mdi(f"G38.3 Z-{zdrop} F{vel}")
    s.poll()
    if s.probe_tripped:
        run_mdi("G90")
        run_mdi(f"G53 G0 X{cx} Y{start_y}")
        run_mdi(f"G53 G0 Z{start_z}")
        sys.exit(0)
    run_mdi(f"G38.2 Y{-search} F{vel}")
    s.poll()
    if not s.probe_tripped:
        run_mdi("G90")
        run_mdi(f"G53 G0 X{cx} Y{start_y}")
        sys.exit(0)
    y2 = s.probed_position[1]
    run_mdi("G90")
    run_mdi(f"G53 G0 Y{start_y}")
    run_mdi(f"G53 G0 Z{start_z}")
    
    cy = (y1 + y2) / 2.0
    run_mdi(f"G53 G0 Y{cy}")
    run_mdi("G10 L20 P0 Y0")
    run_mdi(f"G53 G0 X{cx} Y{cy}")
"""
            else: # boss
                return """
    run_mdi("G90")
    run_mdi(f"G53 G0 X{start_x + clear}")
    run_mdi("G91")
    run_mdi(f"G38.3 Z-{zdrop} F{vel}")
    s.poll(); 
    if s.probe_tripped:
        run_mdi("G90")
        run_mdi(f"G53 G0 Z{start_z}")
        run_mdi(f"G53 G0 X{start_x} Y{start_y}")
        sys.exit(0)
    run_mdi(f"G38.2 X-{search} F{vel}")
    s.poll(); 
    if not s.probe_tripped:
        run_mdi("G90")
        run_mdi(f"G53 G0 Z{start_z}")
        run_mdi(f"G53 G0 X{start_x} Y{start_y}")
        sys.exit(0)
    x1 = s.probed_position[0]
    run_mdi(f"G0 X{search/4.0}")
    run_mdi("G90")
    run_mdi(f"G53 G0 Z{start_z}")
    
    run_mdi("G90")
    run_mdi(f"G53 G0 X{start_x - clear}")
    run_mdi("G91")
    run_mdi(f"G38.3 Z-{zdrop} F{vel}")
    s.poll(); 
    if s.probe_tripped:
        run_mdi("G90")
        run_mdi(f"G53 G0 Z{start_z}")
        run_mdi(f"G53 G0 X{start_x} Y{start_y}")
        sys.exit(0)
    run_mdi(f"G38.2 X{search} F{vel}")
    s.poll(); 
    if not s.probe_tripped:
        run_mdi("G90")
        run_mdi(f"G53 G0 Z{start_z}")
        run_mdi(f"G53 G0 X{start_x} Y{start_y}")
        sys.exit(0)
    x2 = s.probed_position[0]
    run_mdi(f"G0 X{-search/4.0}")
    run_mdi("G90")
    run_mdi(f"G53 G0 Z{start_z}")
    
    run_mdi("G90")
    cx = (x1 + x2) / 2.0
    run_mdi(f"G53 G0 X{cx}")
    run_mdi("G10 L20 P0 X0")
    
    run_mdi(f"G53 G0 X{cx} Y{start_y + clear}")
    run_mdi("G91")
    run_mdi(f"G38.3 Z-{zdrop} F{vel}")
    s.poll(); 
    if s.probe_tripped:
        run_mdi("G90")
        run_mdi(f"G53 G0 Z{start_z}")
        run_mdi(f"G53 G0 X{cx} Y{start_y}")
        sys.exit(0)
    run_mdi(f"G38.2 Y-{search} F{vel}")
    s.poll(); 
    if not s.probe_tripped:
        run_mdi("G90")
        run_mdi(f"G53 G0 Z{start_z}")
        run_mdi(f"G53 G0 X{cx} Y{start_y}")
        sys.exit(0)
    y1 = s.probed_position[1]
    run_mdi(f"G0 Y{search/4.0}")
    run_mdi("G90")
    run_mdi(f"G53 G0 Z{start_z}")
    
    run_mdi("G90")
    run_mdi(f"G53 G0 X{cx} Y{start_y - clear}")
    run_mdi("G91")
    run_mdi(f"G38.3 Z-{zdrop} F{vel}")
    s.poll(); 
    if s.probe_tripped:
        run_mdi("G90")
        run_mdi(f"G53 G0 Z{start_z}")
        run_mdi(f"G53 G0 X{cx} Y{start_y}")
        sys.exit(0)
    run_mdi(f"G38.2 Y{search} F{vel}")
    s.poll(); 
    if not s.probe_tripped:
        run_mdi("G90")
        run_mdi(f"G53 G0 Z{start_z}")
        run_mdi(f"G53 G0 X{cx} Y{start_y}")
        sys.exit(0)
    y2 = s.probed_position[1]
    run_mdi(f"G0 Y{-search/4.0}")
    run_mdi("G90")
    run_mdi(f"G53 G0 Z{start_z}")
    
    run_mdi("G90")
    cy = (y1 + y2) / 2.0
    run_mdi(f"G53 G0 Y{cy}")
    run_mdi("G10 L20 P0 Y0")
    run_mdi(f"G53 G0 X{cx} Y{cy}")
"""

        def probe_z():
            return """
    run_mdi("G91")
    run_mdi(f"G38.2 Z-{search} F{vel}")
    s.poll()
    if not s.probe_tripped:
        run_mdi("G90")
        run_mdi(f"G53 G0 Z{start_z}")
        sys.exit(0)
    run_mdi("G10 L20 P0 Z0")
    run_mdi("G90")
    run_mdi(f"G53 G0 Z{start_z}")
"""

        logic = ""
        final_move = ""
        if routine_name == "out_bl": 
            logic = probe_out('Y', 1, -1) + probe_out('X', -1, 1)
            final_move = '    run_mdi("G90")\n    run_mdi("G0 X0 Y0")'
        elif routine_name == "out_b": 
            logic = probe_out('Y', 1, -1)
            final_move = '    run_mdi("G90")\n    run_mdi("G0 Y0")'
        elif routine_name == "out_br": 
            logic = probe_out('Y', 1, -1) + probe_out('X', 1, -1)
            final_move = '    run_mdi("G90")\n    run_mdi("G0 X0 Y0")'
        elif routine_name == "out_l": 
            logic = probe_out('X', -1, 1)
            final_move = '    run_mdi("G90")\n    run_mdi("G0 X0")'
        elif routine_name == "out_boss": 
            logic = probe_center('boss')
            final_move = '    run_mdi("G90")\n    run_mdi("G0 X0 Y0")'
        elif routine_name == "out_r": 
            logic = probe_out('X', 1, -1)
            final_move = '    run_mdi("G90")\n    run_mdi("G0 X0")'
        elif routine_name == "out_fl": 
            logic = probe_out('Y', -1, 1) + probe_out('X', -1, 1)
            final_move = '    run_mdi("G90")\n    run_mdi("G0 X0 Y0")'
        elif routine_name == "out_f": 
            logic = probe_out('Y', -1, 1)
            final_move = '    run_mdi("G90")\n    run_mdi("G0 Y0")'
        elif routine_name == "out_fr": 
            logic = probe_out('Y', -1, 1) + probe_out('X', 1, -1)
            final_move = '    run_mdi("G90")\n    run_mdi("G0 X0 Y0")'
        
        elif routine_name == "in_bl": 
            logic = probe_in('Y', 1) + probe_in('X', -1)
            final_move = '    run_mdi("G90")\n    run_mdi("G0 X0 Y0")'
        elif routine_name == "in_b": 
            logic = probe_in('Y', 1)
            final_move = '    run_mdi("G90")\n    run_mdi("G0 Y0")'
        elif routine_name == "in_br": 
            logic = probe_in('Y', 1) + probe_in('X', 1)
            final_move = '    run_mdi("G90")\n    run_mdi("G0 X0 Y0")'
        elif routine_name == "in_l": 
            logic = probe_in('X', -1)
            final_move = '    run_mdi("G90")\n    run_mdi("G0 X0")'
        elif routine_name == "in_hole": 
            logic = probe_center('hole')
            final_move = '    run_mdi("G90")\n    run_mdi("G0 X0 Y0")'
        elif routine_name == "in_r": 
            logic = probe_in('X', 1)
            final_move = '    run_mdi("G90")\n    run_mdi("G0 X0")'
        elif routine_name == "in_fl": 
            logic = probe_in('Y', -1) + probe_in('X', -1)
            final_move = '    run_mdi("G90")\n    run_mdi("G0 X0 Y0")'
        elif routine_name == "in_f": 
            logic = probe_in('Y', -1)
            final_move = '    run_mdi("G90")\n    run_mdi("G0 Y0")'
        elif routine_name == "in_fr": 
            logic = probe_in('Y', -1) + probe_in('X', 1)
            final_move = '    run_mdi("G90")\n    run_mdi("G0 X0 Y0")'
        
        elif routine_name == "probe_z": logic = probe_z()
        else: return        
       
        
       

        py_code = f"""import linuxcnc, time, sys, os
try:
    hal.set_p("lut5.0.in-2", "1")
    time.sleep(0.2)
    c = linuxcnc.command(); s = linuxcnc.stat()
    e = linuxcnc.error_channel()
    e = getattr(linuxcnc, 'error_channel', lambda: None)()
    def wait_idle(timeout=120):
        start_time = time.time()
        err = e.poll()
        if err:
            sys.stderr.write(str(err[1]))
            sys.exit(1)
        while time.time() - start_time < timeout:
            s.poll()
            if s.interp_state == linuxcnc.INTERP_IDLE: return
            err = e.poll()
            if err:
                sys.stderr.write(str(err[1]))
                sys.exit(1)
            if s.task_state == linuxcnc.STATE_ESTOP:
                sys.stderr.write("Machine is in E-STOP state.")
                sys.exit(1)
            time.sleep(0.05)
        sys.stderr.write("MDI command timed out.")
        sys.exit(1)
    s.poll()
    if s.task_mode != linuxcnc.MODE_MDI: c.mode(linuxcnc.MODE_MDI); wait_idle()
    def run_mdi(cmd_str):
        c.mdi(cmd_str); time.sleep(0.05); wait_idle()
        
    s.poll()
    if getattr(s, 'probe_val', 0):
        sys.exit(0)
        
    start_x = s.actual_position[0]
    start_y = s.actual_position[1]
    start_z = s.actual_position[2]
    
    pr = {pr}
    clear = {clear}
    zdrop = {zdrop}
    search = {search}
    vel = {vel}
{logic}
{final_move}
except Exception as e: pass"""
        
        self._send_b64_py_cmd(py_code, timeout=120)

    def open_probing_tab(self):
        parent_app = getattr(self, 'parent_app', None)
        if parent_app and hasattr(parent_app, 'tabs') and hasattr(parent_app, 'probing_tab'):
            parent_app.tabs.setCurrentWidget(parent_app.probing_tab)

    def move_to_tool_setter(self):
        if self.is_estopped:
            QMessageBox.warning(self, "E-STOP", "Machine is in E-STOP state. Release E-STOP before running.")
            return
            
        if not self.mock_coords.get('MACHINE_ON', False):
            QMessageBox.warning(self, "Power Off", "Machine power is OFF.\n\nPlease click the 'POWER ON' button before moving.")
            return

        remote_homed_count = int(self.mock_coords.get('HOMED_COUNT', 0))
        remote_joint_count = int(self.mock_coords.get('JOINTS', 0))
        if remote_joint_count > 0 and remote_homed_count < remote_joint_count:
            QMessageBox.warning(self, "Homing Required", "Please home all axes before moving to tool setter position.")
            return

        if self.mock_coords.get('STATUS') in ('RUNNING', 'PAUSED'):
            QMessageBox.warning(self, "Interpreter Busy", "Cannot move while a program is running or paused.\nPlease stop the program first.")
            return

        setter_x = float(self.settings.get_setting("PROBING", "tool_setter_x", 0.0))
        setter_y = float(self.settings.get_setting("PROBING", "tool_setter_y", 0.0))
        setter_z = float(self.settings.get_setting("PROBING", "tool_setter_z", 0.0))
        
        confirm = QMessageBox.question(self, "Confirm Move", 
                                       f"Safely retract Z to absolute {setter_z}, then move to Tool Change location (X:{setter_x}, Y:{setter_y})?", 
                                       QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
                                       
        if confirm == QMessageBox.StandardButton.Yes:
            print(f"[dashboard.py] Moving to Tool Setter Location: X{setter_x} Y{setter_y}")
            agent_client = client_from_settings(self.settings)
            if agent_client is not None:
                try:
                    agent_client.move_tool_setter(setter_x, setter_y, setter_z)
                except Exception as error:
                    self.error_signal.emit(f"Tool-setter move failed: {error}")
                return

            if getattr(self.settings, "local_only", False) and self.local_api:
                try:
                    status = self.local_api.status()
                    status.poll()
                    if status.task_state != self.local_api.linuxcnc.STATE_ON:
                        QMessageBox.warning(self, "Power Off", "Machine power is OFF.\nPlease click 'POWER ON' before moving.")
                        return
                    if status.interp_state != self.local_api.linuxcnc.INTERP_IDLE:
                        QMessageBox.warning(self, "Interpreter Busy", "Cannot move while interpreter is busy.")
                        return
                    command = self.local_api.command()
                    if status.task_mode != self.local_api.linuxcnc.MODE_MDI:
                        command.mode(self.local_api.linuxcnc.MODE_MDI)
                        command.wait_complete(1.0)
                    command.mdi(f"G53 G0 Z{setter_z}")
                    command.wait_complete(30.0)
                    command.mdi(f"G53 G0 X{setter_x} Y{setter_y}")
                    command.wait_complete(30.0)
                    self.mock_coords["MODE"] = "MDI"
                    self.update_status()
                except Exception as error:
                    self.error_signal.emit(f"Tool-setter move failed: {error}")
                return

            py_code = f"""import linuxcnc, time, sys
try:
    c = linuxcnc.command(); s = linuxcnc.stat()
    def wait_idle(timeout=30):
        start_time = time.time()
        while time.time() - start_time < timeout:
            s.poll()
            if s.interp_state == linuxcnc.INTERP_IDLE: return
            if s.task_state == linuxcnc.STATE_ESTOP:
                sys.stderr.write("Machine is in E-STOP state.")
                sys.exit(1)
            time.sleep(0.05)
        sys.stderr.write("MDI command timed out.")
        sys.exit(1)
            
    s.poll()
    if s.task_mode != linuxcnc.MODE_MDI:
        c.mode(linuxcnc.MODE_MDI)
        wait_idle()
        
    c.mdi(f"G53 G0 Z{setter_z}")
    time.sleep(0.1); wait_idle()
    c.mdi("G53 G0 X{setter_x} Y{setter_y}")
except Exception as e: pass"""
            self._send_b64_py_cmd(py_code)

    def park_machine(self):
        if self.is_estopped:
            QMessageBox.warning(self, "E-STOP", "Machine is in E-STOP state. Release E-STOP before running.")
            return
            
        if not self.mock_coords.get('MACHINE_ON', False):
            QMessageBox.warning(self, "Power Off", "Machine power is OFF.\n\nPlease click the 'POWER ON' button before parking.")
            return

        remote_homed_count = int(self.mock_coords.get('HOMED_COUNT', 0))
        remote_joint_count = int(self.mock_coords.get('JOINTS', 0))
        if remote_joint_count > 0 and remote_homed_count < remote_joint_count:
            QMessageBox.warning(self, "Homing Required", "Please home all axes before moving to park position.")
            return

        if self.mock_coords.get('STATUS') in ('RUNNING', 'PAUSED'):
            QMessageBox.warning(self, "Interpreter Busy", "Cannot park machine while a program is running or paused.\nPlease stop the program first.")
            return

        park_x = float(self.settings.get_setting("LOCATIONS", "park_x", 0.0))
        park_y = float(self.settings.get_setting("LOCATIONS", "park_y", 0.0))
        park_z = float(self.settings.get_setting("LOCATIONS", "park_z", 0.0))
        
        confirm = QMessageBox.question(self, "Confirm Park", 
                                       f"Safely retract Z to absolute {park_z}, then move to Park location (X:{park_x}, Y:{park_y})?", 
                                       QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
                                       
        if confirm == QMessageBox.StandardButton.Yes:
            print(f"[dashboard.py] Parking Machine: X{park_x} Y{park_y}")
            agent_client = client_from_settings(self.settings)
            if agent_client is not None:
                try:
                    agent_client.move_tool_setter(park_x, park_y, park_z)
                except Exception as error:
                    self.error_signal.emit(f"Park move failed: {error}")
                return

            if getattr(self.settings, "local_only", False) and self.local_api:
                try:
                    status = self.local_api.status()
                    status.poll()
                    if status.task_state != self.local_api.linuxcnc.STATE_ON:
                        QMessageBox.warning(self, "Power Off", "Machine power is OFF.\nPlease click 'POWER ON' before parking.")
                        return
                    if status.interp_state != self.local_api.linuxcnc.INTERP_IDLE:
                        QMessageBox.warning(self, "Interpreter Busy", "Cannot park machine while interpreter is busy.")
                        return
                    command = self.local_api.command()
                    if status.task_mode != self.local_api.linuxcnc.MODE_MDI:
                        command.mode(self.local_api.linuxcnc.MODE_MDI)
                        command.wait_complete(1.0)
                    command.mdi(f"G53 G0 Z{park_z}")
                    command.wait_complete(30.0)
                    command.mdi(f"G53 G0 X{park_x} Y{park_y}")
                    command.wait_complete(30.0)
                    self.mock_coords["MODE"] = "MDI"
                    self.update_status()
                except Exception as error:
                    self.error_signal.emit(f"Park move failed: {error}")
                return

            py_code = f"""import linuxcnc, time, sys
try:
    c = linuxcnc.command(); s = linuxcnc.stat()
    def wait_idle(timeout=30):
        start_time = time.time()
        while time.time() - start_time < timeout:
            s.poll()
            if s.interp_state == linuxcnc.INTERP_IDLE: return
            if s.task_state == linuxcnc.STATE_ESTOP:
                sys.stderr.write("Machine is in E-STOP state.")
                sys.exit(1)
            time.sleep(0.05)
        sys.stderr.write("MDI command timed out.")
        sys.exit(1)
            
    s.poll()
    if s.task_mode != linuxcnc.MODE_MDI:
        c.mode(linuxcnc.MODE_MDI)
        wait_idle()
        
    c.mdi(f"G53 G0 Z{park_z}")
    time.sleep(0.1); wait_idle()
    c.mdi("G53 G0 X{park_x} Y{park_y}")
except Exception as e: pass"""
            self._send_b64_py_cmd(py_code)

    def _send_linuxcnc_py_cmd(self, cmd, timeout=5):
        self.cmd_queue.put((cmd, timeout))

    def _execute_local_linuxcnc_code(self, source):
        try:
            self.local_api.execute_application_command(source)
        except SystemExit as error:
            if error.code not in (None, 0):
                self.error_signal.emit(f"LinuxCNC command exited with status {error.code}.")
        except Exception as error:
            self.error_signal.emit(f"LinuxCNC command failed: {error}")

    def _cmd_worker(self):
        while True:
            cmd = self.cmd_queue.get()
            if cmd is None: break
            
            # Unpack tuple
            command_type = "shell"
            if isinstance(cmd, tuple) and len(cmd) == 3:
                command_type, command, timeout = cmd
            elif isinstance(cmd, tuple):
                command, timeout = cmd
            else:
                command, timeout = cmd, 5 # Default timeout for old-style calls

            if getattr(self.settings, "local_only", False):
                if command_type == "api":
                    self._execute_local_linuxcnc_code(command)
                continue

            ip = self.settings.get_setting("SSH", "ip_address", "")
            user = self.settings.get_setting("SSH", "username", "")
            password = self.settings.get_setting("SSH", "password", "")

            if is_local_controller(ip):
                try: 
                    result = subprocess.run(command, shell=True, capture_output=True, text=True, timeout=timeout)
                    if result.stderr:
                        self.error_signal.emit(result.stderr.strip())
                except Exception as e:
                    self.error_signal.emit(str(e))
                continue

            if not ip or not user: continue
                
            try: 
                result = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=3", f"{user}@{ip}", command], capture_output=True, text=True, timeout=timeout)
                if result.stderr:
                    self.error_signal.emit(result.stderr.strip())
            except Exception as e:
                self.error_signal.emit(str(e))

    def set_feed_override(self):
        agent_client = client_from_settings(self.settings)
        if agent_client is not None:
            try:
                agent_client.set_override("feed", self.feed_slider.value() / 100.0)
            except Exception as error:
                self.error_signal.emit(f"Feed override failed: {error}")
            return
        py_code = f"import linuxcnc\ntry:\n    c=linuxcnc.command()\n    c.feedrate({self.feed_slider.value() / 100.0})\nexcept: pass"
        self._send_b64_py_cmd(py_code)

    def set_spindle_override(self):
        agent_client = client_from_settings(self.settings)
        if agent_client is not None:
            try:
                agent_client.set_override("spindle", self.spindle_slider.value() / 100.0)
            except Exception as error:
                self.error_signal.emit(f"Spindle override failed: {error}")
            return
        py_code = f"import linuxcnc\ntry:\n    c=linuxcnc.command()\n    c.spindleoverride({self.spindle_slider.value() / 100.0})\nexcept: pass"
        self._send_b64_py_cmd(py_code)

    def reset_overrides(self):
        self.feed_slider.setValue(100)
        self.spindle_slider.setValue(100)
        self.set_feed_override()
        self.set_spindle_override()

    def pulse_hal_pin(self, pin_name):
        """Safely simulates a momentary button press over HAL."""
        self.send_halcmd_setp(pin_name, "True")
        QTimer.singleShot(200, lambda: self.send_halcmd_setp(pin_name, "False"))
        print(f"[dashboard.py] Pulsed HAL Pin: {pin_name}")

    def run_ocode(self):
        agent_client = client_from_settings(self.settings)
        if agent_client is not None:
            ocode = self.combo_ocodes.currentData()
            if not ocode:
                self.error_signal.emit("No managed O-code macro is installed on the machine.")
                return
            macro_text = self.combo_ocodes.currentText()
        else:
            macro_text = self.combo_ocodes.currentText()
            ocode = macro_text.split(" ")[0]
        
        confirm = QMessageBox.question(self, "Confirm Macro", 
                                       f"Are you sure you want to execute the following macro?\n\n{macro_text}", 
                                       QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
                                       
        if confirm == QMessageBox.StandardButton.Yes:
            print(f"[dashboard.py] Triggering Subroutine: {ocode}")
            if agent_client is not None:
                try:
                    agent_client.run_subroutine(ocode)
                except Exception as error:
                    self.error_signal.emit(f"Subroutine failed: {error}")
                return
            self.execute_mdi(f"{ocode} call")

    def save_toolpath_screenshot(self):
        if not hasattr(self, 'toolpath_viewer'): return
        
        default_dir = os.path.expanduser("~")
        last_dir = self.settings.get_setting("PATHS", "last_screenshot_dir", default_dir)
        if not os.path.exists(last_dir): last_dir = default_dir
        path, _ = QFileDialog.getSaveFileName(self, "Save Toolpath Screenshot", os.path.join(last_dir, "toolpath.png"), "Images (*.png)")
        if not path: return
        self.settings.save_setting("PATHS", "last_screenshot_dir", os.path.dirname(path))
        
        pixmap = self.toolpath_viewer.grab()
        if pixmap.save(path, "PNG"):
            QMessageBox.information(self, "Success", f"Screenshot saved to:\n{path}")
        else:
            QMessageBox.critical(self, "Error", "Failed to save screenshot.")

    def update_preview_toggle_button(self):
        if not hasattr(self, 'btn_toggle_preview'):
            return
        is_enabled = getattr(self.toolpath_viewer, 'preview_enabled', True)
        if is_enabled:
            self.btn_toggle_preview.setText("👁️ Disable Preview")
            self.btn_toggle_preview.setStyleSheet(
                "QPushButton { background-color: #7f1d1d; color: #fecaca; padding: 4px 10px; border-radius: 3px; font-weight: bold; border: 1px solid #ef4444; } "
                "QPushButton:hover { background-color: #991b1b; } "
                "QPushButton:pressed { background-color: #450a0a; }"
            )
            self.btn_toggle_preview.setToolTip("Disable 3D toolpath preview parsing (significantly speeds up file loading)")
        else:
            self.btn_toggle_preview.setText("👁️ Enable Preview")
            self.btn_toggle_preview.setStyleSheet(
                "QPushButton { background-color: #14532d; color: #bbf7d0; padding: 4px 10px; border-radius: 3px; font-weight: bold; border: 1px solid #22c55e; } "
                "QPushButton:hover { background-color: #166534; } "
                "QPushButton:pressed { background-color: #052e16; }"
            )
            self.btn_toggle_preview.setToolTip("Enable 3D toolpath preview parsing and display")

    def toggle_toolpath_preview(self):
        viewer = self.__dict__.get('toolpath_viewer')
        new_state = not getattr(viewer, 'preview_enabled', True)
        target_path = self.__dict__.get('loaded_gcode_path')
        gcode = ""
        if not self.__dict__.get('_is_large_file_preview', False):
            editor = self.__dict__.get('default_gcode_editor')
            gcode = editor.toPlainText() if editor else ""
        if viewer:
            viewer.set_preview_enabled(new_state, gcode_text=gcode, file_path=target_path)
        if self.__dict__.get('settings') is not None:
            try:
                self.settings.save_setting('UI_PREFERENCES', 'enable_toolpath_preview', str(new_state))
            except Exception:
                pass
        self.update_preview_toggle_button()

    def _on_gcode_text_changed(self):
        viewer = self.__dict__.get('toolpath_viewer')
        if not viewer:
            return
        if self.__dict__.get('_is_large_file_preview', False):
            return
        if not getattr(viewer, 'preview_enabled', True):
            viewer.last_gcode_text = None
            return
        editor = self.__dict__.get('default_gcode_editor')
        if editor:
            viewer.update_path(editor.toPlainText())

    def get_nc_files_dir(self):
        default_dir = os.path.expanduser("~/linuxcnc/nc_files")
        nc_dir = ""
        if hasattr(self, "settings") and self.settings is not None:
            nc_dir = self.settings.get_setting("PATHS", "machine_gcode_root", "")
            if not nc_dir:
                nc_dir = self.settings.get_setting("PATHS", "nc_files_dir", default_dir)
        if not nc_dir:
            nc_dir = default_dir
        nc_dir = os.path.expanduser(nc_dir)
        os.makedirs(nc_dir, exist_ok=True)
        return nc_dir

    def sync_gcode_to_machine(self, content, file_path=None):
        if self.__dict__.get('_is_large_file_preview', False) and self.__dict__.get('loaded_gcode_path'):
            file_path = self.__dict__['loaded_gcode_path']
        elif file_path is None:
            file_path = self.__dict__.get('loaded_gcode_path')
        if not content.strip() and not file_path: return
        ip = self.settings.get_setting("SSH", "ip_address", "")
        user = self.settings.get_setting("SSH", "username", "")
        password = self.settings.get_setting("SSH", "password", "")
        agent_client = client_from_settings(self.settings)
        is_local = getattr(self.settings, "local_only", False) or is_local_controller(ip)
        if agent_client is None and not is_local and (not ip or not user): return

        self._program_sync_in_progress = True
        self.program_sync_state.emit(True)
        
        def task():
            try:
                if agent_client is not None:
                    try:
                        if file_path and os.path.exists(file_path):
                            with open(file_path, 'rb') as f:
                                file_bytes = f.read()
                            target_name = os.path.basename(file_path)
                            agent_client.upload_content("gcode", target_name, file_bytes)
                            agent_client.open_program(target_name)
                        else:
                            agent_client.upload_content("gcode", "active_program.ngc", content.encode("utf-8"))
                            agent_client.open_program("active_program.ngc")
                    except Exception as error:
                        self.error_signal.emit(f"G-code upload failed: {error}")
                    return

                if is_local:
                    try:
                        if file_path and os.path.exists(file_path):
                            remote_path = file_path
                        else:
                            local_dir = self.get_nc_files_dir()
                            local_dest = os.path.join(local_dir, "active_program.ngc")
                            with open(local_dest, 'w', encoding='utf-8') as f:
                                f.write(content)
                            remote_path = local_dest
                    except Exception as error:
                        self.error_signal.emit(f"G-code save failed: {error}")
                        return
                else:
                    remote_dir = f"/home/{user}/linuxcnc/nc_files"
                    target_name = os.path.basename(file_path) if file_path else "active_program.ngc"
                    remote_path = f"{remote_dir}/{target_name}"
                    try:
                        subprocess.run(["ssh", "-o", "BatchMode=yes", f"{user}@{ip}", f"mkdir -p {remote_dir}"], capture_output=True, timeout=5)
                        if file_path and os.path.exists(file_path):
                            subprocess.run(["scp", "-o", "BatchMode=yes", file_path, f"{user}@{ip}:{remote_path}"], capture_output=True, timeout=30)
                        else:
                            with tempfile.NamedTemporaryFile(delete=False, mode='w', encoding='utf-8') as f:
                                f.write(content)
                                temp_path = f.name
                            subprocess.run(["scp", "-o", "BatchMode=yes", temp_path, f"{user}@{ip}:{remote_path}"], capture_output=True, timeout=10)
                            os.remove(temp_path)
                    except Exception as error:
                        self.error_signal.emit(f"G-code upload failed: {error}")
                        return
                    
                if getattr(self.settings, "local_only", False) and self.local_api:
                    try:
                        command = self.local_api.command()
                        command.program_open(remote_path)
                    except Exception as error:
                        self.error_signal.emit(f"Failed to open G-code program: {error}")
                else:
                    py_code = f"import linuxcnc\ntry:\n    c=linuxcnc.command()\n    c.program_open({repr(remote_path)})\nexcept: pass"
                    self._send_b64_py_cmd(py_code)
            finally:
                self.program_sync_state.emit(False)
            
        threading.Thread(target=task, daemon=True).start()

    def _set_program_sync_state(self, in_progress):
        self._program_sync_in_progress = in_progress
        if hasattr(self, 'btn_cycle_start'):
            self.btn_cycle_start.setEnabled(not in_progress)
            self.btn_cycle_start.setToolTip(
                "Waiting for the G-code program to finish uploading."
                if in_progress else "Start the loaded G-code program."
            )

    def _check_tool_change_request(self):
        """Detect a pending manual M6 and offer an automatic tool-length probe."""
        blocked = self.mock_coords.get('TASK_BLOCKED', False)
        if not blocked:
            if getattr(self, '_tool_change_prompt_active', False):
                self._tool_change_prompt_active = False
                self._tool_change_last_pocket = -1
            return
        if getattr(self, '_tool_change_prompt_active', False):
            return
        pocket = self.mock_coords.get('POCKET_PREPPED', -1)
        current_tool = self.mock_coords.get('TOOL_IN_SPINDLE', 0)
        if pocket < 0 or pocket == current_tool or pocket == self._tool_change_last_pocket:
            return
        self._tool_change_last_pocket = pocket
        self._tool_change_prompt_active = True
        reply = QMessageBox.question(
            self,
            "Manual Tool Change",
            f"LinuxCNC is requesting Tool T{pocket}.\n\n"
            "Install the tool now, then click Yes to continue the program.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self._run_manual_tool_change_sequence(pocket)
        else:
            self._tool_change_prompt_active = False

    def _run_manual_tool_change_sequence(self, tool_num):
        """Acknowledge a manual tool change so the paused AUTO cycle can resume.

        LinuxCNC will not accept MDI/mode-switch commands while the AUTO
        interpreter is paused mid-M6, so this only asserts the tool-changed
        handshake. Automatic tool-length probing must be embedded as ordinary
        G-code (e.g. a probing subroutine call) immediately after the T#/M6
        line in the post-processed program, since that runs in normal AUTO
        mode with no mode conflict.
        """
        py_code = """import os, time
try:
    hal.set_p("iocontrol.0.tool-changed", "1")
    time.sleep(0.3)
    hal.set_p("iocontrol.0.tool-changed", "0")
except Exception: pass"""
        agent_client = client_from_settings(self.settings)
        if agent_client is not None:
            try:
                agent_client.machine_command("tool_change_ack", timeout=15.0)
                self._tool_change_prompt_active = False
                self.update_real_coords(agent_client.telemetry())
            except Exception as error:
                self._tool_change_prompt_active = False
                self.error_signal.emit(f"Tool-change acknowledge failed: {error}")
            return
        self._send_b64_py_cmd(py_code, timeout=15)

    def load_gcode(self, text_widget):
        default_dir = os.path.expanduser("~")
        last_dir = default_dir
        if hasattr(self, "settings") and self.settings is not None:
            last_dir = self.settings.get_setting("PATHS", "last_local_gcode_dir", default_dir)
        if not os.path.exists(last_dir): last_dir = default_dir
        path, _ = QFileDialog.getOpenFileName(self, "Load G-Code", last_dir, "G-Code Files (*.ngc *.gcode *.nc);;All Files (*)")
        if not path: return
        if hasattr(self, "settings") and self.settings is not None:
            self.settings.save_setting("PATHS", "last_local_gcode_dir", os.path.dirname(path))
        
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            nc_dir = self.get_nc_files_dir()
            filename = os.path.basename(path)
            local_target = os.path.join(nc_dir, filename)

            is_same = False
            try:
                if os.path.exists(local_target):
                    is_same = os.path.samefile(path, local_target)
            except Exception:
                is_same = (os.path.abspath(path) == os.path.abspath(local_target))

            if not is_same:
                shutil.copy2(path, local_target)

            self.loaded_gcode_path = local_target
            file_size = os.path.getsize(local_target)
            threshold = self.__dict__.get('LARGE_FILE_THRESHOLD', getattr(self.__class__, 'LARGE_FILE_THRESHOLD', 2 * 1024 * 1024))

            if file_size > threshold:
                self._is_large_file_preview = True
                with open(local_target, 'rb') as bf:
                    total_lines = sum(chunk.count(b'\n') for chunk in iter(lambda: bf.read(1024 * 1024), b''))
                if hasattr(self, 'mock_coords') and isinstance(self.mock_coords, dict):
                    self.mock_coords['TOTAL_LINES'] = total_lines

                head_lines = []
                tail_buf = collections.deque(maxlen=200)
                with open(local_target, 'r', encoding='utf-8', errors='ignore') as f:
                    for i, line in enumerate(f):
                        if i < 1000:
                            head_lines.append(line)
                        else:
                            tail_buf.append(line)
                tail_lines = list(tail_buf)

                size_mb = file_size / (1024 * 1024)
                banner = (
                    f"( ==================================================================== )\n"
                    f"( LARGE FILE PREVIEW MODE - {filename} ({size_mb:.1f} MB, {total_lines:,} lines) )\n"
                    f"( Showing first {len(head_lines):,} lines and last {len(tail_lines):,} lines for inspection. )\n"
                    f"( The full {size_mb:.1f} MB program has been loaded directly into LinuxCNC. )\n"
                    f"( Note: Inline editing/optimizing is disabled to protect CAM toolpaths. )\n"
                    f"( ==================================================================== )\n\n"
                )
                omitted_count = max(0, total_lines - len(head_lines) - len(tail_lines))
                separator = f"\n( ... {omitted_count:,} intermediate CAM toolpath lines omitted for UI responsiveness ... )\n\n"
                preview_text = banner + "".join(head_lines) + separator + "".join(tail_lines)

                text_widget.blockSignals(True)
                try:
                    text_widget.setPlainText(preview_text)
                finally:
                    text_widget.blockSignals(False)

                if hasattr(self, 'toolpath_viewer') and self.toolpath_viewer:
                    self.toolpath_viewer.update_path("", file_path=local_target)
                self.sync_gcode_to_machine("", file_path=local_target)
            else:
                self._is_large_file_preview = False
                with open(local_target, 'r', encoding='utf-8', errors='ignore') as f:
                    content = f.read()

                if hasattr(self, 'mock_coords') and isinstance(self.mock_coords, dict):
                    self.mock_coords['TOTAL_LINES'] = content.count('\n') + 1

                text_widget.blockSignals(True)
                try:
                    text_widget.setPlainText(content)
                finally:
                    text_widget.blockSignals(False)

                if hasattr(self, 'toolpath_viewer') and self.toolpath_viewer:
                    self.toolpath_viewer.update_path(content, file_path=local_target)
                self.sync_gcode_to_machine(content)
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to load G-Code:\n{e}")
        finally:
            QApplication.restoreOverrideCursor()

    def clear_gcode(self, text_widget=None):
        """Clear the G-code editor, 3D toolpath preview, and unload the active program from LinuxCNC."""
        self._is_large_file_preview = False
        self.loaded_gcode_path = None
        if hasattr(self, 'mock_coords') and isinstance(self.mock_coords, dict):
            self.mock_coords['TOTAL_LINES'] = 0

        # 1. Clear editor
        if text_widget is not None:
            text_widget.clear()
        elif hasattr(self, 'default_gcode_editor') and self.default_gcode_editor:
            self.default_gcode_editor.clear()

        # 2. Clear 3D preview
        if hasattr(self, 'toolpath_viewer') and self.toolpath_viewer:
            self.toolpath_viewer.update_path("")
            self.toolpath_viewer.last_gcode_text = None

        # 3. Reset progress bar
        if hasattr(self, 'progress_bar') and self.progress_bar:
            self.progress_bar.setValue(0)
            self.progress_bar.setFormat("No Program Loaded")

        # 4. Remove / truncate active_program.ngc locally
        try:
            nc_dir = self.get_nc_files_dir()
            active_dest = os.path.join(nc_dir, "active_program.ngc")
            if os.path.exists(active_dest):
                try:
                    os.remove(active_dest)
                except Exception:
                    with open(active_dest, 'w', encoding='utf-8') as f:
                        f.write("")
        except Exception:
            pass

        # 5. Tell LinuxCNC controller to abort, return to manual mode, and reset interpreter
        agent_client = client_from_settings(self.settings) if hasattr(self, 'settings') and self.settings else None
        if agent_client is not None:
            try:
                agent_client.machine_command("abort")
                agent_client.upload_content("gcode", "active_program.ngc", b"")
            except Exception as error:
                self.error_signal.emit(f"Failed to clear program: {error}")
            return

        ip = self.settings.get_setting("SSH", "ip_address", "") if hasattr(self, 'settings') and self.settings else ""
        is_local = getattr(self.settings, "local_only", False) or is_local_controller(ip)

        if is_local:
            if getattr(self.settings, "local_only", False) and self.local_api:
                try:
                    command = self.local_api.command()
                    try:
                        command.set_feed_hold(0)
                    except Exception:
                        pass
                    command.abort()
                    command.wait_complete(1.0)
                    command.mode(self.local_api.linuxcnc.MODE_MANUAL)
                    command.wait_complete(1.0)
                    try:
                        command.reset_interpreter()
                    except Exception:
                        pass
                except Exception as error:
                    self.error_signal.emit(f"Failed to clear machine program: {error}")
            else:
                py_code = (
                    "import linuxcnc\n"
                    "try:\n"
                    "    c = linuxcnc.command()\n"
                    "    try: c.set_feed_hold(0)\n"
                    "    except: pass\n"
                    "    c.abort()\n"
                    "    c.wait_complete(1.0)\n"
                    "    c.mode(linuxcnc.MODE_MANUAL)\n"
                    "    c.wait_complete(1.0)\n"
                    "    try: c.reset_interpreter()\n"
                    "    except: pass\n"
                    "except Exception: pass"
                )
                self._send_b64_py_cmd(py_code, urgent=True)
        else:
            user = self.settings.get_setting("SSH", "username", "") if hasattr(self, 'settings') and self.settings else ""
            if ip and user:
                remote_dir = f"/home/{user}/linuxcnc/nc_files"
                remote_path = f"{remote_dir}/active_program.ngc"
                try:
                    subprocess.run(
                        ["ssh", "-o", "BatchMode=yes", f"{user}@{ip}", f"rm -f {remote_path}"],
                        capture_output=True,
                        timeout=5,
                    )
                except Exception:
                    pass
            py_code = (
                "import linuxcnc\n"
                "try:\n"
                "    c = linuxcnc.command()\n"
                "    try: c.set_feed_hold(0)\n"
                "    except: pass\n"
                "    c.abort()\n"
                "    c.wait_complete(1.0)\n"
                "    c.mode(linuxcnc.MODE_MANUAL)\n"
                "    c.wait_complete(1.0)\n"
                "    try: c.reset_interpreter()\n"
                "    except: pass\n"
                "except Exception: pass"
            )
            self._send_b64_py_cmd(py_code, urgent=True)

    def merge_gcode_files(self, text_widget):
        default_dir = os.path.expanduser("~")
        last_dir = self.settings.get_setting("PATHS", "last_local_gcode_dir", default_dir)
        if not os.path.exists(last_dir): last_dir = default_dir
        
        files, _ = QFileDialog.getOpenFileNames(self, "Select G-Code Files to Merge (Select multiple)", last_dir, "G-Code Files (*.ngc *.gcode *.nc);;All Files (*)")
        if not files: return
        
        self.settings.save_setting("PATHS", "last_local_gcode_dir", os.path.dirname(files[0]))
        
        if len(files) < 2:
            QMessageBox.information(self, "Merge", "Please select at least 2 files to merge.")
            return
            
        files.sort() # Ensure alphabetical order (e.g., 1_face.nc, 2_pocket.nc)
        
        m1_reply = QMessageBox.question(self, "Optional Stop", 
                                        "Do you want to insert an M1 Optional Stop between the merged programs?", 
                                        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        insert_m1 = (m1_reply == QMessageBox.StandardButton.Yes)

        merged_text = ["%"]
        for i, file_path in enumerate(files):
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    content = f.read()
                    
                if insert_m1 and i > 0:
                    merged_text.append("M1 (Optional Stop Between Files)")
                    
                merged_text.append(f"( --- Start of {os.path.basename(file_path)} --- )")
                
                for line in content.splitlines():
                    clean_str = line.split(';')[0].split('(')[0].strip().upper()
                    if clean_str == '%':
                        continue # Strip existing % marks
                        
                    if i < len(files) - 1:
                        # Strip program ends from intermediate files so it doesn't stop halfway!
                        if clean_str in ['M2', 'M02', 'M30']:
                            merged_text.append(f"( Stripped program end: {line} )")
                            continue
                            
                    merged_text.append(line)
                merged_text.append(f"( --- End of {os.path.basename(file_path)} --- )\n")
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Failed to read {file_path}:\n{e}")
                return
                
        merged_text.append("%")
        full_text = '\n'.join(merged_text)
        text_widget.setPlainText(full_text)
        self.sync_gcode_to_machine(full_text)
        QMessageBox.information(self, "Merge Complete", f"Successfully merged {len(files)} files!\n\nThe combined program has been synced to the machine.\n\nMake sure to verify the tool changes and transition moves in the 3D preview.")

    def save_gcode(self, text_widget):
        if self.__dict__.get('_is_large_file_preview', False):
            QMessageBox.warning(
                self,
                "Save Disabled",
                "Saving is disabled in Large File Preview Mode to prevent overwriting the full program with preview lines."
            )
            return

        default_dir = os.path.expanduser("~")
        last_dir = self.settings.get_setting("PATHS", "last_local_gcode_dir", default_dir)
        if not os.path.exists(last_dir): last_dir = default_dir
        path, _ = QFileDialog.getSaveFileName(self, "Save G-Code", os.path.join(last_dir, "program.ngc"), "G-Code Files (*.ngc *.gcode *.nc);;All Files (*)")
        if not path: return
        self.settings.save_setting("PATHS", "last_local_gcode_dir", os.path.dirname(path))
        
        content = text_widget.toPlainText()
        try:
            with open(path, 'w', encoding='utf-8', newline='\n') as f:
                f.write(content)
            self.sync_gcode_to_machine(content)
            QMessageBox.information(self, "Saved", f"G-Code successfully saved locally and synced to the machine!")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to save G-Code:\n{e}")

    def fix_fusion_rapids(self):
        if self.__dict__.get('_is_large_file_preview', False):
            QMessageBox.warning(
                self,
                "Optimizer Disabled",
                "Rapids optimization is disabled in Large File Preview Mode to protect the original file."
            )
            return

        text = self.default_gcode_editor.toPlainText()
        if not text.strip(): return
        
        max_f = 0.0
        for line in text.splitlines():
            clean = line.split(';')[0].split('(')[0]
            for match in re.finditer(r'[Ff]\s*(\d+\.?\d*)', clean):
                max_f = max(max_f, float(match.group(1)))
                
        if max_f == 0:
            QMessageBox.information(self, "Optimizer", "No Feedrate (F) commands found in the file.")
            return
            
        val, ok = QInputDialog.getDouble(self, "Fusion 360 Rapid Fix", 
            "Enter the 'High Feedrate' Fusion 360 is using for rapid travel moves:\n(We auto-detected the max feedrate below)", 
            value=max_f, min=1.0, max=1000000.0, decimals=1)
        if not ok: return
        
        target_f = val
        new_lines = []
        modal_f = 0.0
        changes = 0
        in_injected_g0 = False
        
        for line in text.splitlines():
            original_line = line
            clean_line = line.split(';')[0].split('(')[0].strip().upper()
            
            f_match = re.search(r'[Ff]\s*(\d+\.?\d*)', clean_line)
            if f_match:
                modal_f = float(f_match.group(1))
                
            has_move = bool(re.search(r'[XxYyZzAaBbCc]', clean_line))
            has_explicit_g = bool(re.search(r'[Gg]0?[0123]\b', clean_line))
            
            if modal_f >= target_f - 0.001 and has_move:
                is_g1 = bool(re.search(r'[Gg]0?1\b', clean_line))
                is_implicit = not bool(re.search(r'[Gg]\d+', clean_line))
                
                if is_g1 or is_implicit:
                    if is_g1: line = re.sub(r'[Gg]0?1\b', 'G0', line)
                    else: line = re.sub(r'([XxYyZzAaBbCc])', r'G0 \1', line, count=1, flags=re.IGNORECASE)
                    
                    line = re.sub(r'[Ff]\s*\d+\.?\d*', '', line, flags=re.IGNORECASE).strip()
                    in_injected_g0 = True
                    changes += 1
            else:
                if has_move and in_injected_g0:
                    if not has_explicit_g:
                        line = re.sub(r'([XxYyZzAaBbCc])', r'G1 \1', line, count=1, flags=re.IGNORECASE)
                    in_injected_g0 = False
                    
            new_lines.append(line)
            
        if changes > 0:
            self.default_gcode_editor.setPlainText('\n'.join(new_lines))
            QMessageBox.information(self, "Optimization Complete", f"Successfully converted {changes} slow travel moves back into G0 rapids!\n\n(Note: Always verify the 3D toolpath preview before running to ensure no unwanted collisions.)")
        else:
            QMessageBox.information(self, "No Changes", "No linear moves were found matching that high feedrate.")

    def clean_gcode(self):
        if self.__dict__.get('_is_large_file_preview', False):
            QMessageBox.warning(
                self,
                "Clean Disabled",
                "G-code cleaning is disabled in Large File Preview Mode to protect the original file."
            )
            return

        text = self.default_gcode_editor.toPlainText()
        if not text.strip(): return
        
        new_lines = []
        changes = 0
        for line in text.splitlines():
            # Remove trailing dots that are preceded by a space
            cleaned_line = line.rstrip()
            if cleaned_line.endswith(' .'):
                cleaned_line = cleaned_line[:-2].rstrip()
                changes += 1
            new_lines.append(cleaned_line)
        
        if changes > 0:
            self.default_gcode_editor.setPlainText('\n'.join(new_lines))
            QMessageBox.information(self, "Clean Complete", f"Successfully removed trailing characters from {changes} lines.")
        else:
            QMessageBox.information(self, "No Changes", "No trailing dots found to clean.")

    def _send_b64_py_cmd(self, py_code, urgent=False, timeout=60):
        if client_from_settings(self.settings) is not None:
            self.error_signal.emit(
                "Secure mode blocked a legacy remote command. Use a named machine-agent operation instead."
            )
            return

        if getattr(self.settings, "local_only", False):
            if urgent:
                threading.Thread(
                    target=self._execute_local_linuxcnc_code,
                    args=(py_code,),
                    daemon=True,
                ).start()
            else:
                self.cmd_queue.put(("api", py_code, timeout))
            return

        b64_code = base64.b64encode(py_code.encode()).decode()
        cmd = f"python3 -u -c \"import base64; exec(base64.b64decode('{b64_code}').decode())\""
        if urgent:
            threading.Thread(target=self._execute_urgent_cmd, args=(cmd, timeout), daemon=True).start()
        else:
            self._send_linuxcnc_py_cmd(cmd, timeout=timeout)

    def _execute_urgent_cmd(self, cmd, timeout=60):
        ip = self.settings.get_setting("SSH", "ip_address", "")
        user = self.settings.get_setting("SSH", "username", "")
        password = self.settings.get_setting("SSH", "password", "")

        if is_local_controller(ip):
            try: 
                subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
            except Exception as e:
                with open('/tmp/linuxcnc_jog_debug.txt', 'a') as f: f.write(f"\\nURGENT SUBPROCESS EXCEPTION:\\n{e}\\n")
            return

        if not ip or not user: return

        try:
            subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=3", f"{user}@{ip}", cmd], capture_output=True, timeout=timeout)
        except:
            pass

    def toggle_estop(self):
        agent_client = client_from_settings(self.__dict__.get("settings"))
        is_estopped = self.__dict__.get("is_estopped", False)
        if agent_client is not None:
            try:
                agent_client.machine_command("estop_reset" if is_estopped else "estop")
                if is_estopped:
                    try:
                        agent_client.machine_command("mode_manual")
                    except Exception:
                        pass
                self.is_estopped = not is_estopped
                if "mock_coords" in self.__dict__:
                    self.mock_coords["ESTOP_ACTIVATED"] = self.is_estopped
                    if self.is_estopped:
                        self.mock_coords["MACHINE_ON"] = False
                    else:
                        self.mock_coords["MODE"] = "MANUAL"
                if "update_status" in self.__dict__ or hasattr(self.__class__, "update_status"):
                    try:
                        self.update_status()
                    except Exception:
                        pass
            except Exception as error:
                if self.__dict__.get("error_signal"):
                    self.error_signal.emit(f"E-stop command failed: {error}")
            return
        if getattr(self.__dict__.get("settings"), "local_only", False) and self.__dict__.get("local_api"):
            try:
                status = self.local_api.status()
                status.poll()
                command = self.local_api.command()
                if status.task_state == self.local_api.linuxcnc.STATE_ESTOP:
                    try:
                        command.set_feed_hold(0)
                    except Exception:
                        pass
                    command.abort()
                    command.wait_complete(1.0)
                    # Reset E-STOP first before changing mode!
                    command.state(self.local_api.linuxcnc.STATE_ESTOP_RESET)
                    command.wait_complete(2.0)
                    try:
                        err_chan = self.local_api.error_channel()
                        for _ in range(100):
                            if not err_chan.poll():
                                break
                    except Exception:
                        pass
                    try:
                        command.mode(self.local_api.linuxcnc.MODE_MANUAL)
                        command.wait_complete(1.0)
                    except Exception:
                        pass
                    self.is_estopped = False
                    if "mock_coords" in self.__dict__:
                        self.mock_coords["ESTOP_ACTIVATED"] = False
                        self.mock_coords["MODE"] = "MANUAL"
                    if "update_status" in self.__dict__ or hasattr(self.__class__, "update_status"):
                        try:
                            self.update_status()
                        except Exception:
                            pass
                else:
                    command.state(self.local_api.linuxcnc.STATE_OFF)
                    command.wait_complete(2.0)
                    command.state(self.local_api.linuxcnc.STATE_ESTOP)
                    command.wait_complete(2.0)
                    self.is_estopped = True
                    if "mock_coords" in self.__dict__:
                        self.mock_coords["ESTOP_ACTIVATED"] = True
                        self.mock_coords["MACHINE_ON"] = False
                    if "update_status" in self.__dict__ or hasattr(self.__class__, "update_status"):
                        try:
                            self.update_status()
                        except Exception:
                            pass
            except Exception as error:
                if self.__dict__.get("error_signal"):
                    self.error_signal.emit(f"E-stop command failed: {error}")
            return
        if is_estopped:
            # Send real commands to reset E-Stop FIRST, then restore MANUAL mode
            py_code = (
                "import linuxcnc, time\n"
                "try:\n"
                "    c = linuxcnc.command()\n"
                "    try: c.set_feed_hold(0)\n"
                "    except: pass\n"
                "    c.abort()\n"
                "    c.wait_complete(1.0)\n"
                "    c.state(linuxcnc.STATE_ESTOP_RESET)\n"
                "    c.wait_complete(2.0)\n"
                "    try:\n"
                "        e = linuxcnc.error_channel()\n"
                "        for _ in range(100):\n"
                "            if not e.poll(): break\n"
                "    except: pass\n"
                "    s = linuxcnc.stat(); s.poll()\n"
                "    if s.task_state != linuxcnc.STATE_ESTOP:\n"
                "        try:\n"
                "            c.mode(linuxcnc.MODE_MANUAL)\n"
                "            c.wait_complete(1.0)\n"
                "        except: pass\n"
                "except: pass"
            )
            self._send_b64_py_cmd(py_code, urgent=True)
            self.is_estopped = False
            if "mock_coords" in self.__dict__:
                self.mock_coords["ESTOP_ACTIVATED"] = False
                self.mock_coords["MODE"] = "MANUAL"
            if "update_status" in self.__dict__ or hasattr(self.__class__, "update_status"):
                try:
                    self.update_status()
                except Exception:
                    pass
        else:
            # Send real commands to Power Off and trigger E-Stop
            py_code = (
                "import linuxcnc, time\n"
                "try:\n"
                "    c = linuxcnc.command()\n"
                "    c.state(linuxcnc.STATE_OFF)\n"
                "    time.sleep(0.1)\n"
                "    c.state(linuxcnc.STATE_ESTOP)\n"
                "except: pass"
            )
            self._send_b64_py_cmd(py_code, urgent=True)
            self.is_estopped = True
            if "mock_coords" in self.__dict__:
                self.mock_coords["ESTOP_ACTIVATED"] = True
                self.mock_coords["MACHINE_ON"] = False
            if "update_status" in self.__dict__ or hasattr(self.__class__, "update_status"):
                try:
                    self.update_status()
                except Exception:
                    pass

    def toggle_power(self):
        agent_client = client_from_settings(self.__dict__.get("settings"))
        is_estopped = self.__dict__.get("is_estopped", False)
        is_powered_on = self.__dict__.get("mock_coords", {}).get("MACHINE_ON", False)
        if agent_client is not None:
            if is_estopped:
                try:
                    agent_client.machine_command("estop_reset")
                    self.is_estopped = False
                    if "mock_coords" in self.__dict__:
                        self.mock_coords["ESTOP_ACTIVATED"] = False
                    try:
                        agent_client.machine_command("mode_manual")
                    except Exception:
                        pass
                except Exception as error:
                    if self.__dict__.get("error_signal"):
                        self.error_signal.emit(f"E-stop reset before power on failed: {error}")
                    return
            try:
                agent_client.machine_command("power_off" if is_powered_on else "power_on")
            except Exception as error:
                if self.__dict__.get("error_signal"):
                    self.error_signal.emit(f"Power command failed: {error}")
            return

        if getattr(self.__dict__.get("settings"), "local_only", False) and self.__dict__.get("local_api"):
            try:
                status = self.local_api.status()
                status.poll()
                command = self.local_api.command()
                if status.task_state == self.local_api.linuxcnc.STATE_ON:
                    command.state(self.local_api.linuxcnc.STATE_OFF)
                    command.wait_complete(2.0)
                    return
                if status.task_state == self.local_api.linuxcnc.STATE_ESTOP:
                    physical_estop_ok = True
                    for estop_pin in ("iocontrol.0.emc-enable-in", "halui.estop.is-activated"):
                        try:
                            val = self.local_api.get_hal_value(estop_pin)
                            if estop_pin == "iocontrol.0.emc-enable-in" and not val:
                                physical_estop_ok = False
                                break
                            elif estop_pin == "halui.estop.is-activated" and val:
                                physical_estop_ok = False
                                break
                        except Exception:
                            pass
                    if not physical_estop_ok:
                        QMessageBox.warning(self, "E-STOP", "Reset the physical E-stop before enabling machine power.")
                        return
                    try:
                        command.set_feed_hold(0)
                    except Exception:
                        pass
                    command.abort()
                    command.wait_complete(1.0)
                    # Reset E-STOP first before changing mode or turning on power
                    command.state(self.local_api.linuxcnc.STATE_ESTOP_RESET)
                    command.wait_complete(2.0)
                    status.poll()
                    if status.task_state == self.local_api.linuxcnc.STATE_ESTOP:
                        QMessageBox.warning(self, "E-STOP", "Reset the physical E-stop before enabling machine power.")
                        return
                    try:
                        command.mode(self.local_api.linuxcnc.MODE_MANUAL)
                        command.wait_complete(1.0)
                    except Exception:
                        pass
                    self.is_estopped = False
                    if "mock_coords" in self.__dict__:
                        self.mock_coords["ESTOP_ACTIVATED"] = False
                active_limits = []
                for joint_index in range(getattr(status, "joints", 0)):
                    for suffix, label in (("neg-lim-sw-in", "negative"), ("pos-lim-sw-in", "positive")):
                        pin_name = f"joint.{joint_index}.{suffix}"
                        try:
                            if self.local_api.get_hal_value(pin_name):
                                active_limits.append(f"joint {joint_index} {label}")
                        except Exception as error:
                            if self.__dict__.get("error_signal"):
                                self.error_signal.emit(f"Unable to read {pin_name}; power remains off: {error}")
                            return
                if active_limits:
                    QMessageBox.warning(
                        self,
                        "Power On Blocked",
                        "LinuxCNC reports active hard-limit inputs: "
                        + ", ".join(active_limits)
                        + ". Inspect the switches and the saved HAL mapping; no limit override was applied.",
                    )
                    return
                command.state(self.local_api.linuxcnc.STATE_ON)
                command.wait_complete(2.0)
            except Exception as error:
                if self.__dict__.get("error_signal"):
                    self.error_signal.emit(f"Power command failed: {error}")
            return
            
        if is_estopped:
            # Send real commands to reset E-Stop first, then enable Power
            py_code = (
                "import linuxcnc, time\n"
                "try:\n"
                "    c = linuxcnc.command()\n"
                "    try: c.set_feed_hold(0)\n"
                "    except: pass\n"
                "    c.abort()\n"
                "    c.wait_complete(1.0)\n"
                "    c.state(linuxcnc.STATE_ESTOP_RESET)\n"
                "    c.wait_complete(2.0)\n"
                "    s = linuxcnc.stat(); s.poll()\n"
                "    if s.task_state != linuxcnc.STATE_ESTOP:\n"
                "        try:\n"
                "            c.mode(linuxcnc.MODE_MANUAL)\n"
                "            c.wait_complete(1.0)\n"
                "        except: pass\n"
                "        c.state(linuxcnc.STATE_ON)\n"
                "except: pass"
            )
            self._send_b64_py_cmd(py_code, urgent=True)
            self.is_estopped = False
            if "mock_coords" in self.__dict__:
                self.mock_coords['ESTOP_ACTIVATED'] = False
                self.mock_coords['MACHINE_ON'] = True
                self.mock_coords['MODE'] = 'MANUAL'
            if "update_status" in self.__dict__ or hasattr(self.__class__, "update_status"):
                try:
                    self.update_status()
                except Exception:
                    pass
            return
        
        if is_powered_on:
            py_code = "import linuxcnc\ntry:\n    c=linuxcnc.command()\n    c.state(linuxcnc.STATE_OFF)\nexcept: pass"
            self._send_b64_py_cmd(py_code, urgent=True)
            if "mock_coords" in self.__dict__:
                self.mock_coords['MACHINE_ON'] = False
        else:
            py_code = "import linuxcnc\ntry:\n    c=linuxcnc.command()\n    c.state(linuxcnc.STATE_ON)\nexcept: pass"
            self._send_b64_py_cmd(py_code, urgent=True)
            if "mock_coords" in self.__dict__:
                self.mock_coords['MACHINE_ON'] = True
            
        if "update_status" in self.__dict__ or hasattr(self.__class__, "update_status"):
            try:
                self.update_status()
            except Exception:
                pass

    def override_limits(self):
        # Allow overriding even if in E-STOP, as physical limit switches force an E-STOP state!
        agent_client = client_from_settings(self.settings)
        if agent_client is not None:
            try:
                agent_client.machine_command("override_limits")
            except Exception as error:
                self.error_signal.emit(f"Limit override failed: {error}")
            return
        if getattr(self.settings, "local_only", False):
            confirm = QMessageBox.question(
                self,
                "Confirm Limit Override",
                "Request a temporary LinuxCNC hard-limit override so an operator can carefully jog clear. "
                "This does not reset E-stop or enable machine power. Continue only if the area is safe.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if confirm != QMessageBox.StandardButton.Yes:
                return
            try:
                self.local_api.command().override_limits()
            except Exception as error:
                self.error_signal.emit(f"Limit override failed: {error}")
            return
        py_code = "import linuxcnc, time\ntry:\n    c=linuxcnc.command()\n    c.override_limits()\n    time.sleep(0.1)\n    c.state(linuxcnc.STATE_ESTOP_RESET)\n    time.sleep(0.1)\n    c.state(linuxcnc.STATE_ON)\nexcept: pass"
        self._send_b64_py_cmd(py_code, urgent=True)
        
        self.is_estopped = False
        self.mock_coords['ESTOP_ACTIVATED'] = False
        self.mock_coords['MACHINE_ON'] = True
        self.update_status()
        QMessageBox.information(self, "Limits Overridden", "Limits have been overridden and machine powered ON.\n\nYou can now carefully jog the machine off the switches.")

    def home_axis(self, axis):
        agent_client = client_from_settings(self.settings)
        if agent_client is not None:
            try:
                agent_client.home_axis(axis)
            except Exception as error:
                self.error_signal.emit(f"Axis homing failed: {error}")
            return
        if self.is_estopped:
            return
        
        is_gantry, is_4_axis, is_5_axis, total_joints = self.get_kinematics_info()
        
        if is_gantry: joint_idx = {'X': 0, 'Y': 1, 'Z': 3}.get(axis, 0)
        elif is_4_axis: joint_idx = {'X': 0, 'Y': 1, 'Z': 2, 'A': 3}.get(axis, 0)
        elif is_5_axis: joint_idx = {'X': 0, 'Y': 1, 'Z': 2, 'A': 3, 'B': 4}.get(axis, 0)
        else: joint_idx = {'X': 0, 'Y': 1, 'Z': 2}.get(axis, 0)
        
        print(f"[dashboard.py] Homing {axis} Axis (Joint {joint_idx}) remotely...")
        py_code = f"""import linuxcnc, time
try:
    c=linuxcnc.command(); s=linuxcnc.stat(); s.poll()
    if s.task_mode != linuxcnc.MODE_MANUAL:
        c.mode(linuxcnc.MODE_MANUAL)
        c.wait_complete()
    if getattr(s, 'motion_teleop', False):
        c.teleop_enable(0)
        c.wait_complete()
    time.sleep(0.1)
    c.home({joint_idx})
except Exception as e: pass"""
        self._send_b64_py_cmd(py_code)

    def fast_probe(self):
        if self.is_estopped:
            QMessageBox.warning(self, "E-STOP", "Machine is in E-STOP state. Release E-STOP before probing.")
            return
            
        if not self.mock_coords.get('MACHINE_ON', False):
            QMessageBox.warning(self, "Power Off", "Machine power is OFF.\n\nPlease click the 'POWER ON' button before probing.")
            return

        agent_client = client_from_settings(self.settings)
        if agent_client is not None:
            self.run_touch_off_z()
            return

        probe_vel = float(self.settings.get_setting("PROBING", "probe_vel", 100.0))
        z_min = float(self.settings.get_setting("AXIS_Z", "min", -125.0))
        try:
            plate_thickness = float(self.input_z_thickness.text() or 0.0)
        except ValueError:
            plate_thickness = 0.0
        
        confirm = QMessageBox.question(self, "Confirm Probe", 
                                       f"Start Fast Z Probe?\n\nThe Z axis will move down until it hits the probe at {probe_vel} velocity, then set Z to {plate_thickness}.", 
                                       QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if confirm == QMessageBox.StandardButton.Yes:
            self.probe_selector.setCurrentIndex(0)
            print("[dashboard.py] Triggering Fast Z Probe")
            py_code = f"""import linuxcnc, time, sys, os
try:
    hal.set_p("lut5.0.in-2", "0")
    time.sleep(0.2)
    
    c = linuxcnc.command()
    s = linuxcnc.stat()
    
    e = getattr(linuxcnc, 'error_channel', lambda: None)()
    def wait_idle(timeout=120):
        start_time = time.time()
        err = e.poll()
        if err:
            sys.stderr.write(str(err[1]))
            sys.exit(1)
        while time.time() - start_time < timeout:
            s.poll()
            if s.interp_state == linuxcnc.INTERP_IDLE: return
            err = e.poll()
            if err:
                sys.stderr.write(str(err[1]))
                sys.exit(1)
            if s.task_state == linuxcnc.STATE_ESTOP:
                sys.stderr.write("Machine is in E-STOP state.")
                sys.exit(1)
            time.sleep(0.05)
        sys.stderr.write("MDI command timed out.")
        sys.exit(1)

    s.poll()
    if s.task_mode != linuxcnc.MODE_MDI:
        c.mode(linuxcnc.MODE_MDI)
        wait_idle()

    z_offset = s.g5x_offset[2] + s.g92_offset[2] + s.tool_offset[2]
    
    z_limit = {z_min}
    try: z_limit = s.axis[2]['min_position_limit']
    except: pass
    
    z_target = z_limit - z_offset + 0.1
    
    s.poll()
    current_z = s.position[2] - z_offset
    if z_target >= current_z:
        z_target = current_z - 200.0
        
    def run_mdi(cmd_str):
        c.mdi(cmd_str)
        time.sleep(0.05)
        wait_idle()
        
    run_mdi("G90")
    
    s.poll()
    if getattr(s, 'probe_val', 0):
        run_mdi("G91")
        run_mdi(f"G38.4 Z20.0 F{probe_vel}")
        run_mdi("G90")
        
    c.mdi(f"G38.2 Z{{z_target:.4f}} F{probe_vel}")
    time.sleep(0.1)
    wait_idle()
    
    s.poll()
    if s.probe_tripped:
        run_mdi("G10 L20 P0 Z{plate_thickness}")
        run_mdi("G90")
        run_mdi("G53 G0 Z0")
except Exception as e: pass"""
            self._send_b64_py_cmd(py_code, timeout=120)

    def set_z_zero(self):
        if self.is_estopped:
            return
        try:
            val = float(self.input_z_thickness.text() or 0.0)
            agent_client = client_from_settings(self.settings)
            if agent_client is not None:
                agent_client.touch_off_axis("Z", val)
                self.update_real_coords(agent_client.telemetry())
                self.update_status()
                return
            self.execute_mdi(f"G10 L20 P0 Z{val}")
            print(f"[dashboard.py] Z Zero set to {val}")
        except ValueError:
            print("[dashboard.py] Invalid input for Z Zero")


    def run_raw_z_probe(self):
        """Run the typed downward probe primitive without selecting hardware or changing offsets."""
        agent_client = client_from_settings(self.settings)
        if agent_client is None:
            QMessageBox.information(
                self,
                "Raw Z Probe Not Available",
                "Raw Z Probe is available only through the secure machine agent.",
            )
            return
        try:
            target = float(self.input_raw_probe_distance.text())
            velocity = float(self.settings.get_setting("PROBING", "probe_vel", 100.0))
        except ValueError:
            QMessageBox.warning(self, "Invalid Probe Input", "Enter a numeric downward Z travel distance.")
            return
        if target >= 0:
            QMessageBox.warning(self, "Invalid Probe Input", "Raw Z Probe requires a negative downward travel distance.")
            return
        confirm = QMessageBox.question(
            self,
            "Confirm Raw Z Probe",
            f"Probe downward {abs(target):g} units at {velocity:g} velocity, then retract 2 units?\n\n"
            "This operation does not change work offsets or select probe hardware.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return
        try:
            if self.active_jogs:
                self.jog_button_released()
            agent_client.probe_z(target, velocity)
        except Exception as error:
            self.error_signal.emit(f"Raw Z probe failed: {error}")

    def run_touch_off_z(self):
        """Run the typed local-touch-plate workflow and set only the Z work offset."""
        agent_client = client_from_settings(self.settings)
        if agent_client is None:
            QMessageBox.information(
                self,
                "Touch Plate Z Offset Not Available",
                "This touch-off workflow is available only through the secure machine agent.",
            )
            return
        try:
            target = float(self.input_raw_probe_distance.text())
            plate_thickness = float(self.input_z_thickness.text())
            velocity = float(self.settings.get_setting("PROBING", "probe_vel", 100.0))
        except ValueError:
            QMessageBox.warning(self, "Invalid Touch-Off Input", "Enter numeric plate thickness and downward Z travel values.")
            return
        if target >= 0:
            QMessageBox.warning(self, "Invalid Touch-Off Input", "Touch-off requires a negative downward Z travel distance.")
            return
        confirm = QMessageBox.question(
            self,
            "Confirm Touch Plate Z Offset",
            f"Probe downward {abs(target):g} units at {velocity:g} velocity, set the Z work offset to {plate_thickness:g}, then retract 2 units?\n\n"
            "The probe source must already be selected and verified locally.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return
        try:
            if self.active_jogs:
                self.jog_button_released()
            agent_client.touch_off_z(target, velocity, plate_thickness)
        except Exception as error:
            self.error_signal.emit(f"Touch-off failed: {error}")

    def update_status(self):
        """Refreshes DRO labels and LED indicator colors."""
        if getattr(self, '_last_update_state', None) is None:
            self._last_update_state = {}
            
        # Update the numbers in the dictionary instead of using old variable names
        dro_labels = getattr(self, 'dro_labels', {})
        dro_mode = getattr(self, 'dro_mode', 'WORK')
        for axis in ['X', 'Y', 'Z', 'FEED', 'RPM']:
            if axis in dro_labels:                
                if axis in ['X', 'Y', 'Z']:
                    if dro_mode == 'WORK':
                        val = self.mock_coords.get(axis, 0.0)
                    elif dro_mode == 'MACHINE':
                        val = self.mock_coords.get(f'ABSOLUTE_{axis}', 0.0)
                    elif dro_mode == 'DTG':
                        val = self.mock_coords.get(f'DTG_{axis}', 0.0)
                    else: # Fallback
                        val = self.mock_coords.get(axis, 0.0)
                    text = f"{val:.3f}"
                else: # FEED, RPM
                    val = self.mock_coords.get(axis, 0.0)
                    text = f"{val:.1f}"
                if self._last_update_state.get(f"dro_{axis}") != text:
                    dro_labels[axis].setText(text)
                    self._last_update_state[f"dro_{axis}"] = text
                    custom_label = getattr(self, 'custom_dro_labels', {}).get(axis)
                    if custom_label is not None:
                        custom_label.setText(text)

        # Update live tool position in the 3D viewer
        tool_x = self.mock_coords.get('X', 0.0)
        tool_y = self.mock_coords.get('Y', 0.0)
        tool_z = self.mock_coords.get('Z', 0.0)
        tool_pos = (tool_x, tool_y, tool_z)
        if self._last_update_state.get("tool_pos") != tool_pos:
            if hasattr(self, 'toolpath_viewer'):
                self.toolpath_viewer.update_tool_position(tool_x, tool_y, tool_z)
            self._last_update_state["tool_pos"] = tool_pos

        if 'FEED_OVR' in self.mock_coords and hasattr(self, 'feed_slider') and not self.feed_slider.isSliderDown():
            feed_val = int(self.mock_coords['FEED_OVR'] * 100)
            if self.feed_slider.value() != feed_val:
                self.feed_slider.blockSignals(True)
                self.feed_slider.setValue(feed_val)
                self.lbl_feed_val.setText(f"{feed_val}%")
                self.feed_slider.blockSignals(False)
                
        if 'SPINDLE_OVR' in self.mock_coords and hasattr(self, 'spindle_slider') and not self.spindle_slider.isSliderDown():
            spindle_val = int(self.mock_coords['SPINDLE_OVR'] * 100)
            if self.spindle_slider.value() != spindle_val:
                self.spindle_slider.blockSignals(True)
                self.spindle_slider.setValue(spindle_val)
                self.lbl_spindle_val.setText(f"{spindle_val}%")
                self.spindle_slider.blockSignals(False)

        # Update SSH LED Visuals
        current_time = time.time()
        leds = getattr(self, 'status_leds', {})
        conn_led = leds.get("CONNECTION")
        if conn_led:
            if self.ssh_connected:
                if self._last_update_state.get("ssh") != "online":
                    conn_led.setStyleSheet("background-color: #00FF00; color: black; border-radius: 4px; font-weight: bold; padding: 8px;")
                    conn_led.setText("MACHINE: ONLINE")
                    self._last_update_state["ssh"] = "online"
                self._was_connected_once = True
            else:
                if getattr(self, '_was_connected_once', False):
                    blink_state = "reconnect_1" if int(current_time * 2) % 2 == 0 else "reconnect_2"
                    if self._last_update_state.get("ssh") != blink_state:
                        if blink_state == "reconnect_1":
                            conn_led.setStyleSheet("background-color: #f39c12; color: black; border-radius: 4px; font-weight: bold; padding: 8px;")
                            conn_led.setText("MACHINE: RECONNECTING")
                        else:
                            conn_led.setStyleSheet("background-color: #c0392b; color: white; border-radius: 4px; font-weight: bold; padding: 8px;")
                            conn_led.setText("MACHINE: CONNECTION LOST")
                        self._last_update_state["ssh"] = blink_state
                else:
                    if self._last_update_state.get("ssh") != "offline":
                        conn_led.setStyleSheet("background-color: red; color: white; border-radius: 4px; font-weight: bold; padding: 8px;")
                        conn_led.setText("MACHINE: OFFLINE")
                        self._last_update_state["ssh"] = "offline"

        # Update LED Visuals
        
        # Drive 'Homed' status from the actual remote machine state
        _, _, _, expected_joints = self.get_kinematics_info()
        
        # Prefer the machine-reported joint count whenever secure telemetry exists.
        remote_joint_count = self.mock_coords.get('JOINTS', 0)
        remote_homed_count = self.mock_coords.get('HOMED_COUNT', 0)
        has_remote_homing_state = isinstance(remote_joint_count, (int, float)) and isinstance(remote_homed_count, (int, float)) and remote_joint_count > 0
        if has_remote_homing_state:
            is_homed = remote_homed_count >= remote_joint_count
            is_powered_on = self.mock_coords.get('MACHINE_ON', not self.is_estopped)
            
            if 'ESTOP_ACTIVATED' in self.mock_coords:
                self.is_estopped = self.mock_coords['ESTOP_ACTIVATED']
                self.machine_state = "ESTOP" if self.is_estopped else "STANDBY"
        elif getattr(self, 'ssh_connected', False):
            is_homed = expected_joints > 0 and remote_homed_count >= expected_joints
            is_powered_on = self.mock_coords.get('MACHINE_ON', not self.is_estopped)
        else:
            is_homed = expected_joints > 0 and all(not btn.isEnabled() for btn in getattr(self, 'home_buttons', {}).values())
            is_powered_on = not self.is_estopped
            
        status_text = self.mock_coords.get('STATUS', 'IDLE')
        current_mode = self.mock_coords.get('MODE', 'MANUAL')
        
        # --- Progress Bar Update ---
        current_line = self.mock_coords.get('LINE', 0)
        total_lines = self.mock_coords.get('TOTAL_LINES', 0)
        
        if total_lines == 0 and hasattr(self, 'default_gcode_editor'):
            if not self.default_gcode_editor.document().isEmpty():
                total_lines = self.default_gcode_editor.document().blockCount()
                
        if hasattr(self, 'progress_bar'):
            if total_lines > 0:
                pct = int((current_line / total_lines) * 100)
                pb_val = min(100, pct)
                pb_format = f"{pb_val}% (Line {current_line}/{total_lines})"
            else:
                pb_val = 0
                pb_format = "No Program Loaded"
                
            if self.progress_bar.value() != pb_val:
                self.progress_bar.setValue(pb_val)
            if self.progress_bar.format() != pb_format:
                self.progress_bar.setFormat(pb_format)
        # ---------------------------

        # Granular Cache: Modes
        if self._last_update_state.get("drawn_mode") != current_mode:
            self._last_update_state["drawn_mode"] = current_mode
            if hasattr(self, 'btn_mode_manual') and hasattr(self, 'btn_mode_auto'):
                btn_mdi = getattr(self, 'btn_mode_mdi', None)
                active_style = "QPushButton { background-color: #27ae60; color: white; font-weight: bold; border-radius: 4px; }"
                inactive_style = "QPushButton { background-color: #34495e; color: white; font-weight: bold; border-radius: 4px; } QPushButton:pressed { background-color: #2c3e50; }"
                self.btn_mode_manual.setStyleSheet(active_style if current_mode == 'MANUAL' else inactive_style)
                self.btn_mode_auto.setStyleSheet(active_style if current_mode == 'AUTO' else inactive_style)
                if btn_mdi:
                    btn_mdi.setStyleSheet(active_style if current_mode == 'MDI' else inactive_style)

        # Granular Cache: DRO Work/Abs/DTG
        dro_mode_val = getattr(self, 'dro_mode', 'WORK')
        if self._last_update_state.get("drawn_dro") != dro_mode_val:
            self._last_update_state["drawn_dro"] = dro_mode_val
            if hasattr(self, 'dro_mode_buttons'):
                for mode, btn in self.dro_mode_buttons.items():
                    btn.setStyleSheet(f"background-color: {'#2980b9' if mode == dro_mode_val else '#34495e'}; color: white; padding: 4px; border-radius: 3px; font-weight: bold;")

        # Granular Cache: Pause/Resume Button
        if self._last_update_state.get("drawn_pause") != status_text:
            self._last_update_state["drawn_pause"] = status_text
            if hasattr(self, 'btn_cycle_pause'):
                if status_text == 'PAUSED':
                    self.btn_cycle_pause.setText("▶ RESUME")
                    self.btn_cycle_pause.setStyleSheet("QPushButton { background-color: #3498db; color: white; font-weight: bold; border-radius: 4px; } QPushButton:pressed { background-color: #2980b9; }")
                else:
                    self.btn_cycle_pause.setText("⏸ PAUSE")
                    self.btn_cycle_pause.setStyleSheet("QPushButton { background-color: #f39c12; color: black; font-weight: bold; border-radius: 4px; } QPushButton:pressed { background-color: #c87f0a; }")

        # Granular Cache: Jog Controls Enablement
        jog_enabled = bool(is_powered_on and not self.is_estopped and (current_mode == 'MANUAL') and (status_text not in ('RUNNING', 'PAUSED')))
        if self._last_update_state.get("jog_enabled") != jog_enabled:
            self._last_update_state["jog_enabled"] = jog_enabled
            jog_buttons = getattr(self, 'jog_buttons', None)
            if isinstance(jog_buttons, dict):
                for jog_button in jog_buttons.values():
                    jog_button.setEnabled(jog_enabled)
            elif isinstance(jog_buttons, (list, tuple, set)):
                for jog_button in jog_buttons:
                    jog_button.setEnabled(jog_enabled)

        state_tuple = (self.is_estopped, is_powered_on, is_homed, status_text)

        if self._last_update_state.get("led_state") != state_tuple:
            self._last_update_state["led_state"] = state_tuple

            estop_led = leds.get("ESTOP")
            power_led = leds.get("POWER")
            homed_led = leds.get("HOMED")
            ready_led = leds.get("READY")

            if self.is_estopped:
                if estop_led: estop_led.setStyleSheet("background-color: red; color: white; font-weight: bold; border: 2px solid darkred; padding: 8px; border-radius: 4px;")
                if power_led: power_led.setStyleSheet("background-color: #222; color: #444; padding: 8px; border-radius: 4px;")
                if homed_led: homed_led.setStyleSheet("background-color: #555; color: white; font-weight: bold; padding: 8px; border-radius: 4px;")
                if ready_led:
                    ready_led.setText("OFFLINE")
                    ready_led.setStyleSheet("background-color: #555; color: white; font-weight: bold; padding: 8px; border-radius: 4px;")
                if hasattr(self, 'btn_power'):
                    self.btn_power.setText("POWER ON")
                    self.btn_power.setStyleSheet("QPushButton { background-color: #444; color: #888; font-weight: bold; font-size: 16px; border-radius: 5px; } QPushButton:pressed { background-color: #222; padding-top: 2px; padding-left: 2px; }")
                if hasattr(self, 'btn_master_toggle'):
                    self.btn_master_toggle.setText("RESET (E-STOP)")
                    self.btn_master_toggle.setStyleSheet("QPushButton { background-color: #c0392b; color: white; font-weight: bold; font-size: 20px; border-radius: 5px; } QPushButton:pressed { background-color: #922b21; padding-top: 2px; padding-left: 2px; }")
                for button in getattr(self, 'home_buttons', {}).values():
                    button.setEnabled(False)
                if hasattr(self, 'btn_home_all'):
                    self.btn_home_all.setEnabled(False)
            else:
                if estop_led: estop_led.setStyleSheet("background-color: #222; color: #444; padding: 8px; border-radius: 4px;")
                
                if is_powered_on:
                    if power_led: power_led.setStyleSheet("background-color: #00FF00; color: black; font-weight: bold; padding: 8px; border-radius: 4px;")
                    if hasattr(self, 'btn_power'):
                        self.btn_power.setText("POWER OFF")
                        self.btn_power.setStyleSheet("QPushButton { background-color: #e74c3c; color: white; font-weight: bold; font-size: 16px; border-radius: 5px; } QPushButton:pressed { background-color: #c0392b; padding-top: 2px; padding-left: 2px; }")
                else:
                    if power_led: power_led.setStyleSheet("background-color: #f39c12; color: black; font-weight: bold; padding: 8px; border-radius: 4px;")
                    if hasattr(self, 'btn_power'):
                        self.btn_power.setText("POWER ON")
                        self.btn_power.setStyleSheet("QPushButton { background-color: #7f8c8d; color: white; font-weight: bold; font-size: 16px; border-radius: 5px; } QPushButton:pressed { background-color: #616a6b; padding-top: 2px; padding-left: 2px; }")
                if hasattr(self, 'btn_master_toggle'):
                    self.btn_master_toggle.setText("E-STOP")
                    self.btn_master_toggle.setStyleSheet("QPushButton { background-color: orange; color: black; font-weight: bold; font-size: 20px; border-radius: 5px; } QPushButton:pressed { background-color: #cc8400; padding-top: 2px; padding-left: 2px; }")
                for button in getattr(self, 'home_buttons', {}).values():
                    button.setEnabled(True)
                if hasattr(self, 'btn_home_all'):
                    self.btn_home_all.setEnabled(True)

                self.is_machine_homed = is_homed

                if is_homed:
                    if homed_led: homed_led.setStyleSheet("background-color: #00FF00; color: black; font-weight: bold; padding: 8px; border-radius: 4px;")
                    if ready_led:
                        ready_led.setText(f"STATE: {status_text}")
                        if status_text == 'RUNNING':
                            ready_led.setStyleSheet("background-color: #39FF14; color: black; font-weight: bold; padding: 8px; border-radius: 4px;")
                        elif status_text == 'PAUSED':
                            ready_led.setStyleSheet("background-color: #f1c40f; color: black; font-weight: bold; padding: 8px; border-radius: 4px;")
                        else:
                            ready_led.setStyleSheet("background-color: #3498db; color: white; font-weight: bold; padding: 8px; border-radius: 4px;")
                        
                    for btn in getattr(self, 'home_buttons', {}).values():
                        btn.setEnabled(False) # Visually disable individual home buttons
                    if hasattr(self, 'btn_home_all'):
                        self.btn_home_all.setEnabled(True)
                        self.btn_home_all.setText("UNHOME ALL")
                        self.btn_home_all.setStyleSheet("QPushButton { background-color: #e74c3c; color: white; font-weight: bold; font-size: 16px; border-radius: 5px; } QPushButton:pressed { background-color: #c0392b; padding-top: 2px; padding-left: 2px; }")
                else:
                    if homed_led: homed_led.setStyleSheet("background-color: #f39c12; color: black; font-weight: bold; padding: 8px; border-radius: 4px;")
                    if ready_led:
                        ready_led.setText("UNHOMED")
                        ready_led.setStyleSheet("background-color: #f39c12; color: black; font-weight: bold; padding: 8px; border-radius: 4px;")
                    for btn in getattr(self, 'home_buttons', {}).values():
                        btn.setEnabled(True) # Re-enable if machine loses homed state
                    if hasattr(self, 'btn_home_all'):
                        self.btn_home_all.setEnabled(True)
                        self.btn_home_all.setText("HOME ALL")
                        self.btn_home_all.setStyleSheet("QPushButton { background-color: #27ae60; color: white; font-weight: bold; font-size: 16px; border-radius: 5px; } QPushButton:pressed { background-color: #229954; padding-top: 2px; padding-left: 2px; }")
                        

    def home_all_axes(self):
        """Homes all axes simultaneously."""
        agent_client = client_from_settings(self.settings)
        if agent_client is not None:
            try:
                telemetry = agent_client.telemetry()
                self.update_real_coords(telemetry)
                machine_is_estopped = telemetry.get("ESTOP_ACTIVATED", False)
                is_machine_homed = telemetry.get("JOINTS", 0) > 0 and telemetry.get("HOMED_COUNT", 0) >= telemetry.get("JOINTS", 0)
            except Exception as error:
                self.error_signal.emit(f"Home state unavailable: {error}")
                return
        else:
            machine_is_estopped = getattr(self, 'is_estopped', False)
            is_machine_homed = getattr(self, 'is_machine_homed', False)

        if machine_is_estopped:
            QMessageBox.warning(self, "E-STOP", "Cannot home machine while E-STOP is active.\nPlease release the E-STOP first.")
            return

        if is_machine_homed:
            confirm = QMessageBox.question(self, "Clear Homed State?",
                                           "The machine is currently homed.\n\nDo you want to unhome all axes so you can home them again?",
                                           QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if confirm != QMessageBox.StandardButton.Yes:
                return

            if agent_client is not None:
                self.unhome_all_worker = AgentUnhomeAllWorker(agent_client)
                self.btn_home_all.setEnabled(False)
                self.btn_home_all.setText("UNHOMING...")
                self.unhome_all_worker.completed.connect(self._unhome_all_completed)
                self.unhome_all_worker.failed.connect(self._unhome_all_failed)
                self.unhome_all_worker.start()
                return

            print("[dashboard.py] Unhoming all axes remotely...")
            py_code = """import linuxcnc, time
try:
    c = linuxcnc.command()
    s = linuxcnc.stat()
    s.poll()
    
    if s.task_mode != linuxcnc.MODE_MANUAL:
        c.mode(linuxcnc.MODE_MANUAL)
        c.wait_complete()
        
    c.teleop_enable(0)
        
    c.wait_complete()
    time.sleep(0.2)

    c.unhome(-1) # Unhome all axes
except Exception as e:
    pass
"""
            self._send_b64_py_cmd(py_code)
            return

        agent_client = client_from_settings(self.settings)
        if agent_client is not None:
            try:
                self.home_all_worker = AgentHomeAllWorker(agent_client)
                self.btn_home_all.setEnabled(False)
                self.btn_home_all.setText("HOMING...")
                self.home_all_worker.progress.connect(self.btn_home_all.setText)
                for button in self.custom_home_all_buttons:
                    button.setEnabled(False)
                    button.setText("HOMING...")
                    self.home_all_worker.progress.connect(button.setText)
                self.home_all_worker.completed.connect(self._home_all_completed)
                self.home_all_worker.failed.connect(self._home_all_failed)
                self.home_all_worker.start()
            except Exception as error:
                self.error_signal.emit(f"Home all failed: {error}")
            return

        print("[dashboard.py] Homing all axes remotely...")
        py_code = """import linuxcnc, time
try:
    c = linuxcnc.command()
    s = linuxcnc.stat()
    s.poll()
    
    if s.task_mode != linuxcnc.MODE_MANUAL:
        c.mode(linuxcnc.MODE_MANUAL)
        c.wait_complete()
        
    c.teleop_enable(0)
        
    c.wait_complete()
    time.sleep(0.2)

    c.unhome(-1)
    c.wait_complete()
    time.sleep(0.1)
    c.home(-1)
except Exception as e:
    pass
"""
        self._send_b64_py_cmd(py_code)

    def _home_all_completed(self):
        """Refresh secure state after the named homing operations finish."""
        self.btn_home_all.setEnabled(True)
        agent_client = client_from_settings(self.settings)
        if agent_client is not None:
            try:
                telemetry = agent_client.telemetry()
                self.update_real_coords(telemetry)
                joints = int(telemetry.get("JOINTS", 0))
                homed = int(telemetry.get("HOMED_COUNT", 0))
                self.btn_home_all.setText("UNHOME ALL" if joints > 0 and homed >= joints else "HOME ALL")
                for button in self.custom_home_all_buttons:
                    button.setEnabled(True)
                    button.setText("UNHOME ALL" if joints > 0 and homed >= joints else "HOME ALL")
            except Exception as error:
                self.error_signal.emit(f"Home state refresh failed: {error}")
        self.update_status()

    def _home_all_failed(self, error):
        """Restore the home control and expose the verified failure state."""
        self.btn_home_all.setEnabled(True)
        self.btn_home_all.setText("HOME ALL")
        for button in self.custom_home_all_buttons:
            button.setEnabled(True)
            button.setText("HOME ALL")
        self.error_signal.emit(f"Home all failed: {error}")
        agent_client = client_from_settings(self.settings)
        if agent_client is not None:
            try:
                self.update_real_coords(agent_client.telemetry())
                self.update_status()
            except Exception:
                pass

    def _unhome_all_completed(self):
        """Refresh secure state after unhoming completes."""
        self.btn_home_all.setEnabled(True)
        self.btn_home_all.setText("HOME ALL")
        self._last_update_state.pop("led_state", None)
        agent_client = client_from_settings(self.settings)
        if agent_client is not None:
            try:
                self.update_real_coords(agent_client.telemetry())
            except Exception as error:
                self.error_signal.emit(f"Unhome state refresh failed: {error}")
        self.update_status()

    def _unhome_all_failed(self, error):
        """Restore the button and surface an unhome failure."""
        self.btn_home_all.setEnabled(True)
        self.btn_home_all.setText("HOME ALL")
        self._last_update_state.pop("led_state", None)
        self.error_signal.emit(f"Unhome all failed: {error}")

    def send_ping(self):
        """Silently checks if the LinuxCNC machine is reachable on the network."""
        if client_from_settings(self.settings) is not None:
            return
        ip = self.settings.get_setting("SSH", "ip_address", "")
        if is_local_controller(ip):
            was_connected = self.ssh_connected
            self.ssh_connected = is_local_linuxcnc_running()
            if self.ssh_connected and not was_connected:
                self.start_position_fetcher()
            elif was_connected and not self.ssh_connected:
                self.stop_position_fetcher()
            return
        if self.ping_process.state() == QProcess.ProcessState.NotRunning:
            if not ip:
                self.ssh_connected = False
                return
            if platform.system().lower() == "windows":
                self.ping_process.start("ping", ["-n", "1", "-w", "1000", ip])
            else:
                self.ping_process.start("ping", ["-c", "1", "-W", "1", ip])

    def handle_ping_result(self, exitCode, exitStatus):
        if client_from_settings(self.settings) is not None:
            return
        was_connected = self.ssh_connected
        self.ssh_connected = (exitCode == 0 and exitStatus == QProcess.ExitStatus.NormalExit)
        
        if self.ssh_connected and not was_connected:
            self.start_position_fetcher()
        elif not self.ssh_connected and was_connected:
            self.stop_position_fetcher()

    def start_position_fetcher(self):
        self.settings.reload()
        ip = self.settings.get_setting("SSH", "ip_address", "")
        user = self.settings.get_setting("SSH", "username", "")
        password = self.settings.get_setting("SSH", "password", "")
        agent_client = client_from_settings(self.settings)
        
        if agent_client is not None:
            self.pos_fetcher = PositionFetcherThread(ip, user, password, agent_client)
            self.pos_fetcher.position_fetched.connect(self.handle_secure_telemetry)
            self.pos_fetcher.error.connect(self.handle_secure_telemetry_error)
            self.pos_fetcher.start()
            return

        if is_local_controller(ip):
            if not self.pos_fetcher:
                self.pos_fetcher = PositionFetcherThread(ip or "127.0.0.1", user, password)
                self.pos_fetcher.position_fetched.connect(self.update_real_coords)
                self.pos_fetcher.error.connect(self.show_error_message)
                self.pos_fetcher.start()
            return

        if not ip or not user: return

        if not self.pos_fetcher:
            self.pos_fetcher = PositionFetcherThread(ip, user, password)
            self.pos_fetcher.position_fetched.connect(self.update_real_coords)
            self.pos_fetcher.error.connect(self.show_error_message)
            self.pos_fetcher.start()

    def handle_secure_telemetry(self, coords):
        """Treat a successful authenticated telemetry snapshot as machine connectivity."""
        self._telemetry_error_count = 0
        self.ssh_connected = True
        self.update_real_coords(coords)

    def handle_secure_telemetry_error(self, message):
        """Mark the secure transport offline without presenting polling errors as motion errors."""
        self._telemetry_error_count = getattr(self, "_telemetry_error_count", 0) + 1
        if hasattr(self, 'status_leds') and "CONNECTION" in self.status_leds:
            self.status_leds["CONNECTION"].setToolTip(message)

    def stop_position_fetcher(self):
        if self.pos_fetcher:
            self.pos_fetcher.stop()
            self.pos_fetcher.wait()
            self.pos_fetcher = None

    def update_real_coords(self, coords):
        if getattr(self, '_last_update_state', None) is None:
            self._last_update_state = {}
        for axis in ['X', 'Y', 'Z', 'FEED', 'RPM']:
            if axis in coords:
                self.mock_coords[axis] = coords[axis]
                
        for key in ['HOMED_COUNT', 'MACHINE_ON', 'ESTOP_ACTIVATED', 'JOINTS', 'AXIS_MASK', 'STATUS', 'MODE', 'PROGRAM_LOADED', 'TOTAL_LINES', 'LINE', 'CURRENT_LINE', 'READ_LINE', 'EXEC_STATE', 'INTERP_STATE', 'TASK_STATE', 'COMMAND', 'PROGRAM_FILE', 'LIMITS_ACTIVE', 'ACTIVE_LIMITS', 'IS_PAUSED', 'PROBE_VAL', 'probe_tripped']:
            if key in coords:
                self.mock_coords[key] = coords[key]
        self.ssh_connected = True
        self.is_estopped = bool(coords.get('ESTOP_ACTIVATED', self.is_estopped))
                
        for key in ['FEED_OVR', 'SPINDLE_OVR', 'ABSOLUTE_X', 'ABSOLUTE_Y', 'ABSOLUTE_Z', 'DTG_X', 'DTG_Y', 'DTG_Z']:
            if key in coords:
                self.mock_coords[key] = coords[key]

        for axis in ("X", "Y", "Z", "FEED", "RPM"):
            custom_label = self.custom_dro_labels.get(axis)
            if custom_label is not None:
                if axis in ("X", "Y", "Z"):
                    value = coords.get(axis, self.mock_coords.get(axis, 0.0))
                    text = f"{value:.3f}"
                else:
                    value = coords.get(axis, self.mock_coords.get(axis, 0.0))
                    text = f"{value:.1f}"
                if self._last_update_state.get(f"custom_dro_{axis}") != text:
                    self._last_update_state[f"custom_dro_{axis}"] = text
                    custom_label.setText(text)

        homed = int(coords.get("HOMED_COUNT", self.mock_coords.get("HOMED_COUNT", 0)))
        joints = int(coords.get("JOINTS", self.mock_coords.get("JOINTS", 0)))
        self.ssh_connected = True
        self.is_estopped = bool(coords.get("ESTOP_ACTIVATED", self.is_estopped))
        homed_active = joints > 0 and homed >= joints
        if self._last_update_state.get("custom_homed_active") != homed_active:
            self._last_update_state["custom_homed_active"] = homed_active
            for title, label in self.custom_status_labels.items():
                if "HOMED" in title or "READY" in title:
                    label.setStyleSheet(
                        "background-color: #00FF00; color: black; font-weight: bold;"
                        if homed_active else
                        "background-color: #f39c12; color: black; font-weight: bold;"
                    )

        if 'TOOL_IN_SPINDLE' in coords:
            self.mock_coords['TOOL_IN_SPINDLE'] = coords['TOOL_IN_SPINDLE']
        if 'POCKET_PREPPED' in coords:
            self.mock_coords['POCKET_PREPPED'] = coords['POCKET_PREPPED']
        if 'TASK_BLOCKED' in coords:
            self.mock_coords['TASK_BLOCKED'] = coords['TASK_BLOCKED']
        self._check_tool_change_request()

        if 'ERROR' in coords and coords['ERROR']:
            if getattr(self, '_last_lcnc_err', '') != coords['ERROR']:
                QMessageBox.warning(self, "LinuxCNC Error", f"The machine aborted with the following error:\n\n{coords['ERROR']}")
                self._last_lcnc_err = coords['ERROR']
                
        if 'LINE' in coords:
            new_line = max(0, coords['LINE'] - 1)
            if getattr(self, '_last_highlighted_line', -1) != new_line:
                self.highlight_gcode_line(new_line)
                self._last_highlighted_line = new_line

        if hasattr(self, 'update_status'):
            self.update_status()
            
    def highlight_gcode_line(self, line_number):
        # Add Toolpath Highlight
        if hasattr(self, 'toolpath_viewer') and self.toolpath_viewer:
            self.toolpath_viewer.highlight_line(line_number)

        if not hasattr(self, 'default_gcode_editor') or not self.default_gcode_editor:
            return

        if self.__dict__.get('_is_large_file_preview', False):
            doc = self.default_gcode_editor.document()
            if doc and line_number >= doc.blockCount():
                return
        
        selection = QTextEdit.ExtraSelection()
        selection.format.setBackground(QColor("#1f618d")) # A vibrant blue selection background
        selection.format.setProperty(QTextFormat.Property.FullWidthSelection, True)
        
        cursor = self.default_gcode_editor.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.Start)
        cursor.movePosition(QTextCursor.MoveOperation.Down, QTextCursor.MoveMode.MoveAnchor, line_number)
        
        selection.cursor = cursor
        selection.cursor.clearSelection()
        
        self.default_gcode_editor.setExtraSelections([selection])
        
        # Move the actual editor cursor so we can track its position
        self.default_gcode_editor.setTextCursor(cursor)
        
        self.default_gcode_editor.centerCursor()
    def init_heartbeat(self):
        """Starts the background timer for DRO and LED updates."""
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.update_status)
        self.timer.start(100)  # 10Hz

    def get_kinematics_info(self):
        """Dynamically determines kinematics from remote state to prevent gantry racking if local settings mismatch."""
        joints = self.mock_coords.get('JOINTS', 0)
        axis_mask = self.mock_coords.get('AXIS_MASK', 0)
        
        if joints > 0 and axis_mask > 0:
            num_axes = bin(axis_mask).count('1')
            is_gantry = joints > num_axes
            is_4_axis = (num_axes == 4 and not is_gantry)
            is_5_axis = (num_axes == 5)
            total_joints = joints
        else:
            # Fallback to local settings if remote hasn't sent data yet
            k_str = self.settings.get_setting('MACHINE_CONFIG', 'kinematics', 'XYZ')
            is_gantry = 'XYYZ' in k_str
            is_4_axis = '4-Axis' in k_str
            is_5_axis = '5-Axis' in k_str
            total_joints = 4 if is_gantry or is_4_axis else (5 if is_5_axis else 3)
            
        return is_gantry, is_4_axis, is_5_axis, total_joints

    def jog_button_pressed(self, axis, direction, speed_multiplier=1):
        agent_client = client_from_settings(self.settings)
        if agent_client is not None:
            max_vel = float(self.settings.get_setting(f"AXIS_{axis}", "vel", 50.0))
            velocity = (self.jog_speed_slider.value() / 100.0) * max_vel * speed_multiplier * direction
            self.active_jogs.add(axis)
            try:
                agent_client.jog_start(axis, velocity)
            except Exception as error:
                self.active_jogs.discard(axis)
                self.error_signal.emit(f"Jog start failed: {error}")
            return

        if self.is_estopped or self.mock_coords.get('MACHINE_ON') is False:
            return

        # Jogging is strictly forbidden when a program is running, paused, or in any mode other than MANUAL
        if self.mock_coords.get('STATUS') in ('RUNNING', 'PAUSED') or self.mock_coords.get('MODE', 'MANUAL') != 'MANUAL':
            return

        is_gantry, is_4_axis, is_5_axis, total_joints = self.get_kinematics_info()

        if getattr(self.settings, "local_only", False) and getattr(self, "local_api", None):
            try:
                status = self.local_api.status()
                status.poll()
                if status.task_state != self.local_api.linuxcnc.STATE_ON:
                    return
                if status.task_mode != self.local_api.linuxcnc.MODE_MANUAL:
                    return
                istate = getattr(status, 'interp_state', 1)
                if isinstance(istate, int) and istate != getattr(self.local_api.linuxcnc, 'INTERP_IDLE', 1):
                    return

                homed_count = sum(1 for h in getattr(status, 'homed', []) if h)
                joints = int(getattr(status, 'joints', getattr(status, 'axes', total_joints)))
                is_homed = (homed_count >= joints)

                if is_gantry and axis == 'Y' and not is_homed:
                    self.active_jogs.discard(axis)
                    QMessageBox.warning(
                        self,
                        "Homing Required",
                        f"Cannot manually jog the {axis} axis before homing!\n\n"
                        f"Because your machine is configured as a Gantry (XYYZ), the {axis} axis uses dual independent motors. "
                        f"Jogging them before the machine has executed its squaring sequence can rack and damage your linear rails.\n\n"
                        f"Please click 'Home All' or 'Home {axis}' to safely synchronize the motors first.",
                    )
                    return

                self.active_jogs.add(axis)
                max_vel = float(self.settings.get_setting(f"AXIS_{axis}", "vel", 50.0))
                slider = getattr(self, "jog_speed_slider", None)
                slider_pct = (slider.value() / 100.0) if slider is not None else 1.0
                velocity = slider_pct * max_vel * speed_multiplier * direction

                if is_gantry:
                    joint_idx = {'X': 0, 'Y': 1, 'Z': 3}.get(axis, 0)
                elif is_4_axis:
                    joint_idx = {'X': 0, 'Y': 1, 'Z': 2, 'A': 3}.get(axis, 0)
                elif is_5_axis:
                    joint_idx = {'X': 0, 'Y': 1, 'Z': 2, 'A': 3, 'B': 4}.get(axis, 0)
                else:
                    joint_idx = {'X': 0, 'Y': 1, 'Z': 2}.get(axis, 0)

                axis_idx = {'X': 0, 'Y': 1, 'Z': 2, 'A': 3, 'B': 4}.get(axis, 0)

                command = self.local_api.command()
                if is_homed:
                    if not getattr(status, 'motion_teleop', False):
                        command.teleop_enable(1)
                    command.jog(self.local_api.linuxcnc.JOG_CONTINUOUS, False, axis_idx, velocity)
                else:
                    if getattr(status, 'motion_teleop', False):
                        command.teleop_enable(0)
                    command.jog(self.local_api.linuxcnc.JOG_CONTINUOUS, True, joint_idx, velocity)
            except Exception as error:
                self.active_jogs.discard(axis)
                self.error_signal.emit(f"Jog start failed: {error}")
            return

        # Remote / fallback path
        homed_count = self.mock_coords.get('HOMED_COUNT')
        is_homed = None if homed_count is None else int(homed_count) >= total_joints

        if is_gantry and axis == 'Y' and is_homed is False:
            self.active_jogs.discard(axis)
            QMessageBox.warning(
                self,
                "Homing Required",
                f"Cannot manually jog the {axis} axis before homing!\n\n"
                f"Because your machine is configured as a Gantry (XYYZ), the {axis} axis uses dual independent motors. "
                f"Jogging them before the machine has executed its squaring sequence can rack and damage your linear rails.\n\n"
                f"Please click 'Home All' or 'Home {axis}' to safely synchronize the motors first.",
            )
            return

        self.active_jogs.add(axis)
        max_vel = float(self.settings.get_setting(f"AXIS_{axis}", "vel", 50.0))
        velocity = (self.jog_speed_slider.value() / 100.0) * max_vel * speed_multiplier * direction

        if is_gantry:
            joint_idx = {'X': 0, 'Y': 1, 'Z': 3}.get(axis, 0)
        elif is_4_axis:
            joint_idx = {'X': 0, 'Y': 1, 'Z': 2, 'A': 3}.get(axis, 0)
        elif is_5_axis:
            joint_idx = {'X': 0, 'Y': 1, 'Z': 2, 'A': 3, 'B': 4}.get(axis, 0)
        else:
            joint_idx = {'X': 0, 'Y': 1, 'Z': 2}.get(axis, 0)

        axis_idx = {'X': 0, 'Y': 1, 'Z': 2, 'A': 3, 'B': 4}.get(axis, 0)

        py_code = f"""import linuxcnc
import time
try:
    c=linuxcnc.command(); s=linuxcnc.stat(); s.poll()
    if s.task_mode != linuxcnc.MODE_MANUAL:
        c.mode(linuxcnc.MODE_MANUAL)
        time.sleep(0.1)
    homed_count = sum(1 for h in getattr(s, 'homed', []) if h)
    joints = getattr(s, 'joints', getattr(s, 'axes', 3))
    if homed_count >= joints:
        if not getattr(s, 'motion_teleop', False):
            c.teleop_enable(1)
            time.sleep(0.1)
        c.jog(linuxcnc.JOG_CONTINUOUS, False, {axis_idx}, {velocity})
    else:
        if getattr(s, 'motion_teleop', False):
            c.teleop_enable(0)
            time.sleep(0.1)
        c.jog(linuxcnc.JOG_CONTINUOUS, True, {joint_idx}, {velocity})
except Exception as e: 
    with open('/tmp/linuxcnc_jog_debug.txt', 'a') as f:
        f.write("JOG PRESS ERR: " + str(e) + "\\n")
"""
        self._send_b64_py_cmd(py_code, urgent=True)

    def jog_button_released(self, axis=None):
        agent_client = client_from_settings(self.settings)
        if agent_client is not None:
            axes_to_stop = [axis] if axis else list(self.active_jogs)
            for ax in axes_to_stop:
                self.active_jogs.discard(ax)
                try:
                    agent_client.jog_stop(ax)
                except Exception as error:
                    self.error_signal.emit(f"Jog stop failed: {error}")
            return

        axes_to_stop = [axis] if axis else list(self.active_jogs)
        for ax in axes_to_stop:
            self.active_jogs.discard(ax)

        if not axes_to_stop:
            return

        is_gantry, is_4_axis, is_5_axis, total_joints = self.get_kinematics_info()

        if getattr(self.settings, "local_only", False) and getattr(self, "local_api", None):
            try:
                command = self.local_api.command()
                status = self.local_api.status()
                status.poll()
                if status.task_state == self.local_api.linuxcnc.STATE_ESTOP:
                    self.active_jogs.clear()
                    return

                homed_count = sum(1 for h in getattr(status, 'homed', []) if h)
                joints = int(getattr(status, 'joints', getattr(status, 'axes', total_joints)))
                is_homed = (homed_count >= joints)

                for ax in axes_to_stop:
                    if is_gantry: ax_joint_idx = {'X': 0, 'Y': 1, 'Z': 3}.get(ax, 0)
                    elif is_4_axis: ax_joint_idx = {'X': 0, 'Y': 1, 'Z': 2, 'A': 3}.get(ax, 0)
                    elif is_5_axis: ax_joint_idx = {'X': 0, 'Y': 1, 'Z': 2, 'A': 3, 'B': 4}.get(ax, 0)
                    else: ax_joint_idx = {'X': 0, 'Y': 1, 'Z': 2}.get(ax, 0)
                    ax_axis_idx = {'X': 0, 'Y': 1, 'Z': 2, 'A': 3, 'B': 4}.get(ax, 0)

                    if is_homed:
                        command.jog(self.local_api.linuxcnc.JOG_STOP, False, ax_axis_idx)
                    else:
                        command.jog(self.local_api.linuxcnc.JOG_STOP, True, ax_joint_idx)
            except Exception as error:
                self.error_signal.emit(f"Jog stop failed: {error}")
            return

        for ax in axes_to_stop:
            if is_gantry: joint_idx = {'X': 0, 'Y': 1, 'Z': 3}.get(ax, 0)
            elif is_4_axis: joint_idx = {'X': 0, 'Y': 1, 'Z': 2, 'A': 3}.get(ax, 0)
            elif is_5_axis: joint_idx = {'X': 0, 'Y': 1, 'Z': 2, 'A': 3, 'B': 4}.get(ax, 0)
            else: joint_idx = {'X': 0, 'Y': 1, 'Z': 2}.get(ax, 0)
                
            axis_idx = {'X': 0, 'Y': 1, 'Z': 2, 'A': 3, 'B': 4}.get(ax, 0)
            
            py_code = f"""import linuxcnc, sys
try:
    c=linuxcnc.command(); s=linuxcnc.stat(); s.poll()
    if s.task_state == linuxcnc.STATE_ESTOP:
        sys.exit(0)
    homed_count = sum(1 for h in getattr(s, 'homed', []) if h)
    joints = getattr(s, 'joints', getattr(s, 'axes', 3))
    if homed_count >= joints:
        c.jog(linuxcnc.JOG_STOP, False, {axis_idx})
    else:
        c.jog(linuxcnc.JOG_STOP, True, {joint_idx})
except Exception as e:
    with open('/tmp/linuxcnc_jog_debug.txt', 'a') as f:
        f.write("JOG REL ERR: " + str(e) + "\\n")
"""
            self._send_b64_py_cmd(py_code, urgent=True)

    def keyPressEvent(self, event):
        if event is None or event.isAutoRepeat():
            return

        key = event.key()

        # Store the currently pressed key
        self.current_key = key
        
        if key == Qt.Key.Key_F11:
            window = self.window()
            if window.isFullScreen():
                window.showNormal()
            else:
                window.showFullScreen()
            return

        # Check if Shift is held for Fast Jog
        speed_multiplier = 2 if event.modifiers() & Qt.KeyboardModifier.ShiftModifier else 1
        
        if key == Qt.Key.Key_Escape:
            self.toggle_estop()
            return

        if self.is_estopped or self.mock_coords.get('MACHINE_ON') is False:
            return

        if self.mock_coords.get('STATUS') in ('RUNNING', 'PAUSED') or self.mock_coords.get('MODE', 'MANUAL') != 'MANUAL':
            return

        # Allow input fields (MDI, text editors, etc.) to receive keyboard input
        focused_widget = self.focusWidget()
        if focused_widget is not None and isinstance(focused_widget, (QLineEdit, QTextEdit, QPlainTextEdit)):
            super().keyPressEvent(event)
            return

        if client_from_settings(self.settings) is not None:
            if key in [Qt.Key.Key_Up, Qt.Key.Key_Down, Qt.Key.Key_Left, Qt.Key.Key_Right, Qt.Key.Key_PageUp, Qt.Key.Key_PageDown]:
                event.accept()
            else:
                super().keyPressEvent(event)
            return

        # Jogging Logic with PyQt6 naming and speed multiplier
        if key in [Qt.Key.Key_Up, Qt.Key.Key_Down, Qt.Key.Key_Left, Qt.Key.Key_Right, Qt.Key.Key_PageUp, Qt.Key.Key_PageDown]:
            event.accept() # PREVENT tabs from stealing focus!
            if key == Qt.Key.Key_Up:
                self.jog_button_pressed('Y', 1, speed_multiplier)
            elif key == Qt.Key.Key_Down:
                self.jog_button_pressed('Y', -1, speed_multiplier)
            elif key == Qt.Key.Key_Left:
                self.jog_button_pressed('X', -1, speed_multiplier)
            elif key == Qt.Key.Key_Right:
                self.jog_button_pressed('X', 1, speed_multiplier)
            elif key == Qt.Key.Key_PageUp:
                self.jog_button_pressed('Z', 1, speed_multiplier)
            elif key == Qt.Key.Key_PageDown:
                self.jog_button_pressed('Z', -1, speed_multiplier)
        else:
            super().keyPressEvent(event)

    def focusOutEvent(self, event):
        super().focusOutEvent(event)
        active = self.__dict__.get('active_jogs')
        if active:
            for axis in list(active):
                self.jog_button_released(axis)
        self.current_key = None

    def keyReleaseEvent(self, event):
        if event is None or event.isAutoRepeat():
            return

        key = event.key()

        # Handle Shift release without stopping jogging
        if key == Qt.Key.Key_Shift:
            if self.current_key in [Qt.Key.Key_Up, Qt.Key.Key_Down, Qt.Key.Key_Left, Qt.Key.Key_Right, Qt.Key.Key_PageUp, Qt.Key.Key_PageDown]:
                event.accept()
                # Reapply jogging with normal speed multiplier
                speed_multiplier = 1
                if self.current_key == Qt.Key.Key_Up:
                    self.jog_button_pressed('Y', 1, speed_multiplier)
                elif self.current_key == Qt.Key.Key_Down:
                    self.jog_button_pressed('Y', -1, speed_multiplier)
                elif self.current_key == Qt.Key.Key_Left:
                    self.jog_button_pressed('X', -1, speed_multiplier)
                elif self.current_key == Qt.Key.Key_Right:
                    self.jog_button_pressed('X', 1, speed_multiplier)
                elif self.current_key == Qt.Key.Key_PageUp:
                    self.jog_button_pressed('Z', 1, speed_multiplier)
                elif self.current_key == Qt.Key.Key_PageDown:
                    self.jog_button_pressed('Z', -1, speed_multiplier)
            return

        # Only stop jogging if an arrow key is released
        if key in [Qt.Key.Key_Up, Qt.Key.Key_Down, Qt.Key.Key_Left, Qt.Key.Key_Right, Qt.Key.Key_PageUp, Qt.Key.Key_PageDown]:
            event.accept() # Stop from bubbling to tabs
            
            axis = None
            if key in [Qt.Key.Key_Up, Qt.Key.Key_Down]: axis = 'Y'
            elif key in [Qt.Key.Key_Left, Qt.Key.Key_Right]: axis = 'X'
            elif key in [Qt.Key.Key_PageUp, Qt.Key.Key_PageDown]: axis = 'Z'
            
            if axis:
                self.jog_button_released(axis)
                
            if self.current_key == key:
                self.current_key = None
            return
            
        super().keyReleaseEvent(event)

    def update_local_settings(self):
        """Refreshes the dashboard variables when settings change."""
        print("--- [dashboard.py] Signal Received: Updating live settings ---")
        machine_units_str = self.settings.get_setting('MACHINE_CONFIG', 'units', 'Metric (mm)')
        display_units_str = self.settings.get_setting('UI_PREFERENCES', 'display_units', 'Metric (mm)')
        self.display_units = "inch" if "Imperial" in display_units_str else "mm"
        if hasattr(self, 'toolpath_viewer'):
            self.toolpath_viewer.machine_units = "inch" if "Imperial" in machine_units_str else "mm"
            self.toolpath_viewer.display_units = self.display_units
            preview_pref = self.settings.get_setting('UI_PREFERENCES', 'enable_toolpath_preview', 'True') == 'True'
            if getattr(self.toolpath_viewer, 'preview_enabled', True) != preview_pref:
                target_path = self.__dict__.get('loaded_gcode_path')
                gcode = ""
                if not self.__dict__.get('_is_large_file_preview', False):
                    editor = self.__dict__.get('default_gcode_editor')
                    gcode = editor.toPlainText() if editor else ""
                self.toolpath_viewer.set_preview_enabled(preview_pref, gcode_text=gcode, file_path=target_path)
                self.update_preview_toggle_button()
        self.jog_speed = float(self.settings.get_setting('JOG_SETTINGS', 'jog_speed', 100))
        self.turbo_multiplier = float(self.settings.get_setting('JOG_SETTINGS', 'turbo_multiplier', 5.0))
        self.update_status() # Force a full redraw with new units