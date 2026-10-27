import time
import os
import json
import base64
import subprocess
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QPushButton, 
                             QLabel, QTableWidget, QTableWidgetItem, QHeaderView, 
                             QLineEdit, QGraphicsView, QGraphicsScene, QColorDialog, QMessageBox, QComboBox)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QTimer
from PyQt6.QtGui import QColor, QPen, QPainter, QBrush, QPainterPath

from core.machine_agent import client_from_settings
from core.linuxcnc_api import LocalLinuxCNCAPI

LOCAL_IPS = ['localhost', '127.0.0.1']
try:
    _out = subprocess.check_output("hostname -I", shell=True, text=True, stderr=subprocess.DEVNULL)
    LOCAL_IPS.extend(_out.split())
except:
    pass

def is_local_ip(ip):
    return ip in LOCAL_IPS

class ScopeFetcherThread(QThread):
    data_fetched = pyqtSignal(dict)
    error_occurred = pyqtSignal(str)

    def __init__(self, ip, user, password, pins, local_api=None):
        super().__init__()
        self.ip = ip
        self.user = user
        self.password = password
        self.pins = pins
        self.local_api = local_api
        self.running = False
        
        cmd_str = " ; ".join([f"halcmd getp {p}" for p in self.pins])
        py_code = f"""import subprocess, time, sys
while True:
    try:
        out = subprocess.check_output("{cmd_str}", shell=True, stderr=subprocess.DEVNULL).decode()
        vals = out.strip().split('\\n')
        print(",".join(vals))
        sys.stdout.flush()
    except Exception:
        pass
    time.sleep(0.1)"""
        b64 = base64.b64encode(py_code.encode()).decode()
        self.cmd = f"python3 -u -c \"import base64; exec(base64.b64decode('{b64}').decode())\""

    def run(self):
        if not self.pins:
            return

        if self.local_api is not None:
            self.running = True
            while self.running:
                try:
                    values = {}
                    for pin_name in self.pins:
                        value = self.local_api.get_hal_value(pin_name)
                        if isinstance(value, bool):
                            values[pin_name] = float(value)
                        else:
                            values[pin_name] = float(value)
                    self.data_fetched.emit(values)
                except Exception as error:
                    self.error_occurred.emit(f"HAL scope read failed: {error}")
                    return
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
                    if line:
                        vals = {}
                        parts = line.split(',')
                        for i, val_str in enumerate(parts):
                            if i < len(self.pins):
                                if val_str.lower() == "true": val = 1.0
                                elif val_str.lower() == "false": val = 0.0
                                else:
                                    try: val = float(val_str)
                                    except: val = 0.0
                                vals[self.pins[i]] = val
                        if vals:
                            self.data_fetched.emit(vals)
                proc.terminate()
                for _ in range(20):
                    if not self.running: break
                    self.msleep(100)
            return

        if not self.ip or not self.user:
            self.error_occurred.emit("Remote credentials are required for live monitoring.")
            return
            
        self.running = True
        while self.running:
            try:
                proc = subprocess.Popen(
                    ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", f"{self.user}@{self.ip}", self.cmd],
                    stdout=subprocess.PIPE,
                    text=True,
                )
                while self.running and proc.poll() is None:
                    try:
                        line = proc.stdout.readline()
                        if not line: break
                        line = line.strip()
                        if line:
                            vals = {}
                            parts = line.split(',')
                            for i, val_str in enumerate(parts):
                                if i < len(self.pins):
                                    if val_str.lower() == "true": val = 1.0
                                    elif val_str.lower() == "false": val = 0.0
                                    else:
                                        try: val = float(val_str)
                                        except: val = 0.0
                                    vals[self.pins[i]] = val
                            if vals:
                                self.data_fetched.emit(vals)
                    except Exception:
                        pass
                proc.terminate()
            except Exception as e:
                pass
            
            for _ in range(20):
                if not self.running: break
                self.msleep(100)

    def stop(self):
        self.running = False


class AgentScopeFetcherThread(QThread):
    """Poll bounded HAL value snapshots through the enrolled machine agent."""

    data_fetched = pyqtSignal(dict)
    error_occurred = pyqtSignal(str)

    def __init__(self, agent_client, pins):
        super().__init__()
        self.agent_client = agent_client
        self.pins = pins
        self.running = False

    def run(self):
        self.running = True
        while self.running:
            try:
                result = self.agent_client.read_hal_values(self.pins)
                values = {}
                for pin, value in result.get("values", {}).items():
                    if value.lower() == "true":
                        values[pin] = 1.0
                    elif value.lower() == "false":
                        values[pin] = 0.0
                    else:
                        values[pin] = float(value)
                if values:
                    self.data_fetched.emit(values)
                if result.get("error"):
                    self.error_occurred.emit(result["error"])
                    return
            except (TypeError, ValueError) as error:
                self.error_occurred.emit(f"HAL value response is invalid: {error}")
                return
            except Exception as error:
                self.error_occurred.emit(f"HAL value polling failed: {error}")
                return
            self.msleep(100)

    def stop(self):
        self.running = False

class ScopeView(QGraphicsView):
    def __init__(self):
        super().__init__()
        self.scene = QGraphicsScene()
        self.setScene(self.scene)
        self.setBackgroundBrush(QBrush(QColor("#1e1e1e")))
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        self.buffer_size = 100
        self.data_buffers = {}
        self.colors = {}
        
        self.grid_pen = QPen(QColor(50, 50, 50), 1, Qt.PenStyle.DotLine)
        self.grid_pen.setCosmetic(True)
        self.axis_pen = QPen(QColor(100, 100, 100), 2)
        self.axis_pen.setCosmetic(True)
        
    def add_pin(self, pin_name, color):
        self.data_buffers[pin_name] = [0.0] * self.buffer_size
        self.colors[pin_name] = QColor(color)
        
    def remove_pin(self, pin_name):
        if pin_name in self.data_buffers:
            del self.data_buffers[pin_name]
            del self.colors[pin_name]
            self.redraw()
            
    def update_data(self, vals):
        needs_redraw = False
        for pin_name, val in vals.items():
            if pin_name in self.data_buffers:
                self.data_buffers[pin_name].append(val)
                if len(self.data_buffers[pin_name]) > self.buffer_size:
                    self.data_buffers[pin_name].pop(0)
                needs_redraw = True
                
        if needs_redraw:
            self.redraw()
            
    def clear_data(self):
        for pin_name in self.data_buffers:
            self.data_buffers[pin_name] = [0.0] * self.buffer_size
        self.redraw()
        
    def redraw(self):
        self.scene.clear()
        
        rect = self.viewport().rect()
        w = max(rect.width(), 800)
        h = max(rect.height(), 400)
        self.setSceneRect(0, -h/2, w, h)
        
        # Draw grid
        for y in range(int(-h/2), int(h/2), 50):
            self.scene.addLine(0, y, w, y, self.grid_pen)
        for x in range(0, int(w), 50):
            self.scene.addLine(x, -h/2, x, h/2, self.grid_pen)
            
        self.scene.addLine(0, 0, w, 0, self.axis_pen)
        
        x_step = w / max(1, self.buffer_size - 1)
        
        # Auto-scale Y based on the max visible value across all pins
        max_val = 0.01 # Prevent divide by zero
        for buf in self.data_buffers.values():
            if buf: max_val = max(max_val, max(abs(v) for v in buf))
            
        y_scale = (h/2.2) / max_val
        
        for pin_name, buf in self.data_buffers.items():
            if not buf: continue
            
            path = QPainterPath()
            path.moveTo(0, -buf[0] * y_scale)
            for i, val in enumerate(buf):
                path.lineTo(i * x_step, -val * y_scale)
                
            pen = QPen(self.colors[pin_name], 2)
            pen.setCosmetic(True)
            self.scene.addPath(path, pen)
            
    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.redraw()

class HalScopeTab(QWidget):
    def __init__(self, settings):
        super().__init__()
        self.settings = settings
        self.parent_app = None
        self.fetcher = None
        self.active_pins = {}
        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout(self)
        
        self.setStyleSheet("""
            QWidget { background-color: #1e1e1e; color: #ffffff; font-family: 'Segoe UI', Arial, sans-serif; }
            QLabel { font-weight: bold; color: #ecf0f1; font-size: 13px; }
            QPushButton { background-color: #2980b9; color: white; font-weight: bold; border-radius: 4px; padding: 6px 12px; border: none; font-size: 13px;}
            QPushButton:hover { background-color: #3498db; }
            QPushButton:pressed { background-color: #1f618d; }
            QLineEdit, QComboBox { background-color: #2d2d30; color: white; border: 1px solid #555555; padding: 6px; border-radius: 4px; font-size: 13px; min-width: 250px; }
            QLineEdit:focus, QComboBox:focus { border: 1px solid #3498db; }
            QComboBox::drop-down { border-left: 1px solid #555555; }
            QTableWidget { background-color: #252526; color: #ffffff; border: 1px solid #3e3e42; font-size: 13px; alternate-background-color: #2d2d30; }
            QTableWidget::item:selected { background-color: #2980b9; color: #ffffff; }
            QHeaderView::section { background-color: #333337; color: white; padding: 6px; border: 1px solid #3e3e42; font-weight: bold; }
        """)
        
        # Toolbar
        toolbar = QHBoxLayout()
        
        self.btn_run = QPushButton("▶ Run Scope")
        self.btn_run.setStyleSheet("background-color: #27ae60;")
        self.btn_run.clicked.connect(self.toggle_run)
        toolbar.addWidget(self.btn_run)
        
        self.btn_clear = QPushButton("🗑️ Clear Buffer")
        self.btn_clear.clicked.connect(self.clear_buffer)
        toolbar.addWidget(self.btn_clear)
        
        self.btn_pop_out = QPushButton("🗔 Pop Out")
        self.btn_pop_out.clicked.connect(self.toggle_pop_out)
        toolbar.addWidget(self.btn_pop_out)
        
        toolbar.addStretch()
        
        self.pin_input = QComboBox()
        self.pin_input.setEditable(True)
        self.pin_input.lineEdit().setPlaceholderText("Select or type HAL pin...")
        self.populate_pin_dropdown()
        toolbar.addWidget(self.pin_input)
        
        self.btn_add_pin = QPushButton("➕ Add Channel")
        self.btn_add_pin.clicked.connect(self.add_pin)
        toolbar.addWidget(self.btn_add_pin)
        
        layout.addLayout(toolbar)
        
        # Scope View
        self.scope = ScopeView()
        layout.addWidget(self.scope, 3)
        
        # Channel Table
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Color", "Pin Name", "Current Value", "Remove"])
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        layout.addWidget(self.table, 1)

    def toggle_pop_out(self):
        if not self.isWindow():
            if self.parent_app and hasattr(self.parent_app, 'tabs'):
                idx = self.parent_app.tabs.indexOf(self)
                if idx != -1:
                    self.parent_app.tabs.removeTab(idx)
            self.setWindowFlags(Qt.WindowType.Window)
            self.setWindowTitle("HAL Oscilloscope - Live Data")
            self.resize(1000, 600)
            self.show()
            self.btn_pop_out.setText("🗗 Dock to Tabs")
        else:
            self.setWindowFlags(Qt.WindowType.Widget)
            if self.parent_app and hasattr(self.parent_app, 'tabs'):
                self.parent_app.tabs.insertTab(5, self, "📈 HAL OSCILLOSCOPE")
                self.parent_app.tabs.setCurrentWidget(self)
            self.show()
            self.btn_pop_out.setText("🗔 Pop Out")

    def populate_pin_dropdown(self):
        pins = set()
        if os.path.exists("component_library.json"):
            try:
                with open("component_library.json", 'r') as f:
                    data = json.load(f)
                    for comp, pin_list in data.items():
                        for pin in pin_list:
                            # Filter out empty pins or the dummy "<=>" mapping
                            if len(pin) > 2 and pin[2].strip() and pin[2].strip() != "<=>":
                                pins.add(pin[2].strip())
            except Exception:
                pass
        
        self.pin_input.addItems(sorted(list(pins)))
        self.pin_input.setCurrentText("")

    def add_pin(self):
        pin_name = self.pin_input.currentText().strip()
        if not pin_name or pin_name in self.active_pins: return
            
        color = QColorDialog.getColor(Qt.GlobalColor.green, self, "Select Trace Color")
        if not color.isValid(): return
            
        self.active_pins[pin_name] = color.name()
        self.scope.add_pin(pin_name, color.name())
        self.pin_input.setCurrentText("")
        
        self.update_table()
        
        # Restart fetcher with new pins if running
        if self.fetcher and self.fetcher.running:
            self.toggle_run()
            self.toggle_run()

    def remove_pin(self, pin_name):
        if pin_name in self.active_pins:
            del self.active_pins[pin_name]
            self.scope.remove_pin(pin_name)
            self.update_table()
            
            if self.fetcher and self.fetcher.running:
                self.toggle_run()
                self.toggle_run()

    def update_table(self):
        self.table.setRowCount(len(self.active_pins))
        for i, (pin_name, color) in enumerate(self.active_pins.items()):
            # Color block
            color_lbl = QLabel()
            color_lbl.setStyleSheet(f"background-color: {color}; margin: 2px;")
            self.table.setCellWidget(i, 0, color_lbl)
            
            self.table.setItem(i, 1, QTableWidgetItem(pin_name))
            
            val_item = QTableWidgetItem("0.0")
            val_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(i, 2, val_item)
            
            btn_rem = QPushButton("Remove")
            btn_rem.setStyleSheet("background-color: #c0392b; padding: 2px;")
            btn_rem.clicked.connect(lambda _, p=pin_name: self.remove_pin(p))
            self.table.setCellWidget(i, 3, btn_rem)

    def toggle_run(self):
        if self.fetcher and self.fetcher.running:
            self.fetcher.stop()
            self.fetcher.wait()
            self.btn_run.setText("▶ Run Scope")
            self.btn_run.setStyleSheet("background-color: #27ae60;")
        else:
            if not self.active_pins:
                QMessageBox.information(self, "No Pins", "Please add HAL pins to monitor first.")
                return

            agent_client = client_from_settings(self.settings)
            if agent_client is not None:
                self.fetcher = AgentScopeFetcherThread(agent_client, list(self.active_pins.keys()))
                self.fetcher.data_fetched.connect(self.handle_new_data)
                self.fetcher.error_occurred.connect(self.handle_error)
                self.fetcher.start()
                self.btn_run.setText("⏹ Stop Scope")
                self.btn_run.setStyleSheet("background-color: #c0392b;")
                return

            if getattr(self.settings, "local_only", False):
                self.fetcher = ScopeFetcherThread(
                    "127.0.0.1", "", "", list(self.active_pins.keys()), LocalLinuxCNCAPI()
                )
                self.fetcher.data_fetched.connect(self.handle_new_data)
                self.fetcher.error_occurred.connect(self.handle_error)
                self.fetcher.start()
                self.btn_run.setText("⏹ Stop Scope")
                self.btn_run.setStyleSheet("background-color: #c0392b;")
                return
                
            ip = self.settings.get_setting("SSH", "ip_address", "")
            user = self.settings.get_setting("SSH", "username", "")
            password = self.settings.get_setting("SSH", "password", "")
            
            if not ip or not user:
                QMessageBox.warning(self, "Missing Credentials", "Please set SSH credentials in Settings.")
                return
                
            self.fetcher = ScopeFetcherThread(ip, user, password, list(self.active_pins.keys()))
            self.fetcher.data_fetched.connect(self.handle_new_data)
            self.fetcher.error_occurred.connect(self.handle_error)
            self.fetcher.start()
            
            self.btn_run.setText("⏹ Stop Scope")
            self.btn_run.setStyleSheet("background-color: #c0392b;")

    def handle_new_data(self, vals):
        self.scope.update_data(vals)
        for i, pin_name in enumerate(self.active_pins.keys()):
            if pin_name in vals:
                item = self.table.item(i, 2)
                if item: item.setText(f"{vals[pin_name]:.4f}")

    def handle_error(self, err_msg):
        if self.fetcher and self.fetcher.running:
            self.toggle_run()
        QMessageBox.critical(self, "Scope Error", f"Live Monitor Stopped:\n{err_msg}")
        
    def clear_buffer(self):
        self.scope.clear_data()
        
    def hideEvent(self, event):
        if not self.isWindow() and self.fetcher and self.fetcher.running:
            self.toggle_run()
        super().hideEvent(event)
        
    def closeEvent(self, event):
        """If closed while popped out, dock it back automatically."""
        if self.isWindow():
            self.toggle_pop_out()
            event.ignore()
        else:
            super().closeEvent(event)