import tempfile
import os
import subprocess
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QPushButton, 
                             QTableWidget, QTableWidgetItem, QHeaderView, QMessageBox, QInputDialog)
from PyQt6.QtCore import Qt, QTimer
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

TOOL_COLUMNS = [
    ("T", "Tool #"),
    ("P", "Pocket #"),
    ("X", "X Offset"),
    ("Y", "Y Offset"),
    ("Z", "Z Offset"),
    ("A", "A Offset"),
    ("B", "B Offset"),
    ("C", "C Offset"),
    ("U", "U Offset"),
    ("V", "V Offset"),
    ("W", "W Offset"),
    ("D", "Diameter"),
    ("I", "Front Angle"),
    ("J", "Back Angle"),
    ("Q", "Orientation"),
]
COMMENT_COLUMN = len(TOOL_COLUMNS)
FIELD_TO_COLUMN = {field: index for index, (field, _) in enumerate(TOOL_COLUMNS)}

class ToolTableTab(QWidget):
    def __init__(self, settings):
        super().__init__()
        self.settings = settings
        self.init_ui()
        QTimer.singleShot(0, self.auto_load_table)

    def init_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(15, 15, 15, 15)

        self.setStyleSheet("""
            QWidget { background-color: #1e1e1e; color: #ffffff; font-family: 'Segoe UI', Arial, sans-serif; }
            QPushButton { background-color: #2980b9; color: white; font-weight: bold; border-radius: 4px; padding: 8px 12px; border: none; font-size: 13px; }
            QPushButton:hover { background-color: #3498db; }
            QPushButton:pressed { background-color: #1f618d; }
            QTableWidget { background-color: #252526; color: #ffffff; gridline-color: #3e3e42; border: 1px solid #3e3e42; font-size: 13px; alternate-background-color: #2d2d30; }
            QTableWidget::item:selected { background-color: #2980b9; color: #ffffff; }
            QHeaderView::section { background-color: #333337; color: white; padding: 6px; border: 1px solid #3e3e42; font-weight: bold; font-size: 13px; }
        """)

        # Top Bar
        top_bar = QHBoxLayout()
        btn_load = QPushButton("📥 Load from LinuxCNC")
        btn_load.setStyleSheet("background-color: #27ae60;")
        btn_load.clicked.connect(self.load_table)
        top_bar.addWidget(btn_load)

        self.btn_save = QPushButton("📤 Save to LinuxCNC")
        self.btn_save.setStyleSheet("background-color: #8e44ad;")
        self.btn_save.clicked.connect(self.save_table)
        top_bar.addWidget(self.btn_save)
        
        btn_active = QPushButton("🎯 Highlight Active")
        btn_active.setStyleSheet("background-color: #f39c12; color: black;")
        btn_active.clicked.connect(self.highlight_active_tool)
        top_bar.addWidget(btn_active)
        
        top_bar.addStretch()
        
        btn_add = QPushButton("➕ Add Tool")
        btn_add.clicked.connect(self.add_tool)
        top_bar.addWidget(btn_add)

        btn_rem = QPushButton("➖ Remove Tool")
        btn_rem.clicked.connect(self.remove_tool)
        btn_rem.setStyleSheet("background-color: #c0392b;")
        top_bar.addWidget(btn_rem)

        btn_convert = QPushButton("🔄 Convert (25.4)")
        btn_convert.setStyleSheet("background-color: #34495e;")
        btn_convert.clicked.connect(self.convert_selected_tool)
        top_bar.addWidget(btn_convert)

        layout.addLayout(top_bar)

        # Table
        self.table = QTableWidget(0, len(TOOL_COLUMNS) + 1)
        
        u_str = "mm" if "mm" in self.settings.get_setting('MACHINE_CONFIG', 'units', 'Metric (mm)') else "in"
        headers = []
        for field, label in TOOL_COLUMNS:
            if field in {"X", "Y", "Z", "A", "B", "C", "U", "V", "W", "D"}:
                headers.append(f"{label} ({field}) [{u_str}]")
            else:
                headers.append(f"{label} ({field})")
        headers.append("Description / Comment")
        self.table.setHorizontalHeaderLabels(headers)
        self.table.horizontalHeader().setMinimumSectionSize(90)
        for column, header in enumerate(headers):
            width = max(96, min(180, len(header) * 9 + 24))
            self.table.setColumnWidth(column, width)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.table.horizontalHeader().setSectionResizeMode(COMMENT_COLUMN, QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().setDefaultSectionSize(28)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        layout.addWidget(self.table)

        self.table.itemChanged.connect(self.mark_unsaved)

    def mark_unsaved(self, *args):
        if getattr(self, '_loading', False): return
        self.has_unsaved_changes = True
        self.btn_save.setText("📤 Save to LinuxCNC (Unsaved*)")
        self.btn_save.setStyleSheet("background-color: #e74c3c; border: 2px solid #c0392b;")

    def clear_unsaved(self):
        self.has_unsaved_changes = False
        self.btn_save.setText("📤 Save to LinuxCNC")
        self.btn_save.setStyleSheet("background-color: #8e44ad;")

    def add_tool(self):
        row = self.table.rowCount()
        self.table.insertRow(row)
        for col, (field, _) in enumerate(TOOL_COLUMNS):
            default_value = str(row + 1) if field in {"T", "P"} else "0"
            self.table.setItem(row, col, QTableWidgetItem(default_value))
        self.table.setItem(row, COMMENT_COLUMN, QTableWidgetItem("New Tool"))

    def remove_tool(self):
        curr = self.table.currentRow()
        if curr >= 0:
            self.table.removeRow(curr)
            self.mark_unsaved()

    def convert_selected_tool(self):
        curr = self.table.currentRow()
        if curr < 0:
            QMessageBox.warning(self, "Selection Required", "Please select a tool row to convert.")
            return

        msg = QMessageBox(self)
        msg.setWindowTitle("Magic Number: 25.4")
        msg.setText("Convert the selected tool's Z Offset and Diameter?")
        btn_to_mm = msg.addButton("Inch ➔ mm (* 25.4)", QMessageBox.ButtonRole.ActionRole)
        btn_to_in = msg.addButton("mm ➔ Inch (/ 25.4)", QMessageBox.ButtonRole.ActionRole)
        msg.addButton(QMessageBox.StandardButton.Cancel)

        msg.exec()

        if msg.clickedButton() == btn_to_mm:
            factor = 25.4
        elif msg.clickedButton() == btn_to_in:
            factor = 1.0 / 25.4
        else:
            return

        for col in [FIELD_TO_COLUMN[field] for field in ("X", "Y", "Z", "A", "B", "C", "U", "V", "W", "D")]:
            item = self.table.item(curr, col)
            if item:
                try:
                    val = float(item.text())
                    new_val = val * factor
                    item.setText(f"{new_val:.4f}")
                except ValueError:
                    pass

    def load_table(self):
        ip = self.settings.get_setting("SSH", "ip_address", "")
        user = self.settings.get_setting("SSH", "username", "")
        password = self.settings.get_setting("SSH", "password", "")
        agent_client = client_from_settings(self.settings)
        mach_name = self.settings.get_setting('MACHINE_CONFIG', 'name', 'LinuxCNC')

        if agent_client is not None:
            try:
                result = agent_client.read_tool_table(f"{mach_name}/tool.tbl")
                out = result.get("content", "")
                if result.get("error"):
                    raise RuntimeError(result["error"])
                self._populate_table(out)
                return
            except Exception as error:
                QMessageBox.critical(self, "Load Failed", f"Secure tool-table load failed:\n{error}")
                return

        if getattr(self.settings, "local_only", False):
            local_path = os.path.expanduser(
                self.settings.get_setting(
                    "PATHS",
                    "last_tool_table_path",
                    f"~/linuxcnc/configs/{mach_name}/tool.tbl",
                )
            )
            try:
                with open(local_path, "r", encoding="utf-8") as tool_table:
                    self._populate_table(tool_table.read())
                self.settings.save_setting("PATHS", "last_tool_table_path", local_path)
            except Exception as error:
                QMessageBox.critical(self, "Load Failed", f"Could not read local tool table {local_path}:\n{error}")
            return
        
        if not ip or not user:
            QMessageBox.warning(self, "Missing Credentials", "Please set SSH credentials in Settings.")
            return

        default_path = self.settings.get_setting("PATHS", "last_tool_table_path", f"~/linuxcnc/configs/{mach_name}/tool.tbl")
        
        remote_path, ok = QInputDialog.getText(self, "Load Tool Table", "Enter remote tool.tbl path:", text=default_path)
        if not ok or not remote_path:
            return
            
        self.settings.save_setting("PATHS", "last_tool_table_path", remote_path)
        
        cmd = f"cat {remote_path}"
        try:
            out = ""
            if is_local_ip(ip):
                res = subprocess.run(cmd, shell=True, capture_output=True, text=True)
                out = res.stdout
            else:
                res = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", f"{user}@{ip}", cmd], capture_output=True, text=True, timeout=10)
                out = res.stdout

            if not out:
                QMessageBox.warning(self, "Empty/Not Found", f"No tool table found or file is empty at {remote_path}")
                return

            self._populate_table(out)
            QMessageBox.information(self, "Success", "Tool table loaded successfully!")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to load tool table:\n{e}")

    def auto_load_table(self):
        """Load the tool table at startup when the secure agent is configured."""
        agent_client = client_from_settings(self.settings)
        if agent_client is None:
            return
        mach_name = self.settings.get_setting('MACHINE_CONFIG', 'name', 'LinuxCNC')
        try:
            result = agent_client.read_tool_table(f"{mach_name}/tool.tbl")
            out = result.get("content", "")
            if out and not result.get("error"):
                self._populate_table(out)
        except Exception:
            return

    def _populate_table(self, content):
        """Populate the tool table grid from standard LinuxCNC tool-table text."""
        if not content:
            raise ValueError("Tool table is empty.")
        self._loading = True
        self.table.setRowCount(0)
        for line in content.splitlines():
            line = line.strip()
            if not line or line.startswith(';'):
                continue
            parts = line.split(';', 1)
            data_part = parts[0].strip()
            comment_part = parts[1].strip() if len(parts) > 1 else ""
            values = {field: "0" for field, _ in TOOL_COLUMNS}
            for token in data_part.split():
                token = token.upper()
                if token and token[0] in values:
                    values[token[0]] = token[1:]
            row = self.table.rowCount()
            self.table.insertRow(row)
            for col, (field, _) in enumerate(TOOL_COLUMNS):
                self.table.setItem(row, col, QTableWidgetItem(values[field]))
            self.table.setItem(row, COMMENT_COLUMN, QTableWidgetItem(comment_part))
        self._loading = False
        self.clear_unsaved()

    def save_table(self):
        ip = self.settings.get_setting("SSH", "ip_address", "")
        user = self.settings.get_setting("SSH", "username", "")
        password = self.settings.get_setting("SSH", "password", "")
        agent_client = client_from_settings(self.settings)
        mach_name = self.settings.get_setting('MACHINE_CONFIG', 'name', 'LinuxCNC')
        local_only = getattr(self.settings, "local_only", False)
        
        if agent_client is not None:
            remote_path = f"{mach_name}/tool.tbl"
        else:
            remote_path = ""
        
        if agent_client is None and not local_only and (not ip or not user):
            QMessageBox.warning(self, "Missing Credentials", "Please set SSH credentials in Settings.")
            return

        default_path = self.settings.get_setting("PATHS", "last_tool_table_path", f"~/linuxcnc/configs/{mach_name}/tool.tbl")

        if local_only:
            remote_path = os.path.expanduser(default_path)
        elif agent_client is None:
            remote_path, ok = QInputDialog.getText(self, "Save Tool Table", "Enter remote tool.tbl path:", text=default_path)
            if not ok or not remote_path:
                return
            
        self.settings.save_setting("PATHS", "last_tool_table_path", remote_path)
        
        lines = ["; Auto-generated Tool Table from LinuxCNC Pro"]
        for r in range(self.table.rowCount()):
            parts = []
            for col, (field, _) in enumerate(TOOL_COLUMNS):
                default_value = str(r + 1) if field in {"T", "P"} else "0"
                item = self.table.item(r, col)
                value = item.text().strip() if item and item.text().strip() else default_value
                parts.append(f"{field}{value}")
            c = self.table.item(r, COMMENT_COLUMN).text() if self.table.item(r, COMMENT_COLUMN) else ""
            line = " ".join(parts)
            if c.strip():
                line += f" ; {c.strip()}"
            lines.append(line)
            
        content = "\\n".join(lines) + "\\n"
        
        try:
            if agent_client is not None:
                agent_client.upload_content(
                    "config",
                    f"{mach_name}/tool.tbl",
                    content.replace("\\n", "\n").encode("utf-8"),
                )
                agent_client.reload_tool_table()
                self.clear_unsaved()
                QMessageBox.information(self, "Success", "Tool table securely staged on the machine.")
                return
            if local_only:
                with open(remote_path, "w", encoding="utf-8", newline="\n") as tool_table:
                    tool_table.write(content.replace("\\n", "\n"))
                self._reload_linuxcnc_tool_table(ip, user, password)
                self.clear_unsaved()
                QMessageBox.information(self, "Success", f"Tool table saved locally to {remote_path}")
                return
            if is_local_ip(ip):
                with open(os.path.expanduser(remote_path), 'w') as f:
                    f.write(content.replace("\\n", "\n"))
                self._reload_linuxcnc_tool_table(ip, user, password)
                self.clear_unsaved()
                QMessageBox.information(self, "Success", f"Tool table successfully saved to {remote_path}")
            else:
                with tempfile.NamedTemporaryFile(delete=False, mode='w', newline='\n') as f:
                    f.write(content.replace("\\n", "\n"))
                    temp_path = f.name
                
                res = subprocess.run(["scp", "-o", "BatchMode=yes", temp_path, f"{user}@{ip}:{remote_path}"], capture_output=True, text=True)
                os.remove(temp_path)
                
                if res.returncode == 0:
                    self._reload_linuxcnc_tool_table(ip, user, password)
                    self.clear_unsaved()
                    QMessageBox.information(self, "Success", f"Tool table successfully saved to {remote_path}")
                else:
                    QMessageBox.critical(self, "Error", f"Failed to save tool table:\n{res.stderr}")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to save tool table:\n{e}")

    def _reload_linuxcnc_tool_table(self, ip, user, password):
        if getattr(self.settings, "local_only", False):
            try:
                LocalLinuxCNCAPI().command().load_tool_table()
            except Exception as error:
                QMessageBox.warning(self, "Tool Table Reload Failed", str(error))
            return
        import threading
        cmd = "python3 -c \"import linuxcnc; c=linuxcnc.command(); c.load_tool_table()\""
        def run():
            try:
                if is_local_ip(ip):
                    subprocess.run(cmd, shell=True, capture_output=True)
                else:
                    subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", f"{user}@{ip}", cmd], capture_output=True)
            except Exception as e:
                print(f"[tool_table_tab.py] Failed to remote reload tool table: {e}")
        threading.Thread(target=run, daemon=True).start()

    def highlight_active_tool(self):
        agent_client = client_from_settings(self.settings)
        if agent_client is not None:
            try:
                result = agent_client.read_hal_values(["iocontrol.0.tool-number"])
                if result.get("error"):
                    raise RuntimeError(result["error"])
                out = result.get("values", {}).get("iocontrol.0.tool-number", "")
            except Exception as error:
                QMessageBox.warning(self, "Warning", f"Could not determine active tool:\n{error}")
                return
        else:
            out = ""

        ip = self.settings.get_setting("SSH", "ip_address", "")
        user = self.settings.get_setting("SSH", "username", "")
        password = self.settings.get_setting("SSH", "password", "")

        local_only = getattr(self.settings, "local_only", False)
        if agent_client is None and not local_only and (not ip or not user):
            QMessageBox.warning(self, "Missing Credentials", "Please set SSH credentials in Settings.")
            return
            
        cmd = "halcmd getp iocontrol.0.tool-number"
        
        try:
            if agent_client is None:
                if local_only:
                    out = str(self.settings.get_setting("MACHINE_CONFIG", "active_tool", "0"))
                    try:
                        out = str(LocalLinuxCNCAPI().get_hal_value("iocontrol.0.tool-number"))
                    except Exception:
                        status = LocalLinuxCNCAPI().status()
                        status.poll()
                        out = str(status.tool_in_spindle)
                elif is_local_ip(ip):
                    res = subprocess.run(cmd, shell=True, capture_output=True, text=True)
                    out = res.stdout.strip()
                else:
                    res = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", f"{user}@{ip}", cmd], capture_output=True, text=True, timeout=10)
                    out = res.stdout.strip()

            try:
                active_tool = int(float(out))
            except ValueError:
                QMessageBox.warning(self, "Warning", f"Could not determine active tool. Is LinuxCNC running?\nOutput: {out}")
                return

            found = False
            for r in range(self.table.rowCount()):
                tool_item = self.table.item(r, 0) # Column 0 is Tool (T)
                if tool_item and tool_item.text().isdigit() and int(tool_item.text()) == active_tool:
                    self.table.selectRow(r)
                    found = True
                    break
            
            if not found:
                QMessageBox.information(self, "Not Found", f"Tool {active_tool} is currently active in LinuxCNC, but it is not listed in this table view.")

        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to fetch active tool:\n{e}")

    def hideEvent(self, event):
        if getattr(self, 'has_unsaved_changes', False):
            reply = QMessageBox.question(self, "Unsaved Tool Table Changes", "You have unsaved changes in the Tool Table.\n\nWould you like to save them to LinuxCNC before leaving this tab?", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if reply == QMessageBox.StandardButton.Yes:
                self.save_table()
        super().hideEvent(event)