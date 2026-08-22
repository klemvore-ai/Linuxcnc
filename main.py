import sys
import os
import threading
from PyQt6.QtWidgets import QApplication, QMainWindow, QTabWidget, QWidget, QVBoxLayout, QLabel, QMessageBox
from PyQt6.QtCore import QTimer, QThread, pyqtSignal
from PyQt6.QtGui import QShortcut, QKeySequence, QIcon

HAS_PSUTIL = False
try:
    import psutil
    HAS_PSUTIL = True
except ImportError:
    pass

HAS_PARAMIKO = False
try:
    import paramiko
    HAS_PARAMIKO = True
except ImportError:
    pass

LOCAL_IPS = ['localhost', '127.0.0.1']
try:
    import subprocess
    _out = subprocess.check_output("hostname -I", shell=True, text=True, stderr=subprocess.DEVNULL)
    LOCAL_IPS.extend(_out.split())
except:
    pass

def is_local_ip(ip):
    return ip in LOCAL_IPS

# Import modules
from settings_manager import SettingsManager
from core.lifecycle import LinuxCNCLifecycle
from core.local_lifecycle import LocalLinuxCNCBackend
from core.machine_agent import client_from_settings
from core.remote_lifecycle import RemoteLinuxCNCBackend
from tabs.dashboard import MachineDashboard
from tabs.settings_tab import SettingsTab
from tabs.library_tab import LibraryTab
from tabs.wiring_tab import WiringTab
from tabs.layout_editor_tab import LayoutEditorTab
from tabs.mesa_util_tab import MesaUtilTab
from tabs.tool_table_tab import ToolTableTab
from tabs.hal_show_tab import HalShowTab
from tabs.hal_scope_tab import HalScopeTab
from tabs.macro_editor_tab import MacroEditorTab

class SSHSyncWorker(QThread):
    sync_complete = pyqtSignal(bool, str)
    
    def __init__(self, ip, user, password, remote_path):
        super().__init__()
        self.ip = ip
        self.user = user
        self.password = password
        self.remote_path = remote_path
        
    def run(self):
        if not self.ip or not self.user:
            self.sync_complete.emit(False, "No credentials")
            return
            
        remote_file = f"{self.remote_path.rstrip('/')}/settings.ini"
        local_file = os.path.join(os.getcwd(), "settings.ini")
        
        # Don't overwrite local dev credentials when pulling
        import configparser
        local_config = configparser.ConfigParser()
        local_config.read(local_file)
        
        try:
            if HAS_PARAMIKO:
                ssh = paramiko.SSHClient()
                ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                ssh.connect(self.ip, username=self.user, password=self.password, timeout=3, look_for_keys=not bool(self.password), allow_agent=not bool(self.password))
                sftp = ssh.open_sftp()
                sftp_file = remote_file.replace("~/", f"/home/{self.user}/", 1) if remote_file.startswith("~/") else remote_file
                sftp.get(sftp_file, local_file)
                sftp.close()
                ssh.close()
            else:
                res = subprocess.run(["scp", "-o", "BatchMode=yes", "-o", "ConnectTimeout=3", f"{self.user}@{self.ip}:{remote_file}", local_file], capture_output=True, timeout=5)
                if res.returncode != 0:
                    self.sync_complete.emit(False, "SCP error")
                    return
                    
            # Protect local SSH credentials from being overwritten by remote's config
            temp_config = configparser.ConfigParser()
            temp_config.read(local_file)
            if "SSH" not in temp_config: temp_config["SSH"] = {}
            temp_config["SSH"]["ip_address"] = self.ip
            temp_config["SSH"]["username"] = self.user
            temp_config["SSH"]["password"] = self.password
            with open(local_file, "w") as f:
                temp_config.write(f)
                
            self.sync_complete.emit(True, "Synced from Control Machine")
        except Exception as e:
            self.sync_complete.emit(False, str(e))

class CNCFusionApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("CNC Fusion Pro - Master Control")
        self.resize(1400, 900)

        # Set Application Icon
        self.setWindowIcon(QIcon("assets/CNCFusionWindowsicon.ico"))

        # Initialize the Settings Manager
        self.settings = SettingsManager("settings.ini")

        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        layout = QVBoxLayout(central_widget)
        
        # System Monitor Status Bar
        self.status_bar = self.statusBar()
        self.status_bar.setStyleSheet("background-color: #1e1e1e; border-top: 1px solid #333;")
        
        self.sync_status_lbl = QLabel("Checking connection...")
        self.sync_status_lbl.setStyleSheet("color: #f39c12; font-weight: bold; padding-left: 15px;")
        self.status_bar.addWidget(self.sync_status_lbl)
        
        self.sys_monitor_lbl = QLabel("Initializing System Monitor...")
        self.sys_monitor_lbl.setStyleSheet("color: #ecf0f1; font-weight: bold; padding-right: 15px;")
        self.status_bar.addPermanentWidget(self.sys_monitor_lbl)

        self.tabs = QTabWidget()
        self.tabs.setStyleSheet("""
            QTabBar::tab { background: #2f3640; color: white; padding: 10px; }
            QTabBar::tab:selected { background: #341f97; font-weight: bold; }
        """)

        # Instantiate Tabs (Order matters for dependencies)
        self.library_tab = LibraryTab(self)
        self.dashboard_tab = MachineDashboard(self.settings)
        self.wiring_tab = WiringTab(self.library_tab)
        self.layout_editor_tab = LayoutEditorTab(self)
        self.mesa_util_tab = MesaUtilTab(self.library_tab)
        self.tool_table_tab = ToolTableTab(self.settings)
        self.hal_show_tab = HalShowTab(self.settings)
        self.hal_show_tab.parent_app = self
        self.hal_scope_tab = HalScopeTab(self.settings)
        self.hal_scope_tab.parent_app = self
        self.macro_editor_tab = MacroEditorTab(self.settings)
        self.settings_tab = SettingsTab(self.settings, self)

        # Wire up settings changed signal
        self.settings_tab.settings_changed.connect(self.dashboard_tab.update_local_settings)
        self.settings_tab.load_custom_ui_requested.connect(self.dashboard_tab.load_custom_ui)
        self.settings_tab.restore_default_ui_requested.connect(self.dashboard_tab.restore_default_ui)
        

        # Define all available tabs
        self.all_tabs = [
            (self.dashboard_tab, "🖥️ DASHBOARD"),
            (self.tool_table_tab, "🧰 TOOL TABLE"),
            (self.macro_editor_tab, "📝 MACROS"),
            (self.library_tab, "📚 COMPONENT LIBRARY"),
            (self.wiring_tab, "🔌 LOGIC DESIGNER"),
            (self.hal_show_tab, "🔎 HAL MONITOR"),
            (self.hal_scope_tab, "📈 HAL OSCILLOSCOPE"),
            (self.layout_editor_tab, "🎨 UI DESIGNER"),
            (self.mesa_util_tab, "🛠️ MESA UTILITIES"),
            (self.settings_tab, "⚙️ SETTINGS")
        ]
        
        self.op_mode = self.settings.get_setting('MACHINE_CONFIG', 'operator_mode', 'False') == 'True'
        self.apply_tab_visibility()
        
        layout.addWidget(self.tabs)
        
        self.op_shortcut = QShortcut(QKeySequence("Ctrl+Shift+O"), self)
        self.op_shortcut.activated.connect(self.toggle_operator_mode)
        
        self.monitor_timer = QTimer(self)
        self.monitor_timer.timeout.connect(self.update_system_monitor)
        self.monitor_timer.start(2000) # Update every 2 seconds

        # Kick off background Auto Sync
        self.perform_auto_sync()

    def perform_auto_sync(self):
        ip = self.settings.get_setting("SSH", "ip_address", "")
        user = self.settings.get_setting("SSH", "username", "")
        password = self.settings.get_setting("SSH", "password", "")
        remote_path = self.settings.get_setting("PATHS", "last_remote_cncfusion_dir", "~/CNCFusion")
        
        if ip and user and not is_local_ip(ip):
            self.sync_worker = SSHSyncWorker(ip, user, password, remote_path)
            self.sync_worker.sync_complete.connect(self.on_auto_sync_complete)
            self.sync_worker.start()
        else:
            self.sync_status_lbl.setText("Local Dev Mode")
            self.sync_status_lbl.setStyleSheet("color: #3498db; font-weight: bold; padding-left: 15px;")
            
    def on_auto_sync_complete(self, success, msg):
        if success:
            self.sync_status_lbl.setText("🟢 Synced with Control Machine")
            self.sync_status_lbl.setStyleSheet("color: #27ae60; font-weight: bold; padding-left: 15px;")
            self.settings_tab.reload_ui_from_settings()
        else:
            if "No credentials" in msg:
                self.sync_status_lbl.setText("Connect via Setup Wizard")
                self.sync_status_lbl.setStyleSheet("color: #7f8c8d; font-weight: bold; padding-left: 15px;")
            else:
                self.sync_status_lbl.setText("🟡 Control Machine Offline (Using Local Mode)")
                self.sync_status_lbl.setStyleSheet("color: #f1c40f; font-weight: bold; padding-left: 15px;")

    def apply_tab_visibility(self):
        self.tabs.clear()
        if self.op_mode:
            self.tabs.addTab(self.all_tabs[0][0], self.all_tabs[0][1])
        else:
            for widget, title in self.all_tabs:
                self.tabs.addTab(widget, title)
                
    def toggle_operator_mode(self):
        self.op_mode = not self.op_mode
        self.apply_tab_visibility()
        mode_str = "ON (Press Ctrl+Shift+O to unlock)" if self.op_mode else "OFF"
        self.sys_monitor_lbl.setText(f"Operator Mode toggled {mode_str}")
        QTimer.singleShot(3000, self.update_system_monitor)

    def closeEvent(self, event):
        reply = QMessageBox.question(
            self, 
            "Exit CNC Fusion", 
            "Do you want to shut down the LinuxCNC machine as well?\n\n"
            "• 'Yes' will stop the machine and close this app.\n"
            "• 'No' will leave the machine running in the background and close this app.\n"
            "• 'Cancel' will return to the app.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No | QMessageBox.StandardButton.Cancel
        )
        
        if reply == QMessageBox.StandardButton.Cancel:
            event.ignore()
            return

        if reply == QMessageBox.StandardButton.Yes:
            # Stop the monitor timer so it doesn't overwrite our status message
            self.monitor_timer.stop()
            self.sys_monitor_lbl.setText("⏳ Shutting down LinuxCNC smoothly... please wait.")
            self.sys_monitor_lbl.setStyleSheet("color: #f39c12; font-weight: bold; font-size: 14px; padding-right: 15px;")
            QApplication.processEvents()
            
            ip = self.settings.get_setting("SSH", "ip_address", "")
            user = self.settings.get_setting("SSH", "username", "")
            password = self.settings.get_setting("SSH", "password", "")
            
            base_cmd = "if pgrep linuxcnc > /dev/null; then python3 -c 'import linuxcnc; c=linuxcnc.command(); c.exit()' 2>/dev/null; for i in 1 2 3 4 5 6 7 8 9 10; do if ! pgrep linuxcnc > /dev/null; then break; fi; sleep 1; done; if pgrep linuxcnc > /dev/null; then killall -TERM linuxcnc linuxcncsvr milltask io 2>/dev/null; sleep 2; fi; if pgrep linuxcnc > /dev/null; then halrun -U 2>/dev/null; killall -9 linuxcnc linuxcncsvr milltask io halrun rtapi_app halcmd 2>/dev/null; rm -rf /tmp/linuxcnc.lock* /tmp/linuxcnc.print /tmp/linuxcnc.error 2>/dev/null; rm -f /dev/shm/linuxcnc* /dev/shm/rtapi* /dev/shm/hal* 2>/dev/null; fi; fi; pkill -TERM -f cncfusion_gui 2>/dev/null"
            
            if is_local_ip(ip):
                print("Shutting down local LinuxCNC instance...")
                try:
                    lifecycle = LinuxCNCLifecycle(LocalLinuxCNCBackend())
                    result = lifecycle.graceful_shutdown(timeout=12.0)
                    print(result.message)
                except Exception as e:
                    print(f"Failed to shutdown local LinuxCNC: {e}")
            elif client_from_settings(self.settings) is not None:
                print("Shutting down LinuxCNC through the secure machine agent...")
                try:
                    agent_client = client_from_settings(self.settings)
                    lifecycle = LinuxCNCLifecycle(RemoteLinuxCNCBackend(agent_client))
                    result = lifecycle.graceful_shutdown(timeout=12.0)
                    print(result.message)
                except Exception as e:
                    print(f"Failed to shutdown through the machine agent: {e}")
            elif HAS_PARAMIKO and ip and user:
                print(f"Shutting down remote LinuxCNC instance at {ip}...")
                def kill_remote():
                    try:
                        ssh = paramiko.SSHClient()
                        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                        ssh.connect(ip, username=user, password=password, timeout=3, banner_timeout=5, auth_timeout=5, look_for_keys=not bool(password), allow_agent=not bool(password))
                        if password:
                            cmd = f"echo '{password}' | sudo -S bash -c \"{base_cmd}\""
                        else:
                            cmd = f"sudo bash -c \"{base_cmd}\""
                        ssh.exec_command(cmd)
                        ssh.close()
                    except Exception as e:
                        print(f"Failed to shutdown remote LinuxCNC: {e}")
                
                t = threading.Thread(target=kill_remote, daemon=True)
                t.start()
                t.join(timeout=4.0)
                
        super().closeEvent(event)

    def update_tool_setter_loc(self):
        x = self.dashboard_tab.mock_coords.get('X', 0.0)
        y = self.dashboard_tab.mock_coords.get('Y', 0.0)
        self.settings_tab.tool_setter_x.setValue(x)
        self.settings_tab.tool_setter_y.setValue(y)
        
        self.settings.save_setting('PROBING', 'tool_setter_x', x)
        self.settings.save_setting('PROBING', 'tool_setter_y', y)
        
        self.settings_tab.btn_set_ts_loc.setText("✅ Location Saved!")
        QTimer.singleShot(2000, lambda: self.settings_tab.btn_set_ts_loc.setText("📍 Set to Current Machine Location"))

    def update_system_monitor(self):
        mem_str = "RAM: (pip install psutil)"
        if HAS_PSUTIL:
            try:
                process = psutil.Process(os.getpid())
                mem_mb = process.memory_info().rss / (1024 * 1024)
                mem_str = f"RAM: {mem_mb:.1f} MB"
            except Exception:
                pass
                
        threads = threading.active_count()
        
        logic_items = len(self.wiring_tab.canvas.scene.items()) if hasattr(self, 'wiring_tab') else 0
        ui_items = len(self.layout_editor_tab.canvas.scene.items()) if hasattr(self, 'layout_editor_tab') else 0
        tp_items = len(self.dashboard_tab.toolpath_viewer.scene.items()) if hasattr(self.dashboard_tab, 'toolpath_viewer') and hasattr(self.dashboard_tab.toolpath_viewer, 'scene') else 0
        
        self.sys_monitor_lbl.setText(f"🖥️ System Diagnostics  |  {mem_str}  |  Active Threads: {threads}  |  Scene Objects ➔ Logic: {logic_items} | UI: {ui_items} | Toolpath: {tp_items}")

if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    
    # Check for existing background instance to prevent duplicates
    from PyQt6.QtCore import QSharedMemory
    shared_mem = QSharedMemory("CNCFusionPro_Inst_Lock")
    if shared_mem.attach() or not shared_mem.create(1):
        QMessageBox.warning(None, "Application Already Running", "An instance of CNC Fusion is already running.", QMessageBox.StandardButton.Ok)
        sys.exit(0)
        
    app.setWindowIcon(QIcon("assets/CNCFusionWindowsicon.ico"))
    window = CNCFusionApp()
    
    # Close PyInstaller splash screen once the UI is ready
    try:
        import pyi_splash # type: ignore
        pyi_splash.close()
    except ImportError:
        pass
        
    window.show()
    sys.exit(app.exec())