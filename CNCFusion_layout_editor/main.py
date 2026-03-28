import sys
import os
import threading
from PyQt6.QtWidgets import QApplication, QMainWindow, QTabWidget, QWidget, QVBoxLayout, QMessageBox, QLabel
from PyQt6.QtGui import QAction, QKeySequence, QIcon
from PyQt6.QtCore import QTimer

HAS_PSUTIL = False
try:
    import psutil
    HAS_PSUTIL = True
except ImportError:
    pass

# Import standalone modules
from settings_manager import SettingsManager
from dashboard import MachineDashboard
from layout_editor_tab import LayoutEditorTab

class EditorApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("CNC Fusion Pro - UI Designer Edition (Mac)")
        self.resize(1200, 800)
        
        # Set Application Icon
        self.setWindowIcon(QIcon("assets/CNCFusionWindowsicon.ico"))
        
        # Initialize the Settings Manager for standalone
        self.settings = SettingsManager("editor_settings.ini")

        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        layout = QVBoxLayout(central_widget)
        
        # System Monitor Status Bar
        self.status_bar = self.statusBar()
        self.status_bar.setStyleSheet("background-color: #1e1e1e; border-top: 1px solid #333;")
        self.sys_monitor_lbl = QLabel("Initializing System Monitor...")
        self.sys_monitor_lbl.setStyleSheet("color: #ecf0f1; font-weight: bold; padding-right: 15px;")
        self.status_bar.addPermanentWidget(self.sys_monitor_lbl)

        self.tabs = QTabWidget()
        self.tabs.setStyleSheet("""
            QTabBar::tab { background: #2f3640; color: white; padding: 10px; }
            QTabBar::tab:selected { background: #341f97; font-weight: bold; }
        """)

        self.dashboard_tab = MachineDashboard(self.settings)
        self.layout_editor_tab = LayoutEditorTab(self)

        self.tabs.addTab(self.layout_editor_tab, "LAYOUT EDITOR")
        self.tabs.addTab(self.dashboard_tab, "DASHBOARD PREVIEW")

        layout.addWidget(self.tabs)

        self.create_menu_bar()
        
        fullscreen_action = QAction("Fullscreen", self)
        fullscreen_action.setShortcut(QKeySequence("F11"))
        fullscreen_action.triggered.connect(self.toggle_fullscreen)
        self.addAction(fullscreen_action)
        
        self.monitor_timer = QTimer(self)
        self.monitor_timer.timeout.connect(self.update_system_monitor)
        self.monitor_timer.start(2000) # Update every 2 seconds

    def closeEvent(self, event):
        # Intercept the exit command to check for unsaved layout changes
        if hasattr(self, 'layout_editor_tab') and self.layout_editor_tab.has_unsaved_changes:
            reply = QMessageBox.question(self, 'Unsaved Changes',
                                         "You have unsaved changes to your layout.\nAre you sure you want to exit without saving?",
                                         QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                         QMessageBox.StandardButton.No)
            if reply == QMessageBox.StandardButton.Yes:
                event.accept()
            else:
                event.ignore()
        else:
            event.accept()
            
    def toggle_fullscreen(self):
        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()

    def create_menu_bar(self):
        menu_bar = self.menuBar()
        
        file_menu = menu_bar.addMenu("&File")
        
        new_action = QAction("&New Layout (Clear)", self)
        new_action.setShortcut(QKeySequence("Ctrl+N"))
        new_action.triggered.connect(self.layout_editor_tab.clear_canvas)
        file_menu.addAction(new_action)
        
        exit_action = QAction("E&xit", self)
        exit_action.setShortcut(QKeySequence("Ctrl+Q"))
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)
        
        edit_menu = menu_bar.addMenu("&Edit")
        
        undo_action = QAction("&Undo Layout Action", self)
        undo_action.triggered.connect(self.layout_editor_tab.undo)
        
        redo_action = QAction("&Redo Layout Action", self)
        redo_action.triggered.connect(self.layout_editor_tab.redo)
        
        edit_menu.addAction(undo_action)
        edit_menu.addAction(redo_action)
        
        help_menu = menu_bar.addMenu("&Help")
        
        about_action = QAction("About CNC Fusion Pro...", self)
        about_action.triggered.connect(self.show_about_dialog)
        help_menu.addAction(about_action)

    def show_about_dialog(self):
        QMessageBox.about(self, "About", 
                          "<h3>CNC Fusion Pro - UI Designer Edition</h3>"
                          "<p>A standalone visual editor for crafting custom LinuxCNC operator panels and generating logic maps.</p>")
                          
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
        
        ui_items = len(self.layout_editor_tab.canvas.scene.items()) if hasattr(self, 'layout_editor_tab') else 0
        tp_items = len(self.dashboard_tab.toolpath_viewer.scene.items()) if hasattr(self.dashboard_tab, 'toolpath_viewer') and hasattr(self.dashboard_tab.toolpath_viewer, 'scene') else 0
        
        self.sys_monitor_lbl.setText(f"🖥️ System Diagnostics  |  {mem_str}  |  Active Threads: {threads}  |  Scene Objects ➔ UI: {ui_items} | Toolpath: {tp_items}")

if __name__ == "__main__":
    app = QApplication(sys.argv)
    # Fusion style looks great cross-platform
    app.setStyle("Fusion")
    app.setWindowIcon(QIcon("assets/CNCFusionWindowsicon.ico"))
    window = EditorApp()
    
    # Close PyInstaller splash screen once the UI is ready
    try:
        import pyi_splash # type: ignore
        pyi_splash.close()
    except ImportError:
        pass
        
    window.show()
    sys.exit(app.exec())