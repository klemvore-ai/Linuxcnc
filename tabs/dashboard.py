from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, 
                             QLabel, QPushButton, QGroupBox, QSlider, QLineEdit, 
                             QSizePolicy, QScrollArea, QMessageBox, QStackedWidget, QFileDialog, QTextEdit, QInputDialog, QGraphicsDropShadowEffect, QTabWidget, QGraphicsView, QGraphicsScene, QComboBox, QGraphicsItem, QProgressBar, QAbstractButton)
from PyQt6.QtCore import Qt, QTimer, QProcess, QThread, pyqtSignal, QRectF, QRect, QSize, QFileSystemWatcher
from PyQt6.QtGui import QColor, QPainterPath, QPen, QPainter, QTextCursor, QTextFormat, QBrush, QFont, QSyntaxHighlighter, QTextCharFormat
import platform
import threading
import subprocess
import base64
import json
import os
import tempfile
import math
import queue
import time
import socket
import re
from core.machine_agent import client_from_settings

LOCAL_IPS = ['localhost', '127.0.0.1']
try:
    _out = subprocess.check_output("hostname -I", shell=True, text=True, stderr=subprocess.DEVNULL)
    LOCAL_IPS.extend(_out.split())
except:
    pass

def is_local_ip(ip):
    return ip in LOCAL_IPS

HAS_PARAMIKO = False
try:
    import paramiko
    HAS_PARAMIKO = True
except ImportError:
    pass

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

class ToolpathViewer(QGraphicsView):
    segment_double_clicked = pyqtSignal(int)
    MAX_PREVIEW_LINES = 100000
    MAX_PREVIEW_SEGMENTS = 25000
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.scene = QGraphicsScene(self)
        self.setScene(self.scene)
        self.setStyleSheet("background-color: #111; border: 2px solid #444; border-radius: 4px;")
        self.machine_units = "mm"  # The units the machine is currently operating in
        self.display_units = "mm"  # The units the user wants to see
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        # Enable drag-to-pan and zoom-to-mouse
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        
        self.segment_items = {}
        self.active_line = -1

        # Live tool position indicator. Its geometry is updated to match the path scale.
        self.tool_marker = self.scene.addEllipse(-1, -1, 2, 2, QPen(QColor("#ffffff"), 1.5), QBrush(QColor("#ff0000")))
        self.tool_marker.setZValue(100) # Always on top

    def _update_tool_marker_size(self, bounds=None):
        if bounds is None or bounds.isEmpty():
            diameter = 2.0
        else:
            extent = max(bounds.width(), bounds.height())
            diameter = max(0.5, min(8.0, extent * 0.008))
        half_diameter = diameter / 2.0
        self.tool_marker.setRect(-half_diameter, -half_diameter, diameter, diameter)

    def drawForeground(self, painter: QPainter, rect: QRectF):
        super().drawForeground(painter, rect)
        painter.save()
        painter.resetTransform()

        view_rect = self.viewport().rect()
        margin_x = 40
        margin_y = 60
        length = 40

        # Anchor visually at Bottom-Right natively for CNC!
        ox = view_rect.right() - 80
        oy = view_rect.bottom() - 40

        font = QFont("Arial", 10, QFont.Weight.Bold)
        painter.setFont(font)

        cos_a = math.cos(math.radians(30))
        sin_a = math.sin(math.radians(30))

        # CAD Standard right-handed ISO mapping:
        # X: Right and Up on screen
        # Y: Left and Up on screen
        # Z: Straight Up
        def to_iso(px, py, pz):
            sx = (px - py) * cos_a
            sy = -(px + py) * sin_a - pz
            return int(ox + sx), int(oy + sy)

        # Z-axis (Blue) straight UP
        zx, zy = to_iso(0, 0, length * 0.8)
        painter.setPen(QPen(QColor(50, 150, 255, 200), 3))
        painter.drawLine(int(ox), int(oy), zx, zy)
        painter.setPen(QColor(50, 150, 255, 200))
        painter.drawText(zx - 15, zy - 5, "Z")

        # X-axis (Red) Right-Up
        hx, hy = to_iso(length, 0, 0)
        painter.setPen(QPen(QColor(255, 50, 50, 200), 3))
        painter.drawLine(int(ox), int(oy), hx, hy)
        painter.setPen(QColor(255, 50, 50, 200))
        painter.drawText(hx + 8, hy + 5, "X")

        # Y-axis (Green) Left-Up
        yx, yy = to_iso(0, length, 0)
        painter.setPen(QPen(QColor(50, 255, 50, 200), 3))
        painter.drawLine(int(ox), int(oy), yx, yy)
        painter.setPen(QColor(50, 255, 50, 200))
        painter.drawText(yx - 20, yy + 5, "Y")

        # Origin dot
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(255, 255, 255, 200))
        painter.drawEllipse(int(ox) - 3, int(oy) - 3, 6, 6)
        painter.restore()

    def wheelEvent(self, event):
        if event.angleDelta().y() == 0:
            event.ignore()
            return
        factor = 1.15 if event.angleDelta().y() > 0 else 1.0 / 1.15
        self.scale(factor, factor)
        event.accept()

    def update_path(self, gcode_text):
        for item in list(self.scene.items()):
            if item != getattr(self, 'tool_marker', None):
                self.scene.removeItem(item)
        self.segment_items.clear()
        self.active_line = -1

        if not gcode_text or not str(gcode_text).strip():
            return

        text = str(gcode_text)
        lines = text.splitlines()
        line_count = len(lines)
        preview_note = None

        if line_count > self.MAX_PREVIEW_LINES:
            sample_step = max(1, line_count // self.MAX_PREVIEW_SEGMENTS)
            preview_lines = [(idx, line) for idx, line in enumerate(lines) if idx % sample_step == 0]
            preview_note = self.scene.addText(
                f"Preview simplified: {line_count:,} lines sampled to {len(preview_lines):,} moves"
            )
            preview_note.setDefaultTextColor(QColor("#ffb347"))
            preview_note.setPos(10, 10)
        else:
            preview_lines = list(enumerate(lines))

        cos_a = math.cos(math.radians(30))
        sin_a = math.sin(math.radians(30))
        
        def to_iso(px, py, pz):
            sx = (px - py) * cos_a
            sy = -(px + py) * sin_a - pz
            return sx, sy

        x, y, z = 0.0, 0.0, 0.0
        current_mode = 'G1'
        distance_mode = 'G90'

        feed_pen = QPen(QColor("#39FF14"), 1.5)
        feed_pen.setCosmetic(True)

        rapid_pen = QPen(QColor(150, 150, 150, 100), 1, Qt.PenStyle.DashLine)
        rapid_pen.setCosmetic(True)

        for line_idx, line in preview_lines:
            clean_line = line.split(';')[0].split('(')[0].strip().upper()
            if not clean_line: continue

            tokens = re.findall(r'[A-Z][-+]?[^A-Z\s]*', clean_line)
            for token in tokens:
                if token in ['G0', 'G00']: current_mode = 'G0'
                elif token in ['G1', 'G01']: current_mode = 'G1'
                elif token in ['G2', 'G02']: current_mode = 'G2'
                elif token in ['G3', 'G03']: current_mode = 'G3'
                elif token in ['G38.2', 'G38.3', 'G38.4', 'G38.5']:
                    current_mode = 'G38'
                elif token in ['G90', 'G90.0']:
                    distance_mode = 'G90'
                elif token in ['G91', 'G91.0']:
                    distance_mode = 'G91'

            new_x, new_y, new_z = x, y, z
            i, j = 0.0, 0.0
            has_move = False
            move_values = {}
            for token in tokens:
                if token.startswith('X'):
                    try: move_values['X'] = float(token[1:]); has_move = True
                    except: pass
                elif token.startswith('Y'):
                    try: move_values['Y'] = float(token[1:]); has_move = True
                    except: pass
                elif token.startswith('Z'):
                    try: move_values['Z'] = float(token[1:]); has_move = True
                    except: pass
                elif token.startswith('I'):
                    try: i = float(token[1:])
                    except: pass
                elif token.startswith('J'):
                    try: j = float(token[1:])
                    except: pass

            if has_move:
                if distance_mode == 'G91':
                    new_x += move_values.get('X', 0.0)
                    new_y += move_values.get('Y', 0.0)
                    new_z += move_values.get('Z', 0.0)
                else:
                    new_x = move_values.get('X', new_x)
                    new_y = move_values.get('Y', new_y)
                    new_z = move_values.get('Z', new_z)

                path = QPainterPath()
                path.moveTo(*to_iso(x, y, z))

                if current_mode == 'G0':
                    path.lineTo(*to_iso(new_x, new_y, new_z))
                    item = self.scene.addPath(path, rapid_pen)
                    item.setData(0, line_idx)
                    self.segment_items[line_idx] = {'item': item, 'is_rapid': True}
                elif current_mode in ['G2', 'G3']:
                    cx, cy = x + i, y + j
                    rx, ry = x - cx, y - cy
                    nx, ny = new_x - cx, new_y - cy
                    radius = math.hypot(rx, ry)

                    if radius > 0.0001:
                        start_ang = math.degrees(math.atan2(ry, rx))
                        end_ang = math.degrees(math.atan2(ny, nx))
                        sweep = end_ang - start_ang

                        if current_mode == 'G2': # CW
                            # Ensure sweep is negative for CW
                            if sweep > 0: sweep -= 360
                        else: # CCW
                            # Ensure sweep is positive for CCW
                            if sweep < 0: sweep += 360

                        steps = max(int(abs(sweep) / 5.0), 3) 
                        
                        for step in range(1, steps + 1):
                            t = step / steps
                            ang = math.radians(start_ang + sweep * t)
                            arc_x = cx + radius * math.cos(ang)
                            arc_y = cy + radius * math.sin(ang)
                            arc_z = z + (new_z - z) * t
                            path.lineTo(*to_iso(arc_x, arc_y, arc_z))
                            
                    else:
                        path.lineTo(*to_iso(new_x, new_y, new_z))

                    item = self.scene.addPath(path, feed_pen)
                    item.setData(0, line_idx)
                    self.segment_items[line_idx] = {'item': item, 'is_rapid': False}
                else:
                    path.lineTo(*to_iso(new_x, new_y, new_z))
                    item = self.scene.addPath(path, feed_pen)
                    item.setData(0, line_idx)
                    self.segment_items[line_idx] = {'item': item, 'is_rapid': False}

                x, y, z = new_x, new_y, new_z

        rect = self.scene.itemsBoundingRect()
        if rect.width() > 0 and rect.height() > 0:
            self._update_tool_marker_size(rect)
            padded_rect = rect.adjusted(-rect.width()*0.05, -rect.height()*0.05, rect.width()*0.05, rect.height()*0.05)
            self.fitInView(padded_rect, Qt.AspectRatioMode.KeepAspectRatio)

                
    def highlight_line(self, line_idx):
        if not hasattr(self, 'segment_items'): return
        
        # Reset previous highlight
        if self.active_line in self.segment_items:
            data = self.segment_items[self.active_line]
            pen = QPen(QColor(150, 150, 150, 100), 1, Qt.PenStyle.DashLine) if data['is_rapid'] else QPen(QColor("#39FF14"), 1.5)
            pen.setCosmetic(True)
            data['item'].setPen(pen)
            data['item'].setZValue(0)
            
        self.active_line = line_idx
        
        if line_idx in self.segment_items:
            data = self.segment_items[line_idx]
            hl_pen = QPen(QColor("#ff00ff"), 3) # Vibrant magenta for active toolpath
            hl_pen.setCosmetic(True)
            data['item'].setPen(hl_pen)
            data['item'].setZValue(1) # Pop to front
            
    def update_tool_position(self, x, y, z=0.0):
        # The toolpath is always drawn in mm, so we must scale the live position
        # if the machine is operating in inches. The incoming x,y,z are already
        # converted to the display units, so we just need to convert them back to
        # the canonical 'mm' that the drawing uses.
        scale = 1.0
        if self.display_units == "inch": scale = 25.4

        try:
            x, y, z = float(x) * scale, float(y) * scale, float(z) * scale
        except:
            return

        cos_a = math.cos(math.radians(30))
        sin_a = math.sin(math.radians(30))

        # No offset here, it tracks the same projection mapping relative to path!
        iso_x = (x - y) * cos_a
        iso_y = -(x + y) * sin_a - z
        self.tool_marker.setPos(iso_x, iso_y)
        
    def mouseDoubleClickEvent(self, event):
        super().mouseDoubleClickEvent(event)
        scene_pos = self.mapToScene(event.pos())
        
        # Create a small 4x4 bounding box to make it easier to hit thin lines
        rect = QRectF(scene_pos.x() - 2, scene_pos.y() - 2, 4, 4)
        for item in self.scene.items(rect, Qt.ItemSelectionMode.IntersectsItemShape):
            line_idx = item.data(0)
            if line_idx is not None:
                self.segment_double_clicked.emit(line_idx)
                break

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

def safety_shutdown(*args):
    try:
        if s is not None:
            s.poll()
            if getattr(s, 'interp_state', 1) == linuxcnc.INTERP_IDLE:
                c.state(linuxcnc.STATE_ESTOP)
    except: pass
    sys.exit(0)

signal.signal(signal.SIGHUP, safety_shutdown)
signal.signal(signal.SIGTERM, safety_shutdown)
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
            safety_shutdown()
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

        if is_local_ip(self.ip):
            self.running = True
            while self.running:
                proc = subprocess.Popen(self.cmd, shell=True, stdout=subprocess.PIPE, text=True)
                while self.running:
                    line = proc.stdout.readline()
                    if not line: break
                    line = line.strip()
                    if line.startswith('{') and line.endswith('}'):
                        try:
                            coords = json.loads(line)
                            self.position_fetched.emit(coords)
                        except json.JSONDecodeError: pass
                proc.terminate()
                for _ in range(20):
                    if not self.running: break
                    self.msleep(100)
            return

        if not HAS_PARAMIKO:
            return
            
        self.running = True
        while self.running:
            try:
                ssh = paramiko.SSHClient()
                ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                ssh.connect(self.ip, username=self.user, password=self.password, timeout=10, banner_timeout=15, auth_timeout=15, look_for_keys=not bool(self.password), allow_agent=not bool(self.password))
                
                stdin, stdout, stderr = ssh.exec_command(self.cmd)
                stdout.channel.settimeout(1.0)
                
                import socket
                while self.running:
                    try:
                        line = stdout.readline()
                        if not line: break
                        line = line.strip()
                        if line.startswith('{') and line.endswith('}'):
                            coords = json.loads(line)
                            self.position_fetched.emit(coords)
                    except (socket.timeout, json.JSONDecodeError):
                        continue # Ignore read timeouts and malformed JSON and keep waiting
                    except Exception as e:
                        if self.running:
                            self.error.emit(f"Position fetcher error: {e}")
                        break # The socket died or had a fatal error, break to trigger reconnect timer!
                        
                ssh.close()
            except Exception as e:
                if self.running:
                    self.error.emit(f"SSH connection failed: {e}")
            
            # Reconnect loop
            for _ in range(20):
                if not self.running: break
                self.msleep(100)

    def stop(self):
        self.running = False


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
        if not ip or not user:
            self.calibration_failed.emit("SSH credentials are not configured.")
            return

        b64_code = base64.b64encode(self.py_code.encode()).decode()
        cmd = f"python3 -u -c \"import base64; exec(base64.b64decode('{b64_code}').decode())\""

        try:
            ssh = paramiko.SSHClient()
            ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            ssh.connect(ip, username=user, password=password, timeout=10, look_for_keys=not bool(password), allow_agent=not bool(password))
            stdin, stdout, stderr = ssh.exec_command(cmd, timeout=300)
            result = stdout.read().decode().strip() # This will block until the remote command finishes
            error = stderr.read().decode().strip()
            ssh.close()
            
            if "ERROR" in result or (error and "Traceback" not in error): self.calibration_failed.emit(result or error)
            elif "USER_CANCEL" in result: pass # User cancelled, do nothing
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

# Define MockLinuxCNC before using it
class MockLinuxCNC:
    def command(self): return self
    def stat(self): return self
    def mode(self, *args): pass
    def wait_complete(self): pass
    def home(self, *args): print("[dashboard.py] SIM: Homing axes...")
    def state(self, *args): print(f"[dashboard.py] SIM: State changed to {args}")
    def abort(self): print("[dashboard.py] SIM: Abort triggered")
    @property
    def position(self): return [1.23, 4.56, 7.89, 0, 0, 0]
    def poll(self): pass
    MODE_MANUAL = 1
    MODE_MDI = 2
    STATE_ON = 1
    STATE_OFF = 0

# Moved linuxcnc and IS_SIM initialization to the top of the file for better visibility
linuxcnc = MockLinuxCNC()
IS_SIM = True

class MachineDashboard(QWidget):
    error_signal = pyqtSignal(str)
    program_sync_state = pyqtSignal(bool)

    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.error_signal.connect(self.show_error_message)
        self.program_sync_state.connect(self._set_program_sync_state)
        self.settings = settings
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
        self._program_sync_in_progress = False
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
            "mode_control": [self.btn_mode_manual, self.btn_mode_auto],
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
        btn_sync.clicked.connect(lambda: self.sync_gcode_to_machine(self.default_gcode_editor.toPlainText()))
        btn_clear.clicked.connect(self.default_gcode_editor.clear)
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
        self.toolpath_viewer = ToolpathViewer()
        preview_vbox.addWidget(self.toolpath_viewer)
        
        btn_screenshot = QPushButton("📸 Save Screenshot")
        btn_screenshot.setStyleSheet("QPushButton { background-color: #2c3e50; color: white; padding: 4px 10px; border-radius: 3px; font-weight: bold; } QPushButton:pressed { background-color: #1a252f; }")
        btn_screenshot.clicked.connect(self.save_toolpath_screenshot)
        preview_vbox.addWidget(btn_screenshot)
        
        preview_group.setLayout(preview_vbox)
        
        gcode_preview_layout.addWidget(preview_group, 2)
        
        # Wire Editor changes to Toolpath Viewer
        self.default_gcode_editor.textChanged.connect(lambda: self.toolpath_viewer.update_path(self.default_gcode_editor.toPlainText()))
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
        
        # --- TOUCH TAB ---
        touch_tab = QWidget()
        touch_layout = QHBoxLayout(touch_tab)
        
        # OUTSIDE GRID
        out_group = QGroupBox("Measure Outside")
        out_grid = QGridLayout()
        out_btns = [
            ("⌜ B-L", "out_bl"), ("⬆ Back", "out_b"), ("B-R ⌝", "out_br"),
            ("⬅ Left", "out_l"), ("⏺ Boss", "out_boss"), ("Right ➡", "out_r"),
            ("⌞ F-L", "out_fl"), ("⬇ Front", "out_f"), ("F-R ⌟", "out_fr")
        ]
        for i, (text, action) in enumerate(out_btns):
            btn = QPushButton(text)
            btn.setStyleSheet("QPushButton { background-color: #2980b9; color: white; font-weight: bold; padding: 6px; border-radius: 4px; } QPushButton:pressed { background-color: #1f618d; }")
            btn.clicked.connect(lambda _, a=action: self.trigger_probing(a))
            btn.setObjectName(f"probe_{action}")
            out_grid.addWidget(btn, i // 3, i % 3)
        out_group.setLayout(out_grid)
        
        # SETTINGS
        mid_group = QGroupBox("Settings")
        mid_layout = QVBoxLayout()
        
        self.input_probe_diam = QLineEdit(self.settings.get_setting("PROBING", "probe_diam", ""))
        self.input_probe_diam.setPlaceholderText("Dia")
        self.input_probe_diam.textChanged.connect(lambda t: self.settings.save_setting("PROBING", "probe_diam", t))
        
        self.input_xy_clearance = QLineEdit(self.settings.get_setting("PROBING", "xy_clear", ""))
        self.input_xy_clearance.setPlaceholderText("Clear")
        self.input_xy_clearance.textChanged.connect(lambda t: self.settings.save_setting("PROBING", "xy_clear", t))
        
        self.input_z_drop = QLineEdit(self.settings.get_setting("PROBING", "z_drop", ""))
        self.input_z_drop.setPlaceholderText("Drop")
        self.input_z_drop.textChanged.connect(lambda t: self.settings.save_setting("PROBING", "z_drop", t))
        
        self.input_search_dist = QLineEdit(self.settings.get_setting("PROBING", "search_dist", ""))
        self.input_search_dist.setPlaceholderText("Search")
        self.input_search_dist.textChanged.connect(lambda t: self.settings.save_setting("PROBING", "search_dist", t))
        
        for w in [self.input_probe_diam, self.input_xy_clearance, self.input_z_drop, self.input_search_dist]:
            w.setStyleSheet("max-width: 60px;")
            
        form = QGridLayout()
        form.addWidget(QLabel("Dia:"), 0, 0); form.addWidget(self.input_probe_diam, 0, 1)
        form.addWidget(QLabel("XY Clr:"), 1, 0); form.addWidget(self.input_xy_clearance, 1, 1)
        form.addWidget(QLabel("Z Drop:"), 2, 0); form.addWidget(self.input_z_drop, 2, 1)
        form.addWidget(QLabel("Search:"), 3, 0); form.addWidget(self.input_search_dist, 3, 1)
        mid_layout.addLayout(form)
        
        btn_probe_z = QPushButton("Probe Z")
        btn_probe_z.setStyleSheet("QPushButton { background-color: #8e44ad; color: white; font-weight: bold; padding: 6px; border-radius: 4px; } QPushButton:pressed { background-color: #732d91; }")
        btn_probe_z.clicked.connect(lambda: self.trigger_probing("probe_z"))
        mid_layout.addWidget(btn_probe_z)
        mid_layout.addStretch()
        mid_group.setLayout(mid_layout)
        
        # INSIDE GRID
        in_group = QGroupBox("Measure Inside")
        in_grid = QGridLayout()
        in_btns = [
            ("⌟ B-L", "in_bl"), ("⬇ Back", "in_b"), ("B-R ⌞", "in_br"),
            ("➡ Left", "in_l"), ("⏺ Hole", "in_hole"), ("Right ⬅", "in_r"),
            ("⌝ F-L", "in_fl"), ("⬆ Front", "in_f"), ("F-R ⌜", "in_fr")
        ]
        for i, (text, action) in enumerate(in_btns):
            btn = QPushButton(text)
            btn.setStyleSheet("QPushButton { background-color: #27ae60; color: white; font-weight: bold; padding: 6px; border-radius: 4px; } QPushButton:pressed { background-color: #229954; }")
            btn.clicked.connect(lambda _, a=action: self.trigger_probing(a))
            btn.setObjectName(f"probe_{action}")
            in_grid.addWidget(btn, i // 3, i % 3)
        in_group.setLayout(in_grid)
        
        touch_layout.addWidget(out_group)
        touch_layout.addWidget(mid_group)
        touch_layout.addWidget(in_group)
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
        
        self.btn_mode_manual = QPushButton("MANUAL")
        self.btn_mode_auto = QPushButton("AUTO")
        self.btn_cycle_start = QPushButton("▶ CYCLE START")
        self.btn_cycle_pause = QPushButton("⏸ PAUSE")
        self.btn_cycle_stop = QPushButton("⏹ STOP")
        
        for btn in [self.btn_mode_manual, self.btn_mode_auto, self.btn_cycle_start, self.btn_cycle_pause, self.btn_cycle_stop]:
            btn.setFixedHeight(40)
            btn.setStyleSheet("QPushButton { background-color: #34495e; color: white; font-weight: bold; border-radius: 4px; } QPushButton:pressed { background-color: #2c3e50; }")
            run_layout.addWidget(btn)
            
        self.btn_mode_manual.clicked.connect(lambda: self.select_machine_mode("manual"))
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
                btn_sync.clicked.connect(lambda _, t=txt: self.sync_gcode_to_machine(t.toPlainText()))
                btn_clear.clicked.connect(txt.clear)
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

        ip = self.settings.get_setting("SSH", "ip_address", "")
        user = self.settings.get_setting("SSH", "username", "")
        password = self.settings.get_setting("SSH", "password", "")
        
        if not ip or not user: return
            
        # Translate virtual UI pins to their real-time command parameters
        if pin_name.startswith("ui."):
            clean_name = pin_name.replace("ui.", "").replace(".", "_").replace("-", "_")
            target = f"linuxcnc_ui.0.{clean_name}_cmd"
        else:
            target = pin_name
            
        cmd = f"halcmd setp {target} {value_str}"
        self.cmd_queue.put(cmd)

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
        """Abort the active program through the typed agent when enrolled."""
        agent_client = client_from_settings(self.settings)
        if agent_client is not None:
            try:
                agent_client.machine_command("abort")
            except Exception as error:
                self.error_signal.emit(f"Cycle stop failed: {error}")
            return
        self._send_b64_py_cmd("import linuxcnc\nc=linuxcnc.command(); c.abort()", urgent=True)

    def start_cycle(self):
        """Start the loaded program through a typed command when enrolled."""
        agent_client = client_from_settings(self.settings)
        if agent_client is not None:
            if self._program_sync_in_progress:
                self.error_signal.emit("Cycle start is unavailable while the G-code program is still being uploaded.")
                return
            try:
                agent_client.open_program("active_program.ngc")
                agent_client.machine_command("program_start", timeout=15.0)
            except Exception as error:
                self.error_signal.emit(f"Cycle start failed: {error}")
            return
        self._send_b64_py_cmd(
            "import linuxcnc, time\nc=linuxcnc.command(); c.mode(linuxcnc.MODE_AUTO); time.sleep(0.1); c.auto(linuxcnc.AUTO_RUN, 0)"
        )

    def select_machine_mode(self, mode):
        """Select Manual or Auto through the typed agent when enrolled."""
        # Ignore mode changes while actively jogging to prevent interruption
        if self.active_jogs:
            return
            
        agent_client = client_from_settings(self.settings)
        if agent_client is not None:
            try:
                agent_client.machine_command(f"mode_{mode}")
            except Exception as error:
                self.error_signal.emit(f"Mode selection failed: {error}")
            return
        legacy_code = {
            "manual": "import linuxcnc\nc=linuxcnc.command(); c.mode(linuxcnc.MODE_MANUAL)",
            "auto": "import linuxcnc\nc=linuxcnc.command(); c.mode(linuxcnc.MODE_AUTO)",
        }
        self._send_b64_py_cmd(legacy_code[mode])

    def pause_or_resume_cycle(self):
        """Pause or resume the program through a typed command when enrolled."""
        agent_client = client_from_settings(self.settings)
        if agent_client is not None:
            try:
                agent_client.machine_command("program_pause_resume", timeout=15.0)
            except Exception as error:
                self.error_signal.emit(f"Pause/resume failed: {error}")
            return
        self._send_b64_py_cmd(
            "import linuxcnc\nc=linuxcnc.command(); s=linuxcnc.stat(); s.poll(); "
            "c.auto(linuxcnc.AUTO_RESUME if s.interp_state == linuxcnc.INTERP_PAUSED else linuxcnc.AUTO_PAUSE)",
            urgent=True,
        )

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
    os.system("halcmd setp lut5.0.in-2 0") #Force Touch Plate Mode
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
        os.system("export DISPLAY=:0; zenity --error --title='Probe Failed' --text='Fast search failed to make contact.' --width=300")
        sys.exit(1)
        
    run_mdi("G91"); run_mdi("G0 Z2.0"); run_mdi("G90")
    run_mdi(f"G38.2 Z{{z_target:.4f}} F{setter_vel}")
    s.poll()
    if s.probe_tripped:
        tool_num = s.tool_in_spindle
        if tool_num == 0:
            # Cannot set length for T0, prompt user to load a tool
            os.system("export DISPLAY=:0; zenity --error --title='No Tool Loaded' --text='Cannot set tool length because no tool is currently loaded (T0). Please load a tool (e.g., T1 M6) and try again.' --width=300")
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
    run_mdi(f"G38.2 {{'{ax}'}}{{search * {srch_sgn}}} F{{vel}}")
    s.poll()
    if not s.probe_tripped:
        run_mdi("G90")
        run_mdi(f"G53 G0 X{{start_x}} Y{{start_y}}")
        sys.exit(0)
    run_mdi(f"G10 L20 P0 {{'{ax}'}}{{pr if {srch_sgn} < 0 else -pr}}")
    run_mdi(f"G0 {{'{ax}'}}{{-search/4.0 * {srch_sgn}}}")
    run_mdi("G90")
    run_mdi(f"G53 G0 X{{start_x}} Y{{start_y}}")
"""

        def probe_center(ctype):
            if ctype == "hole":
                return """
    run_mdi("G91")
    run_mdi(f"G38.2 X{search} F{vel}")
    s.poll(); 
    if not s.probe_tripped:
        run_mdi("G90")
        run_mdi(f"G53 G0 X{{start_x}} Y{{start_y}}")
        sys.exit(0)
    x1 = s.probed_position[0]
    
    run_mdi("G90")
    run_mdi(f"G53 G0 X{start_x}")
    run_mdi("G91")
    
    run_mdi(f"G38.2 X{-search} F{vel}")
    s.poll(); 
    if not s.probe_tripped:
        run_mdi("G90")
        run_mdi(f"G53 G0 X{{start_x}} Y{{start_y}}")
        sys.exit(0)
    x2 = s.probed_position[0]
    
    run_mdi("G90")
    cx = (x1 + x2) / 2.0
    run_mdi(f"G53 G0 X{cx}")
    run_mdi("G10 L20 P0 X0")
    
    run_mdi(f"G53 G0 Y{start_y}")
    run_mdi("G91")
    run_mdi(f"G38.2 Y{search} F{vel}")
    s.poll(); 
    if not s.probe_tripped:
        run_mdi("G90")
        run_mdi(f"G53 G0 X{{cx}} Y{{start_y}}")
        sys.exit(0)
    y1 = s.probed_position[1]
    
    run_mdi("G90")
    run_mdi(f"G53 G0 Y{start_y}")
    run_mdi("G91")
    
    run_mdi(f"G38.2 Y{-search} F{vel}")
    s.poll(); 
    if not s.probe_tripped:
        run_mdi("G90")
        run_mdi(f"G53 G0 X{{cx}} Y{{start_y}}")
        sys.exit(0)
    y2 = s.probed_position[1]
    
    run_mdi("G90")
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
        run_mdi(f"G53 G0 Z{{start_z}}")
        run_mdi(f"G53 G0 X{{start_x}} Y{{start_y}}")
        sys.exit(0)
    run_mdi(f"G38.2 X-{search} F{vel}")
    s.poll(); 
    if not s.probe_tripped:
        run_mdi("G90")
        run_mdi(f"G53 G0 Z{{start_z}}")
        run_mdi(f"G53 G0 X{{start_x}} Y{{start_y}}")
        sys.exit(0)
    x1 = s.probed_position[0]
    run_mdi(f"G0 X{search/4.0}")
    run_mdi("G90")
    run_mdi(f"G53 G0 Z{{start_z}}")
    
    run_mdi("G90")
    run_mdi(f"G53 G0 X{start_x - clear}")
    run_mdi("G91")
    run_mdi(f"G38.3 Z-{zdrop} F{vel}")
    s.poll(); 
    if s.probe_tripped:
        run_mdi("G90")
        run_mdi(f"G53 G0 Z{{start_z}}")
        run_mdi(f"G53 G0 X{{start_x}} Y{{start_y}}")
        sys.exit(0)
    run_mdi(f"G38.2 X{search} F{vel}")
    s.poll(); 
    if not s.probe_tripped:
        run_mdi("G90")
        run_mdi(f"G53 G0 Z{{start_z}}")
        run_mdi(f"G53 G0 X{{start_x}} Y{{start_y}}")
        sys.exit(0)
    x2 = s.probed_position[0]
    run_mdi(f"G0 X{-search/4.0}")
    run_mdi("G90")
    run_mdi(f"G53 G0 Z{{start_z}}")
    
    run_mdi("G90")
    cx = (x1 + x2) / 2.0
    run_mdi(f"G53 G0 X{cx}")
    run_mdi("G10 L20 P0 X0")
    
    run_mdi(f"G53 G0 X{start_x} Y{start_y + clear}")
    run_mdi("G91")
    run_mdi(f"G38.3 Z-{zdrop} F{vel}")
    s.poll(); 
    if s.probe_tripped:
        run_mdi("G90")
        run_mdi(f"G53 G0 Z{{start_z}}")
        run_mdi(f"G53 G0 X{{cx}} Y{{start_y}}")
        sys.exit(0)
    run_mdi(f"G38.2 Y-{search} F{vel}")
    s.poll(); 
    if not s.probe_tripped:
        run_mdi("G90")
        run_mdi(f"G53 G0 Z{{start_z}}")
        run_mdi(f"G53 G0 X{{cx}} Y{{start_y}}")
        sys.exit(0)
    y1 = s.probed_position[1]
    run_mdi(f"G0 Y{search/4.0}")
    run_mdi("G90")
    run_mdi(f"G53 G0 Z{{start_z}}")
    
    run_mdi("G90")
    run_mdi(f"G53 G0 Y{start_y - clear}")
    run_mdi("G91")
    run_mdi(f"G38.3 Z-{zdrop} F{vel}")
    s.poll(); 
    if s.probe_tripped:
        run_mdi("G90")
        run_mdi(f"G53 G0 Z{{start_z}}")
        run_mdi(f"G53 G0 X{{cx}} Y{{start_y}}")
        sys.exit(0)
    run_mdi(f"G38.2 Y{search} F{vel}")
    s.poll(); 
    if not s.probe_tripped:
        run_mdi("G90")
        run_mdi(f"G53 G0 Z{{start_z}}")
        run_mdi(f"G53 G0 X{{cx}} Y{{start_y}}")
        sys.exit(0)
    y2 = s.probed_position[1]
    run_mdi(f"G0 Y{-search/4.0}")
    run_mdi("G90")
    run_mdi(f"G53 G0 Z{{start_z}}")
    
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
        run_mdi(f"G53 G0 Z{{start_z}}")
        sys.exit(0)
    run_mdi("G10 L20 P0 Z0")
    run_mdi("G90")
    run_mdi(f"G53 G0 Z{{start_z}}")
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
    os.system("halcmd setp lut5.0.in-2 1")
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

    def move_to_tool_setter(self):
        if self.is_estopped:
            QMessageBox.warning(self, "E-STOP", "Machine is in E-STOP state. Release E-STOP before running.")
            return
            
        if not self.mock_coords.get('MACHINE_ON', False):
            QMessageBox.warning(self, "Power Off", "Machine power is OFF.\n\nPlease click the 'POWER ON' button before moving.")
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
            py_code = f"""import linuxcnc, time, sys
try:
    c = linuxcnc.command(); s = linuxcnc.stat()
    e = linuxcnc.error_channel()
    e = getattr(linuxcnc, 'error_channel', lambda: None)()
    def wait_idle(timeout=30):
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

            py_code = f"""import linuxcnc, time, sys
try:
    c = linuxcnc.command(); s = linuxcnc.stat()
    e = linuxcnc.error_channel()
    e = getattr(linuxcnc, 'error_channel', lambda: None)()
    def wait_idle(timeout=30):
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
        
    c.mdi(f"G53 G0 Z{park_z}")
    time.sleep(0.1); wait_idle()
    c.mdi("G53 G0 X{park_x} Y{park_y}")
except Exception as e: pass"""
            self._send_b64_py_cmd(py_code)

    def _send_linuxcnc_py_cmd(self, cmd, timeout=5):
        self.cmd_queue.put((cmd, timeout))

    def _cmd_worker(self):
        while True:
            cmd = self.cmd_queue.get()
            if cmd is None: break
            
            # Unpack tuple
            if isinstance(cmd, tuple):
                command, timeout = cmd
            else:
                command, timeout = cmd, 5 # Default timeout for old-style calls

            ip = self.settings.get_setting("SSH", "ip_address", "")
            user = self.settings.get_setting("SSH", "username", "")
            password = self.settings.get_setting("SSH", "password", "")
            if not ip or not user: continue
            
            if is_local_ip(ip):
                try: 
                    result = subprocess.run(command, shell=True, capture_output=True, text=True, timeout=timeout)
                    if result.stderr:
                        self.error_signal.emit(result.stderr.strip())
                except Exception as e:
                    self.error_signal.emit(str(e))
                continue
                
            if HAS_PARAMIKO:
                ssh = None
                try:
                    ssh = paramiko.SSHClient()
                    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                    ssh.connect(ip, username=user, password=password, timeout=2, banner_timeout=3, auth_timeout=3, look_for_keys=not bool(password), allow_agent=not bool(password))
                    stdin, stdout, stderr = ssh.exec_command(command, timeout=timeout)
                    error_output = stderr.read().decode().strip()
                    if error_output:
                        self.error_signal.emit(error_output)
                except Exception as e:
                    self.error_signal.emit(str(e))
                finally:
                    if ssh:
                        ssh.close()
            else: # Fallback to system SSH
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

    def sync_gcode_to_machine(self, content):
        if not content.strip(): return
        ip = self.settings.get_setting("SSH", "ip_address", "")
        user = self.settings.get_setting("SSH", "username", "")
        password = self.settings.get_setting("SSH", "password", "")
        agent_client = client_from_settings(self.settings)
        if agent_client is None and (not ip or not user): return

        self._program_sync_in_progress = True
        self.program_sync_state.emit(True)
        
        def task():
            try:
                if agent_client is not None:
                    try:
                        agent_client.upload_content("gcode", "active_program.ngc", content.encode("utf-8"))
                        agent_client.open_program("active_program.ngc")
                    except Exception as error:
                        self.error_signal.emit(f"G-code upload failed: {error}")
                    return

                remote_dir = f"/home/{user}/linuxcnc/nc_files"
                remote_path = f"{remote_dir}/active_program.ngc"
            
                if is_local_ip(ip):
                    try:
                        os.makedirs(os.path.expanduser("~/linuxcnc/nc_files"), exist_ok=True)
                        local_dest = os.path.expanduser("~/linuxcnc/nc_files/active_program.ngc")
                        with open(local_dest, 'w', encoding='utf-8') as f:
                            f.write(content)
                        remote_path = local_dest
                    except Exception as error:
                        self.error_signal.emit(f"G-code upload failed: {error}")
                        return
                elif HAS_PARAMIKO:
                    try:
                        ssh = paramiko.SSHClient()
                        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                        ssh.connect(ip, username=user, password=password, timeout=5, look_for_keys=not bool(password), allow_agent=not bool(password))
                        ssh.exec_command(f"mkdir -p {remote_dir}")
                        sftp = ssh.open_sftp()
                        with sftp.file(remote_path, 'w') as f:
                            f.write(content.encode('utf-8'))
                        sftp.close()
                        ssh.close()
                    except Exception as error:
                        self.error_signal.emit(f"G-code upload failed: {error}")
                        return
                else:
                    try:
                        subprocess.run(["ssh", "-o", "BatchMode=yes", f"{user}@{ip}", f"mkdir -p {remote_dir}"], capture_output=True, timeout=5)
                        with tempfile.NamedTemporaryFile(delete=False, mode='w', encoding='utf-8') as f:
                            f.write(content)
                            temp_path = f.name
                        subprocess.run(["scp", "-o", "BatchMode=yes", temp_path, f"{user}@{ip}:{remote_path}"], capture_output=True, timeout=10)
                        os.remove(temp_path)
                    except Exception as error:
                        self.error_signal.emit(f"G-code upload failed: {error}")
                        return
                    
                py_code = f"import linuxcnc\ntry:\n    c=linuxcnc.command()\n    c.program_open('{remote_path}')\nexcept: pass"
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
            if self._tool_change_prompt_active:
                self._tool_change_prompt_active = False
                self._tool_change_last_pocket = -1
            return
        if self._tool_change_prompt_active:
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
    os.system("halcmd setp iocontrol.0.tool-changed 1")
    time.sleep(0.3)
    os.system("halcmd setp iocontrol.0.tool-changed 0")
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
        last_dir = self.settings.get_setting("PATHS", "last_local_gcode_dir", default_dir)
        if not os.path.exists(last_dir): last_dir = default_dir
        path, _ = QFileDialog.getOpenFileName(self, "Load G-Code", last_dir, "G-Code Files (*.ngc *.gcode *.nc);;All Files (*)")
        if not path: return
        self.settings.save_setting("PATHS", "last_local_gcode_dir", os.path.dirname(path))
        
        try:
            with open(path, 'r', encoding='utf-8') as f:
                content = f.read()
            text_widget.setPlainText(content)
            self.sync_gcode_to_machine(content)
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to load G-Code:\n{e}")

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
        if not ip or not user: return
        
        if is_local_ip(ip):
            try: 
                subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
            except Exception as e:
                with open('/tmp/linuxcnc_jog_debug.txt', 'a') as f: f.write(f"\\nURGENT SUBPROCESS EXCEPTION:\\n{e}\\n")
            return
            
        if HAS_PARAMIKO:
            try:
                ssh = paramiko.SSHClient()
                ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                ssh.connect(ip, username=user, password=password, timeout=5, look_for_keys=not bool(password), allow_agent=not bool(password))
                ssh.exec_command(cmd, timeout=timeout)
                ssh.close()
            except Exception:
                pass
        else:
            try: subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=3", f"{user}@{ip}", cmd], capture_output=True, timeout=timeout)
            except: pass

    def toggle_estop(self):
        agent_client = client_from_settings(self.settings)
        if agent_client is not None:
            try:
                agent_client.machine_command("estop_reset" if self.is_estopped else "estop")
            except Exception as error:
                self.error_signal.emit(f"E-stop command failed: {error}")
            return
        if self.is_estopped:
                
            # Send real commands to reset E-Stop ONLY
            py_code = "import linuxcnc\ntry:\n    c=linuxcnc.command()\n    c.state(linuxcnc.STATE_ESTOP_RESET)\nexcept: pass"
            self._send_b64_py_cmd(py_code, urgent=True)
        else:
                
            # Send real commands to Power Off and trigger E-Stop
            py_code = "import linuxcnc, time\ntry:\n    c=linuxcnc.command()\n    c.state(linuxcnc.STATE_OFF)\n    time.sleep(0.1)\n    c.state(linuxcnc.STATE_ESTOP)\nexcept: pass"
            self._send_b64_py_cmd(py_code, urgent=True)

    def toggle_power(self):
        if self.is_estopped:
            QMessageBox.warning(self, "E-STOP", "Cannot turn on machine power while E-STOP is active.\nPlease release the E-STOP first.")
            return
            
        is_powered_on = self.mock_coords.get('MACHINE_ON', False)
        agent_client = client_from_settings(self.settings)
        if agent_client is not None:
            try:
                agent_client.machine_command("power_off" if is_powered_on else "power_on")
            except Exception as error:
                self.error_signal.emit(f"Power command failed: {error}")
            return
        
        if is_powered_on:
            py_code = "import linuxcnc\ntry:\n    c=linuxcnc.command()\n    c.state(linuxcnc.STATE_OFF)\nexcept: pass"
            self._send_b64_py_cmd(py_code, urgent=True)
            self.mock_coords['MACHINE_ON'] = False
        else:
            py_code = "import linuxcnc\ntry:\n    c=linuxcnc.command()\n    c.state(linuxcnc.STATE_ON)\nexcept: pass"
            self._send_b64_py_cmd(py_code, urgent=True)
            self.mock_coords['MACHINE_ON'] = True
            
        self.update_status()

    def override_limits(self):
        # Allow overriding even if in E-STOP, as physical limit switches force an E-STOP state!
        agent_client = client_from_settings(self.settings)
        if agent_client is not None:
            try:
                agent_client.machine_command("override_limits")
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
    os.system("halcmd setp lut5.0.in-2 0")
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
        for axis in ['X', 'Y', 'Z', 'FEED', 'RPM']:
            if axis in self.dro_labels:                
                if axis in ['X', 'Y', 'Z']:
                    if self.dro_mode == 'WORK':
                        val = self.mock_coords.get(axis, 0.0)
                    elif self.dro_mode == 'MACHINE':
                        val = self.mock_coords.get(f'ABSOLUTE_{axis}', 0.0)
                    elif self.dro_mode == 'DTG':
                        val = self.mock_coords.get(f'DTG_{axis}', 0.0)
                    else: # Fallback
                        val = self.mock_coords.get(axis, 0.0)
                    text = f"{val:.3f}"
                else: # FEED, RPM
                    val = self.mock_coords.get(axis, 0.0)
                    text = f"{val:.1f}"
                if self._last_update_state.get(f"dro_{axis}") != text:
                    self.dro_labels[axis].setText(text)
                    self._last_update_state[f"dro_{axis}"] = text
                custom_label = self.custom_dro_labels.get(axis)
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
        if self.ssh_connected:
            if self._last_update_state.get("ssh") != "online":
                self.status_leds["CONNECTION"].setStyleSheet("background-color: #00FF00; color: black; border-radius: 4px; font-weight: bold; padding: 8px;")
                self.status_leds["CONNECTION"].setText("MACHINE: ONLINE")
                self._last_update_state["ssh"] = "online"
            self._was_connected_once = True
        else:
            if getattr(self, '_was_connected_once', False):
                blink_state = "reconnect_1" if int(current_time * 2) % 2 == 0 else "reconnect_2"
                if self._last_update_state.get("ssh") != blink_state:
                    if blink_state == "reconnect_1":
                        self.status_leds["CONNECTION"].setStyleSheet("background-color: #f39c12; color: black; border-radius: 4px; font-weight: bold; padding: 8px;")
                        self.status_leds["CONNECTION"].setText("MACHINE: RECONNECTING")
                    else:
                        self.status_leds["CONNECTION"].setStyleSheet("background-color: #c0392b; color: white; border-radius: 4px; font-weight: bold; padding: 8px;")
                        self.status_leds["CONNECTION"].setText("MACHINE: CONNECTION LOST")
                    self._last_update_state["ssh"] = blink_state
            else:
                if self._last_update_state.get("ssh") != "offline":
                    self.status_leds["CONNECTION"].setStyleSheet("background-color: red; color: white; border-radius: 4px; font-weight: bold; padding: 8px;")
                    self.status_leds["CONNECTION"].setText("MACHINE: OFFLINE")
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
        elif self.ssh_connected:
            is_homed = expected_joints > 0 and remote_homed_count >= expected_joints
            is_powered_on = self.mock_coords.get('MACHINE_ON', not self.is_estopped)
        else:
            is_homed = expected_joints > 0 and all(not btn.isEnabled() for btn in self.home_buttons.values())
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
                if current_mode == 'MANUAL':
                    self.btn_mode_manual.setStyleSheet("QPushButton { background-color: #27ae60; color: white; font-weight: bold; border-radius: 4px; }")       
                    self.btn_mode_auto.setStyleSheet("QPushButton { background-color: #34495e; color: white; font-weight: bold; border-radius: 4px; } QPushButton:pressed { background-color: #2c3e50; }")
                elif current_mode == 'AUTO':
                    self.btn_mode_auto.setStyleSheet("QPushButton { background-color: #27ae60; color: white; font-weight: bold; border-radius: 4px; }")
                    self.btn_mode_manual.setStyleSheet("QPushButton { background-color: #34495e; color: white; font-weight: bold; border-radius: 4px; } QPushButton:pressed { background-color: #2c3e50; }")
                else:
                    self.btn_mode_manual.setStyleSheet("QPushButton { background-color: #34495e; color: white; font-weight: bold; border-radius: 4px; }")       
                    self.btn_mode_auto.setStyleSheet("QPushButton { background-color: #34495e; color: white; font-weight: bold; border-radius: 4px; }")

        # Granular Cache: DRO Work/Abs/DTG
        if self._last_update_state.get("drawn_dro") != self.dro_mode:
            self._last_update_state["drawn_dro"] = self.dro_mode
            if hasattr(self, 'dro_mode_buttons'):
                for mode, btn in self.dro_mode_buttons.items():
                    btn.setStyleSheet(f"background-color: {'#2980b9' if mode == self.dro_mode else '#34495e'}; color: white; padding: 4px; border-radius: 3px; font-weight: bold;")

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

        state_tuple = (self.is_estopped, is_powered_on, is_homed, status_text)

        if self._last_update_state.get("led_state") != state_tuple:
            self._last_update_state["led_state"] = state_tuple

            if self.is_estopped:
                self.status_leds["ESTOP"].setStyleSheet("background-color: red; color: white; font-weight: bold; border: 2px solid darkred; padding: 8px; border-radius: 4px;")
                self.status_leds["POWER"].setStyleSheet("background-color: #222; color: #444; padding: 8px; border-radius: 4px;")
                self.status_leds["HOMED"].setStyleSheet("background-color: #555; color: white; font-weight: bold; padding: 8px; border-radius: 4px;")
                self.status_leds["READY"].setText("OFFLINE")
                self.status_leds["READY"].setStyleSheet("background-color: #555; color: white; font-weight: bold; padding: 8px; border-radius: 4px;")
                if hasattr(self, 'btn_power'):
                    self.btn_power.setText("POWER ON")
                    self.btn_power.setStyleSheet("QPushButton { background-color: #444; color: #888; font-weight: bold; font-size: 16px; border-radius: 5px; } QPushButton:pressed { background-color: #222; padding-top: 2px; padding-left: 2px; }")
                if hasattr(self, 'btn_master_toggle'):
                    self.btn_master_toggle.setText("RESET (E-STOP)")
                    self.btn_master_toggle.setStyleSheet("QPushButton { background-color: #c0392b; color: white; font-weight: bold; font-size: 20px; border-radius: 5px; } QPushButton:pressed { background-color: #922b21; padding-top: 2px; padding-left: 2px; }")
                for button in self.home_buttons.values():
                    button.setEnabled(False)
                if hasattr(self, 'btn_home_all'):
                    self.btn_home_all.setEnabled(False)
                for jog_button in self.jog_buttons.values():
                    jog_button.setEnabled(False)
            else:
                self.status_leds["ESTOP"].setStyleSheet("background-color: #222; color: #444; padding: 8px; border-radius: 4px;")
                
                if is_powered_on:
                    self.status_leds["POWER"].setStyleSheet("background-color: #00FF00; color: black; font-weight: bold; padding: 8px; border-radius: 4px;")
                    if hasattr(self, 'btn_power'):
                        self.btn_power.setText("POWER OFF")
                        self.btn_power.setStyleSheet("QPushButton { background-color: #e74c3c; color: white; font-weight: bold; font-size: 16px; border-radius: 5px; } QPushButton:pressed { background-color: #c0392b; padding-top: 2px; padding-left: 2px; }")
                else:
                    self.status_leds["POWER"].setStyleSheet("background-color: #f39c12; color: black; font-weight: bold; padding: 8px; border-radius: 4px;")
                    if hasattr(self, 'btn_power'):
                        self.btn_power.setText("POWER ON")
                        self.btn_power.setStyleSheet("QPushButton { background-color: #7f8c8d; color: white; font-weight: bold; font-size: 16px; border-radius: 5px; } QPushButton:pressed { background-color: #616a6b; padding-top: 2px; padding-left: 2px; }")
                if hasattr(self, 'btn_master_toggle'):
                    self.btn_master_toggle.setText("E-STOP")
                    self.btn_master_toggle.setStyleSheet("QPushButton { background-color: orange; color: black; font-weight: bold; font-size: 20px; border-radius: 5px; } QPushButton:pressed { background-color: #cc8400; padding-top: 2px; padding-left: 2px; }")
                for button in self.home_buttons.values():
                    button.setEnabled(True)
                if hasattr(self, 'btn_home_all'):
                    self.btn_home_all.setEnabled(True)
                for jog_button in self.jog_buttons.values():
                    jog_button.setEnabled(True)

                self.is_machine_homed = is_homed

                if is_homed:
                    self.status_leds["HOMED"].setStyleSheet("background-color: #00FF00; color: black; font-weight: bold; padding: 8px; border-radius: 4px;")
                    self.status_leds["READY"].setText(f"STATE: {status_text}")
                    if status_text == 'RUNNING':
                        self.status_leds["READY"].setStyleSheet("background-color: #39FF14; color: black; font-weight: bold; padding: 8px; border-radius: 4px;")
                    elif status_text == 'PAUSED':
                        self.status_leds["READY"].setStyleSheet("background-color: #f1c40f; color: black; font-weight: bold; padding: 8px; border-radius: 4px;")
                    else:
                        self.status_leds["READY"].setStyleSheet("background-color: #3498db; color: white; font-weight: bold; padding: 8px; border-radius: 4px;")
                        
                    for btn in self.home_buttons.values():
                        btn.setEnabled(False) # Visually disable individual home buttons
                    if hasattr(self, 'btn_home_all'):
                        self.btn_home_all.setEnabled(True)
                        self.btn_home_all.setText("UNHOME ALL")
                        self.btn_home_all.setStyleSheet("QPushButton { background-color: #e74c3c; color: white; font-weight: bold; font-size: 16px; border-radius: 5px; } QPushButton:pressed { background-color: #c0392b; padding-top: 2px; padding-left: 2px; }")
                else:
                    self.status_leds["HOMED"].setStyleSheet("background-color: #f39c12; color: black; font-weight: bold; padding: 8px; border-radius: 4px;")
                    self.status_leds["READY"].setText("UNHOMED")
                    self.status_leds["READY"].setStyleSheet("background-color: #f39c12; color: black; font-weight: bold; padding: 8px; border-radius: 4px;")
                    for btn in self.home_buttons.values():
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
        if self.ping_process.state() == QProcess.ProcessState.NotRunning:
            ip = self.settings.get_setting("SSH", "ip_address", "")
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

        if not ip or not user: return
            
        if (HAS_PARAMIKO or is_local_ip(ip)) and not self.pos_fetcher:
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
        for axis in ['X', 'Y', 'Z', 'FEED', 'RPM']:
            if axis in coords:
                self.mock_coords[axis] = coords[axis]
                
        for key in ['HOMED_COUNT', 'MACHINE_ON', 'ESTOP_ACTIVATED', 'JOINTS', 'AXIS_MASK', 'STATUS', 'MODE', 'PROGRAM_LOADED', 'TOTAL_LINES', 'LINE', 'CURRENT_LINE', 'READ_LINE', 'EXEC_STATE', 'INTERP_STATE', 'TASK_STATE', 'COMMAND', 'PROGRAM_FILE']:
            if key in coords:
                self.mock_coords[key] = coords[key]
                
        for key in ['FEED_OVR', 'SPINDLE_OVR', 'ABSOLUTE_X', 'ABSOLUTE_Y', 'ABSOLUTE_Z', 'DTG_X', 'DTG_Y', 'DTG_Z']:
            if key in coords:
                self.mock_coords[key] = coords[key]

        for axis in ("X", "Y", "Z", "FEED", "RPM"):
            custom_label = self.custom_dro_labels.get(axis)
            if custom_label is not None:
                if axis in ("X", "Y", "Z"):
                    value = coords.get(axis, self.mock_coords.get(axis, 0.0))
                    custom_label.setText(f"{value:.3f}")
                else:
                    value = coords.get(axis, self.mock_coords.get(axis, 0.0))
                    custom_label.setText(f"{value:.1f}")

        homed = int(coords.get("HOMED_COUNT", self.mock_coords.get("HOMED_COUNT", 0)))
        joints = int(coords.get("JOINTS", self.mock_coords.get("JOINTS", 0)))
        homed_active = joints > 0 and homed >= joints
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
            
    def highlight_gcode_line(self, line_number):
        if not hasattr(self, 'default_gcode_editor'): return
        
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
            
        # Add Toolpath Highlight
        if hasattr(self, 'toolpath_viewer'):
            self.toolpath_viewer.highlight_line(line_number)
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

        if self.is_estopped:
            return

        self.active_jogs.add(axis)
        
        # Calculate exact unit/sec velocity based on your slider % and INI max velocity
        max_vel = float(self.settings.get_setting(f"AXIS_{axis}", "vel", 50.0))
        velocity = (self.jog_speed_slider.value() / 100.0) * max_vel * speed_multiplier
        velocity *= direction
        
        is_gantry, is_4_axis, is_5_axis, total_joints = self.get_kinematics_info()
        homed_count = self.mock_coords.get('HOMED_COUNT')
        is_homed = None if homed_count is None else int(homed_count) >= total_joints

        if is_gantry and axis == 'Y' and is_homed is False:
            self.active_jog = None
            QMessageBox.warning(self, "Homing Required",
                                f"Cannot manually jog the {axis} axis before homing!\n\n"
                                f"Because your machine is configured as a Gantry (XYYZ), the {axis} axis uses dual independent motors. "
                                f"Jogging them before the machine has executed its squaring sequence can rack and damage your linear rails.\n\n"
                                f"Please click 'Home All' or 'Home {axis}' to safely synchronize the motors first.")
            return
            
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

        self._send_b64_py_cmd(py_code)

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
            if ax in self.active_jogs:
                self.active_jogs.remove(ax)
                
            is_gantry, is_4_axis, is_5_axis, total_joints = self.get_kinematics_info()
            
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

            self._send_b64_py_cmd(py_code)

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

        if self.is_estopped:
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
        # If we lose focus while holding down a key (e.g. clicking a tab), stop jogging!
        if hasattr(self, 'active_jogs') and self.active_jogs:
            for axis in list(self.active_jogs):
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
        self.jog_speed = float(self.settings.get_setting('JOG_SETTINGS', 'jog_speed', 100))
        self.turbo_multiplier = float(self.settings.get_setting('JOG_SETTINGS', 'turbo_multiplier', 5.0))
        self.update_status() # Force a full redraw with new units