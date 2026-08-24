import threading
import subprocess
import os
import base64
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QPushButton, 
                             QLabel, QSplitter, QListWidget, QTableWidget, 
                             QTableWidgetItem, QHeaderView, QCheckBox, QMessageBox, QLineEdit, QInputDialog, QFileDialog)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QTimer

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

class LedIndicator(QLabel):
    clicked_led = pyqtSignal(bool)

    def __init__(self):
        super().__init__()
        self.setFixedSize(20, 20)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.raw_state = False
        self.set_state(False)

    def set_state(self, state, invert=False):
        self.raw_state = state
        actual_state = not state if invert else state
        color = "#2ecc71" if actual_state else "#e74c3c" # Green for True, Red for False
        self.setStyleSheet(f"""
            QLabel {{
                background-color: {color};
                border-radius: 10px;
                border: 2px solid #111;
            }}
        """)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked_led.emit(not self.raw_state)
        super().mousePressEvent(event)

class HalFetcherThread(QThread):
    data_fetched = pyqtSignal(str)
    error_occurred = pyqtSignal(str)

    def __init__(self, ip, user, password):
        super().__init__()
        self.ip = ip
        self.user = user
        self.password = password
        self.running = False
        
        py_code = """import subprocess, time, sys, base64
while True:
    try:
        out = subprocess.check_output("halcmd show pin", shell=True, stderr=subprocess.DEVNULL).decode()
        print(base64.b64encode(out.encode()).decode())
        sys.stdout.flush()
    except Exception:
        pass
    time.sleep(1.0)"""
        b64 = base64.b64encode(py_code.encode()).decode()
        self.cmd = f"python3 -u -c \"import base64; exec(base64.b64decode('{b64}').decode())\""

    def run(self):
        if is_local_ip(self.ip):
            self.running = True
            while self.running:
                proc = subprocess.Popen(self.cmd, shell=True, stdout=subprocess.PIPE, text=True)
                while self.running:
                    line = proc.stdout.readline()
                    if not line: break
                    line = line.strip()
                    if line:
                        try:
                            out = base64.b64decode(line).decode()
                            self.data_fetched.emit(out)
                        except: pass
                proc.terminate()
                for _ in range(20):
                    if not self.running: break
                    self.msleep(100)
            return

        if not HAS_PARAMIKO:
            self.error_occurred.emit("Paramiko is required for live remote monitoring. Please install it.")
            return
            
        self.running = True
        while self.running:
            try:
                ssh = paramiko.SSHClient()
                ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                ssh.connect(self.ip, username=self.user, password=self.password, timeout=10, banner_timeout=15, auth_timeout=15, look_for_keys=not bool(self.password), allow_agent=not bool(self.password))
                
                stdin, stdout, stderr = ssh.exec_command(self.cmd)
                stdout.channel.settimeout(2.0) 
                
                while self.running:
                    try:
                        line = stdout.readline()
                        if not line: break
                        line = line.strip()
                        if line:
                            try:
                                out = base64.b64decode(line).decode()
                                self.data_fetched.emit(out)
                            except: pass
                    except Exception:
                        pass
                ssh.close()
            except Exception as e:
                pass
            
            for _ in range(20):
                if not self.running: break
                self.msleep(100)

    def stop(self):
        self.running = False


class AgentHalFetcherThread(QThread):
    """Poll typed HAL pin listings through an enrolled machine agent."""

    data_fetched = pyqtSignal(str)
    error_occurred = pyqtSignal(str)

    def __init__(self, agent_client):
        super().__init__()
        self.agent_client = agent_client
        self.running = False

    def run(self):
        self.running = True
        while self.running:
            try:
                result = self.agent_client.show_hal_pins()
                output = result.get("output", "")
                error = result.get("error", "")
                if output:
                    self.data_fetched.emit(output)
                if error:
                    self.error_occurred.emit(error)
                    return
            except Exception as error:
                self.error_occurred.emit(f"HAL pin polling failed: {error}")
                return
            self.msleep(1000)

    def stop(self):
        self.running = False

class HalShowTab(QWidget):
    def __init__(self, settings):
        super().__init__()
        self.settings = settings
        self.parent_app = None
        self.fetcher = None
        self.parsed_data = {}
        self.invert_states = {}
        self.watchlist = set()
        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout(self)
        
        self.setStyleSheet("""
            QWidget { background-color: #1e1e1e; color: #ffffff; font-family: 'Segoe UI', Arial, sans-serif; }
            QLabel { font-weight: bold; color: #ecf0f1; font-size: 13px; }
            QPushButton { background-color: #27ae60; color: white; font-weight: bold; border-radius: 4px; padding: 8px; border: none; font-size: 14px;}
            QPushButton:hover { background-color: #2ecc71; }
            QPushButton#btn_snapshot { background-color: #2980b9; }
            QPushButton#btn_snapshot:hover { background-color: #3498db; }
            QLineEdit { background-color: #2d2d30; color: white; border: 1px solid #555555; padding: 6px; border-radius: 4px; font-size: 13px; max-width: 300px; min-width: 150px; }
            QLineEdit:focus { border: 1px solid #3498db; }
            QListWidget, QTableWidget { background-color: #252526; color: #ffffff; border: 1px solid #3e3e42; font-size: 13px; alternate-background-color: #2d2d30; }
            QListWidget::item, QTableWidget::item { color: #ffffff; }
            QListWidget::item:alternate, QTableWidget::item:alternate { background-color: #2d2d30; color: #ffffff; }
            QListWidget::item:selected, QTableWidget::item:selected { background-color: #2980b9; color: #ffffff; }
            QHeaderView::section { background-color: #333337; color: white; padding: 6px; border: 1px solid #3e3e42; font-weight: bold; }
            QTableWidget::item { padding: 4px; }
            QCheckBox { color: white; }
            QCheckBox::indicator { width: 16px; height: 16px; background: #333; border: 1px solid #555; }
            QCheckBox::indicator:checked { background: #3498db; }
        """)
        
        # Top Control Bar
        top_bar = QHBoxLayout()
        self.btn_live = QPushButton("▶ Start Live Monitor")
        self.btn_live.clicked.connect(self.toggle_monitor)
        top_bar.addWidget(self.btn_live)
        
        self.search_bar = QLineEdit()
        self.search_bar.setPlaceholderText("🔍 Search pins in this category...")
        self.search_bar.textChanged.connect(self.filter_table)
        top_bar.addWidget(self.search_bar)
        
        self.btn_snapshot = QPushButton("📸 Snapshot")
        self.btn_snapshot.setObjectName("btn_snapshot")
        self.btn_snapshot.clicked.connect(self.export_snapshot)
        top_bar.addWidget(self.btn_snapshot)
        
        self.btn_pop_out = QPushButton("🗔 Pop Out")
        self.btn_pop_out.clicked.connect(self.toggle_pop_out)
        top_bar.addWidget(self.btn_pop_out)
        
        top_bar.addStretch()
        layout.addLayout(top_bar)

        # Splitter for Categories and Pins
        splitter = QSplitter(Qt.Orientation.Horizontal)
        
        # Categories List
        self.cat_list = QListWidget()
        self.cat_list.itemSelectionChanged.connect(self.update_tables)
        splitter.addWidget(self.cat_list)
        
        # Right Side (Vertical Layout for two tables)
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(0, 0, 0, 0)
        
        # Watchlist section
        watch_header_layout = QHBoxLayout()
        lbl_watch = QLabel("⭐ Watchlist")
        lbl_watch.setStyleSheet("font-size: 16px; color: #f1c40f;")
        watch_header_layout.addWidget(lbl_watch)
        
        self.btn_clear_watch = QPushButton("🗑️ Clear Watchlist")
        self.btn_clear_watch.setStyleSheet("background-color: #c0392b; color: white; font-weight: bold; border-radius: 4px; padding: 4px 8px; border: none;")
        self.btn_clear_watch.clicked.connect(self.clear_watchlist)
        watch_header_layout.addWidget(self.btn_clear_watch)
        watch_header_layout.addStretch()
        right_layout.addLayout(watch_header_layout)
        
        self.watch_table = QTableWidget(0, 6)
        self.watch_table.setHorizontalHeaderLabels(["Watch", "Pin Name", "Type", "Direction", "Value / State", "Invert Logic"])
        self.watch_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.watch_table.setColumnWidth(0, 55)
        self.watch_table.setAlternatingRowColors(True)
        self.watch_table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        self.watch_table.cellDoubleClicked.connect(lambda r, c: self.prompt_set_value(r, c, self.watch_table))
        right_layout.addWidget(self.watch_table, 1)
        
        # Category section
        lbl_cat = QLabel("📂 Category Pins")
        lbl_cat.setStyleSheet("font-size: 16px; color: #3498db; margin-top: 10px;")
        right_layout.addWidget(lbl_cat)
        
        # Pins Table
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["Watch", "Pin Name", "Type", "Direction", "Value / State", "Invert Logic"])
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.setColumnWidth(0, 55)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        self.table.cellDoubleClicked.connect(lambda r, c: self.prompt_set_value(r, c, self.table))
        right_layout.addWidget(self.table, 2)
        
        splitter.addWidget(right_widget)
        
        splitter.setSizes([250, 750])
        layout.addWidget(splitter)
        
    def toggle_pop_out(self):
        if not self.isWindow():
            if self.parent_app and hasattr(self.parent_app, 'tabs'):
                idx = self.parent_app.tabs.indexOf(self)
                if idx != -1:
                    self.parent_app.tabs.removeTab(idx)
            self.setWindowFlags(Qt.WindowType.Window)
            self.setWindowTitle("HAL Monitor - Live Diagnostics")
            self.resize(1000, 600)
            self.show()
            self.btn_pop_out.setText("🗗 Dock to Tabs")
        else:
            self.setWindowFlags(Qt.WindowType.Widget)
            if self.parent_app and hasattr(self.parent_app, 'tabs'):
                self.parent_app.tabs.insertTab(4, self, "🔎 HAL MONITOR")
                self.parent_app.tabs.setCurrentWidget(self)
            self.show()
            self.btn_pop_out.setText("🗔 Pop Out")

    def toggle_monitor(self):
        if self.fetcher and self.fetcher.running:
            self.fetcher.stop()
            self.fetcher.wait()
            self.btn_live.setText("▶ Start Live Monitor")
            self.btn_live.setStyleSheet("background-color: #27ae60;")
        else:
            agent_client = client_from_settings(self.settings)
            if agent_client is not None:
                self.fetcher = AgentHalFetcherThread(agent_client)
                self.fetcher.data_fetched.connect(self.handle_new_data)
                self.fetcher.error_occurred.connect(self.handle_error)
                self.fetcher.start()
                self.btn_live.setText("⏹ Stop Live Monitor")
                self.btn_live.setStyleSheet("background-color: #c0392b;")
                return

            ip = self.settings.get_setting("SSH", "ip_address", "")
            user = self.settings.get_setting("SSH", "username", "")
            password = self.settings.get_setting("SSH", "password", "")
            
            if not ip or not user:
                QMessageBox.warning(self, "Missing Credentials", "Please set SSH credentials in Settings or Mesa Utilities tab first.")
                return
                
            self.fetcher = HalFetcherThread(ip, user, password)
            self.fetcher.data_fetched.connect(self.handle_new_data)
            self.fetcher.error_occurred.connect(self.handle_error)
            self.fetcher.start()
            
            self.btn_live.setText("⏹ Stop Live Monitor")
            self.btn_live.setStyleSheet("background-color: #c0392b;")

    def handle_error(self, err_msg):
        if self.fetcher and self.fetcher.running:
            self.toggle_monitor()
        QMessageBox.critical(self, "Monitor Error", f"Live Monitor Stopped:\n{err_msg}")

    def handle_new_data(self, raw_text):
        categories = {}
        for line in raw_text.splitlines():
            parts = line.split()
            if len(parts) >= 5 and parts[2] in ["IN", "OUT", "I/O"]:
                pin_type, dir_str, val_str, hal_name = parts[1], parts[2], parts[3], parts[4]
                cat = hal_name.split('.')[0]
                
                pin_data = {"name": hal_name, "type": pin_type, "dir": dir_str, "value": val_str}
                if cat not in categories: categories[cat] = []
                categories[cat].append(pin_data)
        
        self.parsed_data = categories
        self.refresh_category_list()
        self.update_tables()

    def refresh_category_list(self):
        current_cats = set([self.cat_list.item(i).text() for i in range(self.cat_list.count())])
        new_cats = set(self.parsed_data.keys())
        
        if current_cats != new_cats:
            current_sel = self.cat_list.currentItem().text() if self.cat_list.currentItem() else None
            
            self.cat_list.blockSignals(True) # Prevent false updates while rebuilding
            self.cat_list.clear()
            
            sorted_cats = sorted(list(new_cats))
            self.cat_list.addItems(sorted_cats)
            
            if current_sel and current_sel in new_cats:
                items = self.cat_list.findItems(current_sel, Qt.MatchFlag.MatchExactly)
                if items: self.cat_list.setCurrentItem(items[0])
                
            self.cat_list.blockSignals(False)
            
            # If the category they were looking at vanished (e.g. un-starred the last item)
            if not current_sel or current_sel not in new_cats:
                self.update_tables()

    def update_tables(self):
        self.update_watch_table()
        self.update_main_table()

    def update_watch_table(self):
        watched_pins = []
        for cat, pins in self.parsed_data.items():
            for p in pins:
                if p['name'] in self.watchlist:
                    watched_pins.append(p)
                    
        watched_pins.sort(key=lambda x: x['name'])
        
        needs_rebuild = getattr(self, '_last_watch_count', -1) != len(watched_pins)
        self._populate_table(self.watch_table, watched_pins, needs_rebuild)
        self._last_watch_count = len(watched_pins)

    def update_main_table(self):
        category = self.cat_list.currentItem().text() if self.cat_list.currentItem() else None
        if not category: 
            self.table.setRowCount(0)
            self._last_category = None
            return
            
        pins = self.parsed_data.get(category, [])
        category_changed = getattr(self, '_last_category', None) != category
        
        self._populate_table(self.table, pins, category_changed)
        self._last_category = category
        self.filter_table(self.search_bar.text())

    def _populate_table(self, table_widget, pins, needs_rebuild):
        if needs_rebuild or table_widget.rowCount() != len(pins) or (pins and table_widget.item(0, 1) and table_widget.item(0, 1).text() != pins[0]['name']):
            table_widget.setRowCount(len(pins))
            for i, p in enumerate(pins):
                btn_watch = QPushButton("⭐" if p['name'] in self.watchlist else "☆")
                btn_watch.setStyleSheet("background: transparent; color: #f1c40f; font-size: 18px; border: none;")
                btn_watch.setCursor(Qt.CursorShape.PointingHandCursor)
                
                # Defuse execution with a timer to prevent destroying the button mid-click
                btn_watch.clicked.connect(lambda checked, name=p['name']: QTimer.singleShot(0, lambda: self.toggle_watch(name)))
                table_widget.setCellWidget(i, 0, btn_watch)
                
                table_widget.setItem(i, 1, QTableWidgetItem(p['name']))
                table_widget.setItem(i, 2, QTableWidgetItem(p['type']))
                table_widget.setItem(i, 3, QTableWidgetItem(p['dir']))
                
                # Setup Invert Checkbox
                chk_container = QWidget()
                chk_layout = QHBoxLayout(chk_container)
                chk = QCheckBox("Invert")
                chk.setChecked(self.invert_states.get(p['name'], False))
                chk.toggled.connect(lambda checked, name=p['name']: self.set_invert(name, checked))
                chk_layout.addWidget(chk)
                chk_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
                chk_layout.setContentsMargins(0,0,0,0)
                table_widget.setCellWidget(i, 5, chk_container)
                
                # Setup Visual State
                if p['type'] == 'bit':
                    led_container = QWidget()
                    led_layout = QHBoxLayout(led_container)
                    led = LedIndicator()
                    secure_mode = client_from_settings(self.settings) is not None
                    if secure_mode:
                        led.setToolTip("Secure mode does not permit arbitrary HAL pin writes")
                    else:
                        led.setCursor(Qt.CursorShape.PointingHandCursor)
                        led.setToolTip("Click to manually force state (halcmd setp)")
                    led.clicked_led.connect(lambda new_state, name=p['name']: self.toggle_pin_state(name, new_state))
                    
                    led_layout.addWidget(led)
                    led_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
                    led_layout.setContentsMargins(0,0,0,0)
                    table_widget.setCellWidget(i, 4, led_container)
                else:
                    table_widget.removeCellWidget(i, 4)
                    item = QTableWidgetItem()
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                    if p['type'] in ['float', 's32', 'u32']:
                        item.setToolTip(
                            "Secure mode does not permit arbitrary HAL pin writes"
                            if client_from_settings(self.settings) is not None
                            else "Double-click to set value (halcmd setp)"
                        )
                    table_widget.setItem(i, 4, item)
                    
        # Update live values smoothly without rebuilding the table
        for i, p in enumerate(pins):
            btn_watch = table_widget.cellWidget(i, 0)
            if btn_watch:
                btn_watch.setText("⭐" if p['name'] in self.watchlist else "☆")
                
            is_inverted = self.invert_states.get(p['name'], False)
            if p['type'] == 'bit':
                container = table_widget.cellWidget(i, 4)
                if container:
                    led = container.findChild(LedIndicator)
                    new_state = (p['value'].upper() == 'TRUE')
                    if led and led.raw_state != new_state: 
                        led.set_state(new_state, is_inverted)
            else:
                item = table_widget.item(i, 4)
                if item and item.text() != p['value']: 
                    item.setText(p['value'])

    def toggle_watch(self, name):
        if name in self.watchlist:
            self.watchlist.remove(name)
        else:
            self.watchlist.add(name)
            
        self.update_tables()

    def clear_watchlist(self):
        self.watchlist.clear()
        self.update_tables()

    def filter_table(self, text):
        search_text = text.lower()
        for r in range(self.table.rowCount()):
            item = self.table.item(r, 1)
            if item:
                pin_name = item.text().lower()
                self.table.setRowHidden(r, search_text not in pin_name)

    def set_invert(self, name, checked):
        self.invert_states[name] = checked
        self.update_tables()

    def export_snapshot(self):
        category = self.cat_list.currentItem().text() if self.cat_list.currentItem() else "All"
        
        exports_dir = os.path.join(os.getcwd(), "exports")
        if not os.path.exists(exports_dir):
            os.makedirs(exports_dir, exist_ok=True)
            
        last_dir = self.settings.get_setting("PATHS", "last_snapshot_dir", exports_dir)
        default_name = f"hal_snapshot_{category}.txt"
        
        path, _ = QFileDialog.getSaveFileName(self, "Export HAL Snapshot", os.path.join(last_dir, default_name), "Text Files (*.txt);;All Files (*)")
        if not path:
            return
            
        self.settings.save_setting("PATHS", "last_snapshot_dir", os.path.dirname(path))
        
        try:
            with open(path, 'w') as f:
                f.write(f"--- HAL Snapshot: {category} ---\n\n")
                f.write(f"{'Pin Name'.ljust(45)} | {'Type'.ljust(10)} | {'Direction'.ljust(10)} | Value\n")
                f.write("-" * 85 + "\n")
                
                for r in range(self.table.rowCount()):
                    if not self.table.isRowHidden(r):
                        name = self.table.item(r, 1).text() if self.table.item(r, 1) else ""
                        ptype = self.table.item(r, 2).text() if self.table.item(r, 2) else ""
                        pdir = self.table.item(r, 3).text() if self.table.item(r, 3) else ""
                        
                        val = ""
                        if ptype == 'bit':
                            container = self.table.cellWidget(r, 4)
                            if container:
                                led = container.findChild(LedIndicator)
                                if led: val = "TRUE" if led.raw_state else "FALSE"
                        else:
                            val = self.table.item(r, 4).text() if self.table.item(r, 4) else ""
                            
                        f.write(f"{name.ljust(45)} | {ptype.ljust(10)} | {pdir.ljust(10)} | {val}\n")
                        
            QMessageBox.information(self, "Success", f"Snapshot exported successfully to:\n{path}")
        except Exception as e:
            QMessageBox.critical(self, "Export Failed", f"Failed to write snapshot:\n{e}")

    def prompt_set_value(self, row, col, table_widget):
        if col != 4: return

        if client_from_settings(self.settings) is not None:
            QMessageBox.information(
                self,
                "HAL Write Not Available",
                "Secure mode does not permit arbitrary HAL pin writes. Use a named, approved tuning operation instead.",
            )
            return
        
        name_item = table_widget.item(row, 1)
        type_item = table_widget.item(row, 2)
        val_item = table_widget.item(row, 4)
        
        if not name_item or not type_item or not val_item: return
        
        pin_name, pin_type, current_val = name_item.text(), type_item.text(), val_item.text()
        
        if pin_type in ["float", "s32", "u32"]:
            new_val, ok = QInputDialog.getText(self, f"Set {pin_name}", f"Enter new value ({pin_type}):", text=current_val)
            if ok and new_val.strip() != "":
                self.send_setp_cmd(pin_name, new_val.strip())

    def send_setp_cmd(self, name, value_str):
        if client_from_settings(self.settings) is not None:
            QMessageBox.information(
                self,
                "HAL Write Not Available",
                "Secure mode does not permit arbitrary HAL pin writes. Use a named, approved tuning operation instead.",
            )
            return

        ip = self.settings.get_setting("SSH", "ip_address", "")
        user = self.settings.get_setting("SSH", "username", "")
        password = self.settings.get_setting("SSH", "password", "")
        
        if not ip or not user: return
            
        cmd = f"halcmd setp {name} {value_str}"
        
        def send_cmd():
            if is_local_ip(ip):
                try:
                    subprocess.run(cmd, shell=True, capture_output=True)
                except: pass
                return
            if HAS_PARAMIKO:
                try:
                    ssh = paramiko.SSHClient()
                    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                    ssh.connect(ip, username=user, password=password, timeout=10, banner_timeout=15, auth_timeout=15, look_for_keys=not bool(password), allow_agent=not bool(password))
                    ssh.exec_command(cmd)
                    ssh.close()
                except:
                    pass
            else:
                try:
                    subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=3", f"{user}@{ip}", cmd], 
                                   capture_output=True, timeout=5)
                except:
                    pass
                    
        threading.Thread(target=send_cmd, daemon=True).start()

    def toggle_pin_state(self, name, new_state):
        state_str = "True" if new_state else "False"
        self.send_setp_cmd(name, state_str)

    def hideEvent(self, event):
        """Auto-Stop the monitor if the user switches to a different tab."""
        if not self.isWindow() and self.fetcher and self.fetcher.running:
            self.toggle_monitor()
        super().hideEvent(event)
        
    def closeEvent(self, event):
        """If closed while popped out, dock it back automatically."""
        if self.isWindow():
            self.toggle_pop_out()
            event.ignore()
        else:
            super().closeEvent(event)