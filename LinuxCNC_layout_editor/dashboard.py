from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, 
                             QLabel, QPushButton, QGroupBox, QSlider, QLineEdit, 
                             QSizePolicy, QScrollArea, QMessageBox, QStackedWidget, QFileDialog, QTextEdit, QInputDialog, QGraphicsDropShadowEffect, QTabWidget)
from PyQt6.QtCore import Qt, QTimer, QProcess, QThread, pyqtSignal, QFileSystemWatcher, QRect, QSize
from PyQt6.QtGui import QColor, QPainter, QFont, QSyntaxHighlighter, QTextCharFormat
import platform
import threading
import subprocess
import json
import os
import tempfile
import math
import queue
import time
import re

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

class PositionFetcherThread(QThread):
    position_fetched = pyqtSignal(dict)

    def __init__(self, ip, user, password):
        super().__init__()
        self.ip = ip
        self.user = user
        self.password = password
        self.running = False

    def run(self):
        if not HAS_PARAMIKO:
            return
            
        try:
            self.running = True
            ssh = paramiko.SSHClient()
            ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            ssh.connect(self.ip, username=self.user, password=self.password, timeout=3)
            
            while self.running:
                # Fetch positional feedback from HALUI
                stdin, stdout, stderr = ssh.exec_command("halcmd show pin halui")
                out = stdout.read().decode()
                
                coords = {}
                for line in out.splitlines():
                    parts = line.split()
                    if len(parts) >= 5 and "halui.axis." in parts[4]:
                        val_str = parts[3]
                        name = parts[4]
                        try:
                            val = float(val_str)
                            if '.x.' in name: coords['X'] = val
                            elif '.y.' in name: coords['Y'] = val
                            elif '.z.' in name: coords['Z'] = val
                            elif 'halui.feed-override.value' in name:
                                coords['FEED_OVR'] = val
                            elif 'halui.spindle-override.value' in name:
                                coords['SPINDLE_OVR'] = val
                                
                        except ValueError:
                            pass
                
                if coords:
                    self.position_fetched.emit(coords)
                
                for _ in range(5):  # 500ms loop for quick exit
                    if not self.running: break
                    self.msleep(100)
                    
            ssh.close()
        except Exception:
            pass
        finally:
            self.running = False

    def stop(self):
        self.running = False

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
    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.home_buttons = {}
        self.dro_labels = {}
        self.jog_buttons = {}
        self.status_leds = {}
        self.mock_coords = {'X': 0.0, 'Y': 0.0, 'Z': 0.0}
        self.is_estopped = True
        self.machine_state = "ESTOP"
        self.jog_timer = QTimer()
        self.jog_timer.timeout.connect(self.run_jog_pulse)
        self.active_jog = None
        self.current_step_size = 0.1
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        
        self.ssh_connected = False
        self.ping_process = QProcess(self)
        self.ping_process.finished.connect(self.handle_ping_result)
        self.ping_timer = QTimer(self)
        self.ping_timer.timeout.connect(self.send_ping)
        
        self.pos_fetcher = None

        self.ui_file_watcher = QFileSystemWatcher(self)
        self.ui_file_watcher.fileChanged.connect(self.on_ui_file_changed)
        self.current_ui_file = None

        self.ssh_client = None
        self.cmd_queue = queue.Queue()
        self.cmd_thread = threading.Thread(target=self._cmd_worker, daemon=True)
        self.cmd_thread.start()

        self.init_ui()
        self.init_heartbeat()
        self.ping_timer.start(3000)  # Ping in the background every 3 seconds
        self.send_ping()
        self.setFocus()  # Ensure focus on startup

    def get_probe_plate_thickness(self):
        val = self.settings.get_setting('Calibration', 'probe_plate_thickness')
        try:
            return float(val) if val and str(val).strip() else 5.0
        except (ValueError, TypeError):
            return 5.0

    def update_jog_speed(self):
        # Get the value from the slider (0-100)
        val = self.jog_speed_slider.value() / 100.0
        self.current_step_size = val
        # Update the label to show the current speed
        if hasattr(self, 'lbl_jog_speed_val'):
            self.lbl_jog_speed_val.setText(f"{val:.2f} mm")
        
        # Add this line to give focus back to the main dashboard
        self.setFocus()

    def _toggle_setter_mode(self):
        if self.setter_mode_cb.currentIndex() == 0:
            self.input_tool_setter.setPlaceholderText("Setter Ref Z (Machine Coordinate)")
            self.input_tool_setter.setReadOnly(True)
            self.input_tool_setter.setStyleSheet("background-color: #111; color: #f39c12; font-weight: bold; border: 1px solid #555;")
            self.input_tool_setter.setText(self.settings.get_setting("PROBING", "setter_ref_z", ""))
            if hasattr(self, 'btn_calibrate_setter'): self.btn_calibrate_setter.setVisible(True)
        else:
            self.input_tool_setter.setPlaceholderText("Setter Thickness off Spoilboard (mm)")
            self.input_tool_setter.setReadOnly(False)
            self.input_tool_setter.setStyleSheet("background-color: #2d2d30; color: white; font-weight: bold; border: 1px solid #555;")
            self.input_tool_setter.setText(self.settings.get_setting("PROBING", "setter_thickness", "50.0"))
            if hasattr(self, 'btn_calibrate_setter'): self.btn_calibrate_setter.setVisible(False)
            
    def _save_setter_val(self, text):
        if self.setter_mode_cb.currentIndex() == 0: self.settings.save_setting("PROBING", "setter_ref_z", text)
        else: self.settings.save_setting("PROBING", "setter_thickness", text)

    def init_ui(self):
        # 1. KILL THE ZOMBIE LAYOUTS
        if self.layout() is not None:
            # We move the old layout to a 'dead' widget to clear the screen
            QWidget().setLayout(self.layout())

        self.main_layout = QVBoxLayout(self)
        
        # Top Toolbar for Custom UI Loading
        toolbar = QHBoxLayout()
        
        self.btn_load_custom = QPushButton("📂 Load Custom UI (.linuxcncui)")
        self.btn_load_custom.setStyleSheet("QPushButton { background-color: #8e44ad; color: white; font-weight: bold; padding: 6px; } QPushButton:pressed { background-color: #732d91; }")
        self.btn_load_custom.clicked.connect(self.load_custom_ui)
        toolbar.addWidget(self.btn_load_custom)
        
        self.btn_default_ui = QPushButton("↺ Restore Default UI")
        self.btn_default_ui.setStyleSheet("QPushButton { background-color: #2c3e50; color: white; font-weight: bold; padding: 6px; } QPushButton:pressed { background-color: #1a252f; }")
        self.btn_default_ui.clicked.connect(self.restore_default_ui)
        toolbar.addWidget(self.btn_default_ui)
        
        toolbar.addStretch()
        self.main_layout.addLayout(toolbar)

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

    def build_default_ui(self):
        # 2. DEFINE THE DICTIONARY IMMEDIATELY (Prevents KeyError)
        self.status_leds = {
            "SSH": QLabel("SSH: OFFLINE"),
            "ESTOP": QLabel("ESTOP"),
            "POWER": QLabel("POWER"),
            "HOMED": QLabel("HOMED"),
            "READY": QLabel("READY")
        }

        # 3. START THE FRESH GRID
        layout = QGridLayout(self.default_ui_widget)

        # 4. ADD THE LEDS TO THE TOP ROW (Row 0)
        for i, (name, label) in enumerate(self.status_leds.items()):
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            label.setStyleSheet("background-color: gray; color: white; padding: 5px;")
            layout.addWidget(label, 0, i) # Row 0, Column i

        # 5. FIX THE DRO/HOME SECTION (Row 1)
        # Use a sub-grid for the DROs so they don't jump around
        dro_group = QGroupBox("Digital Readout (DRO)")
        dro_v_layout = QVBoxLayout(dro_group)

        # --- Global DRO Mode Buttons ---
        mode_group = QWidget()
        mode_layout = QHBoxLayout(mode_group)
        mode_layout.setContentsMargins(0, 0, 0, 0)
        mode_layout.setSpacing(5)
        
        btn_work = QPushButton("Work")
        btn_abs = QPushButton("Abs")
        btn_dtg = QPushButton("DTG")
        
        self.dro_mode_buttons = {'WORK': btn_work, 'ABS': btn_abs, 'DTG': btn_dtg}
        for mode, btn in self.dro_mode_buttons.items():
            btn.setStyleSheet(f"background-color: {'#2980b9' if mode == self.dro_mode else '#34495e'}; color: white; padding: 4px; border-radius: 3px; font-weight: bold;")
            btn.clicked.connect(lambda _, m=mode: self.set_dro_mode(m))
            mode_layout.addWidget(btn)
            
        dro_v_layout.addWidget(mode_group)

        for i, axis in enumerate(["X", "Y", "Z"]):
            row_layout = QHBoxLayout()
            if axis not in self.dro_labels:
                self.dro_labels[axis] = QLabel("0.000")
            
            self.dro_labels[axis].setFixedWidth(150)
            self.dro_labels[axis].setStyleSheet("font-size: 24px; color: #39FF14; background: black;")
            lbl = QLabel(f"{axis}:")
            row_layout.addWidget(lbl)
            row_layout.addWidget(self.dro_labels[axis])
            dro_v_layout.addLayout(row_layout)

        layout.addWidget(dro_group, 1, 0, 1, 2)

        self.btn_master_toggle = QPushButton("RESET")
        self.btn_master_toggle.setStyleSheet("QPushButton { background-color: red; color: white; font-weight: bold; } QPushButton:pressed { background-color: darkred; padding-top: 2px; padding-left: 2px; }")
        self.btn_master_toggle.clicked.connect(self.toggle_estop)
        layout.addWidget(self.btn_master_toggle, 2, 0, 1, 3)

        power_btn_layout = QHBoxLayout()
        
        self.btn_restart = QPushButton("🔄 RESTART PC")
        self.btn_restart.setStyleSheet("QPushButton { background-color: #2980b9; color: white; font-weight: bold; } QPushButton:pressed { background-color: #1f618d; }")
        self.btn_restart.clicked.connect(self.prompt_remote_restart)
        power_btn_layout.addWidget(self.btn_restart)
        
        self.btn_shutdown = QPushButton("⏻ SHUTDOWN")
        self.btn_shutdown.setStyleSheet("QPushButton { background-color: #8e44ad; color: white; font-weight: bold; } QPushButton:pressed { background-color: #732d91; }")
        self.btn_shutdown.clicked.connect(self.prompt_remote_shutdown)
        power_btn_layout.addWidget(self.btn_shutdown)
        
        layout.addLayout(power_btn_layout, 2, 3, 1, 1)

        self.jog_speed_slider = QSlider(Qt.Orientation.Horizontal)
        self.jog_speed_slider.setRange(1, 100)
        self.jog_speed_slider.setValue(10)
        self.jog_speed_slider.valueChanged.connect(self.update_jog_speed)
        layout.addWidget(QLabel("Jog Speed:"), 3, 0)
        layout.addWidget(self.jog_speed_slider, 3, 1, 1, 2)
        
        self.lbl_jog_speed_val = QLabel("0.10 mm")
        layout.addWidget(self.lbl_jog_speed_val, 3, 3)

        self.input_z_thickness = QLineEdit()
        self.input_z_thickness.setPlaceholderText("Z Offset (e.g. 5.0)")
        layout.addWidget(self.input_z_thickness, 4, 0, 1, 2)

        # 6. PUSH EVERYTHING UP (The Anchor)
        layout.setRowStretch(10, 1)

    def restore_default_ui(self):
        self.stack.setCurrentIndex(0)
        if self.ui_file_watcher.files():
            self.ui_file_watcher.removePaths(self.ui_file_watcher.files())
        self.current_ui_file = None

    def load_custom_ui(self, path=None):
        if not path:
            last_dir = self.settings.get_setting("PATHS", "last_ui_dir", os.path.join(os.getcwd(), "ui_layouts"))
            path, _ = QFileDialog.getOpenFileName(self, "Load Custom UI", last_dir, "LinuxCNC UI (*.linuxcncui)")
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
                
                # Phase 4: Wire Button to SSH HAL Command
                safe_title = title.replace(' ', '_').lower()
                out_pin = f"ui.{safe_title}.out"
                btn.pressed.connect(lambda p=out_pin: self.send_halcmd_setp(p, "True"))
                btn.released.connect(lambda p=out_pin: self.send_halcmd_setp(p, "False"))
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
                widget_obj = grp
            elif w_type == "LED":
                lbl = QLabel(display_title, self.custom_ui_widget)
                lbl.setGeometry(int(x), int(y), int(w), int(h))
                lbl.setAlignment(q_align | q_valign)
                lbl.setStyleSheet(f"QLabel {{ {common_style} border: {border_width}px solid {border_color}; border-radius: {border_radius}px; font-weight: bold; }}")
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
                
                txt = QTextEdit()
                txt.setPlaceholderText(f"(G-Code Editor: {display_title})\n\nLoad a file from LinuxCNC or paste G-Code here.")
                txt.setStyleSheet("background-color: #1e1e1e; color: #ecf0f1; font-family: monospace; border: 1px solid #8e44ad;")
                
                vbox.addLayout(toolbar)
                vbox.addWidget(txt)
                
                btn_load.clicked.connect(lambda _, t=txt: self.load_gcode(t))
                btn_save.clicked.connect(lambda _, t=txt: self.save_gcode(t))
                btn_sync.clicked.connect(lambda _, t=txt: self.sync_gcode_to_machine(t.toPlainText()))
                btn_clear.clicked.connect(txt.clear)
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
        print("[dashboard.py] Custom UI loaded successfully.")

    def send_halcmd_setp(self, pin_name, value_str):
        ip = self.settings.get_setting("SSH", "ip_address", "")
        user = self.settings.get_setting("SSH", "username", "")
        password = self.settings.get_setting("SSH", "password", "")
        
        if not ip or not user: return
            
        cmd = f"halcmd setp {pin_name} {value_str}"
        self.cmd_queue.put(cmd)

    def _cmd_worker(self):
        while True:
            cmd = self.cmd_queue.get()
            if cmd is None: break
            
            ip = self.settings.get_setting("SSH", "ip_address", "")
            user = self.settings.get_setting("SSH", "username", "")
            password = self.settings.get_setting("SSH", "password", "")
            if not ip or not user: continue
            
            if HAS_PARAMIKO:
                try:
                    if not self.ssh_client or not self.ssh_client.get_transport() or not self.ssh_client.get_transport().is_active():
                        self.ssh_client = paramiko.SSHClient()
                        self.ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                        self.ssh_client.connect(ip, username=user, password=password, timeout=5, look_for_keys=not bool(password), allow_agent=not bool(password))
                    self.ssh_client.exec_command(cmd)
                except Exception:
                    self.ssh_client = None
            else:
                try:
                    subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=3", f"{user}@{ip}", cmd], 
                                   capture_output=True, timeout=5)
                except:
                    pass

    def sync_gcode_to_machine(self, content):
        if not content.strip(): return
        ip = self.settings.get_setting("SSH", "ip_address", "")
        user = self.settings.get_setting("SSH", "username", "")
        password = self.settings.get_setting("SSH", "password", "")
        if not ip or not user: return
        
        def task():
            remote_dir = f"/home/{user}/linuxcnc/nc_files"
            remote_path = f"{remote_dir}/active_program.ngc"
            
            if is_local_ip(ip):
                try:
                    os.makedirs(os.path.expanduser("~/linuxcnc/nc_files"), exist_ok=True)
                    local_dest = os.path.expanduser("~/linuxcnc/nc_files/active_program.ngc")
                    with open(local_dest, 'w', encoding='utf-8') as f:
                        f.write(content)
                    remote_path = local_dest
                except: pass
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
                except: pass
            else:
                try:
                    subprocess.run(["ssh", "-o", "BatchMode=yes", f"{user}@{ip}", f"mkdir -p {remote_dir}"], capture_output=True, timeout=5)
                    with tempfile.NamedTemporaryFile(delete=False, mode='w', encoding='utf-8') as f:
                        f.write(content)
                        temp_path = f.name
                    subprocess.run(["scp", "-o", "BatchMode=yes", temp_path, f"{user}@{ip}:{remote_path}"], capture_output=True, timeout=10)
                    os.remove(temp_path)
                except: pass
                
            py_code = f"import linuxcnc\ntry:\n    c=linuxcnc.command()\n    c.program_open('{remote_path}')\nexcept: pass"
            self._send_b64_py_cmd(py_code)
            
        threading.Thread(target=task, daemon=True).start()

    def load_gcode(self, text_widget):
        last_dir = self.settings.get_setting("PATHS", "last_local_gcode_dir", os.path.expanduser("~"))
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

    def save_gcode(self, text_widget):
        default_path = self.settings.get_setting("PATHS", "last_gcode_file", "~/linuxcnc/nc_files/program.ngc")
        remote_path, ok = QInputDialog.getText(self, "Save G-Code", "Enter remote file path:", text=default_path)
        if not ok or not remote_path: return
        self.settings.save_setting("PATHS", "last_gcode_file", remote_path)
        
        content = text_widget.toPlainText()
        ip = self.settings.get_setting("SSH", "ip_address", "")
        user = self.settings.get_setting("SSH", "username", "")
        password = self.settings.get_setting("SSH", "password", "")
        
        if not ip or not user: return
            
        if HAS_PARAMIKO:
            try:
                ssh = paramiko.SSHClient()
                ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                ssh.connect(ip, username=user, password=password, timeout=5)
                sftp = ssh.open_sftp()
                with sftp.file(remote_path, 'w') as f:
                    f.write(content.encode('utf-8'))
                sftp.close()
                ssh.close()
                QMessageBox.information(self, "Saved", f"G-Code successfully saved to:\n{remote_path}")
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Failed to save G-Code:\n{e}")
        else:
            with tempfile.NamedTemporaryFile(delete=False, mode='w') as f:
                f.write(content)
                temp_path = f.name
            
            cmd = ["scp", "-o", "BatchMode=yes", temp_path, f"{user}@{ip}:{remote_path}"]
            res = subprocess.run(cmd, capture_output=True, text=True)
            os.remove(temp_path)
            
            if res.returncode == 0:
                QMessageBox.information(self, "Saved", f"G-Code successfully saved locally and synced to the machine!")
                self.sync_gcode_to_machine(content)
            else:
                QMessageBox.critical(self, "Error", f"Failed to save G-Code:\n{res.stderr}")

    def toggle_estop(self):
        if self.machine_state == "ESTOP":
            self.machine_state = "STANDBY"
            self.is_estopped = False
            self.btn_master_toggle.setText("E-STOP")
            self.btn_master_toggle.setStyleSheet("QPushButton { background-color: orange; color: black; font-weight: bold; } QPushButton:pressed { background-color: #cc8400; padding-top: 2px; padding-left: 2px; }")
            for button in self.home_buttons.values():
                button.setEnabled(True)
            for jog_button in self.jog_buttons.values():
                jog_button.setEnabled(True)
        else:
            self.machine_state = "ESTOP"
            self.is_estopped = True
            self.btn_master_toggle.setText("RESET")
            self.btn_master_toggle.setStyleSheet("QPushButton { background-color: red; color: white; font-weight: bold; } QPushButton:pressed { background-color: darkred; padding-top: 2px; padding-left: 2px; }")
            self.status_leds["ESTOP"].setStyleSheet("background-color: red; color: white; font-weight: bold; padding: 5px; border: 1px solid black;")
            self.status_leds["POWER"].setStyleSheet("background-color: darkgrey; color: white; font-weight: bold; padding: 5px; border: 1px solid black;")
            for button in self.home_buttons.values():
                button.setEnabled(False)
            for jog_button in self.jog_buttons.values():
                jog_button.setEnabled(False)

    def prompt_remote_restart(self):
        confirm = QMessageBox.question(self, "Restart Machine", "Are you sure you want to restart the remote Linux machine?", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if confirm == QMessageBox.StandardButton.Yes:
            self.cmd_queue.put("sudo reboot")

    def prompt_remote_shutdown(self):
        confirm = QMessageBox.question(self, "Shutdown Machine", "Are you sure you want to completely shut down the remote Linux machine?", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if confirm == QMessageBox.StandardButton.Yes:
            self.cmd_queue.put("sudo shutdown -h now")

    def home_axis(self, axis):
        if self.is_estopped:
            return
        self.mock_coords[axis] = 0.0
        if axis in self.home_buttons:
            self.home_buttons[axis].setEnabled(False)
        self.update_status()
        print(f"[tabs.py] {axis} Axis Homed")

    def fast_probe(self):
        if self.is_estopped:
            return
        self.mock_coords['Z'] = 25.0
        print("[tabs.py] Fast Probe Complete")

    def set_z_zero(self):
        if self.is_estopped:
            return
        try:
            val = float(self.input_z_thickness.text() or 0.0)
            self.mock_coords['Z'] = val
            print(f"[tabs.py] Z Zero set to {val}")
        except ValueError:
            print("[tabs.py] Invalid input for Z Zero")

    def update_status(self):
        """Refreshes DRO labels and LED indicator colors."""
        # Update the numbers in the dictionary instead of using old variable names
        for axis in ['X', 'Y', 'Z']:
            if axis in self.dro_labels:
                val = self.mock_coords[axis]
                self.dro_labels[axis].setText(f"{val:.3f}")
                
        if 'FEED_OVR' in self.mock_coords and not self.feed_slider.isSliderDown():
            feed_val = int(self.mock_coords['FEED_OVR'] * 100)
            if self.feed_slider.value() != feed_val:
                self.feed_slider.blockSignals(True)
                self.feed_slider.setValue(feed_val)
                self.lbl_feed_val.setText(f"{feed_val}%")
                self.feed_slider.blockSignals(False)
                
        if 'SPINDLE_OVR' in self.mock_coords and not self.spindle_slider.isSliderDown():
            spindle_val = int(self.mock_coords['SPINDLE_OVR'] * 100)
            if self.spindle_slider.value() != spindle_val:
                self.spindle_slider.blockSignals(True)
                self.spindle_slider.setValue(spindle_val)
                self.lbl_spindle_val.setText(f"{spindle_val}%")
                self.spindle_slider.blockSignals(False)

        # Update SSH LED Visuals
        current_time = time.time()
        if self.ssh_connected:
            self.status_leds["SSH"].setStyleSheet("background-color: #00FF00; color: black; border-radius: 5px; font-weight: bold; padding: 5px;")
            self.status_leds["SSH"].setText("SSH: ONLINE")
            self._was_connected_once = True
        else:
            if getattr(self, '_was_connected_once', False):
                if int(current_time * 2) % 2 == 0:
                    self.status_leds["SSH"].setStyleSheet("background-color: #f39c12; color: black; border-radius: 5px; font-weight: bold; padding: 5px;")
                    self.status_leds["SSH"].setText("RECONNECTING...")
                else:
                    self.status_leds["SSH"].setStyleSheet("background-color: #c0392b; color: white; border-radius: 5px; font-weight: bold; padding: 5px;")
                    self.status_leds["SSH"].setText("CONNECTION LOST")
            else:
                self.status_leds["SSH"].setStyleSheet("background-color: red; color: white; border-radius: 5px; font-weight: bold; padding: 5px;")
                self.status_leds["SSH"].setText("SSH: OFFLINE")

        # Update LED Visuals
        is_homed = len(self.home_buttons) > 0 and all(not btn.isEnabled() for btn in self.home_buttons.values())
        
        if self.is_estopped:
            self.status_leds["ESTOP"].setStyleSheet("background-color: red; color: white; font-weight: bold; border: 2px solid darkred; padding: 5px; border-radius: 5px;")
            self.status_leds["POWER"].setStyleSheet("background-color: #222; color: #444; padding: 5px; border-radius: 5px;")
            self.status_leds["HOMED"].setStyleSheet("background-color: #555; color: white; font-weight: bold; padding: 5px; border: 1px solid black;")
            self.status_leds["READY"].setStyleSheet("background-color: #555; color: white; font-weight: bold; padding: 5px; border: 1px solid black;")
        else:
            self.status_leds["ESTOP"].setStyleSheet("background-color: #222; color: #444; padding: 5px; border-radius: 5px;")
            self.status_leds["POWER"].setStyleSheet("background-color: #00FF00; color: black; border-radius: 5px; font-weight: bold; padding: 5px;")
            
            if is_homed:
                self.status_leds["HOMED"].setStyleSheet("background-color: #00FF00; color: black; border-radius: 5px; font-weight: bold; padding: 5px;")
                self.status_leds["READY"].setStyleSheet("background-color: #00FF00; color: black; border-radius: 5px; font-weight: bold; padding: 5px;")
            else:
                self.status_leds["HOMED"].setStyleSheet("background-color: #f39c12; color: black; border-radius: 5px; font-weight: bold; padding: 5px;")
                self.status_leds["READY"].setStyleSheet("background-color: #f39c12; color: black; border-radius: 5px; font-weight: bold; padding: 5px;")

    def home_all_axes(self):
        """Homes all axes simultaneously."""
        print("[dashboard.py] Homing all axes...")
        for axis in ["X", "Y", "Z"]:
            self.home_axis(axis)

    def send_ping(self):
        """Silently checks if the LinuxCNC machine is reachable on the network."""
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
        was_connected = self.ssh_connected
        self.ssh_connected = (exitCode == 0 and exitStatus == QProcess.ExitStatus.NormalExit)
        
        if self.ssh_connected and not was_connected:
            self.start_position_fetcher()
        elif not self.ssh_connected and was_connected:
            self.stop_position_fetcher()

    def start_position_fetcher(self):
        ip = self.settings.get_setting("SSH", "ip_address", "")
        user = self.settings.get_setting("SSH", "username", "")
        password = self.settings.get_setting("SSH", "password", "")
        
        if not ip or not user: return
            
        if HAS_PARAMIKO and not self.pos_fetcher:
            self.pos_fetcher = PositionFetcherThread(ip, user, password)
            self.pos_fetcher.position_fetched.connect(self.update_real_coords)
            self.pos_fetcher.start()

    def stop_position_fetcher(self):
        if self.pos_fetcher:
            self.pos_fetcher.stop()
            self.pos_fetcher.wait()
            self.pos_fetcher = None

    def update_real_coords(self, coords):
        for axis, val in coords.items():
            self.mock_coords[axis] = val

    def init_heartbeat(self):
        """Starts the background timer for DRO and LED updates."""
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.update_status)
        self.timer.start(100)  # 10Hz

    def jog_button_pressed(self, axis, direction, speed_multiplier=1):
        if self.is_estopped:
            return

        # Fetch limits from SettingsManager
        x_max = float(self.settings.get_setting('MACHINE_LIMITS', 'x_max', 500))
        z_min = float(self.settings.get_setting('MACHINE_LIMITS', 'z_min', 0))

        # Calculate the new position based on the jog step
        new_position = self.mock_coords[axis] + (self.current_step_size * direction * speed_multiplier)

        # Check soft limits
        if axis == 'X' and new_position > x_max:
            print("Soft Limit Reached: X axis")
            return
        if axis == 'Z' and new_position < z_min:
            print("Soft Limit Reached: Z axis")
            return

        self.active_jog = (axis, direction, speed_multiplier)
        self.jog_timer.start(50)  # Move every 50ms (smooth motion)

    def jog_button_released(self):
        self.jog_timer.stop()
        self.active_jog = None

    def run_jog_pulse(self):
        if self.active_jog and not self.is_estopped:
            axis, direction, speed_multiplier = self.active_jog
            # Adjust speed using the current step size and multiplier
            self.mock_coords[axis] += (self.current_step_size * direction * speed_multiplier)
            self.update_status()

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

        # Jogging Logic with PyQt6 naming and speed multiplier
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

    def keyReleaseEvent(self, event):
        if event is None or event.isAutoRepeat():
            return

        key = event.key()

        # Handle Shift release without stopping jogging
        if key == Qt.Key.Key_Shift:
            if self.current_key in [Qt.Key.Key_Up, Qt.Key.Key_Down, Qt.Key.Key_Left, Qt.Key.Key_Right, Qt.Key.Key_PageUp, Qt.Key.Key_PageDown]:
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
            self.jog_button_released()
            self.current_key = None

    def update_local_settings(self):
        """Refreshes the dashboard variables when settings change."""
        print("--- [dashboard.py] Signal Received: Updating live settings ---")
        self.jog_speed = float(self.settings.get_setting('JOG_SETTINGS', 'jog_speed', 100))
        self.turbo_multiplier = float(self.settings.get_setting('JOG_SETTINGS', 'turbo_multiplier', 5.0))