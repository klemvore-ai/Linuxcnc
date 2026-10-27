import subprocess
import os
import shutil
import json
import re
import platform
import base64
from core.credentials import CredentialStore, CredentialStoreError
from core.machine_agent import client_from_settings
from core.linuxcnc_api import LocalLinuxCNCAPI
from tabs.hal_show_tab import format_hal_pin_listing
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
                             QGroupBox, QLineEdit, QPushButton, QTextEdit,
                             QLabel, QMessageBox, QApplication, QFileDialog, QInputDialog)
from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtGui import QDesktopServices

LOCAL_IPS = ['localhost', '127.0.0.1']
try:
    _out = subprocess.check_output("hostname -I", shell=True, text=True, stderr=subprocess.DEVNULL)
    LOCAL_IPS.extend(_out.split())
except:
    pass

def is_local_ip(ip):
    return ip in LOCAL_IPS

class MesaUtilTab(QWidget):
    def __init__(self, parent_library_tab):
        super().__init__()
        self.library_tab = parent_library_tab  # So we can push data to the library
        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(15, 15, 15, 15)

        # Apply Dark Mode "Pro" Styling
        self.setStyleSheet("""
            QWidget { background-color: #1e1e1e; color: #ffffff; font-family: 'Segoe UI', Arial, sans-serif; }
            QLabel { font-weight: bold; font-size: 13px; color: #ecf0f1; }
            QLineEdit { background-color: #2d2d30; color: white; border: 1px solid #555555; padding: 6px; border-radius: 4px; font-size: 13px; max-width: 250px; min-width: 150px; }
            QLineEdit:focus { border: 1px solid #3498db; }
            QPushButton { background-color: #2980b9; color: white; font-weight: bold; border-radius: 4px; padding: 8px 12px; border: none; font-size: 13px; }
            QPushButton:hover { background-color: #3498db; }
            QPushButton:pressed { background-color: #1f618d; }
            QPushButton:disabled { background-color: #555555; color: #888888; }
            QGroupBox { font-weight: bold; border: 1px solid #444444; margin-top: 15px; padding-top: 15px; border-radius: 4px; color: #3498db; }
            QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 5px; }
            QTextEdit { background-color: #151515; color: #00ff00; font-family: 'Consolas', 'Courier New', monospace; border: 1px solid #444444; border-radius: 4px; padding: 5px; }
        """)

        # Connection Header
        conn_group = QGroupBox("Mesa Connection Settings")
        conn_layout = QGridLayout()
        conn_layout.setSpacing(10)
        
        self.settings = self.library_tab.parent_app.settings
        local_only = getattr(self.settings, "local_only", False)
        
        self.ip_input = QLineEdit(self.settings.get_setting("SSH", "ip_address", "192.168.1.125"))
        self.user_input = QLineEdit(self.settings.get_setting("SSH", "username", "linuxcnc"))
        self.pass_input = QLineEdit(self.settings.get_setting("SSH", "password", ""))
        self.ip_input.setReadOnly(local_only)
        self.user_input.setReadOnly(local_only)
        self.pass_input.setReadOnly(local_only)
        self.pass_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.pass_input.setPlaceholderText("Stored in OS credential manager")
        
        self.board_input = QLineEdit(self.settings.get_setting("MESA", "board_model", "7i76eu"))
        self.mesa_ip_input = QLineEdit(self.settings.get_setting("MESA", "ip_address", "10.10.10.10"))
        
        conn_layout.addWidget(QLabel("Local Controller:" if local_only else "Linux Machine IP:"), 0, 0)
        conn_layout.addWidget(self.ip_input, 0, 1)
        if local_only:
            conn_layout.addWidget(QLabel("SSH and remote agent access disabled"), 0, 2, 1, 2)
        else:
            conn_layout.addWidget(QLabel("SSH Username:"), 0, 2)
            conn_layout.addWidget(self.user_input, 0, 3)
        
        conn_layout.addWidget(QLabel("Mesa Board:"), 1, 0)
        board_layout = QHBoxLayout()
        board_layout.addWidget(self.board_input)
        board_layout.addWidget(QLabel("Mesa IP:"))
        board_layout.addWidget(self.mesa_ip_input)
        conn_layout.addLayout(board_layout, 1, 1)
        
        if not local_only:
            conn_layout.addWidget(QLabel("SSH Password:"), 1, 2)
            conn_layout.addWidget(self.pass_input, 1, 3)
        self.credential_status = QLabel(self._credential_status_text())
        if local_only:
            self.credential_status.setText("Local-only profile: commands run on this PC.")
        self.credential_status.setStyleSheet("color: #8bc34a; font-style: italic;")
        conn_layout.addWidget(self.credential_status, 2, 0, 1, 4)
        
        btn_layout = QHBoxLayout()
        connection_test_label = "🔌 Test Machine-Agent Connection" if client_from_settings(self.settings) else "🔌 Test SSH Connection"
        self.btn_test_ssh = QPushButton(connection_test_label)
        self.btn_test_ssh.setStyleSheet("background-color: #8e44ad;")
        self.btn_test_ssh.clicked.connect(self.test_ssh)
        self.btn_save_auth = QPushButton("💾 Save Credentials")
        self.btn_save_auth.clicked.connect(self.save_credentials)
        
        btn_layout.addWidget(self.btn_test_ssh)
        btn_layout.addWidget(self.btn_save_auth)
        btn_layout.addStretch()
        if local_only:
            self.btn_test_ssh.hide()
            self.btn_save_auth.hide()
        else:
            conn_layout.addLayout(btn_layout, 3, 0, 1, 4)
        conn_layout.setColumnStretch(4, 1)
        
        conn_group.setLayout(conn_layout)
        layout.addWidget(conn_group)

        # Action Panels (Horizontal)
        actions_layout = QHBoxLayout()
        actions_layout.setSpacing(15)
        # Fix column proportions!
        # stretch factors will go into addWidget calls
        
        # 1. Diagnostics & HAL
        diag_group = QGroupBox("🛠️ Diagnostics & HAL")
        diag_layout = QVBoxLayout(diag_group)
        diag_layout.setSpacing(10)
        
        self.btn_ping = QPushButton("📶 Ping Mesa Board")
        self.btn_ping.clicked.connect(self.ping_mesa)
        self.btn_read_net = QPushButton("🌐 Read Network Config")
        self.btn_read_net.clicked.connect(self.read_network_config)
        self.btn_scan = QPushButton("🔍 Read HMID (Mesaflash)")
        self.btn_scan.clicked.connect(self.run_mesaflash_identify)
        self.btn_hal_show = QPushButton("🛠️ Show HAL Pins")
        self.btn_hal_show.clicked.connect(self.run_hal_show)
        self.btn_linuxcnc_status = QPushButton("📊 Show HAL Comps")
        self.btn_linuxcnc_status.clicked.connect(self.run_hal_components)
        self.btn_kill_hal = QPushButton("🛑 Kill LinuxCNC/HAL")
        self.btn_kill_hal.setStyleSheet("background-color: #c0392b; color: white; font-weight: bold;")
        self.btn_kill_hal.clicked.connect(self.kill_linuxcnc)

        self.custom_cmd_input = QLineEdit()
        self.custom_cmd_input.setPlaceholderText("Custom command (e.g. ip a)")
        self.btn_custom_cmd = QPushButton("🚀 Run Cmd")
        self.btn_custom_cmd.clicked.connect(self.run_custom_cmd)
        
        diag_layout.addWidget(self.btn_ping)
        diag_layout.addWidget(self.btn_read_net)
        diag_layout.addWidget(self.btn_scan)
        diag_layout.addWidget(self.btn_hal_show)
        diag_layout.addWidget(self.btn_linuxcnc_status)
        diag_layout.addWidget(self.btn_kill_hal)
        
        custom_layout = QHBoxLayout()
        custom_layout.addWidget(self.custom_cmd_input)
        custom_layout.addWidget(self.btn_custom_cmd)
        diag_layout.addLayout(custom_layout)
        diag_layout.addStretch()
        actions_layout.addWidget(diag_group, stretch=1)

        # 2. Files & Firmware
        file_group = QGroupBox("📂 Files & Firmware")
        file_layout = QVBoxLayout(file_group)
        file_layout.setSpacing(10)

        self.btn_backup = QPushButton("💾 Backup Firmware")
        self.btn_backup.setStyleSheet("background-color: #27ae60; color: white; font-weight: bold;")
        self.btn_backup.clicked.connect(self.backup_firmware)
        self.btn_flash = QPushButton("⚡ Flash Firmware")
        self.btn_flash.setStyleSheet("background-color: #d35400; color: white; font-weight: bold;")
        self.btn_flash.clicked.connect(self.flash_firmware)
        self.btn_backup_config = QPushButton("📦 Backup Config")
        self.btn_backup_config.setStyleSheet("background-color: #8e44ad; color: white; font-weight: bold;")
        self.btn_backup_config.clicked.connect(self.backup_linuxcnc_config)
        self.btn_restore_config = QPushButton("♻️ Restore Config")
        self.btn_restore_config.setStyleSheet("background-color: #f39c12; color: white; font-weight: bold;")
        self.btn_restore_config.clicked.connect(self.restore_linuxcnc_config)
        self.btn_backup_nc = QPushButton("🗂️ Backup NC Files")
        self.btn_backup_nc.setStyleSheet("background-color: #2980b9; color: white; font-weight: bold;")
        self.btn_backup_nc.clicked.connect(self.backup_nc_files)
        self.btn_upload_nc = QPushButton("📤 Upload G-Code")
        self.btn_upload_nc.setStyleSheet("background-color: #16a085; color: white; font-weight: bold;")
        self.btn_upload_nc.clicked.connect(self.upload_nc_file)

        file_layout.addWidget(self.btn_backup)
        file_layout.addWidget(self.btn_flash)
        file_layout.addWidget(self.btn_backup_config)
        file_layout.addWidget(self.btn_restore_config)
        file_layout.addWidget(self.btn_backup_nc)
        file_layout.addWidget(self.btn_upload_nc)
        file_layout.addStretch()
        actions_layout.addWidget(file_group, stretch=1)

        # 3. OS System Control
        os_group = QGroupBox("⚙️ Control Machine OS")
        os_layout = QVBoxLayout(os_group)
        os_layout.setSpacing(10)

        self.btn_vnc = QPushButton("🖥️ CNC PC Desktop (VNC)")
        self.btn_vnc.setStyleSheet("background-color: #3498db; color: white; font-weight: bold;")
        self.btn_vnc.clicked.connect(self.launch_vnc)
        self.btn_dl_vnc = QPushButton("🌐 Download VNC Viewer")
        self.btn_dl_vnc.setStyleSheet("background-color: #2c3e50; color: white; font-weight: bold;")
        self.btn_dl_vnc.clicked.connect(lambda: QDesktopServices.openUrl(QUrl("https://www.realvnc.com/en/connect/download/viewer/")))
        self.btn_dl_vnc.setVisible(not getattr(self.settings, "local_only", False))
        self.btn_autostart = QPushButton("🔄 Enable Auto-Start on Boot")
        self.btn_autostart.setStyleSheet("background-color: #27ae60; color: white; font-weight: bold;")
        self.btn_autostart.clicked.connect(self.enable_autostart)
        self.btn_setup_nic = QPushButton("🛠️ Setup PC Static IP")
        self.btn_setup_nic.setStyleSheet("background-color: #2c3e50; color: white; font-weight: bold;")
        self.btn_setup_nic.clicked.connect(self.setup_nic_ip)
        self.btn_optimize_nic = QPushButton("⚡ Optimize Intel NIC")
        self.btn_optimize_nic.setStyleSheet("background-color: #f39c12; color: white; font-weight: bold;")
        self.btn_optimize_nic.clicked.connect(self.optimize_nic)
        self.btn_optimize_os = QPushButton("🚀 Run CNC OS Optimizer")
        self.btn_optimize_os.setStyleSheet("background-color: #8e44ad; color: white; font-weight: bold;")
        self.btn_optimize_os.clicked.connect(self.optimize_os)
        self.btn_nopasswd_sudo = QPushButton("🔓 Setup Passwordless Sudo")
        self.btn_nopasswd_sudo.setStyleSheet("background-color: #c0392b; color: white; font-weight: bold;")
        self.btn_nopasswd_sudo.clicked.connect(self.setup_passwordless_sudo)

        os_layout.addWidget(self.btn_vnc)
        os_layout.addWidget(self.btn_dl_vnc)
        os_layout.addWidget(self.btn_autostart)
        os_layout.addWidget(self.btn_setup_nic)
        os_layout.addWidget(self.btn_optimize_nic)
        os_layout.addWidget(self.btn_optimize_os)
        os_layout.addWidget(self.btn_nopasswd_sudo)
        os_layout.addStretch()
        actions_layout.addWidget(os_group, stretch=1)

        layout.addLayout(actions_layout)

        # Console Output
        layout.addWidget(QLabel("Mesaflash Output:"))
        self.console = QTextEdit()
        self.console.setReadOnly(True)
        layout.addWidget(self.console)

        # Action Footer
        self.btn_import = QPushButton("➕ Convert Scan to Library Component")
        self.btn_import.setEnabled(False)
        self.btn_import.setStyleSheet("""
            QPushButton { background-color: #27ae60; font-size: 14px; min-height: 25px; }
            QPushButton:hover { background-color: #2ecc71; }
            QPushButton:pressed { background-color: #229954; }
            QPushButton:disabled { background-color: #555555; color: #888888; }
        """)
        self.btn_import.clicked.connect(self.parse_hmid_to_library)
        import_layout = QHBoxLayout()
        import_layout.addWidget(self.btn_import)
        import_layout.addStretch()
        layout.addLayout(import_layout)

        # Status Bar
        self.status_label = QLabel("Ready.")
        self.status_label.setStyleSheet("color: #aaaaaa; font-style: italic; padding-top: 5px;")
        layout.addWidget(self.status_label)
        self.apply_secure_mode_restrictions()

    def set_status(self, text):
        self.status_label.setText(text)
        QApplication.processEvents()  # Force the UI to refresh immediately

    def apply_secure_mode_restrictions(self):
        """Disable controls that only have a legacy SSH or shell implementation."""
        if client_from_settings(self.settings) is None:
            return
        message = "This action requires legacy SSH or shell access and is unavailable in secure agent mode."
        controls = [
            self.btn_ping,
            self.btn_read_net,
            self.btn_kill_hal,
            self.custom_cmd_input,
            self.btn_custom_cmd,
            self.btn_backup,
            self.btn_vnc,
            self.btn_autostart,
            self.btn_setup_nic,
            self.btn_optimize_nic,
            self.btn_optimize_os,
            self.btn_nopasswd_sudo,
        ]
        for control in controls:
            control.setEnabled(False)
            control.setToolTip(message)

    def _credential_status_text(self):
        """Describe whether the OS keyring is available without exposing secrets."""
        try:
            CredentialStore()
            return "SSH password storage: OS credential manager"
        except CredentialStoreError:
            return "SSH password storage unavailable: install keyring support"

    def run_custom_cmd(self):
        cmd = self.custom_cmd_input.text().strip()
        if not cmd: return
        if client_from_settings(self.settings) is not None:
            self.console.append("Custom shell commands are disabled in secure agent mode.")
            self.set_status("Use an approved machine-agent operation instead.")
            return
        
        use_sudo = False
        if cmd.startswith("sudo "):
            use_sudo = True
            cmd = cmd[5:].strip() # Strip the first sudo so our wrapper can cleanly inject it with the password
            
        self.execute_remote_command(cmd, use_sudo=use_sudo)

    def save_credentials(self):
        if getattr(self.settings, "local_only", False):
            self.set_status("Remote credentials are disabled in local-only mode.")
            return
        try:
            self.settings.save_setting("SSH", "username", self.user_input.text())
            self.settings.save_setting("SSH", "password", self.pass_input.text())
            self.settings.save_setting("SSH", "ip_address", self.ip_input.text())
            self.settings.save_setting("MESA", "board_model", self.board_input.text())
            self.settings.save_setting("MESA", "ip_address", self.mesa_ip_input.text())
            self.credential_status.setText(self._credential_status_text())
            QMessageBox.information(self, "Saved", "Settings saved. The SSH password is stored in the OS credential manager.")
        except RuntimeError as error:
            QMessageBox.critical(self, "Secure Storage Failed", str(error))

    def execute_remote_command(self, cmd, use_sudo=False):
        if client_from_settings(self.settings) is not None:
            message = "Generic remote shell commands are disabled in secure agent mode. Use an approved machine-agent operation instead."
            self.console.append(message)
            self.set_status("Use an approved machine-agent operation instead.")
            return None, message

        ip = "127.0.0.1" if getattr(self.settings, "local_only", False) else self.ip_input.text()
        user = self.user_input.text()
        password = self.pass_input.text()
        
        self.set_status(f"Preparing to execute on {ip}...")
        display_cmd = cmd
        if use_sudo:
            display_cmd = f"sudo {cmd}"
            if password:
                cmd = f"echo '{password}' | sudo -S bash -c \"{cmd}\""
            else:
                cmd = f"sudo bash -c \"{cmd}\""

        self.console.append(f"\n--- Executing on {ip} ---")
        self.console.append(f"> {display_cmd}")
        
        if is_local_ip(ip):
            try:
                result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
                if result.stdout: self.console.append(result.stdout)
                if result.stderr: self.console.append(f"[ERROR]:\n{result.stderr}")
                self.set_status("Ready.")
                return result.stdout, result.stderr
            except Exception as e:
                self.console.append(f"[LOCAL EXEC FAILED]: {e}")
                self.set_status("Command failed.")
                return None, str(e)
                
        try:
            self.set_status(f"Running command to {ip}...")
            full_cmd = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", f"{user}@{ip}", cmd]
            result = subprocess.run(full_cmd, capture_output=True, text=True, timeout=15)
            if result.stdout: self.console.append(result.stdout)
            if result.stderr: self.console.append(f"[ERROR]:\n{result.stderr}")
            self.set_status("Ready.")
            return result.stdout, result.stderr
        except Exception as e:
            self.console.append(f"[EXEC FAILED]: {e}")
            self.set_status("Command failed.")
            return None, str(e)

    def run_hal_show(self):
        self.set_status("Initializing HAL pin dump. This will take a few seconds...")
        agent_client = client_from_settings(self.settings)
        if agent_client is not None:
            try:
                result = agent_client.show_hal_pins()
                out = result.get("output", "")
                err = result.get("error", "")
                if out:
                    self.console.append(out)
                if err:
                    self.console.append(f"[ERROR]:\n{err}")
                if "Component Pins:" in out:
                    self.btn_import.setEnabled(True)
            except Exception as error:
                self.console.append(f"[AGENT FAILED]: {error}")
            return
        if getattr(self.settings, "local_only", False):
            try:
                output = format_hal_pin_listing(LocalLinuxCNCAPI().get_hal_pins())
                self.console.append(output)
                self.btn_import.setEnabled("Component Pins:" in output)
                self.set_status("Ready.")
            except Exception as error:
                self.console.append(f"[HAL API FAILED]: {error}")
                self.set_status("HAL read failed.")
            return
        out, err = self.execute_remote_command("halcmd show pin")

    def run_hal_components(self):
        """Show HAL components through the typed agent when it is enrolled."""
        agent_client = client_from_settings(self.settings)
        if agent_client is not None:
            try:
                result = agent_client.show_hal_components()
                if result.get("output"):
                    self.console.append(result["output"])
                if result.get("error"):
                    self.console.append(f"[ERROR]:\n{result['error']}")
            except Exception as error:
                self.console.append(f"[AGENT FAILED]: {error}")
            return
        if getattr(self.settings, "local_only", False):
            try:
                api = LocalLinuxCNCAPI()
                component_names = sorted({
                    str(pin.get("NAME", "")).split(".", 1)[0]
                    for pin in api.get_hal_pins()
                    if pin.get("NAME")
                })
                self.console.append("Active HAL components (from exported pins):\n" + "\n".join(component_names))
                self.set_status("Ready.")
            except Exception as error:
                self.console.append(f"[HAL API FAILED]: {error}")
                self.set_status("HAL read failed.")
            return
        self.execute_remote_command("halcmd show comp")

    def run_mesaflash_identify(self):
        """Identify the Mesa board through the typed agent operation when enrolled."""
        agent_client = client_from_settings(self.settings)
        if agent_client is None:
            self.run_mesaflash()
            return
        try:
            result = agent_client.identify_mesa(self.board_input.text().strip(), self.mesa_ip_input.text().strip())
            if result.get("output"):
                self.console.append(result["output"])
            if result.get("error"):
                self.console.append(f"[ERROR]:\n{result['error']}")
        except Exception as error:
            self.console.append(f"[AGENT FAILED]: {error}")

    def test_ssh(self):
        if getattr(self.settings, "local_only", False):
            self.set_status("SSH and remote agent tests are disabled in local-only mode.")
            return
        self.console.clear()
        agent_client = client_from_settings(self.settings)
        if agent_client is not None:
            self.set_status("Testing secure machine-agent connection...")
            try:
                status = agent_client.get("/v1/lifecycle")
                self.console.append("Secure machine-agent connection successful.")
                self.console.append(f"LinuxCNC running: {status.get('running', False)}")
                agent_status = agent_client.agent_status()
                self.console.append(f"Agent version: {agent_status.get('agent_version', 'unknown')}")
                self.console.append(f"Role mode: {agent_status.get('role_mode', 'unknown')}")
                self.console.append("Capabilities: " + ", ".join(agent_status.get("capabilities", [])))
                self._apply_agent_capabilities(agent_status.get("capabilities", []))
                QMessageBox.information(self, "Success", "Secure machine-agent connection established.")
            except Exception as error:
                self.console.append(f"Machine-agent connection failed: {error}")
                QMessageBox.critical(self, "Error", "Secure machine-agent connection failed.")
            return
        self.set_status("Testing SSH Connection...")
        out, err = self.execute_remote_command("echo 'SSH Connection Successful!'")
        if out and "Successful" in out:
            QMessageBox.information(self, "Success", "SSH Connection Established!")
        else:
            QMessageBox.critical(self, "Error", "SSH Connection Failed. Check console.")

    def _apply_agent_capabilities(self, capabilities):
        """Disable secure UI actions the connected agent does not advertise."""
        capability_map = {
            "btn_scan": "mesa_identification",
            "btn_flash": "mesa_firmware",
            "btn_backup_config": "backup_transfer",
            "btn_restore_config": "backup_transfer",
            "btn_backup_nc": "backup_transfer",
            "btn_upload_nc": "file_upload",
        }
        available = set(capabilities)
        for button_name, capability in capability_map.items():
            button = getattr(self, button_name, None)
            if button is not None:
                button.setEnabled(capability in available)

    def ping_mesa(self):
        mesa_ip = self.mesa_ip_input.text()
        self.set_status(f"Pinging {mesa_ip}...")
        self.execute_remote_command(f"ping -c 4 {mesa_ip}")
        self.set_status("Ready.")

    def read_network_config(self):
        board = self.board_input.text()
        mesa_ip = self.mesa_ip_input.text()
        self.set_status("Querying Mesa card network config...")
        cmd = f"mesaflash --device {board} --addr {mesa_ip} --print-pd"
        self.execute_remote_command(cmd, use_sudo=True)
        self.set_status("Ready.")

    def kill_linuxcnc(self):
        confirm = QMessageBox.question(self, "Confirm Kill", 
                                       "Are you sure you want to force kill LinuxCNC and HAL processes on the remote machine?\nThis will stop any running instances.", 
                                       QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if confirm == QMessageBox.StandardButton.Yes:
            self.set_status("⏳ Shutting down LinuxCNC smoothly... please wait.")
            QApplication.processEvents()
            cmd = "if pgrep linuxcnc > /dev/null; then python3 -c 'import linuxcnc; c=linuxcnc.command(); c.exit()' 2>/dev/null; for i in 1 2 3 4 5 6 7 8 9 10; do if ! pgrep linuxcnc > /dev/null; then break; fi; sleep 1; done; if pgrep linuxcnc > /dev/null; then killall -TERM linuxcnc linuxcncsvr milltask io 2>/dev/null; sleep 2; fi; if pgrep linuxcnc > /dev/null; then halrun -U 2>/dev/null; killall -9 linuxcnc linuxcncsvr milltask io halrun rtapi_app halcmd 2>/dev/null; rm -rf /tmp/linuxcnc.lock* /tmp/linuxcnc.print /tmp/linuxcnc.error 2>/dev/null; rm -f /dev/shm/linuxcnc* /dev/shm/rtapi* /dev/shm/hal* 2>/dev/null; fi; fi; pkill -TERM -f linuxcnc_gui 2>/dev/null"
            ip = self.ip_input.text()
            self.execute_remote_command(cmd, use_sudo=not is_local_ip(ip))
            self.set_status("Ready.")

    def run_mesaflash(self):
        board = self.board_input.text()
        mesa_ip = self.mesa_ip_input.text()
        
        self.set_status("Querying Mesa card via mesaflash...")
        cmd = f"mesaflash --device {board} --addr {mesa_ip} --readhmid"
        out, err = self.execute_remote_command(cmd, use_sudo=True)
        
        if out and "I/O" in out:
            self.btn_import.setEnabled(True)

    def parse_hmid_to_library(self):
        """Advanced: Parses the raw text into pins for your Library Manager."""
        self.set_status("Parsing extracted pins into library component...")
        raw_text = self.console.toPlainText()
        lines = raw_text.splitlines()
        
        new_pins = []
        tb_groups = {}
        
        # Auto-detect if we are looking at HALCMD output or Mesaflash HMID output
        is_halcmd = any("Component Pins:" in line for line in lines[:20])
        
        if is_halcmd:
            for line in lines:
                parts = line.split()
                # Look for lines with proper formatting: [Owner, Type, Dir, Value, Name, ...]
                if len(parts) >= 5 and parts[2] in ["IN", "OUT", "I/O"]:
                    pin_type = parts[1]
                    dir_str = parts[2]
                    hal_name = parts[4]
                    
                    if not hal_name.startswith("hm2_"):
                        continue
                    
                    is_out = (dir_str == "OUT")
                    label = hal_name.split('.')[-1].upper() # Clean label, e.g., 'INPUT-00'
                    
                    color = "Default"
                    if pin_type == "bit": color = "Signal (Blue)"
                    elif pin_type in ["float", "s32", "u32"]: color = "Clock (Yellow)"

                    new_pins.append([label, is_out, hal_name, color])
        else:
            # Advanced HMID Parser: Extracts Terminal Block and P1/P2 Connectors
            current_group = "Misc"
            for line in lines:
                if "IO Connections for P1" in line:
                    current_group = "P1"
                elif "IO Connections for P2" in line:
                    current_group = "P2"
                elif "IO Connections for on-card" in line:
                    current_group = "On-Card"
                    
                parts = line.split()
                # Look for lines defining an IO port: "TB2-4,5  0  IOPort  StepGen  0  Dir/Table2  (Out)"
                if len(parts) >= 3 and parts[1].isdigit() and parts[2] == "IOPort":
                    pin_label = parts[0]
                    gpio_idx = parts[1].zfill(3)
                    sec_func = parts[3]
                    
                    is_out = "(Out)" in line or "TXData" in line
                    
                    group_name = current_group
                    if pin_label.startswith("TB"):
                        tb_match = re.search(r'TB(\d+)', pin_label)
                        if tb_match:
                            group_name = f"TB{tb_match.group(1)}"
                    
                    desc = ""
                    board_name = self.board_input.text().replace('eu', 'e') # standard driver is 7i76e
                    hal_name = f"hm2_{board_name}.0.gpio.{gpio_idx}"
                    color = "Default"
                    
                    if len(parts) >= 6 and sec_func != "None":
                        chan = parts[4].zfill(2) if parts[4].isdigit() else "00"
                        func_detail = parts[5]
                        desc = f"[{sec_func} {chan}] {func_detail}"
                        
                        # Auto-assign exact HAL names and colors based on secondary functions!
                        if sec_func == "StepGen":
                            color = "Clock (Yellow)"
                            if "Step" in func_detail: hal_name = f"hm2_{board_name}.0.stepgen.{chan}.step"
                            elif "Dir" in func_detail: hal_name = f"hm2_{board_name}.0.stepgen.{chan}.dir"
                        elif sec_func == "QCount":
                            color = "Power (Red)" # Distinct color for encoders
                            if "Quad-A" in func_detail: hal_name = f"hm2_{board_name}.0.encoder.{chan}.input-a"
                            elif "Quad-B" in func_detail: hal_name = f"hm2_{board_name}.0.encoder.{chan}.input-b"
                            elif "Quad-IDX" in func_detail: hal_name = f"hm2_{board_name}.0.encoder.{chan}.input-index"
                        elif sec_func == "SSerial":
                            color = "Signal (Blue)"
                    
                    full_label = f"{pin_label} {desc}".strip()
                    
                    if group_name not in tb_groups:
                        tb_groups[group_name] = []
                    tb_groups[group_name].append([full_label, is_out, hal_name, color])
                    
            # Auto-inject Field I/O for 7i76e/eu because mesaflash doesn't report Smart Serial pins natively
            if self.board_input.text().lower() in ["7i76e", "7i76eu"]:
                board_n = self.board_input.text().lower().replace('eu', 'e')
                
                tb6_pins = []
                for i in range(16): tb6_pins.append([f"TB6 Input {i:02d}", False, f"hm2_{board_n}.0.7i76.0.0.input-{i:02d}", "Signal (Blue)"])
                tb_groups["TB6"] = tb6_pins
                
                tb5_pins = []
                for i in range(16, 32): tb5_pins.append([f"TB5 Input {i:02d}", False, f"hm2_{board_n}.0.7i76.0.0.input-{i:02d}", "Signal (Blue)"])
                tb5_pins.append(["TB5 MPG0 Count (Pins 16/17)", True, f"hm2_{board_n}.0.7i76.0.0.enc0.count", "Clock (Yellow)"])
                tb5_pins.append(["TB5 MPG1 Count (Pins 18/19)", True, f"hm2_{board_n}.0.7i76.0.0.enc1.count", "Clock (Yellow)"])
                tb_groups["TB5"] = tb5_pins
                
                tb4_pins = []
                for i in range(16): tb4_pins.append([f"TB4 Output {i:02d}", True, f"hm2_{board_n}.0.7i76.0.0.output-{i:02d}", "Signal (Blue)"])
                tb_groups["TB4"] = tb4_pins

        components_to_save = {}
        if is_halcmd and new_pins:
            board_name = f"{self.board_input.text().upper()}_HAL_Auto"
            components_to_save[board_name] = new_pins
        elif not is_halcmd and tb_groups:
            for tb, pins in tb_groups.items():
                comp_name = f"{self.board_input.text().upper()}_{tb}"
                components_to_save[comp_name] = pins
            
        if components_to_save:
            lib = {}
            if os.path.exists("component_library.json"):
                with open("component_library.json", 'r') as f:
                    try: lib = json.load(f)
                    except json.JSONDecodeError: pass
            
            lib.update(components_to_save)
            
            with open("component_library.json", 'w') as f:
                json.dump(lib, f, indent=4)
            
            self.library_tab.refresh_list()
            try: self.library_tab.parent_app.wiring_tab.refresh_component_list()
            except AttributeError: pass
                
            names_str = ", ".join(components_to_save.keys())
            QMessageBox.information(self, "Success", f"Created components in Library:\n{names_str}")
        self.set_status("Ready.")

    def backup_firmware(self):
        board = self.board_input.text()
        
        backup_dir = os.path.join(os.getcwd(), "backups")
        if not os.path.exists(backup_dir):
            os.makedirs(backup_dir, exist_ok=True)
            
        last_dir = self.settings.get_setting("PATHS", "last_backup_dir", backup_dir)
        if not os.path.exists(last_dir): last_dir = backup_dir
        
        default_name = f"{board}_backup.bit"
        
        path, _ = QFileDialog.getSaveFileName(self, "Save Firmware Backup", os.path.join(last_dir, default_name), "Mesa Firmware (*.bit *.bin);;All Files (*)")
        if not path:
            return
            
        self.settings.save_setting("PATHS", "last_backup_dir", os.path.dirname(path))
        
        mesa_ip = self.mesa_ip_input.text()
        remote_dest = "/tmp/mesa_backup.bit"

        self.set_status("Reading firmware from Mesa board (this will take a moment)...")
        read_cmd = f"mesaflash --device {board} --addr {mesa_ip} --read {remote_dest}"
        out, err = self.execute_remote_command(read_cmd, use_sudo=True)
        
        if err and "error" in err.lower() and "successfully" not in err.lower():
            QMessageBox.critical(self, "Backup Failed", f"Error output from mesaflash:\n{err}\n\nPlease check the console output.")
            self.set_status("Ready.")
            return
            
        self.set_status("Downloading firmware file from remote machine...")
        self.console.append(f"\n--- Downloading backup to {path} ---")
        
        ip = self.ip_input.text()
        user = self.user_input.text()
        password = self.pass_input.text()

        try:
            if is_local_ip(ip):
                shutil.copyfile(remote_dest, path)
                try: os.remove(remote_dest)
                except: pass
            else:
                res = subprocess.run(["scp", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", f"{user}@{ip}:{remote_dest}", path], capture_output=True, timeout=30)
                if res.returncode != 0:
                    raise RuntimeError(res.stderr.decode() if isinstance(res.stderr, bytes) else str(res.stderr))
                subprocess.run(["ssh", "-o", "BatchMode=yes", f"{user}@{ip}", f"rm -f {remote_dest}"], capture_output=True)
            self.console.append("Download complete.")
            QMessageBox.information(self, "Success", f"Firmware backed up successfully to:\n{path}")
        except Exception as e:
            self.console.append(f"[DOWNLOAD FAILED]: {e}")
            self.set_status("Download failed.")
            QMessageBox.critical(self, "Download Failed", f"Failed to download firmware backup:\n{e}")
            
        self.set_status("Ready.")

    def backup_linuxcnc_config(self):
        agent_client = client_from_settings(self.settings)
        if agent_client is not None:
            self._download_secure_archive(agent_client, "config", "linuxcnc_configs_backup.tar.gz")
            return
        # Ask for the remote path
        default_remote_path = self.settings.get_setting("PATHS", "last_remote_config_dir", "~/linuxcnc/configs")
        remote_path, ok = QInputDialog.getText(self, "Backup Configuration", 
                                               "Enter the remote folder path to backup:", 
                                               text=default_remote_path)
        if not ok or not remote_path: return
        self.settings.save_setting("PATHS", "last_remote_config_dir", remote_path)

        # Ask where to save the archive locally
        backup_dir = os.path.join(os.getcwd(), "backups")
        if not os.path.exists(backup_dir):
            os.makedirs(backup_dir, exist_ok=True)
            
        last_dir = self.settings.get_setting("PATHS", "last_backup_dir", backup_dir)
        if not os.path.exists(last_dir): last_dir = backup_dir
        
        default_name = "linuxcnc_configs_backup.tar.gz"
        
        path, _ = QFileDialog.getSaveFileName(self, "Save Config Backup", os.path.join(last_dir, default_name), "Archives (*.tar.gz);;All Files (*)")
        if not path: return
        self.settings.save_setting("PATHS", "last_backup_dir", os.path.dirname(path))

        self.set_status(f"Archiving {remote_path} on remote machine...")
        remote_dest = "/tmp/linuxcnc_backup.tar.gz"
        
        # Create a compressed tarball on the Linux machine
        cmd = f"tar -czf {remote_dest} {remote_path}"
        out, err = self.execute_remote_command(cmd)
        
        # `tar` often outputs benign warnings to stderr, so we only trap hard errors.
        if err and ("no such file" in err.lower() or "permission denied" in err.lower() or "fatal error" in err.lower()):
            QMessageBox.critical(self, "Backup Failed", f"Failed to create archive on remote machine:\n{err}")
            self.set_status("Ready.")
            return
            
        self.set_status("Downloading configuration backup...")
        self.console.append(f"\n--- Downloading backup to {path} ---")
        
        ip = self.ip_input.text()
        user = self.user_input.text()
        try:
            if is_local_ip(ip):
                shutil.copyfile(remote_dest, path)
                try: os.remove(remote_dest)
                except: pass
            else:
                res = subprocess.run(["scp", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", f"{user}@{ip}:{remote_dest}", path], capture_output=True, timeout=30)
                if res.returncode != 0:
                    raise RuntimeError(res.stderr.decode() if isinstance(res.stderr, bytes) else str(res.stderr))
                subprocess.run(["ssh", "-o", "BatchMode=yes", f"{user}@{ip}", f"rm -f {remote_dest}"], capture_output=True)
            self.console.append("Download complete.")
            QMessageBox.information(self, "Success", f"LinuxCNC configuration backed up to:\n{path}")
        except Exception as e:
            self.console.append(f"[DOWNLOAD FAILED]: {e}")
            self.set_status("Download failed.")
            QMessageBox.critical(self, "Download Failed", f"Failed to download config backup:\n{e}")
            
        self.set_status("Ready.")

    def backup_nc_files(self):
        agent_client = client_from_settings(self.settings)
        if agent_client is not None:
            self._download_secure_archive(agent_client, "gcode", "nc_files_backup.tar.gz")
            return
        # Ask for the remote path
        default_remote_path = self.settings.get_setting("PATHS", "last_remote_nc_dir", "~/linuxcnc/nc_files")
        remote_path, ok = QInputDialog.getText(self, "Backup NC Files", 
                                               "Enter the remote folder path to backup:", 
                                               text=default_remote_path)
        if not ok or not remote_path: return
        self.settings.save_setting("PATHS", "last_remote_nc_dir", remote_path)

        # Ask where to save the archive locally
        backup_dir = os.path.join(os.getcwd(), "backups")
        if not os.path.exists(backup_dir):
            os.makedirs(backup_dir, exist_ok=True)
            
        last_dir = self.settings.get_setting("PATHS", "last_backup_dir", backup_dir)
        if not os.path.exists(last_dir): last_dir = backup_dir
        
        default_name = "nc_files_backup.tar.gz"
        
        path, _ = QFileDialog.getSaveFileName(self, "Save NC Files Backup", os.path.join(last_dir, default_name), "Archives (*.tar.gz);;All Files (*)")
        if not path: return
        self.settings.save_setting("PATHS", "last_backup_dir", os.path.dirname(path))

        self.set_status(f"Archiving {remote_path} on remote machine...")
        remote_dest = "/tmp/nc_files_backup.tar.gz"
        
        # Create a compressed tarball on the Linux machine
        cmd = f"tar -czf {remote_dest} {remote_path}"
        out, err = self.execute_remote_command(cmd)
        
        if err and ("no such file" in err.lower() or "permission denied" in err.lower() or "fatal error" in err.lower()):
            QMessageBox.critical(self, "Backup Failed", f"Failed to create archive on remote machine:\n{err}")
            self.set_status("Ready.")
            return
            
        self.set_status("Downloading NC files backup...")
        self.console.append(f"\n--- Downloading backup to {path} ---")
        
        ip = self.ip_input.text()
        user = self.user_input.text()
        try:
            if is_local_ip(ip):
                shutil.copyfile(remote_dest, path)
                try: os.remove(remote_dest)
                except: pass
            else:
                res = subprocess.run(["scp", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", f"{user}@{ip}:{remote_dest}", path], capture_output=True, timeout=30)
                if res.returncode != 0:
                    raise RuntimeError(res.stderr.decode() if isinstance(res.stderr, bytes) else str(res.stderr))
                subprocess.run(["ssh", "-o", "BatchMode=yes", f"{user}@{ip}", f"rm -f {remote_dest}"], capture_output=True)
            self.console.append("Download complete.")
            QMessageBox.information(self, "Success", f"NC files backed up to:\n{path}")
        except Exception as e:
            self.console.append(f"[DOWNLOAD FAILED]: {e}")
            self.set_status("Download failed.")
            QMessageBox.critical(self, "Download Failed", f"Failed to download NC files backup:\n{e}")
            
        self.set_status("Ready.")

    def restore_linuxcnc_config(self):
        agent_client = client_from_settings(self.settings)
        backup_dir = os.path.join(os.getcwd(), "backups")
        if not os.path.exists(backup_dir):
            os.makedirs(backup_dir, exist_ok=True)
            
        last_dir = self.settings.get_setting("PATHS", "last_backup_dir", backup_dir)
        if not os.path.exists(last_dir): last_dir = backup_dir
        
        path, _ = QFileDialog.getOpenFileName(self, "Select Config Backup to Restore", last_dir, "Archives (*.tar.gz);;All Files (*)")
        if not path: return
        self.settings.save_setting("PATHS", "last_backup_dir", os.path.dirname(path))

        if agent_client is not None:
            confirm = QMessageBox.question(
                self,
                "Confirm Secure Restore",
                f"Restore '{os.path.basename(path)}' to the LinuxCNC configuration root?\n\n"
                "LinuxCNC must be stopped. The current configuration will be replaced transactionally.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if confirm != QMessageBox.StandardButton.Yes:
                return
            try:
                with open(path, "rb") as archive_file:
                    content = archive_file.read(16 * 1024 * 1024 + 1)
                result = agent_client.restore_backup_archive("config", content)
                QMessageBox.information(self, "Restore Complete", result.get("kind", "Configuration") + " backup restored successfully.")
            except Exception as error:
                QMessageBox.critical(self, "Restore Failed", f"Secure configuration restore failed:\n{error}")
            return

        confirm = QMessageBox.question(self, "Confirm Restore", 
                                       f"Are you sure you want to restore the configuration from '{os.path.basename(path)}'?\n\nThis will overwrite existing files on the remote machine with the contents of the archive.", 
                                       QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if confirm != QMessageBox.StandardButton.Yes:
            return

        self.set_status("Uploading config backup to remote machine...")
        self.console.append(f"\n--- Uploading {os.path.basename(path)} ---")
        
        remote_dest = "/tmp/linuxcnc_restore.tar.gz"
        
        ip = self.ip_input.text()
        user = self.user_input.text()
        try:
            if is_local_ip(ip):
                shutil.copyfile(path, remote_dest)
            else:
                res = subprocess.run(["scp", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", path, f"{user}@{ip}:{remote_dest}"], capture_output=True, timeout=30)
                if res.returncode != 0:
                    raise RuntimeError(res.stderr.decode() if isinstance(res.stderr, bytes) else str(res.stderr))
            self.console.append("Upload complete.")
        except Exception as e:
            self.console.append(f"[UPLOAD FAILED]: {e}")
            self.set_status("Upload failed.")
            QMessageBox.critical(self, "Upload Failed", f"Failed to upload config backup:\n{e}")
            return
            
        self.set_status("Extracting configuration on remote machine...")
        
        # Extract the archive starting from the root directory so the absolute paths perfectly align
        cmd = f"tar -xzf {remote_dest} -C / && rm {remote_dest}"
        out, err = self.execute_remote_command(cmd, use_sudo=True)
        
        if err and "error" in err.lower() and "fatal" in err.lower():
            QMessageBox.critical(self, "Restore Failed", f"Failed to extract archive on remote machine:\n{err}")
        else:
            self.console.append("✅ Restore successful!")
            QMessageBox.information(self, "Success", "LinuxCNC configuration restored successfully!")
            
        self.set_status("Ready.")

    def flash_firmware(self):
        default_dir = os.path.expanduser("~")
        last_dir = self.settings.get_setting("PATHS", "last_firmware_dir", default_dir)
        if not os.path.exists(last_dir): last_dir = default_dir
        
        path, _ = QFileDialog.getOpenFileName(self, "Select Firmware File", last_dir, "Mesa Firmware (*.bit *.bin);;All Files (*)")
        if not path:
            return
            
        self.settings.save_setting("PATHS", "last_firmware_dir", os.path.dirname(path))
            
        board = self.board_input.text()
        mesa_ip = self.mesa_ip_input.text()
        filename = os.path.basename(path)
        remote_dest = f"/tmp/{filename}"

        confirm = QMessageBox.question(self, "Confirm Firmware Flash", 
                                       f"Are you sure you want to flash '{filename}' to the {board} board at {mesa_ip}?\n\nWARNING: Do not interrupt power or the network connection during this process!", 
                                       QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        
        if confirm != QMessageBox.StandardButton.Yes:
            return

        agent_client = client_from_settings(self.settings)
        if agent_client is not None:
            self.set_status("Flashing firmware through secure machine agent...")
            try:
                with open(path, "rb") as firmware_file:
                    result = agent_client.flash_firmware(
                        board,
                        mesa_ip,
                        filename,
                        firmware_file.read(16 * 1024 * 1024 + 1),
                    )
                self.console.append(result.get("output", ""))
                if result.get("error"):
                    self.console.append(f"[ERROR]:\n{result['error']}")
                QMessageBox.information(self, "Firmware Complete", "Mesa firmware was flashed and the FPGA was reloaded.")
            except Exception as error:
                QMessageBox.critical(self, "Flash Failed", f"Secure firmware operation failed:\n{error}")
            self.set_status("Ready.")
            return

        self.set_status("Uploading firmware file to remote machine...")
        self.console.append(f"\n--- Uploading {filename} ---")
        
        ip = self.ip_input.text()
        user = self.user_input.text()
        password = self.pass_input.text()

        try:
            if is_local_ip(ip):
                shutil.copyfile(path, remote_dest)
            else:
                res = subprocess.run(["scp", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", path, f"{user}@{ip}:{remote_dest}"], capture_output=True, timeout=30)
                if res.returncode != 0:
                    raise RuntimeError(res.stderr.decode() if isinstance(res.stderr, bytes) else str(res.stderr))
            self.console.append("Upload complete.")
        except Exception as e:
            self.console.append(f"[UPLOAD FAILED]: {e}")
            self.set_status("Upload failed.")
            QMessageBox.critical(self, "Upload Failed", f"Failed to upload firmware:\n{e}")
            return

        self.set_status("Flashing firmware (this will take a moment)...")
        
        write_cmd = f"mesaflash --device {board} --addr {mesa_ip} --write {remote_dest}"
        out, err = self.execute_remote_command(write_cmd, use_sudo=True)
        
        if err and "error" in err.lower() and "successfully" not in err.lower():
            QMessageBox.critical(self, "Flash Failed", f"Error output from mesaflash:\n{err}\n\nPlease check the console output.")
        else:
            self.set_status("Reloading FPGA...")
            reload_cmd = f"mesaflash --device {board} --addr {mesa_ip} --reload"
            self.execute_remote_command(reload_cmd, use_sudo=True)
            QMessageBox.information(self, "Success", "Firmware flashed and board reloaded successfully!")
            
        self.set_status("Ready.")

    def upload_nc_file(self):
        # Ask for local file
        default_dir = os.path.expanduser("~")
        last_dir = self.settings.get_setting("PATHS", "last_local_gcode_dir", default_dir)
        if not os.path.exists(last_dir): last_dir = default_dir
        
        path, _ = QFileDialog.getOpenFileName(self, "Select G-Code to Upload", last_dir, "G-Code Files (*.ngc *.gcode *.nc);;All Files (*)")
        if not path: return
        self.settings.save_setting("PATHS", "last_local_gcode_dir", os.path.dirname(path))

        # Ask for remote destination dir
        default_remote_path = self.settings.get_setting("PATHS", "last_remote_nc_dir", "~/linuxcnc/nc_files")
        remote_path, ok = QInputDialog.getText(self, "Upload G-Code", 
                                               "Enter the remote destination folder:", 
                                               text=default_remote_path)
        if not ok or not remote_path: return
        self.settings.save_setting("PATHS", "last_remote_nc_dir", remote_path)
        
        filename = os.path.basename(path)
        remote_dest = f"{remote_path.rstrip('/')}/{filename}"
        
        self.set_status(f"Uploading {filename}...")
        self.console.append(f"\n--- Uploading {filename} to {remote_dest} ---")
        
        ip = self.ip_input.text()
        user = self.user_input.text()
        password = self.pass_input.text()

        expanded_dest = remote_dest.replace("~/", f"/home/{user}/", 1) if remote_dest.startswith("~/") else remote_dest
        try:
            if is_local_ip(ip):
                local_target = os.path.expanduser(remote_dest)
                os.makedirs(os.path.dirname(local_target), exist_ok=True)
                shutil.copyfile(path, local_target)
            else:
                remote_dir = os.path.dirname(expanded_dest)
                subprocess.run(["ssh", "-o", "BatchMode=yes", f"{user}@{ip}", f"mkdir -p {remote_dir}"], capture_output=True, timeout=10)
                res = subprocess.run(["scp", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", path, f"{user}@{ip}:{expanded_dest}"], capture_output=True, timeout=30)
                if res.returncode != 0:
                    raise RuntimeError(res.stderr.decode() if isinstance(res.stderr, bytes) else str(res.stderr))
            self.console.append("Upload complete.")
            QMessageBox.information(self, "Success", f"File uploaded successfully to:\n{remote_dest}")
        except Exception as e:
            self.console.append(f"[UPLOAD FAILED]: {e}")
            self.set_status("Upload failed.")
            QMessageBox.critical(self, "Upload Failed", f"Failed to upload G-Code file:\n{e}")
            
        self.set_status("Ready.")
        
    def launch_vnc(self):
        ip = self.ip_input.text()
        user = self.user_input.text()
        password = self.pass_input.text()
        
        if not ip or not user:
            QMessageBox.warning(self, "Missing Credentials", "Please set SSH credentials first.")
            return

        self.set_status("Starting remote VNC server...")
        self.console.append("\n--- Starting x11vnc on Remote Machine ---")
        cmd = "killall x11vnc; nohup x11vnc -display :0 -auth guess -bg -nopw -xkb -q </dev/null > /dev/null 2>&1 &"
        
        if is_local_ip(ip):
            subprocess.Popen(cmd, shell=True, start_new_session=True)
        else:
            self.execute_remote_command(cmd)
        
        vnc_url = f"vnc://{ip}:5900"
        msg = (f"Remote display server started at {ip}:5900!\n\nIf your computer doesn't automatically open a viewer, use RealVNC or TightVNC to connect.\n\n(Note: If connection fails, run 'sudo apt install x11vnc' on the Linux machine first.)")
               
        if platform.system() == "Darwin":
            subprocess.run(["open", vnc_url])
        elif platform.system() == "Windows":
            QMessageBox.information(self, "VNC Started", msg)
        else:
            subprocess.run(["xdg-open", vnc_url])
                
        self.set_status("Remote desktop launched.")

    def enable_autostart(self):
        confirm = QMessageBox.question(self, "Enable Auto-Start", 
                                       "This will configure the remote Linux machine to automatically launch LinuxCNC Pro on boot.\n\n(Ensure you have also checked 'Auto-Launch LinuxCNC on Startup' in the Settings tab).\n\nDo you want to proceed?", 
                                       QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if confirm != QMessageBox.StandardButton.Yes:
            return

        ip = self.ip_input.text()
        user = self.user_input.text()
        
        if not ip or not user:
            QMessageBox.warning(self, "Missing Credentials", "Please set SSH credentials first.")
            return

        remote_linuxcnc = self.settings.get_setting("PATHS", "last_remote_linuxcnc_dir", "~/LinuxCNC").rstrip('/')
        
        remote_dir, ok = QInputDialog.getText(self, "Auto-Start Setup", "Enter remote LinuxCNC directory path:", text=remote_linuxcnc)
        if not ok or not remote_dir:
            return
            
        self.set_status("Configuring auto-start on remote machine...")
        self.console.append("\n--- Setting up LinuxCNC Auto-Start ---")
        
        # Evaluate ~ to $HOME purely for the bash script pathing
        bash_remote_dir = remote_dir.replace("~", "$HOME")

        script_content = f"""#!/bin/bash
exec > $HOME/linuxcnc_autostart.log 2>&1
echo "=== LinuxCNC Autostart Initiated at $(date) ==="

# Give the Linux Desktop Environment plenty of time to load before drawing a Window
sleep 10

export DISPLAY=:0
export XAUTHORITY=$HOME/.Xauthority
export PATH=$PATH:/usr/bin:/usr/local/bin:$HOME/.local/bin

echo "Navigating to {bash_remote_dir}"
cd {bash_remote_dir} || {{ echo "Failed to cd into {bash_remote_dir}. Check directory!"; exit 1; }}

echo "Checking Python environment:"
python3 -c "import PyQt6" || echo "WARNING: PyQt6 module missing for global python3!"

echo "Starting main.py..."
python3 main.py

echo "=== LinuxCNC Exited with code $? ==="
"""

        # Using a regular EOF (not 'EOF') allows bash to evaluate ${remote_dir/#\~/$HOME} correctly during write, 
        # but to keep the literal script pure, we'll write it via base64 to avoid cat << parsing issues.
        b64_script = base64.b64encode(script_content.encode('utf-8')).decode('utf-8')
        
        # For the .desktop file, the Exec path MUST be an absolute path!
        # Tilde (~) or $HOME are often ignored by Linux Desktop Entry parsers.
        desktop_remote_dir = remote_dir.replace("~", f"/home/{user}")
        
        cmd = f"""mkdir -p {remote_dir} && echo "{b64_script}" | base64 -d > {remote_dir}/autostart.sh
chmod +x {remote_dir}/autostart.sh
mkdir -p ~/.config/autostart && cat << 'EOF' > ~/.config/autostart/linuxcnc.desktop
[Desktop Entry]
Type=Application
Exec={desktop_remote_dir}/autostart.sh
Hidden=false
NoDisplay=false
Terminal=false
X-GNOME-Autostart-enabled=true
Name=LinuxCNC Pro
Comment=Launch LinuxCNC on boot
EOF
chmod +x ~/.config/autostart/linuxcnc.desktop"""
        
        out, err = self.execute_remote_command(cmd)
        
        if err and ("error" in err.lower() or "denied" in err.lower()):
            QMessageBox.critical(self, "Failed", f"Failed to setup auto-start:\n{err}")
        else:
            self.console.append("✅ Auto-start configured successfully!")
            QMessageBox.information(self, "Success", f"LinuxCNC will now start automatically when {ip} boots!")
            
        self.set_status("Ready.")

    def setup_nic_ip(self):
        iface, ok = QInputDialog.getText(self, "Network Interface", "Enter your PC's Ethernet interface name (e.g. eth0, enp1s0):", text="eth0")
        if not ok or not iface: return
        
        self.set_status(f"Configuring {iface} to 10.10.10.1...")
        cmd = f"ip addr flush dev {iface} ; ip addr add 10.10.10.1/24 dev {iface} ; ip link set dev {iface} up"
        out, err = self.execute_remote_command(cmd, use_sudo=True)
        
        if err and ("Cannot find device" in err or "error" in err.lower()):
            QMessageBox.critical(self, "Error", f"Failed to configure {iface}. Is the interface name correct?\n\n{err}")
        else:
            self.console.append(f"✅ {iface} configured with IP 10.10.10.1!")
            QMessageBox.information(self, "Success", f"Network interface {iface} is now temporarily configured to 10.10.10.1.\n\nTry reading the HMID again!\n(Note: To make this persistent across reboots, use NetworkManager on the Debian machine).")
        self.set_status("Ready.")

    def optimize_nic(self):
        iface, ok = QInputDialog.getText(self, "Network Interface", "Enter your Intel NIC name on the remote machine (e.g. eth0, enp1s0):", text="enp1s0")
        if not ok or not iface: return
        
        self.set_status(f"Disabling interrupt coalescing on {iface}...")
        cmd = f"apt-get install ethtool -y; ethtool -C {iface} rx-usecs 0 tx-usecs 0"
        out, err = self.execute_remote_command(cmd, use_sudo=True)
        
        if err and "Cannot get" in err:
            QMessageBox.critical(self, "Error", f"Failed to optimize {iface}. Is the interface name correct?\n\n{err}")
        else:
            self.console.append(f"✅ Interrupt coalescing disabled on {iface}!")
            QMessageBox.information(self, "Success", f"Network optimization applied to {iface}.\n\nTry launching LinuxCNC now!")
        self.set_status("Ready.")
        
    def optimize_os(self):
        confirm = QMessageBox.question(self, "Optimize OS for LinuxCNC", 
                                       "This will apply standard CNC optimizations to the remote OS:\n\n"
                                       "1. Set CPU governor to 'performance'.\n"
                                       "2. Disable CPU deep sleep (C-States) in GRUB.\n"
                                       "3. Disable background services (Bluetooth, Printing, ModemManager).\n\n"
                                       "A reboot will be required afterward.\n\nDo you want to proceed?", 
                                       QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if confirm != QMessageBox.StandardButton.Yes:
            return

        self.set_status("Applying OS optimizations...")
        self.console.append("\n--- Running CNC OS Optimizer ---")
        
        script = """#!/bin/bash
# 1. CPU Governor (Modern Debian uses linux-cpupower)
apt-get install -y linux-cpupower cpufrequtils zenity 2>/dev/null || true
if command -v cpupower >/dev/null 2>&1; then
    cpupower frequency-set -g performance >/dev/null 2>&1 || true
fi
# Fallback to direct sysfs write
for cpu in /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor; do echo performance > $cpu 2>/dev/null || true; done

# 2. Disable Services & Sockets
systemctl disable bluetooth cups ModemManager avahi-daemon cups.socket avahi-daemon.socket 2>/dev/null || true
systemctl stop bluetooth cups ModemManager avahi-daemon cups.socket avahi-daemon.socket 2>/dev/null || true

# 3. Disable C-States in GRUB
if ! grep -q "processor.max_cstate=0" /etc/default/grub; then
    sed -i 's/GRUB_CMDLINE_LINUX_DEFAULT="/GRUB_CMDLINE_LINUX_DEFAULT="intel_idle.max_cstate=0 processor.max_cstate=0 idle=poll /' /etc/default/grub
    update-grub
    echo "GRUB updated. Reboot required for C-State changes."
else
    echo "GRUB already optimized."
fi
echo "OS Optimization Complete!"
"""
        
        b64_script = base64.b64encode(script.encode()).decode()
        cmd = f"echo {b64_script} | base64 -d > /tmp/optimize.sh && chmod +x /tmp/optimize.sh && /tmp/optimize.sh"
        out, err = self.execute_remote_command(cmd, use_sudo=True)
        
        if err and "error" in err.lower() and "sudo" not in err.lower():
            QMessageBox.warning(self, "Warning", f"Script ran but reported some warnings:\n{err}")
        else:
            QMessageBox.information(self, "Success", "OS Optimization script completed!\n\nPlease reboot the Linux machine for the GRUB (CPU Sleep) changes to take full effect.")
            
        self.set_status("Ready.")
        
    def setup_passwordless_sudo(self):
        user = self.user_input.text().strip()
        if not user:
            QMessageBox.warning(self, "Missing Username", "Please enter the SSH Username first.")
            return

        confirm = QMessageBox.question(self, "Setup Passwordless Sudo", 
                                       f"This will configure the remote OS to allow the user '{user}' to run 'sudo' commands without entering a password.\n\n"
                                       "This is highly recommended for dedicated CNC controllers to prevent silent background failures when executing hardware commands.\n\n"
                                       "Do you want to proceed?", 
                                       QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if confirm != QMessageBox.StandardButton.Yes:
            return

        self.set_status("Configuring passwordless sudo...")
        self.console.append(f"\n--- Setting up Passwordless Sudo for '{user}' ---")
        
        script = f"#!/bin/bash\necho '{user} ALL=(ALL) NOPASSWD: ALL' > /tmp/99_cnc_nopasswd\nchmod 0440 /tmp/99_cnc_nopasswd\nchown root:root /tmp/99_cnc_nopasswd\nmv /tmp/99_cnc_nopasswd /etc/sudoers.d/99_cnc_nopasswd\n"
        b64_script = base64.b64encode(script.encode()).decode()
        
        cmd = f"echo {b64_script} | base64 -d > /tmp/nopasswd.sh && chmod +x /tmp/nopasswd.sh && /tmp/nopasswd.sh"
        
        out, err = self.execute_remote_command(cmd, use_sudo=True)
        
        if err and "error" in err.lower() and "sudo" not in err.lower():
            QMessageBox.critical(self, "Failed", f"Failed to configure passwordless sudo:\n{err}")
        else:
            self.console.append("✅ Passwordless sudo configured successfully!")
            QMessageBox.information(self, "Success", f"User '{user}' can now execute sudo commands without a password!\n\nYou will no longer be prompted when running hardware diagnostics or network configs.")
            
        self.set_status("Ready.")

    def _save_secure_backup_manifest(self, agent_client, kind, default_name):
        """Save a checksum manifest for secure backup verification."""
        backup_dir = os.path.join(os.getcwd(), "backups")
        os.makedirs(backup_dir, exist_ok=True)
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Save Secure Backup Manifest",
            os.path.join(backup_dir, default_name),
            "JSON Files (*.json)",
        )
        if not path:
            return
        try:
            manifest = agent_client.backup_manifest(kind)
            with open(path, "w", encoding="utf-8") as manifest_file:
                json.dump(manifest, manifest_file, indent=2)
            self.console.append(f"Secure {kind} backup manifest saved to {path}")
            QMessageBox.information(self, "Manifest Saved", "Backup inventory and checksums were saved locally.")
        except Exception as error:
            QMessageBox.critical(self, "Backup Manifest Failed", str(error))

    def _download_secure_archive(self, agent_client, kind, default_name):
        """Download a verified secure archive and save it locally."""
        backup_dir = os.path.join(os.getcwd(), "backups")
        os.makedirs(backup_dir, exist_ok=True)
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Save Secure Backup",
            os.path.join(backup_dir, default_name),
            "Archives (*.tar.gz)",
        )
        if not path:
            return
        try:
            content, checksum = agent_client.download_backup_archive(kind)
            with open(path, "wb") as archive_file:
                archive_file.write(content)
            self.console.append(f"Secure {kind} backup saved to {path} ({checksum})")
            QMessageBox.information(self, "Backup Saved", f"Backup saved successfully.\nSHA-256: {checksum}")
        except Exception as error:
            QMessageBox.critical(self, "Backup Failed", str(error))