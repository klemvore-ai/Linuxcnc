import tempfile
import os
import subprocess
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QPushButton, 
                             QTableWidget, QTableWidgetItem, QHeaderView, QMessageBox, QInputDialog)
from PyQt6.QtCore import Qt
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

class ToolTableTab(QWidget):
    def __init__(self, settings):
        super().__init__()
        self.settings = settings
        self.init_ui()

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
        self.table = QTableWidget(0, 5)
        
        u_str = "mm" if "mm" in self.settings.get_setting('MACHINE_CONFIG', 'units', 'Metric (mm)') else "in"
        self.table.setHorizontalHeaderLabels(["Tool (T)", "Pocket (P)", f"Z Offset ({u_str})", f"Diameter (D) ({u_str})", "Description / Comment"])
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
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
        self.table.setItem(row, 0, QTableWidgetItem(str(row + 1)))
        self.table.setItem(row, 1, QTableWidgetItem(str(row + 1)))
        self.table.setItem(row, 2, QTableWidgetItem("0.0"))
        self.table.setItem(row, 3, QTableWidgetItem("0.0"))
        self.table.setItem(row, 4, QTableWidgetItem("New Tool"))

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

        for col in [2, 3]: # Z Offset, Diameter
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
        mach_name = self.settings.get_setting('MACHINE_CONFIG', 'name', 'CNCFusion')

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
            elif HAS_PARAMIKO:
                ssh = paramiko.SSHClient()
                ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                ssh.connect(ip, username=user, password=password, timeout=10, banner_timeout=15, auth_timeout=15, look_for_keys=not bool(password), allow_agent=not bool(password))
                stdin, stdout, stderr = ssh.exec_command(cmd)
                out = stdout.read().decode()
                ssh.close()
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
            values = {"T": "0", "P": "0", "Z": "0", "D": "0"}
            for token in data_part.split():
                token = token.upper()
                if token and token[0] in values:
                    values[token[0]] = token[1:]
            row = self.table.rowCount()
            self.table.insertRow(row)
            self.table.setItem(row, 0, QTableWidgetItem(values["T"]))
            self.table.setItem(row, 1, QTableWidgetItem(values["P"]))
            self.table.setItem(row, 2, QTableWidgetItem(values["Z"]))
            self.table.setItem(row, 3, QTableWidgetItem(values["D"]))
            self.table.setItem(row, 4, QTableWidgetItem(comment_part))
        self._loading = False
        self.clear_unsaved()

    def save_table(self):
        ip = self.settings.get_setting("SSH", "ip_address", "")
        user = self.settings.get_setting("SSH", "username", "")
        password = self.settings.get_setting("SSH", "password", "")
        agent_client = client_from_settings(self.settings)
        mach_name = self.settings.get_setting('MACHINE_CONFIG', 'name', 'CNCFusion')
        
        if agent_client is not None:
            remote_path = f"{mach_name}/tool.tbl"
        else:
            remote_path = ""
        
        if agent_client is None and (not ip or not user):
            QMessageBox.warning(self, "Missing Credentials", "Please set SSH credentials in Settings.")
            return

        default_path = self.settings.get_setting("PATHS", "last_tool_table_path", f"~/linuxcnc/configs/{mach_name}/tool.tbl")
        
        if agent_client is None:
            remote_path, ok = QInputDialog.getText(self, "Save Tool Table", "Enter remote tool.tbl path:", text=default_path)
            if not ok or not remote_path:
                return
            
        self.settings.save_setting("PATHS", "last_tool_table_path", remote_path)
        
        lines = ["; Auto-generated Tool Table from CNC Fusion Pro"]
        for r in range(self.table.rowCount()):
            t = self.table.item(r, 0).text() if self.table.item(r, 0) else str(r+1)
            p = self.table.item(r, 1).text() if self.table.item(r, 1) else str(r+1)
            z = self.table.item(r, 2).text() if self.table.item(r, 2) else "0.0"
            d = self.table.item(r, 3).text() if self.table.item(r, 3) else "0.0"
            c = self.table.item(r, 4).text() if self.table.item(r, 4) else ""
            
            line = f"T{t} P{p} X0 Y0 Z{z} A0 B0 C0 U0 V0 W0 D{d} I0 J0 Q0"
            if c:
                line += f" ; {c}"
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
            if is_local_ip(ip):
                with open(os.path.expanduser(remote_path), 'w') as f:
                    f.write(content.replace("\\n", "\n"))
                self._reload_linuxcnc_tool_table(ip, user, password)
                self.clear_unsaved()
                QMessageBox.information(self, "Success", f"Tool table successfully saved to {remote_path}")
            elif HAS_PARAMIKO:
                ssh = paramiko.SSHClient()
                ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                ssh.connect(ip, username=user, password=password, timeout=10, banner_timeout=15, auth_timeout=15, look_for_keys=not bool(password), allow_agent=not bool(password))
                sftp = ssh.open_sftp()
                
                sftp_path = remote_path.replace("~/", f"/home/{user}/", 1) if remote_path.startswith("~/") else remote_path
                
                with sftp.file(sftp_path, 'w') as f:
                    f.write(content.replace("\\n", "\n").encode('utf-8'))
                    
                sftp.close()
                ssh.close()
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
        import threading
        cmd = "python3 -c \"import linuxcnc; c=linuxcnc.command(); c.load_tool_table()\""
        def run():
            try:
                if is_local_ip(ip):
                    subprocess.run(cmd, shell=True, capture_output=True)
                elif HAS_PARAMIKO:
                    ssh = paramiko.SSHClient()
                    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                    ssh.connect(ip, username=user, password=password, timeout=5, look_for_keys=not bool(password), allow_agent=not bool(password))
                    ssh.exec_command(cmd)
                    ssh.close()
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

        if agent_client is None and (not ip or not user):
            QMessageBox.warning(self, "Missing Credentials", "Please set SSH credentials in Settings.")
            return
            
        cmd = "halcmd getp iocontrol.0.tool-number"
        
        try:
            if agent_client is None:
                if is_local_ip(ip):
                    res = subprocess.run(cmd, shell=True, capture_output=True, text=True)
                    out = res.stdout.strip()
                elif HAS_PARAMIKO:
                    ssh = paramiko.SSHClient()
                    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                    ssh.connect(ip, username=user, password=password, timeout=10, banner_timeout=15, auth_timeout=15, look_for_keys=not bool(password), allow_agent=not bool(password))
                    stdin, stdout, stderr = ssh.exec_command(cmd)
                    out = stdout.read().decode().strip()
                    ssh.close()
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