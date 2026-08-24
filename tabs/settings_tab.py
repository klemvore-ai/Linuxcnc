import os
import json
import subprocess
import tempfile
import tarfile
import platform
import shutil
import base64
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QFormLayout, QTabWidget, QDoubleSpinBox, QSpinBox, QApplication,
                             QPushButton, QGroupBox, QLabel, QComboBox, QLineEdit, QMessageBox, QScrollArea, QInputDialog, QDialog, QTextEdit, QCheckBox)
from PyQt6.QtCore import pyqtSignal, QTimer, QUrl, QEvent, Qt
from PyQt6.QtGui import QDesktopServices
from core.lifecycle import LinuxCNCLifecycle
from core.machine_agent import client_from_settings
from core.remote_lifecycle import RemoteLinuxCNCBackend
from core.config_model import MachineConfig
from core.hal_documents import HALDocumentComposer
from tabs.setup_wizard import SetupWizard

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

class NoScrollComboBox(QComboBox):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def wheelEvent(self, event):
        if self.view() and self.view().isVisible():
            super().wheelEvent(event)
        else:
            event.ignore()

class NoScrollSpinBox(QSpinBox):
    def wheelEvent(self, event):
        event.ignore()

class NoScrollDoubleSpinBox(QDoubleSpinBox):
    def wheelEvent(self, event):
        event.ignore()

class SettingsTab(QWidget):
    # This is the signal that tells the Dashboard to wake up
    settings_changed = pyqtSignal()
    load_custom_ui_requested = pyqtSignal()
    restore_default_ui_requested = pyqtSignal()

    def __init__(self, settings_manager, parent=None):
        super().__init__(parent)
        self.sm = settings_manager
        self.init_ui()  # <--- This is what the error is looking for!

    def reload_ui_from_settings(self):
        """Called after an auto-sync or manual pull to hot-reload the Settings screen"""
        self.sm.reload()
        if self.layout() is not None:
            # Trick to delete the old layout and all its widgets
            QWidget().setLayout(self.layout())
        self.init_ui()
        self.settings_changed.emit() # Alert other tabs that settings updated

    def init_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        
                # We will separate the panel into Tab categories
        # Horizontal Split: Left is Tabs, Right is Action Sidebar
        horizontal_split = QHBoxLayout()
        main_layout.addLayout(horizontal_split)
        
        # Horizontal Split: Left is Action Sidebar, Right is Tabs
        horizontal_split = QHBoxLayout()
        main_layout.addLayout(horizontal_split)
        
        self.settings_tabs = QTabWidget()
        self.settings_tabs.setStyleSheet("""
            QTabWidget { border: none; }
            QTabWidget::pane { border: 1px solid #444; border-radius: 4px; margin-top: 10px; background-color: #1e1e1e; }
            QTabBar::tab { background-color: #252526; color: #bbbbbb; padding: 10px 20px; border: 1px solid #444; border-bottom: none; border-top-left-radius: 4px; border-top-right-radius: 4px; margin-right: 2px; font-weight: bold; }
            QTabBar::tab:selected { background-color: #1e1e1e; color: #00ff00; border-top: 2px solid #00ff00; }
            QTabBar::tab:hover { background-color: #333333; }
        """)

        def make_tab():
            tab = QWidget()
            layout = QVBoxLayout(tab)
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setStyleSheet('QScrollArea { border: none; background: transparent; }')
            container = QWidget()
            cont_layout = QVBoxLayout(container)
            cont_layout.setContentsMargins(10, 10, 10, 10)
            scroll.setWidget(container)
            layout.addWidget(scroll)
            return tab, cont_layout

        tab_machine, layout_machine = make_tab()
        self.settings_tabs.addTab(tab_machine, 'Machine Setup Wizard')


        tab_features, layout_features = make_tab()
        self.settings_tabs.addTab(tab_features, 'Operations & Jobs')

        tab_ui, layout_ui = make_tab()
        self.settings_tabs.addTab(tab_ui, 'UI & Preferences')

        layout = layout_machine  # fallback for anything else
        
        # Apply the Dark Mode Styling immediately
        self.setStyleSheet("""
            QWidget { background-color: #1e1e1e; color: #ffffff; }
            QGroupBox { font-weight: bold; border: 1px solid #444444; margin-top: 15px; padding-top: 15px; padding-bottom: 10px; background-color: #252526; color: #00ff00; border-radius: 6px; }
            QDoubleSpinBox, QSpinBox, QComboBox, QLineEdit { background-color: #333333; color: white; border: 1px solid #555555; padding: 5px; border-radius: 3px; max-width: 250px; min-width: 150px; }
            QComboBox::drop-down { border-left: 1px solid #555555; }
            QLabel { color: #bbbbbb; }
            QCheckBox { color: #ecf0f1; font-weight: bold; margin-top: 5px; }
        """)

        # --- Machine Configuration Group (Wizard) ---
        config_group = QGroupBox("Machine Configuration (Setup Wizard)")
        config_form = QFormLayout()
        config_form.setHorizontalSpacing(15)
        config_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        
        self.dynamic_widgets = {'Y2': [], 'A': [], 'B': [], 'J3': [], 'J4': []}
        
        self.mach_name_input = QLineEdit(self.sm.get_setting('MACHINE_CONFIG', 'name', 'CNCFusion'))
        
        self.mach_kinematics_cb = NoScrollComboBox()
        self.mach_kinematics_cb.addItems(["XYZ (3-Axis)", "XYYZ (Gantry 3-Axis)", "XYZ A (4-Axis)", "XYZ AB (5-Axis)"])
        self.mach_kinematics_cb.setCurrentText(self.sm.get_setting('MACHINE_CONFIG', 'kinematics', 'XYZ (3-Axis)'))
        
        self.mach_units_cb = NoScrollComboBox()
        self.mach_units_cb.addItems(["Metric (mm)", "Imperial (inch)"])
        self.mach_units_cb.setCurrentText(self.sm.get_setting('MACHINE_CONFIG', 'units', 'Metric (mm)'))
        
        self.mach_drive_cb = NoScrollComboBox()
        self.mach_drive_cb.addItems(["Stepper", "Servo (Analog)", "Step/Dir Servo"])
        self.mach_drive_cb.setCurrentText(self.sm.get_setting('MACHINE_CONFIG', 'drive_type', 'Stepper'))
        
        self.mach_display_cb = NoScrollComboBox()
        self.mach_display_cb.addItems(["CNCFusion", "axis", "dummy", "gmoccapy", "qtdragon", "qtvcp"])
        self.mach_display_cb.setCurrentText(self.sm.get_setting('MACHINE_CONFIG', 'display_ui', 'CNCFusion'))
        
        self.mach_min_spindle_ovr = NoScrollDoubleSpinBox(); self.mach_min_spindle_ovr.setRange(0.0, 5.0); self.mach_min_spindle_ovr.setSingleStep(0.1)
        self.mach_min_spindle_ovr.setValue(float(self.sm.get_setting('MACHINE_CONFIG', 'min_spindle_override', 0.5)))
        
        self.mach_max_spindle_ovr = NoScrollDoubleSpinBox(); self.mach_max_spindle_ovr.setRange(0.0, 5.0); self.mach_max_spindle_ovr.setSingleStep(0.1)
        self.mach_max_spindle_ovr.setValue(float(self.sm.get_setting('MACHINE_CONFIG', 'max_spindle_override', 1.2)))
        
        self.mach_max_feed_ovr = NoScrollDoubleSpinBox(); self.mach_max_feed_ovr.setRange(0.0, 5.0); self.mach_max_feed_ovr.setSingleStep(0.1)
        self.mach_max_feed_ovr.setValue(float(self.sm.get_setting('MACHINE_CONFIG', 'max_feed_override', 2.0)))
        
        config_form.addRow("Machine Name:", self.mach_name_input)
        config_form.addRow("Kinematics / Axes:", self.mach_kinematics_cb)
        config_form.addRow("Default Units:", self.mach_units_cb)
        config_form.addRow("Drive Type:", self.mach_drive_cb)
        config_form.addRow("Display UI:", self.mach_display_cb)
        
        spin_ovr_layout = QHBoxLayout(); spin_ovr_layout.addWidget(QLabel("Min:")); spin_ovr_layout.addWidget(self.mach_min_spindle_ovr); spin_ovr_layout.addWidget(QLabel("Max:")); spin_ovr_layout.addWidget(self.mach_max_spindle_ovr); spin_ovr_layout.addStretch()
        config_form.addRow("Spindle Overrides:", spin_ovr_layout)
        config_form.addRow("Max Feed Override:", self.mach_max_feed_ovr)
        
        # --- Dense Grid: Axis Kinematics & Tuning ---
        def make_sb(val, min_v=-100000, max_v=100000, dec=3):
            sb = NoScrollDoubleSpinBox()
            sb.setRange(min_v, max_v)
            sb.setDecimals(dec)
            sb.setValue(float(val))
            sb.setStyleSheet("max-width: 80px; min-width: 70px;")
            return sb
            
        axis_tuning_group = QGroupBox("Axis Kinematics & Tuning")
        axis_tuning_group.setStyleSheet("QGroupBox { font-weight: bold; border: 1px solid #555555; background-color: #252526; border-radius: 4px; margin-top: 15px; padding-top: 15px; padding-bottom: 10px; color: #f39c12; } QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 5px; }")
        axis_grid = QGridLayout(axis_tuning_group)
        axis_grid.setHorizontalSpacing(15)
        axis_grid.setColumnStretch(9, 1)  # Apply stretch to an empty 10th column instead
        
        headers = ["Axis", "Min Limit", "Max Limit", "Max Vel", "Max Accel", "Steps/Rev", "Units/Rev", "Backlash", "Invert Dir"]
        for col, h in enumerate(headers):
            lbl = QLabel(h)
            lbl.setStyleSheet("color: #ecf0f1; font-weight: bold; padding-bottom: 5px;")
            axis_grid.addWidget(lbl, 0, col)
            
        self.axis_inputs = {}
        for row, ax in enumerate(['X', 'Y', 'Y2', 'Z', 'A', 'B'], start=1):
            key = ax
            inv_cb = QCheckBox()
            inv_cb.setChecked(self.sm.get_setting(f'AXIS_{key}', 'invert_dir', 'False') == 'True')
            
            if key == 'Y2':
                self.axis_inputs[key] = {
                    'invert': inv_cb
                }
                lbl = QLabel(f"<b>{ax} (Squaring)</b>")
                axis_grid.addWidget(lbl, row, 0)
                inherit_lbl = QLabel("<i>(Inherits from Y)</i>")
                axis_grid.addWidget(inherit_lbl, row, 1, 1, 7)
                axis_grid.addWidget(self.axis_inputs[key]['invert'], row, 8, Qt.AlignmentFlag.AlignCenter)
                self.dynamic_widgets['Y2'].extend([lbl, inherit_lbl, self.axis_inputs[key]['invert']])
            else:
                self.axis_inputs[key] = {
                    'min': make_sb(self.sm.get_setting(f'AXIS_{key}', 'min', 0.0 if key != 'Z' else -125.0)),
                    'max': make_sb(self.sm.get_setting(f'AXIS_{key}', 'max', 500.0 if key != 'Z' else 0.0)),
                    'vel': make_sb(self.sm.get_setting(f'AXIS_{key}', 'vel', 75.0 if key != 'Z' else 30.0), dec=2),
                    'accel': make_sb(self.sm.get_setting(f'AXIS_{key}', 'accel', 600.0 if key != 'Z' else 150.0), dec=2),
                    'steps': make_sb(self.sm.get_setting(f'AXIS_{key}', 'steps', 5000), dec=0),
                    'units': make_sb(self.sm.get_setting(f'AXIS_{key}', 'units', 10.0 if key != 'Z' else 4.0)),
                    'backlash': make_sb(self.sm.get_setting(f'AXIS_{key}', 'backlash', 0.0), dec=4),
                    'invert': inv_cb
                }
                lbl = QLabel(f"<b>{ax}</b>")
                axis_grid.addWidget(lbl, row, 0)
                axis_grid.addWidget(self.axis_inputs[key]['min'], row, 1)
                axis_grid.addWidget(self.axis_inputs[key]['max'], row, 2)
                axis_grid.addWidget(self.axis_inputs[key]['vel'], row, 3)
                axis_grid.addWidget(self.axis_inputs[key]['accel'], row, 4)
                axis_grid.addWidget(self.axis_inputs[key]['steps'], row, 5)
                axis_grid.addWidget(self.axis_inputs[key]['units'], row, 6)
                axis_grid.addWidget(self.axis_inputs[key]['backlash'], row, 7)
                axis_grid.addWidget(self.axis_inputs[key]['invert'], row, 8, Qt.AlignmentFlag.AlignCenter)
                
                if key in ['A', 'B']:
                    self.dynamic_widgets[key].extend([lbl, self.axis_inputs[key]['min'], self.axis_inputs[key]['max'], self.axis_inputs[key]['vel'], self.axis_inputs[key]['accel'], self.axis_inputs[key]['steps'], self.axis_inputs[key]['units'], self.axis_inputs[key]['backlash'], self.axis_inputs[key]['invert']])
            
        # --- Dense Grid: Homing Configuration ---
        homing_group = QGroupBox("Homing & Soft Limits")
        homing_group.setStyleSheet("QGroupBox { font-weight: bold; border: 1px solid #555555; background-color: #252526; border-radius: 4px; margin-top: 15px; padding-top: 15px; padding-bottom: 10px; color: #8e44ad; } QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 5px; }")
        homing_grid = QGridLayout(homing_group)
        homing_grid.setHorizontalSpacing(15)
        homing_grid.setColumnStretch(6, 1)
        
        h_headers = ["Axis", "Home Pos", "Home Offset", "Search Vel (Initial)", "Latch Vel (Slow)", "Sequence"]
        for col, h in enumerate(h_headers):
            lbl = QLabel(h)
            lbl.setStyleSheet("color: #ecf0f1; font-weight: bold; padding-bottom: 5px;")
            homing_grid.addWidget(lbl, 0, col)
            
        self.home_inputs = {}
        for row, ax in enumerate(['X', 'Y', 'Y2 (Squaring)', 'Z', 'A', 'B'], start=1):
            key = ax.split()[0].lower()
            is_z = (key == 'z')
            default_seq = {'z': 0, 'x': 1, 'y': 2, 'a': 3, 'b': 4}.get(key, 1)
            
            
            if ax == 'Y2 (Squaring)':
                self.home_inputs[ax] = {
                    'offset': make_sb(self.sm.get_setting('HOMING', f'{key}_offset', 0.0))
                }
                lbl = QLabel(f"<b>{ax}</b>")
                homing_grid.addWidget(lbl, row, 0)
                empty_lbl = QLabel("")
                inherit_lbl = QLabel("<i>(Inherits from Y)</i>")
                homing_grid.addWidget(empty_lbl, row, 1)
                homing_grid.addWidget(self.home_inputs[ax]['offset'], row, 2)
                homing_grid.addWidget(inherit_lbl, row, 3, 1, 3)
                self.dynamic_widgets['Y2'].extend([lbl, empty_lbl, self.home_inputs[ax]['offset'], inherit_lbl])
            else:
                self.home_inputs[ax] = {
                    'home': make_sb(self.sm.get_setting('HOMING', f'{key}_home', -5.0 if is_z else 5.0)),
                    'offset': make_sb(self.sm.get_setting('HOMING', f'{key}_offset', 0.0)),
                    'search': make_sb(self.sm.get_setting('HOMING', f'{key}_search', 5.0 if is_z else -5.0)),
                    'latch': make_sb(self.sm.get_setting('HOMING', f'{key}_latch', -1.0 if is_z else 1.0)),
                    'seq': make_sb(self.sm.get_setting('HOMING', f'{key}_seq', default_seq), dec=0)
                }
                lbl = QLabel(f"<b>{ax}</b>")
                homing_grid.addWidget(lbl, row, 0)
                homing_grid.addWidget(self.home_inputs[ax]['home'], row, 1)
                homing_grid.addWidget(self.home_inputs[ax]['offset'], row, 2)
                homing_grid.addWidget(self.home_inputs[ax]['search'], row, 3)
                homing_grid.addWidget(self.home_inputs[ax]['latch'], row, 4)
                homing_grid.addWidget(self.home_inputs[ax]['seq'], row, 5)
                
                widgets_to_track = [lbl, self.home_inputs[ax]['home'], self.home_inputs[ax]['offset'], self.home_inputs[ax]['search'], self.home_inputs[ax]['latch'], self.home_inputs[ax]['seq']]
                if ax in ['A', 'B']:
                    self.dynamic_widgets[ax.split()[0]].extend(widgets_to_track)
                
        # --- Global Drive Timings & Spindle Limits ---
        global_timing_group = QGroupBox("Global Drive Timings & Spindle")
        global_timing_group.setStyleSheet("QGroupBox { font-weight: bold; border: 1px solid #555555; background-color: #252526; border-radius: 4px; margin-top: 15px; padding-top: 15px; padding-bottom: 10px; color: #e74c3c; } QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 5px; }")
        timing_form = QFormLayout(global_timing_group)
        timing_form.setHorizontalSpacing(15)
        timing_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        
        self.glob_steplen = make_sb(self.sm.get_setting('GLOBAL_TIMINGS', 'steplen', 1000), dec=0)
        self.glob_stepspace = make_sb(self.sm.get_setting('GLOBAL_TIMINGS', 'stepspace', 1000), dec=0)
        self.glob_dirsetup = make_sb(self.sm.get_setting('GLOBAL_TIMINGS', 'dirsetup', 200), dec=0)
        self.glob_dirhold = make_sb(self.sm.get_setting('GLOBAL_TIMINGS', 'dirhold', 200), dec=0)
        sg_layout = QHBoxLayout(); sg_layout.addWidget(QLabel("StepLen:")); sg_layout.addWidget(self.glob_steplen); sg_layout.addWidget(QLabel("StepSpace:")); sg_layout.addWidget(self.glob_stepspace); sg_layout.addWidget(QLabel("DirSetup:")); sg_layout.addWidget(self.glob_dirsetup); sg_layout.addWidget(QLabel("DirHold:")); sg_layout.addWidget(self.glob_dirhold); sg_layout.addStretch()
        timing_form.addRow("StepGen Timing (ns):", sg_layout)
        
        self.glob_ferror = make_sb(self.sm.get_setting('GLOBAL_TIMINGS', 'ferror', 2.0))
        self.glob_min_ferror = make_sb(self.sm.get_setting('GLOBAL_TIMINGS', 'min_ferror', 0.5))
        ferr_layout = QHBoxLayout(); ferr_layout.addWidget(QLabel("Max Error:")); ferr_layout.addWidget(self.glob_ferror); ferr_layout.addWidget(QLabel("Min Error:")); ferr_layout.addWidget(self.glob_min_ferror); ferr_layout.addStretch()
        timing_form.addRow("Following Error (units):", ferr_layout)
        
        self.glob_spindle_max = make_sb(self.sm.get_setting('GLOBAL_TIMINGS', 'spindle_max_rpm', 24000), dec=0)
        self.glob_spindle_min = make_sb(self.sm.get_setting('GLOBAL_TIMINGS', 'spindle_min_rpm', 100), dec=0)
        spin_lim_layout = QHBoxLayout(); spin_lim_layout.addWidget(QLabel("Max RPM:")); spin_lim_layout.addWidget(self.glob_spindle_max); spin_lim_layout.addWidget(QLabel("Min RPM:")); spin_lim_layout.addWidget(self.glob_spindle_min); spin_lim_layout.addStretch()
        timing_form.addRow("Spindle Speed Limits:", spin_lim_layout)
        
        self.chk_enable_probe = QCheckBox("Enable Physical Probe Input (motion.probe-input)")
        self.chk_enable_probe.setChecked(self.sm.get_setting('MACHINE_CONFIG', 'enable_probe', 'True') == 'True')
        self.chk_enable_spindle_at_speed = QCheckBox("Enable Spindle At-Speed Input")
        self.chk_enable_spindle_at_speed.setChecked(self.sm.get_setting('MACHINE_CONFIG', 'enable_spindle_at_speed', 'True') == 'True')
        self.chk_enable_spindle_fault = QCheckBox("Enable Spindle Alarm / Fault Input")
        self.chk_enable_spindle_fault.setChecked(self.sm.get_setting('MACHINE_CONFIG', 'enable_spindle_fault', 'True') == 'True')
        self.chk_enable_tool_change = QCheckBox("Enable Manual Tool Change Loopback")
        self.chk_enable_tool_change.setChecked(self.sm.get_setting('MACHINE_CONFIG', 'enable_tool_change', 'True') == 'True')
        self.chk_enable_coolant = QCheckBox("Enable Coolant (Mist & Flood) Outputs")
        self.chk_enable_coolant.setChecked(self.sm.get_setting('MACHINE_CONFIG', 'enable_coolant', 'True') == 'True')
        self.chk_enable_estop = QCheckBox("Enable Physical E-Stop Input")
        self.chk_enable_estop.setChecked(self.sm.get_setting('MACHINE_CONFIG', 'enable_estop', 'True') == 'True')
        self.chk_spindle_closed_loop = QCheckBox("Enable Closed-Loop Spindle (PID & Encoder)")
        self.chk_spindle_closed_loop.setChecked(self.sm.get_setting('MACHINE_CONFIG', 'spindle_closed_loop', 'False') == 'True')
        
        features_group = QGroupBox("Machine Features")
        features_form = QFormLayout(features_group)
        features_form.setHorizontalSpacing(15)
        
        features_form.addRow(self.chk_enable_probe)
        features_form.addRow(self.chk_enable_spindle_at_speed)
        features_form.addRow(self.chk_enable_spindle_fault)
        features_form.addRow(self.chk_enable_tool_change)
        features_form.addRow(self.chk_enable_coolant)
        features_form.addRow(self.chk_enable_estop)
        features_form.addRow(self.chk_spindle_closed_loop)

        # --- Hardware I/O Pin Mapping ---
        io_group = QGroupBox("Hardware I/O Pin Mapping")
        io_group.setStyleSheet("QGroupBox { font-weight: bold; border: 1px solid #555555; background-color: #252526; border-radius: 4px; margin-top: 15px; padding-top: 15px; padding-bottom: 10px; color: #3498db; } QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 5px; }")
        io_main_layout = QGridLayout()

        left_group = QGroupBox("Limits & Homing")
        left_group.setStyleSheet("QGroupBox { border: 1px solid #444; border-radius: 4px; margin-top: 10px; padding-top: 15px; } QGroupBox::title { subcontrol-origin: margin; left: 10px; color: #ecf0f1; }")
        left_form = QFormLayout()
        left_form.setHorizontalSpacing(15)
        left_group.setLayout(left_form)

        right_group = QGroupBox("Accessories & Probing")
        right_group.setStyleSheet("QGroupBox { border: 1px solid #444; border-radius: 4px; margin-top: 10px; padding-top: 15px; } QGroupBox::title { subcontrol-origin: margin; left: 10px; color: #ecf0f1; }")
        right_form = QFormLayout()
        right_form.setHorizontalSpacing(15)
        right_group.setLayout(right_form)

        btn_group = QGroupBox("Physical Buttons")
        btn_group.setStyleSheet("QGroupBox { border: 1px solid #444; border-radius: 4px; margin-top: 10px; padding-top: 15px; } QGroupBox::title { subcontrol-origin: margin; left: 10px; color: #ecf0f1; }")
        btn_form = QFormLayout()
        btn_form.setHorizontalSpacing(15)
        btn_group.setLayout(btn_form)

        pendant_group = QGroupBox("Pendant / Jogging")
        pendant_group.setStyleSheet("QGroupBox { border: 1px solid #444; border-radius: 4px; margin-top: 10px; padding-top: 15px; } QGroupBox::title { subcontrol-origin: margin; left: 10px; color: #ecf0f1; }")
        pendant_form = QFormLayout()
        pendant_form.setHorizontalSpacing(15)
        pendant_group.setLayout(pendant_form)

        self.io_widgets = {}
        available_pins = self.get_available_pins()

        def create_io_row(form_layout, label_text, key, group_key=None):        
            cb = NoScrollComboBox()
            cb.setEditable(True)
            cb.addItems(available_pins)
            cb.setCurrentText(self.sm.get_setting('IO_MAPPING', key, ''))       

            inv_chk = QCheckBox("Invert (-not)")
            inv_chk.setChecked(self.sm.get_setting('IO_MAPPING', key + '_inv', 'False') == 'True')

            lbl = QLabel(label_text)
            container = QWidget()
            row_layout = QHBoxLayout(container)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.addWidget(cb)
            row_layout.addWidget(inv_chk)

            form_layout.addRow(lbl, container)
            self.io_widgets[key] = (cb, inv_chk, lbl)

            if group_key and group_key in self.dynamic_widgets:
                self.dynamic_widgets[group_key].extend([lbl, container])        

        # 1. Limits & Homing
        for j in range(5):
            grp = 'J3' if j == 3 else ('J4' if j == 4 else None)
            create_io_row(left_form, f"Joint {j} Home:", f"joint_{j}_home", grp)
            create_io_row(left_form, f"Joint {j} Min Limit:", f"joint_{j}_neg", grp)
            create_io_row(left_form, f"Joint {j} Max Limit:", f"joint_{j}_pos", grp)

        # 2. Accessories
        create_io_row(right_form, "3D Touch Probe:", "probe_in")
        create_io_row(right_form, "Touch Plate / Setter:", "plate_setter")      
        create_io_row(right_form, "Setter Overtravel:", "setter_overtravel")    
        create_io_row(right_form, "E-Stop Input:", "estop_ext")
        create_io_row(right_form, "Spindle At-Speed:", "spindle_at_speed")      
        create_io_row(right_form, "Spindle Fault:", "spindle_fault")
        create_io_row(right_form, "Coolant Mist:", "coolant_mist")
        create_io_row(right_form, "Coolant Flood:", "coolant_flood")

        # 3. Physical Buttons
        create_io_row(btn_form, "Cycle Start Button:", "btn_cycle_start")       
        create_io_row(btn_form, "Cycle Stop Button:", "btn_cycle_stop")
        create_io_row(btn_form, "Cycle Pause Button:", "btn_cycle_pause")       
        create_io_row(btn_form, "Cycle Resume Button:", "btn_cycle_resume")     
        create_io_row(btn_form, "Feed Hold (Overtravel):", "btn_feed_hold")     

        # 4. Pendant / Jogging
        for ax in ['x', 'y', 'z', 'a', 'b']:
            grp = 'A' if ax == 'a' else ('B' if ax == 'b' else None)
            create_io_row(pendant_form, f"Jog {ax.upper()}+:", f"jog_{ax}_pos", grp)
            create_io_row(pendant_form, f"Jog {ax.upper()}-:", f"jog_{ax}_neg", grp)
        create_io_row(pendant_form, "MPG Hardware Count:", "mpg_count")
        create_io_row(pendant_form, "MPG Phase A (WHA):", "mpg_a")
        create_io_row(pendant_form, "MPG Phase B (WHB):", "mpg_b")
        for ax in ['x', 'y', 'z', 'a', 'b']:
            grp = 'A' if ax == 'a' else ('B' if ax == 'b' else None)
            create_io_row(pendant_form, f"MPG Select {ax.upper()}:", f"mpg_{ax}_en", grp)
        create_io_row(pendant_form, "MPG Incr Select A (sel0):", "jog_incr_a")  
        create_io_row(pendant_form, "MPG Incr Select B (sel1):", "jog_incr_b")  
        create_io_row(pendant_form, "MPG Incr Select C (sel2):", "jog_incr_c")  
        create_io_row(pendant_form, "MPG Incr Select D (sel3):", "jog_incr_d")  
        create_io_row(pendant_form, "Analog Feed Override:", "feed_override_analog")
        create_io_row(pendant_form, "Analog Spindle Override:", "spindle_override_analog")

        io_main_layout.addWidget(left_group, 0, 0)
        io_main_layout.addWidget(right_group, 0, 1)
        io_main_layout.addWidget(btn_group, 1, 0)
        io_main_layout.addWidget(pendant_group, 1, 1)

        io_group.setLayout(io_main_layout)
        
        self.btn_generate_configs = QPushButton("✨ Generate Base INI & HAL Files")
        self.btn_generate_configs.setStyleSheet("background-color: #8e44ad; color: white; font-weight: bold; padding: 6px; border-radius: 4px;")
        self.btn_generate_configs.clicked.connect(self.generate_base_configs)

        self.btn_validate_model = QPushButton("🔎 Validate Machine Model")
        self.btn_validate_model.setStyleSheet("background-color: #455a64; color: white; font-weight: bold; padding: 6px; border-radius: 4px;")
        self.btn_validate_model.clicked.connect(self.validate_machine_model)

        self.btn_setup_wizard = QPushButton("🧭 Open Setup Wizard")
        self.btn_setup_wizard.setStyleSheet("background-color: #00695c; color: white; font-weight: bold; padding: 6px; border-radius: 4px;")
        self.btn_setup_wizard.clicked.connect(self.open_setup_wizard)
        
        self.btn_launch_linuxcnc = QPushButton("🚀 Launch LinuxCNC on CNC PC")
        self.btn_launch_linuxcnc.setStyleSheet("background-color: #27ae60; color: white; font-weight: bold; padding: 6px; border-radius: 4px;")
        self.btn_launch_linuxcnc.clicked.connect(self.launch_linuxcnc)
        
        self.btn_pull_log = QPushButton("📄 View CNC PC Launch Log")
        self.btn_pull_log.setStyleSheet("background-color: #2980b9; color: white; font-weight: bold; padding: 6px; border-radius: 4px;")
        self.btn_pull_log.clicked.connect(self.pull_remote_log)
        
        self.btn_stop_linuxcnc = QPushButton("🛑 Stop LinuxCNC")
        self.btn_stop_linuxcnc.setStyleSheet("background-color: #c0392b; color: white; font-weight: bold; padding: 6px; border-radius: 4px;")
        self.btn_stop_linuxcnc.clicked.connect(self.stop_linuxcnc)
        
        self.btn_vnc = QPushButton("🖥️ View Screen (VNC)")
        self.btn_vnc.setStyleSheet("background-color: #3498db; color: white; font-weight: bold; padding: 6px; border-radius: 4px;")
        self.btn_vnc.clicked.connect(self.launch_vnc)
        
        self.chk_terminal_view = QCheckBox("Show Terminal View")
        self.chk_terminal_view.setStyleSheet("color: #f1c40f; font-weight: bold; margin-left: 10px;")
        
        self.chk_auto_launch_local = QCheckBox("Auto-Launch LinuxCNC on Startup (Localhost)")
        self.chk_auto_launch_local.setChecked(self.sm.get_setting('MACHINE_CONFIG', 'auto_launch_local', 'False') == 'True')
        self.chk_auto_launch_local.setStyleSheet("color: #3498db; font-weight: bold; margin-left: 10px;")
        
        btn_layout_wizard = QHBoxLayout()
        btn_layout_wizard.addWidget(self.btn_generate_configs)
        btn_layout_wizard.addWidget(self.btn_validate_model)
        btn_layout_wizard.addWidget(self.btn_setup_wizard)
        btn_layout_wizard.addWidget(self.btn_launch_linuxcnc)
        btn_layout_wizard.addWidget(self.btn_vnc)
        btn_layout_wizard.addWidget(self.btn_pull_log)
        btn_layout_wizard.addWidget(self.btn_stop_linuxcnc)
        btn_layout_wizard.addWidget(self.chk_terminal_view)
        btn_layout_wizard.addWidget(self.chk_auto_launch_local)
        btn_layout_wizard.addStretch()
        
        self.terminal_view = QTextEdit()
        self.terminal_view.setReadOnly(True)
        self.terminal_view.setStyleSheet("background-color: #000000; color: #00ff00; font-family: 'Consolas', 'Courier New', monospace; padding: 5px; border-radius: 4px; border: 1px solid #555;")
        self.terminal_view.setFixedHeight(150)
        self.terminal_view.setVisible(False)
        self.chk_terminal_view.toggled.connect(self.terminal_view.setVisible)
        
        config_group.setLayout(config_form)

        # --- Jog Settings Group ---
        jog_group = QGroupBox("Jogging Configuration")
        jog_form = QFormLayout()
        jog_form.setHorizontalSpacing(15)
        jog_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        
        self.jog_speed_input = NoScrollDoubleSpinBox()
        self.jog_speed_input.setRange(1, 5000)
        self.jog_speed_input.setValue(float(self.sm.get_setting('JOG_SETTINGS', 'jog_speed', 100)))
        
        self.turbo_input = NoScrollDoubleSpinBox()
        self.turbo_input.setRange(1, 20)
        self.turbo_input.setValue(float(self.sm.get_setting('JOG_SETTINGS', 'turbo_multiplier', 5.0)))
        
        self.chk_enable_mpg_incr = QCheckBox("Enable MPG Jog Increments (mux16)")
        self.chk_enable_mpg_incr.setChecked(self.sm.get_setting('JOG_SETTINGS', 'enable_mpg_increments', 'False') == 'True')

        self.mpg_debounce_input = NoScrollDoubleSpinBox()
        self.mpg_debounce_input.setRange(0, 1)
        self.mpg_debounce_input.setDecimals(3)
        self.mpg_debounce_input.setValue(float(self.sm.get_setting('JOG_SETTINGS', 'mpg_debounce_time', 0.2)))

        self.chk_mpg_graycode = QCheckBox("Use Graycode for Selector")
        self.chk_mpg_graycode.setChecked(self.sm.get_setting('JOG_SETTINGS', 'mpg_use_graycode', 'False') == 'True')

        self.chk_mpg_suppress = QCheckBox("Suppress No Input")
        self.chk_mpg_suppress.setChecked(self.sm.get_setting('JOG_SETTINGS', 'mpg_suppress_no_input', 'False') == 'True')

        self.mpg_increments_input = QLineEdit(self.sm.get_setting('JOG_SETTINGS', 'mpg_increments', '0.000, 0.001, 0.010, 0.100, 0.125'))
        self.mpg_increments_input.setPlaceholderText("Comma separated list (e.g. 0.0, 0.001, 0.01)")

        jog_form.addRow("Standard Jog Speed:", self.jog_speed_input)
        jog_form.addRow("Turbo Multiplier (Shift):", self.turbo_input)
        jog_form.addRow("", self.chk_enable_mpg_incr)
        jog_form.addRow("MPG Debounce Time (s):", self.mpg_debounce_input)
        jog_form.addRow("", self.chk_mpg_graycode)
        jog_form.addRow("", self.chk_mpg_suppress)
        jog_form.addRow("MPG Increments:", self.mpg_increments_input)
        jog_group.setLayout(jog_form)

        # --- UI Preferences Group ---
        ui_group = QGroupBox("UI Editor Preferences")
        ui_form = QFormLayout()
        ui_form.setHorizontalSpacing(15)
        ui_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        
        self.auto_save_input = NoScrollSpinBox()
        self.auto_save_input.setRange(1, 60)
        self.auto_save_input.setSuffix(" minutes")
        self.auto_save_input.setValue(int(self.sm.get_setting('UI_PREFERENCES', 'auto_save_interval_minutes', 5)))
        
        ui_form.addRow("Auto-Save Interval:", self.auto_save_input)
        
        self.chk_operator_mode = QCheckBox("Operator Mode (Hide advanced tabs on machine boot)")
        self.chk_operator_mode.setChecked(self.sm.get_setting('MACHINE_CONFIG', 'operator_mode', 'False') == 'True')
        ui_form.addRow("Workspace:", self.chk_operator_mode)
        ui_group.setLayout(ui_form)

        self.display_units_cb = NoScrollComboBox()
        self.display_units_cb.addItems(["Metric (mm)", "Imperial (inch)"])
        self.display_units_cb.setCurrentText(self.sm.get_setting('UI_PREFERENCES', 'display_units', 'Metric (mm)'))
        ui_form.addRow("UI Display Units:", self.display_units_cb)

        # --- Probing & Locations Configuration Group ---
        probe_group = QGroupBox("Probing & Locations")
        probe_group.setStyleSheet("QGroupBox { font-weight: bold; border: 1px solid #555555; background-color: #252526; border-radius: 4px; margin-top: 15px; padding-top: 15px; padding-bottom: 10px; color: #1abc9c; } QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 5px; }")
        probe_main_layout = QHBoxLayout(probe_group)

        self.tool_setter_x = NoScrollDoubleSpinBox()
        self.tool_setter_x.setRange(-10000, 10000)
        self.tool_setter_x.setValue(float(self.sm.get_setting('PROBING', 'tool_setter_x', 0.0)))
        
        self.tool_setter_y = NoScrollDoubleSpinBox()
        self.tool_setter_y.setRange(-10000, 10000)
        self.tool_setter_y.setValue(float(self.sm.get_setting('PROBING', 'tool_setter_y', 0.0)))
        
        self.tool_setter_z = NoScrollDoubleSpinBox()
        self.tool_setter_z.setRange(-10000, 10000)
        self.tool_setter_z.setValue(float(self.sm.get_setting('PROBING', 'tool_setter_z', 0.0)))
        
        self.park_x = NoScrollDoubleSpinBox()
        self.park_x.setRange(-10000, 10000)
        self.park_x.setValue(float(self.sm.get_setting('LOCATIONS', 'park_x', 0.0)))
        
        self.park_y = NoScrollDoubleSpinBox()
        self.park_y.setRange(-10000, 10000)
        self.park_y.setValue(float(self.sm.get_setting('LOCATIONS', 'park_y', 0.0)))
        
        self.park_z = NoScrollDoubleSpinBox()
        self.park_z.setRange(-10000, 10000)
        self.park_z.setValue(float(self.sm.get_setting('LOCATIONS', 'park_z', 0.0)))
        
        self.probe_vel = NoScrollDoubleSpinBox()
        self.probe_vel.setRange(1, 10000)
        self.probe_vel.setValue(float(self.sm.get_setting('PROBING', 'probe_vel', 100.0)))
        
        self.setter_vel = NoScrollDoubleSpinBox()
        self.setter_vel.setRange(1, 10000)
        self.setter_vel.setValue(float(self.sm.get_setting('PROBING', 'setter_vel', 150.0)))
        
        ts_group = QGroupBox("Tool Setter Location")
        ts_group.setStyleSheet("QGroupBox { border: 1px solid #444; border-radius: 4px; padding-top: 15px; color: #ecf0f1; } QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 5px; }")
        ts_form = QFormLayout(ts_group)
        ts_form.setHorizontalSpacing(15)
        ts_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        ts_form.addRow("Absolute X:", self.tool_setter_x)
        ts_form.addRow("Absolute Y:", self.tool_setter_y)
        ts_form.addRow("Absolute Z:", self.tool_setter_z)
        self.btn_set_ts_loc = QPushButton("📍 Set to Current Location")
        self.btn_set_ts_loc.setStyleSheet("background-color: #2980b9; color: white; font-weight: bold; padding: 6px; border-radius: 4px;")
        self.btn_set_ts_loc.clicked.connect(self.set_ts_to_current)
        ts_form.addRow(self.btn_set_ts_loc)

        park_group = QGroupBox("Park Position")
        park_group.setStyleSheet("QGroupBox { border: 1px solid #444; border-radius: 4px; padding-top: 15px; color: #ecf0f1; } QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 5px; }")
        park_form = QFormLayout(park_group)
        park_form.setHorizontalSpacing(15)
        park_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        park_form.addRow("Absolute X:", self.park_x)
        park_form.addRow("Absolute Y:", self.park_y)
        park_form.addRow("Absolute Z:", self.park_z)
        self.btn_set_park_loc = QPushButton("📍 Set to Current Location")
        self.btn_set_park_loc.setStyleSheet("background-color: #2980b9; color: white; font-weight: bold; padding: 6px; border-radius: 4px;")
        self.btn_set_park_loc.clicked.connect(self.set_park_to_current)
        park_form.addRow(self.btn_set_park_loc)

        vel_group = QGroupBox("Probing Velocities")
        vel_group.setStyleSheet("QGroupBox { border: 1px solid #444; border-radius: 4px; padding-top: 15px; color: #ecf0f1; } QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 5px; }")
        vel_form = QFormLayout(vel_group)
        vel_form.setHorizontalSpacing(15)
        vel_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        vel_form.addRow("3D Probe Velocity (F):", self.probe_vel)
        vel_form.addRow("Tool Setter Velocity (F):", self.setter_vel)
        
        probe_main_layout.addWidget(ts_group)
        probe_main_layout.addWidget(park_group)
        probe_main_layout.addWidget(vel_group)

        # --- Spindle PID Tuning Group ---
        spindle_pid_group = QGroupBox("Spindle PID Tuning")
        spindle_pid_form = QFormLayout()
        spindle_pid_form.setHorizontalSpacing(15)
        spindle_pid_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        
        self.pid_p = NoScrollDoubleSpinBox(); self.pid_p.setRange(0, 10000); self.pid_p.setDecimals(4)
        self.pid_p.setValue(float(self.sm.get_setting('SPINDLE_PID', 'p', 0.0)))
        self.pid_i = NoScrollDoubleSpinBox(); self.pid_i.setRange(0, 10000); self.pid_i.setDecimals(4)
        self.pid_i.setValue(float(self.sm.get_setting('SPINDLE_PID', 'i', 0.0)))
        self.pid_d = NoScrollDoubleSpinBox(); self.pid_d.setRange(0, 10000); self.pid_d.setDecimals(4)
        self.pid_d.setValue(float(self.sm.get_setting('SPINDLE_PID', 'd', 0.0)))
        self.pid_ff0 = NoScrollDoubleSpinBox(); self.pid_ff0.setRange(-10000, 10000); self.pid_ff0.setDecimals(4)
        self.pid_ff0.setValue(float(self.sm.get_setting('SPINDLE_PID', 'ff0', 1.0)))
        self.pid_ff1 = NoScrollDoubleSpinBox(); self.pid_ff1.setRange(-10000, 10000); self.pid_ff1.setDecimals(4)
        self.pid_ff1.setValue(float(self.sm.get_setting('SPINDLE_PID', 'ff1', 0.0)))
        
        spindle_pid_form.addRow("P (Proportional):", self.pid_p)
        spindle_pid_form.addRow("I (Integral):", self.pid_i)
        spindle_pid_form.addRow("D (Derivative):", self.pid_d)
        spindle_pid_form.addRow("FF0 (Feedforward 0):", self.pid_ff0)
        spindle_pid_form.addRow("FF1 (Feedforward 1):", self.pid_ff1)
        self.btn_apply_pid_live = QPushButton("Apply PID to Running LinuxCNC")
        self.btn_apply_pid_live.clicked.connect(self.apply_pid_live)
        spindle_pid_form.addRow(self.btn_apply_pid_live)
        spindle_pid_group.setLayout(spindle_pid_form)
        spindle_pid_group.setEnabled(self.chk_spindle_closed_loop.isChecked())
        self.chk_spindle_closed_loop.toggled.connect(spindle_pid_group.setEnabled)

        # --- Dashboard UI Management ---
        ui_mgmt_group = QGroupBox("Dashboard UI Management")
        ui_mgmt_layout = QVBoxLayout()
        
        lbl_ui_info = QLabel("Manage the primary operator dashboard interface.")
        lbl_ui_info.setStyleSheet("color: #aaaaaa; font-style: italic;")
        ui_mgmt_layout.addWidget(lbl_ui_info)
        
        btn_layout = QHBoxLayout()
        self.btn_load_custom = QPushButton("📂 Load Custom UI (.fusionui)")
        self.btn_load_custom.setStyleSheet("background-color: #8e44ad; color: white; font-weight: bold; padding: 8px; border-radius: 4px;")
        self.btn_load_custom.clicked.connect(self.load_custom_ui_requested.emit)
        btn_layout.addWidget(self.btn_load_custom)
        
        self.btn_default_ui = QPushButton("↺ Restore Default UI")
        self.btn_default_ui.setStyleSheet("background-color: #2c3e50; color: white; font-weight: bold; padding: 8px; border-radius: 4px;")
        self.btn_default_ui.clicked.connect(self.restore_default_ui_requested.emit)
        btn_layout.addWidget(self.btn_default_ui)
        btn_layout.addStretch()
        
        ui_mgmt_layout.addLayout(btn_layout)
        ui_mgmt_group.setLayout(ui_mgmt_layout)

        # --- Save & Sync Buttons ---
        self.save_btn = QPushButton("Apply & Save Settings")
        self.save_btn.setStyleSheet("background-color: #2e7d32; color: white; font-weight: bold; padding: 10px; border-radius: 4px;")
        self.save_btn.clicked.connect(self.save_settings)
        
        self.btn_pull_settings = QPushButton("📥 Pull Settings.ini from CNC PC")
        self.btn_pull_settings.setStyleSheet("background-color: #d35400; color: white; font-weight: bold; padding: 10px; border-radius: 4px;")
        self.btn_pull_settings.clicked.connect(self.pull_settings)
        
        self.btn_push_settings = QPushButton("📤 Stage Settings.ini on CNC PC")
        self.btn_push_settings.setStyleSheet("background-color: #2980b9; color: white; font-weight: bold; padding: 10px; border-radius: 4px;")
        self.btn_push_settings.clicked.connect(self.push_settings)

        self.btn_activate_settings = QPushButton("✅ Activate Staged Settings")
        self.btn_activate_settings.setStyleSheet("background-color: #6a1b9a; color: white; font-weight: bold; padding: 10px; border-radius: 4px;")
        self.btn_activate_settings.clicked.connect(self.activate_staged_settings)

        self.btn_secure_deploy = QPushButton("🚀 Confirm CNC PC Deployment")
        self.btn_secure_deploy.setStyleSheet("background-color: #f39c12; color: black; font-weight: bold; padding: 10px; border-radius: 4px;")
        self.btn_secure_deploy.setToolTip("Stage and approve the secure application update on the CNC PC.")
        self.btn_secure_deploy.clicked.connect(self.install_app_remote)

        self.btn_install_remote = QPushButton("📦 Stage App Update on CNC PC")
        self.btn_install_remote.setStyleSheet("background-color: #f39c12; color: black; font-weight: bold; padding: 10px; border-radius: 4px;")
        self.btn_install_remote.clicked.connect(self.install_app_remote)
        self.btn_install_remote.setToolTip("Legacy label retained as a compatibility alias for the secure deployment flow.")

        self.btn_update_status = QPushButton("🔎 View Update Status")
        self.btn_update_status.setStyleSheet("background-color: #34495e; color: white; font-weight: bold; padding: 10px; border-radius: 4px;")
        self.btn_update_status.clicked.connect(self.show_application_update_status)
        
        # Global Action Bar / Sidebar
        action_sidebar = QVBoxLayout()
        action_sidebar.setContentsMargins(10, 10, 5, 5)
        # Add sidebar to the layout FIRST (so it is on the left)
        # We use a container widget to enforce a exact maximum width, narrowing the sidebar.
        action_container = QWidget()
        action_container.setLayout(action_sidebar)
        action_container.setMaximumWidth(260)
        horizontal_split.addWidget(action_container)

        sync_layout = QVBoxLayout()
        sync_layout.setSpacing(10)
        sync_layout.addWidget(self.btn_generate_configs)
        sync_layout.addWidget(self.btn_validate_model)
        sync_layout.addWidget(self.btn_setup_wizard)
        sync_layout.addWidget(self.btn_launch_linuxcnc)
        sync_layout.addWidget(self.btn_stop_linuxcnc)
        sync_layout.addWidget(self.save_btn)
        
        sync_layout.addWidget(self.btn_pull_settings)
        sync_layout.addWidget(self.btn_push_settings)
        sync_layout.addWidget(self.btn_activate_settings)
        sync_layout.addWidget(self.btn_secure_deploy)
        sync_layout.addWidget(self.btn_install_remote)
        sync_layout.addWidget(self.btn_update_status)

        sync_layout_widget = QGroupBox("Global Actions")
        sync_layout_widget.setStyleSheet("QGroupBox { border: 1px solid #444; border-top: 2px solid #3498db; border-radius: 4px; padding-top: 20px; padding-bottom: 10px; color: #ecf0f1; font-weight: bold; background-color: #1e1e1e; } QGroupBox::title { left: 10px; }")
        sync_layout_widget.setLayout(sync_layout)

        diag_layout = QVBoxLayout()
        diag_lbl = QLabel("Diagnostics & Boot Options")
        diag_lbl.setStyleSheet("color: #ecf0f1; font-weight: bold; margin-top: 15px;")
        diag_layout.addWidget(diag_lbl)
        diag_layout.addWidget(self.btn_vnc)
        diag_layout.addWidget(self.btn_pull_log)

        self.apply_secure_mode_restrictions()
        diag_layout.addWidget(self.chk_terminal_view)
        diag_layout.addWidget(self.chk_auto_launch_local)
        diag_layout.addStretch()

        diag_widget = QWidget()
        diag_widget.setLayout(diag_layout)

        action_sidebar.addWidget(sync_layout_widget)
        action_sidebar.addWidget(diag_widget)
        action_sidebar.addStretch()

        # Add the Tabs SECOND (so they are on the right)
        horizontal_split.addWidget(self.settings_tabs)

        # Assemble the Setup Wizard in ONE Vertical Stack
        wizard_stack = QVBoxLayout()
        wizard_stack.addWidget(config_group)
        wizard_stack.addWidget(axis_tuning_group)
        wizard_stack.addWidget(homing_group)
        wizard_stack.addWidget(global_timing_group)
        wizard_stack.addWidget(spindle_pid_group)
        wizard_stack.addWidget(io_group)
        
        try:
            wizard_stack.addWidget(self.terminal_view)
        except:
            pass
        wizard_stack.addStretch()
        
        layout_machine.addLayout(wizard_stack)

        layout_features.addWidget(features_group)
        layout_features.addWidget(jog_group)
        layout_features.addWidget(probe_group)
        layout_features.addStretch()

        layout_ui.addWidget(ui_mgmt_group)
        layout_ui.addWidget(ui_group)
        layout_ui.addStretch()
        
        self.mach_kinematics_cb.currentTextChanged.connect(self.update_axis_visibility)
        self.update_axis_visibility(self.mach_kinematics_cb.currentText())
        
        QTimer.singleShot(1000, self.check_pending_updates)

    def _fetch_current_coords(self):
        """
        Attempts to retrieve the current machine coordinates (absolute) from the
        active dashboard tab. This is needed for "Set to Current Location" functionality.
        """
        for widget in QApplication.topLevelWidgets():
            if hasattr(widget, 'tabs'):
                for i in range(widget.tabs.count()):
                    tab = widget.tabs.widget(i)
                    if hasattr(tab, 'mock_coords'):
                        # Park and Tool Setter locations require absolute machine coordinates (G53)
                        # Fallback to Work coordinates if absolute are not available (e.g., in simulation)
                        x = tab.mock_coords.get('ABSOLUTE_X', None)
                        y = tab.mock_coords.get('ABSOLUTE_Y', None)
                        z = tab.mock_coords.get('ABSOLUTE_Z', None)
                        return x, y, z
        return None, None, None

    def set_ts_to_current(self):
        x, y, z = self._fetch_current_coords()
        if x is not None and y is not None and z is not None:
            self.tool_setter_x.setValue(float(x))
            self.tool_setter_y.setValue(float(y))
            self.tool_setter_z.setValue(float(z))
            QMessageBox.information(self, "Location Set", f"Tool Setter location set to X:{x:.3f}, Y:{y:.3f}, Z:{z:.3f}\n\n(Note: Ensure these are the correct absolute machine coordinates before saving)")
        else:
            QMessageBox.warning(self, "Warning", "Could not determine current machine location. Is the Dashboard running?")

    def set_park_to_current(self):
        x, y, z = self._fetch_current_coords()
        if x is not None and y is not None and z is not None:
            self.park_x.setValue(float(x))
            self.park_y.setValue(float(y))
            self.park_z.setValue(float(z))
            QMessageBox.information(self, "Location Set", f"Park Position set to X:{x:.3f}, Y:{y:.3f}, Z:{z:.3f}\n\n(Note: Ensure these are the correct absolute machine coordinates before saving)")
        else:
            QMessageBox.warning(self, "Warning", "Could not determine current machine location. Is the Dashboard running?")

    def check_pending_updates(self):
        # 1. Check for pending settings.ini update
        pending_settings = os.path.join(os.getcwd(), "settings_pending.ini")
        if os.path.exists(pending_settings):
            reply = QMessageBox.question(self, "Version Control - New Settings", 
                                         "A new settings.ini configuration has been pushed from the dev machine.\n\nDo you want to review and apply this update?", 
                                         QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if reply == QMessageBox.StandardButton.Yes:
                try:
                    target = os.path.join(os.getcwd(), "settings.ini")
                    if os.path.exists(target): os.remove(target)
                    shutil.move(pending_settings, target)
                    QMessageBox.information(self, "Settings Applied", "Settings updated! Please restart the application for changes to take effect.")
                except Exception as e:
                    QMessageBox.warning(self, "Warning", f"Could not apply settings:\n{e}")
            else:
                pass
            # Always nuke the pending file to prevent loops
            if os.path.exists(pending_settings):
                try: os.remove(pending_settings)
                except: pass

        # 2. Check for pending LinuxCNC config files
        mach_name = self.sm.get_setting('MACHINE_CONFIG', 'name', 'CNCFusion')
        local_base = os.path.expanduser(self.sm.get_setting("PATHS", "last_remote_config_dir", "~/linuxcnc/configs"))
        local_dir = os.path.join(local_base, mach_name)
        pending_dir = os.path.join(local_dir, "pending_updates")
        
        if os.path.exists(pending_dir) and os.path.isdir(pending_dir):
            files = os.listdir(pending_dir)
            if files:
                file_list = "\n".join([f"- {f}" for f in files])
                reply = QMessageBox.question(self, "Version Control - New Configs", 
                                             f"New LinuxCNC configuration files have been staged by the dev machine:\n\n{file_list}\n\nDo you want to approve and apply these updates to the machine?", 
                                             QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
                if reply == QMessageBox.StandardButton.Yes:
                    try:
                        for f in files:
                            src = os.path.join(pending_dir, f)
                            dst = os.path.join(local_dir, f)
                            
                            # Preserve user data files and custom postgui logic if they already exist
                            if f in ['tool.tbl', 'linuxcnc.var', 'custom_postgui.hal'] and os.path.exists(dst):
                                continue

                            if os.path.exists(dst):
                                if os.path.isfile(dst):
                                    os.remove(dst)
                                else:
                                    shutil.rmtree(dst, ignore_errors=True)
                            shutil.move(src, dst)
                    except Exception as e:
                        print(f"Error applying updates: {e}")
                    finally:
                        shutil.rmtree(pending_dir, ignore_errors=True)
                    QMessageBox.information(self, "Configs Applied", "The machine has been updated with the new configuration files.")
                else:
                    shutil.rmtree(pending_dir, ignore_errors=True)
                    QMessageBox.information(self, "Configs Rejected", "The pending updates have been discarded.")

    def update_axis_visibility(self, kinematics):
        show_y2 = "XYYZ" in kinematics
        show_a = "4-Axis" in kinematics or "5-Axis" in kinematics
        show_b = "5-Axis" in kinematics
        
        joints_count = 3
        if "4-Axis" in kinematics or "XYYZ" in kinematics: joints_count = 4
        if "5-Axis" in kinematics: joints_count = 5
        
        for w in self.dynamic_widgets.get('Y2', []): w.setVisible(show_y2)
        for w in self.dynamic_widgets.get('A', []): w.setVisible(show_a)
        for w in self.dynamic_widgets.get('B', []): w.setVisible(show_b)
        for w in self.dynamic_widgets.get('J3', []): w.setVisible(joints_count > 3)
        for w in self.dynamic_widgets.get('J4', []): w.setVisible(joints_count > 4)

        # Update IO labels to show actual Axis names so you don't mix up J2 and J3!
        joint_names = ['X', 'Y', 'Z', 'A', 'B']
        if kinematics == "XYYZ (Gantry 3-Axis)": joint_names = ['X', 'Y1', 'Y2', 'Z', 'A']
        elif kinematics == "XYZ A (4-Axis)": joint_names = ['X', 'Y', 'Z', 'A', 'B']
            
        for j in range(5):
            ax_name = joint_names[j]
            for key_type, display in [('home', 'Home'), ('neg', 'Min Limit'), ('pos', 'Max Limit')]:
                key = f"joint_{j}_{key_type}"
                if key in getattr(self, 'io_widgets', {}) and len(self.io_widgets[key]) > 2:
                    self.io_widgets[key][2].setText(f"Joint {j} ({ax_name}) {display}:")

    def save_settings(self):
        # Save to INI
        self.sm.save_setting('MACHINE_CONFIG', 'name', self.mach_name_input.text())
        self.sm.save_setting('MACHINE_CONFIG', 'kinematics', self.mach_kinematics_cb.currentText())
        self.sm.save_setting('MACHINE_CONFIG', 'units', self.mach_units_cb.currentText())
        self.sm.save_setting('MACHINE_CONFIG', 'drive_type', self.mach_drive_cb.currentText())
        self.sm.save_setting('MACHINE_CONFIG', 'display_ui', self.mach_display_cb.currentText())
        self.sm.save_setting('MACHINE_CONFIG', 'min_spindle_override', str(self.mach_min_spindle_ovr.value()))
        self.sm.save_setting('MACHINE_CONFIG', 'max_spindle_override', str(self.mach_max_spindle_ovr.value()))
        self.sm.save_setting('MACHINE_CONFIG', 'max_feed_override', str(self.mach_max_feed_ovr.value()))
        self.sm.save_setting('MACHINE_CONFIG', 'enable_probe', str(self.chk_enable_probe.isChecked()))
        self.sm.save_setting('MACHINE_CONFIG', 'enable_spindle_at_speed', str(self.chk_enable_spindle_at_speed.isChecked()))
        self.sm.save_setting('MACHINE_CONFIG', 'enable_spindle_fault', str(self.chk_enable_spindle_fault.isChecked()))
        self.sm.save_setting('MACHINE_CONFIG', 'enable_tool_change', str(self.chk_enable_tool_change.isChecked()))
        self.sm.save_setting('MACHINE_CONFIG', 'enable_coolant', str(self.chk_enable_coolant.isChecked()))
        self.sm.save_setting('MACHINE_CONFIG', 'enable_estop', str(self.chk_enable_estop.isChecked()))
        self.sm.save_setting('MACHINE_CONFIG', 'spindle_closed_loop', str(self.chk_spindle_closed_loop.isChecked()))
        self.sm.save_setting('MACHINE_CONFIG', 'auto_launch_local', str(self.chk_auto_launch_local.isChecked()))
        self.sm.save_setting('MACHINE_CONFIG', 'operator_mode', str(self.chk_operator_mode.isChecked()))
        
        for ax in ['X', 'Y', 'Y2', 'Z', 'A', 'B']:
            if ax in self.axis_inputs:
                for k, v in self.axis_inputs[ax].items():
                    if k == 'invert':
                        self.sm.save_setting(f'AXIS_{ax}', 'invert_dir', str(v.isChecked()))
                    else:
                        self.sm.save_setting(f'AXIS_{ax}', k, str(v.value()))
                
        for ax in ['X', 'Y', 'Y2 (Squaring)', 'Z', 'A', 'B']:
            key = ax.split()[0].lower()
            for k, v in self.home_inputs[ax].items():
                self.sm.save_setting('HOMING', f'{key}_{k}', str(v.value()))
                
        self.sm.save_setting('GLOBAL_TIMINGS', 'steplen', str(self.glob_steplen.value()))
        self.sm.save_setting('GLOBAL_TIMINGS', 'stepspace', str(self.glob_stepspace.value()))
        self.sm.save_setting('GLOBAL_TIMINGS', 'dirsetup', str(self.glob_dirsetup.value()))
        self.sm.save_setting('GLOBAL_TIMINGS', 'dirhold', str(self.glob_dirhold.value()))
        self.sm.save_setting('GLOBAL_TIMINGS', 'ferror', str(self.glob_ferror.value()))
        self.sm.save_setting('GLOBAL_TIMINGS', 'min_ferror', str(self.glob_min_ferror.value()))
        self.sm.save_setting('GLOBAL_TIMINGS', 'spindle_max_rpm', str(self.glob_spindle_max.value()))
        self.sm.save_setting('GLOBAL_TIMINGS', 'spindle_min_rpm', str(self.glob_spindle_min.value()))
        
        self.sm.save_setting('JOG_SETTINGS', 'jog_speed', str(self.jog_speed_input.value()))
        self.sm.save_setting('JOG_SETTINGS', 'turbo_multiplier', str(self.turbo_input.value()))
        self.sm.save_setting('JOG_SETTINGS', 'enable_mpg_increments', str(self.chk_enable_mpg_incr.isChecked()))
        self.sm.save_setting('JOG_SETTINGS', 'mpg_debounce_time', str(self.mpg_debounce_input.value()))
        self.sm.save_setting('JOG_SETTINGS', 'mpg_use_graycode', str(self.chk_mpg_graycode.isChecked()))
        self.sm.save_setting('JOG_SETTINGS', 'mpg_suppress_no_input', str(self.chk_mpg_suppress.isChecked()))
        self.sm.save_setting('JOG_SETTINGS', 'mpg_increments', self.mpg_increments_input.text())
        self.sm.save_setting('UI_PREFERENCES', 'auto_save_interval_minutes', str(self.auto_save_input.value()))
        self.sm.save_setting('UI_PREFERENCES', 'display_units', self.display_units_cb.currentText())
        self.sm.save_setting('PROBING', 'tool_setter_x', str(self.tool_setter_x.value()))
        self.sm.save_setting('PROBING', 'tool_setter_y', str(self.tool_setter_y.value()))
        self.sm.save_setting('PROBING', 'tool_setter_z', str(self.tool_setter_z.value()))
        self.sm.save_setting('PROBING', 'probe_vel', str(self.probe_vel.value()))
        self.sm.save_setting('PROBING', 'setter_vel', str(self.setter_vel.value()))
        self.sm.save_setting('LOCATIONS', 'park_x', str(self.park_x.value()))
        self.sm.save_setting('LOCATIONS', 'park_y', str(self.park_y.value()))
        self.sm.save_setting('LOCATIONS', 'park_z', str(self.park_z.value()))
        
        self.sm.save_setting('SPINDLE_PID', 'p', str(self.pid_p.value()))
        self.sm.save_setting('SPINDLE_PID', 'i', str(self.pid_i.value()))
        self.sm.save_setting('SPINDLE_PID', 'd', str(self.pid_d.value()))
        self.sm.save_setting('SPINDLE_PID', 'ff0', str(self.pid_ff0.value()))
        self.sm.save_setting('SPINDLE_PID', 'ff1', str(self.pid_ff1.value()))
        
        for key, widget_tuple in self.io_widgets.items():
            cb, chk = widget_tuple[0], widget_tuple[1]
            self.sm.save_setting('IO_MAPPING', key, cb.currentText().strip())
            self.sm.save_setting('IO_MAPPING', key + '_inv', str(chk.isChecked()))
        
        # UI Feedback
        self.save_btn.setText("✓ Saved!")
        self.save_btn.setStyleSheet("background-color: #1b5e20; color: white; font-weight: bold; padding: 10px;")
        
        # Emit the signal to notify the Dashboard
        self.settings_changed.emit()
        
        # Reset button text after 1.5 seconds
        QTimer.singleShot(1500, lambda: self.save_btn.setText("Apply & Save Settings"))
        QTimer.singleShot(1500, lambda: self.save_btn.setStyleSheet("background-color: #2e7d32; color: white; font-weight: bold; padding: 10px;"))

    def apply_secure_mode_restrictions(self):
        """Disable Settings actions that only have a legacy SSH/VNC implementation."""
        if client_from_settings(self.sm) is None:
            return
        message = "This action requires legacy SSH or VNC access and is unavailable in secure agent mode."
        for control in [self.btn_pull_settings, self.btn_vnc]:
            control.setEnabled(False)
            control.setToolTip(message)

    def apply_agent_capabilities(self, capabilities):
        """Disable secure Settings actions unavailable on an older machine agent."""
        if client_from_settings(self.sm) is None:
            return
        available = set(capabilities)
        capability_map = {
            "lifecycle": [self.btn_launch_linuxcnc, self.btn_stop_linuxcnc],
            "configuration_activation": [self.btn_activate_settings],
            "file_upload": [self.btn_push_settings],
            "application_update": [self.btn_secure_deploy, self.btn_install_remote, self.btn_update_status],
            "pid_tuning": [self.btn_apply_pid_live],
        }
        for capability, controls in capability_map.items():
            if capability in available:
                continue
            message = f"This machine agent does not support {capability.replace('_', ' ')}."
            for control in controls:
                control.setEnabled(False)
                control.setToolTip(message)

    def pull_settings(self):
        if client_from_settings(self.sm) is not None:
            QMessageBox.information(
                self,
                "Pull Settings Not Available",
                "Secure mode does not retrieve arbitrary CNC PC settings files. Use staged configuration deployment instead.",
            )
            return

        ip = self.sm.get_setting("SSH", "ip_address", "")
        user = self.sm.get_setting("SSH", "username", "")
        password = self.sm.get_setting("SSH", "password", "")
        if not ip or not user:
            QMessageBox.warning(self, "Pull Failed", "SSH credentials are not configured. Please set them in the Mesa Utilities tab.")
            return
        remote_base = self.sm.get_setting("PATHS", "last_remote_cncfusion_dir", "~/CNCFusion")
        remote_dir, ok = QInputDialog.getText(self, "Pull Settings", "Enter the path to the CNCFusion folder on the CNC PC:", text=remote_base)
        if not ok or not remote_dir: return
        self.sm.save_setting("PATHS", "last_remote_cncfusion_dir", remote_dir)
        remote_file = f"{remote_dir.rstrip('/')}/settings.ini"
        local_file = os.path.join(os.getcwd(), "settings.ini")
        try:
            if HAS_PARAMIKO:
                ssh = paramiko.SSHClient()
                ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                ssh.connect(ip, username=user, password=password, timeout=10, banner_timeout=15, auth_timeout=15, look_for_keys=not bool(password), allow_agent=not bool(password))
                sftp = ssh.open_sftp()
                sftp_file = remote_file.replace("~/", f"/home/{user}/", 1) if remote_file.startswith("~/") else remote_file
                sftp.get(sftp_file, local_file)
                sftp.close()
                ssh.close()
            else:
                subprocess.run(["scp", "-o", "BatchMode=yes", f"{user}@{ip}:{remote_file}", local_file], capture_output=True, timeout=10)
                
            # Restore the local SSH credentials so the dev machine doesn't lock itself out!
            self.sm.save_setting("SSH", "ip_address", ip)
            self.sm.save_setting("SSH", "username", user)
            self.sm.save_setting("SSH", "password", password)
            
            self.reload_ui_from_settings()
            
            QMessageBox.information(self, "Sync Success", "Settings successfully pulled from the CNC PC and hot-reloaded into the UI.")
        except Exception as e:
            QMessageBox.critical(self, "Sync Error", f"Failed to pull settings.ini:\n{e}")

    def push_settings(self):
        ip = self.sm.get_setting("SSH", "ip_address", "")
        user = self.sm.get_setting("SSH", "username", "")
        password = self.sm.get_setting("SSH", "password", "")
        agent_client = client_from_settings(self.sm)
        if agent_client is None and (not ip or not user):
            QMessageBox.warning(self, "Push Failed", "SSH credentials are not configured. Please set them in the Mesa Utilities tab.")
            return
        self.save_settings()
        local_file = os.path.join(os.getcwd(), "settings.ini")

        if agent_client is not None:
            reply = QMessageBox.question(
                self,
                "Confirm Secure Deployment",
                "Stage the current settings as settings_pending.ini on the machine?\n\n"
                "The physical operator must approve the pending configuration before it becomes active.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return
            try:
                agent_client.upload_file("config", "settings_pending.ini", local_file)
                QMessageBox.information(
                    self,
                    "Sync Success",
                    "Settings were securely uploaded and staged for operator approval.",
                )
            except Exception as error:
                QMessageBox.critical(self, "Push Failed", f"Secure settings upload failed:\n{error}")
            return

        remote_base = self.sm.get_setting("PATHS", "last_remote_cncfusion_dir", "~/CNCFusion")
        remote_dir, ok = QInputDialog.getText(self, "Push Settings", "Enter the path to the CNCFusion folder on the CNC PC:", text=remote_base)
        if not ok or not remote_dir: return
        self.sm.save_setting("PATHS", "last_remote_cncfusion_dir", remote_dir)
        remote_file = f"{remote_dir.rstrip('/')}/settings_pending.ini"
        try:
            if HAS_PARAMIKO:
                ssh = paramiko.SSHClient()
                ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                ssh.connect(ip, username=user, password=password, timeout=10, banner_timeout=15, auth_timeout=15, look_for_keys=not bool(password), allow_agent=not bool(password))
                sftp = ssh.open_sftp()
                sftp_file = remote_file.replace("~/", f"/home/{user}/", 1) if remote_file.startswith("~/") else remote_file
                sftp.put(local_file, sftp_file)
                sftp.close()
                ssh.close()
            else:
                subprocess.run(["scp", "-o", "BatchMode=yes", local_file, f"{user}@{ip}:{remote_file}"], capture_output=True, timeout=10)
            QMessageBox.information(self, "Sync Success", "Settings successfully pushed and staged on the CNC PC!\n\nThe CNC PC operator will be prompted to approve the update.")
        except Exception as e:
            QMessageBox.critical(self, "Sync Error", f"Failed to push settings.ini:\n{e}")

    def activate_staged_settings(self):
        """Request explicit activation of settings_pending.ini through the agent."""
        agent_client = client_from_settings(self.sm)
        if agent_client is None:
            QMessageBox.warning(self, "Activation Unavailable", "Secure machine-agent settings are not configured.")
            return
        reply = QMessageBox.question(
            self,
            "Confirm Settings Activation",
            "Activate settings_pending.ini as settings.ini?\n\n"
            "LinuxCNC must be stopped. The current settings.ini will be backed up first.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        try:
            agent_client.activate_pending("settings_pending.ini", "settings.ini")
            QMessageBox.information(
                self,
                "Settings Activated",
                "The staged settings are now active. Restart LinuxCNC to use them.",
            )
        except Exception as error:
            QMessageBox.critical(self, "Activation Failed", f"Settings activation failed:\n{error}")

    def show_application_update_status(self):
        """Display staged archive metadata and the last local update result from the agent."""
        agent_client = client_from_settings(self.sm)
        if agent_client is None:
            QMessageBox.information(
                self,
                "Update Status Not Available",
                "Application update status is available only through the secure machine agent.",
            )
            return
        try:
            status = agent_client.application_update_status()
            lines = [
                "Staged update: " + ("yes" if status.get("staged") else "no"),
            ]
            try:
                preflight = agent_client.deployment_preflight()
                ready = all(preflight.values())
                lines.append("Deployment preflight: " + ("ready" if ready else "not ready"))
                if not ready:
                    missing = [name.replace("_", " ") for name, value in preflight.items() if not value]
                    lines.append("Missing: " + ", ".join(missing))
            except Exception:
                lines.append("Deployment preflight: unavailable")
            if status.get("staged"):
                lines.append(f"Staged size: {status.get('staged_size', 0)} bytes")
                if status.get("staged_sha256"):
                    lines.append(f"SHA-256: {status['staged_sha256']}")
            last_apply = status.get("last_apply")
            if isinstance(last_apply, dict):
                lines.append(f"Last local apply: {last_apply.get('status', 'unknown')}")
                if last_apply.get("agent_version"):
                    lines.append(f"Applied agent version: {last_apply['agent_version']}")
                if isinstance(last_apply.get("capabilities"), list):
                    lines.append(f"Applied capability count: {len(last_apply['capabilities'])}")
                if last_apply.get("archive_sha256"):
                    lines.append(f"Applied archive SHA-256: {last_apply['archive_sha256']}")
                if last_apply.get("error"):
                    lines.append(f"Details: {last_apply['error']}")
            try:
                live_agent = agent_client.agent_status()
                live_version = live_agent.get("agent_version", "unknown")
                lines.append(f"Live agent version: {live_version}")
                if isinstance(last_apply, dict) and last_apply.get("agent_version"):
                    if last_apply["agent_version"] == live_version:
                        lines.append("Applied and live agent versions match.")
                    else:
                        lines.append("Warning: applied and live agent versions differ.")
            except Exception:
                lines.append("Live agent version: unavailable")
            QMessageBox.information(self, "Application Update Status", "\n".join(lines))
        except Exception as error:
            QMessageBox.critical(self, "Update Status Failed", f"Failed to read secure update status:\n{error}")

    def install_app_remote(self):
        ip = self.sm.get_setting("SSH", "ip_address", "")
        user = self.sm.get_setting("SSH", "username", "")
        password = self.sm.get_setting("SSH", "password", "")
        agent_client = client_from_settings(self.sm)
        
        if agent_client is None and (not ip or not user):
            QMessageBox.warning(self, "Install Failed", "SSH credentials are not configured.")
            return
            
        reply = QMessageBox.question(self, "Confirm CNC PC Deployment", 
                                     f"Are you sure you want to package and send the CNC Fusion application to {ip}?\n\nThis will prompt the physical machine operator to accept the installation.", 
                                     QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply != QMessageBox.StandardButton.Yes:
            return
            
        if hasattr(self.window(), 'sys_monitor_lbl'):
            self.window().sys_monitor_lbl.setText("⏳ Packaging App for Deployment...")
            QApplication.processEvents()
        
        temp_dir = tempfile.gettempdir()
        tar_path = os.path.join(temp_dir, "cncfusion_install.tar.gz")
        
        try:
            with tarfile.open(tar_path, "w:gz") as tar:
                source_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                for root, dirs, files in os.walk(source_dir):
                    dirs[:] = [d for d in dirs if not d.startswith('.') and d not in ['.venv', '__pycache__', 'exports', 'backups', 'recovery', 'dist', 'build', 'CNCFusion_layout_editor']]
                    for file in files:
                        if file.startswith('.') or file.endswith(('.pyc', '.ini', '.hal')) or file == "cncfusion_install.tar.gz": continue
                        full_path = os.path.join(root, file)
                        arcname = os.path.relpath(full_path, source_dir)
                        tar.add(full_path, arcname=arcname)
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to package app locally:\n{e}")
            return

        if agent_client is not None:
            try:
                with open(tar_path, "rb") as update_file:
                    content = update_file.read(32 * 1024 * 1024 + 1)
                if len(content) > 32 * 1024 * 1024:
                    raise ValueError("Application update archive is too large.")
                result = agent_client.stage_application_update(content)
                update_status = agent_client.application_update_status()
                staged_size = update_status.get("staged_size", 0)
                QMessageBox.information(
                    self,
                    "Update Staged",
                    "Application update was securely staged on the LinuxCNC machine.\n\n"
                    f"Staged archive size: {staged_size} bytes\n\n"
                    "To approve and install locally on the LinuxCNC machine, run:\n\n"
                    "sudo systemctl start cncfusion-apply-update\n\n"
                    "The installer replaces /opt/CNCFusion transactionally and preserves its .venv.",
                )
            except Exception as error:
                QMessageBox.critical(self, "Update Failed", f"Secure application staging failed:\n{error}")
            finally:
                if os.path.exists(tar_path):
                    os.remove(tar_path)
                if hasattr(self.window(), 'sys_monitor_lbl'):
                    self.window().sys_monitor_lbl.setText("System Diagnostics Active")
            return
            
        if hasattr(self.window(), 'sys_monitor_lbl'):
            self.window().sys_monitor_lbl.setText("⏳ Waiting for Remote Operator Approval...")
            QApplication.processEvents()
        
        try:
            if HAS_PARAMIKO:
                ssh = paramiko.SSHClient()
                ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                ssh.connect(ip, username=user, password=password, timeout=10, banner_timeout=15, auth_timeout=15, look_for_keys=not bool(password), allow_agent=not bool(password))
                
                sftp = ssh.open_sftp()
                sftp.put(tar_path, "/tmp/cncfusion_install.tar.gz")
                sftp.close()
                
                # Prompt the physical operator using Zenity
                prompt_cmd = f"export DISPLAY=:0; export WAYLAND_DISPLAY=wayland-0; export XDG_RUNTIME_DIR=/run/user/$(id -u {user}); export XAUTHORITY=/home/{user}/.Xauthority; export DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/$(id -u {user})/bus; zenity --question --title='App Install Request' --text='An installation/update of CNC Fusion Pro was pushed from the Dev machine.\n\nDo you want to authorize and install it now?' --width=400"
                stdin, stdout, stderr = ssh.exec_command(prompt_cmd)
                exit_status = stdout.channel.recv_exit_status()
                err_out = stderr.read().decode().strip()
                
                if exit_status != 0:
                    if exit_status == 1 and "cannot open display" not in err_out.lower() and "not found" not in err_out.lower() and "wayland" not in err_out.lower() and "gtk" not in err_out.lower():
                        QMessageBox.information(self, "Cancelled", "The remote operator declined the installation.")
                        ssh.exec_command("rm -f /tmp/cncfusion_install.tar.gz")
                        ssh.close()
                        if hasattr(self.window(), 'sys_monitor_lbl'): self.window().sys_monitor_lbl.setText("System Diagnostics Active")
                        return
                    else:
                        fallback = QMessageBox.question(self, "Remote Prompt Failed", 
                            f"Could not show the popup on the remote screen (it might be asleep or blocking SSH displays).\n\nDetails: {err_out}\n\nDo you want to FORCE the installation anyway?", 
                            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
                        if fallback != QMessageBox.StandardButton.Yes:
                            ssh.exec_command("rm -f /tmp/cncfusion_install.tar.gz")
                            ssh.close()
                            if hasattr(self.window(), 'sys_monitor_lbl'): self.window().sys_monitor_lbl.setText("System Diagnostics Active")
                            return
                    
                if hasattr(self.window(), 'sys_monitor_lbl'):
                    self.window().sys_monitor_lbl.setText("⏳ Installing and Creating Shortcuts...")
                    QApplication.processEvents()
                
                install_cmd = f"mkdir -p /home/{user}/CNCFusion && tar -xzf /tmp/cncfusion_install.tar.gz -C /home/{user}/CNCFusion && rm -f /tmp/cncfusion_install.tar.gz && mkdir -p /home/{user}/Desktop && cat << 'EOF' > /home/{user}/Desktop/CNCFusion.desktop\n[Desktop Entry]\nType=Application\nExec=python3 /home/{user}/CNCFusion/main.py\nPath=/home/{user}/CNCFusion\nName=CNC Fusion Pro\nIcon=/home/{user}/CNCFusion/assets/CNCFusionDeskTopIcon.png\nTerminal=false\nEOF\nchmod +x /home/{user}/Desktop/CNCFusion.desktop"
                ssh.exec_command(install_cmd)
                ssh.close()
                
                QMessageBox.information(self, "Success", "App successfully installed!\n\nA shortcut has been generated on the CNC PC desktop.")
            else:
                QMessageBox.critical(self, "Failed", "Paramiko is required to deploy the app. Please install it (pip install paramiko).")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Deployment failed:\n{e}")
        finally:
            if os.path.exists(tar_path): os.remove(tar_path)
            if hasattr(self.window(), 'sys_monitor_lbl'): self.window().sys_monitor_lbl.setText("System Diagnostics Active")

    def get_available_pins(self):
        pins = set()
        if os.path.exists("component_library.json"):
            try:
                with open("component_library.json", 'r') as f:
                    data = json.load(f)
                    for comp, pin_list in data.items():
                        for pin in pin_list:
                            if len(pin) > 2 and pin[2].strip() and pin[2].strip() != "<=>":
                                base_pin = pin[2].strip()
                                if base_pin.endswith("-not"): base_pin = base_pin[:-4]
                                pins.add(base_pin)
            except Exception: pass
        return sorted(list(pins))
        
    def align_hal_lines(self, lines):
        max_sig, max_src = 0, 0
        for line in lines:
            line_str = line.strip()
            if line_str.startswith("net "):
                parts = line_str.split("=>")
                left = parts[0].strip().split()
                if len(left) >= 3:
                    max_sig = max(max_sig, len(left[1]))
                    max_src = max(max_src, len(" ".join(left[2:])))
        out_lines = []
        for line in lines:
            indent = line[:len(line) - len(line.lstrip())]
            line_str = line.strip()
            if line_str.startswith("net "):
                parts = line_str.split("=>")
                left = parts[0].strip().split()
                if len(left) >= 3:
                    sig = left[1].ljust(max_sig)
                    src = " ".join(left[2:]).ljust(max_src)
                    dest = parts[1].strip() if len(parts) > 1 else ""
                    out_lines.append(f"{indent}net {sig}  {src}  =>  {dest}" if dest else f"{indent}net {sig}  {src}")
                else: out_lines.append(line)
            else: out_lines.append(line)
        return out_lines

    def validate_machine_model(self):
        """Validate saved settings through the hardware-neutral machine model."""
        try:
            config = MachineConfig.from_settings(self.sm)
            errors = config.validate()
            if errors:
                QMessageBox.warning(
                    self,
                    "Machine Model Needs Attention",
                    "\n".join(f"- {error}" for error in errors),
                )
            else:
                QMessageBox.information(
                    self,
                    "Machine Model Valid",
                    "The saved machine settings form a valid LinuxCNC model.",
                )
        except (TypeError, ValueError) as error:
            QMessageBox.critical(self, "Validation Failed", str(error))

    def open_setup_wizard(self):
        """Open the additive setup wizard using the existing settings manager."""
        wizard = SetupWizard(self.sm, self)
        if wizard.exec():
            self.reload_ui_from_settings()

    def apply_pid_live(self):
        """Apply named spindle PID values through the secure agent only."""
        agent_client = client_from_settings(self.sm)
        if agent_client is None:
            QMessageBox.warning(self, "Live PID Unavailable", "Secure machine-agent settings are required for live PID tuning.")
            return
        reply = QMessageBox.question(
            self,
            "Confirm Live PID Tuning",
            "Apply the current spindle PID values to the running LinuxCNC machine?\n\n"
            "LinuxCNC must be running and idle.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        values = {
            "p": self.pid_p.value(),
            "i": self.pid_i.value(),
            "d": self.pid_d.value(),
            "ff0": self.pid_ff0.value(),
            "ff1": self.pid_ff1.value(),
        }
        applied = []
        try:
            for field, value in values.items():
                agent_client.set_pid(field, value)
                applied.append(field)
            QMessageBox.information(self, "PID Applied", "Applied PID fields: " + ", ".join(applied))
        except Exception as error:
            QMessageBox.critical(
                self,
                "PID Partially Applied",
                f"Applied fields: {', '.join(applied) or '(none)'}\n\n{error}",
            )

    def generate_base_configs(self):
        name = self.mach_name_input.text().strip().replace(" ", "_")
        if not name: name = "CNCFusion"
        
        kinematics = self.mach_kinematics_cb.currentText()
        units = "mm" if "Metric" in self.mach_units_cb.currentText() else "inch"
        drive = self.mach_drive_cb.currentText()
        
        board_model = self.sm.get_setting("MESA", "board_model", "7i76eu").lower()
        
        if kinematics == "XYZ (3-Axis)":
            coords, joints, k_str = "X Y Z", 3, "trivkins coordinates=xyz"
            axes_list, joint_axes = ['X', 'Y', 'Z'], ['X', 'Y', 'Z']
        elif kinematics == "XYYZ (Gantry 3-Axis)":
            coords, joints, k_str = "X Y Z", 4, "trivkins coordinates=xyyz kinstype=BOTH"
            axes_list, joint_axes = ['X', 'Y', 'Z'], ['X', 'Y', 'Y', 'Z']
        elif kinematics == "XYZ A (4-Axis)":
            coords, joints, k_str = "X Y Z A", 4, "trivkins coordinates=xyza"
            axes_list, joint_axes = ['X', 'Y', 'Z', 'A'], ['X', 'Y', 'Z', 'A']
        else: # XYZ AB (5-Axis)
            coords, joints, k_str = "X Y Z A B", 5, "trivkins coordinates=xyzab"
            axes_list, joint_axes = ['X', 'Y', 'Z', 'A', 'B'], ['X', 'Y', 'Z', 'A', 'B']
            
        def get_mapped_pin(key):
            if key not in self.io_widgets: return None
            base = self.io_widgets[key][0].currentText().strip()
            inv = self.io_widgets[key][1].isChecked()
            if not base: return None
            if inv and not base.endswith("-not"): return base + "-not"
            elif not inv and base.endswith("-not"): return base[:-4]
            return base
            
        pin_probe = get_mapped_pin('probe_in')
        pin_plate = get_mapped_pin('plate_setter')
        pin_feed_btn = get_mapped_pin('btn_feed_hold')
        pin_overtravel = get_mapped_pin('setter_overtravel')
        pin_mpg_count = get_mapped_pin('mpg_count')
        pin_mpg_a = get_mapped_pin('mpg_a')
        pin_mpg_b = get_mapped_pin('mpg_b')
        pin_estop = get_mapped_pin('estop_ext')
        
        or2_count = 0
        feed_or2 = -1
        
        if pin_feed_btn and pin_overtravel:
            feed_or2 = or2_count
            or2_count += 1
            
        lut5_count = 0
        probe_lut5 = -1
        if pin_probe and pin_plate:
            probe_lut5 = lut5_count
            lut5_count += 1
            
        and2_count = 0
        estop_and2 = -1
        if self.chk_enable_estop.isChecked() and pin_estop:
            estop_and2 = and2_count
            and2_count += 1
            
        display_sel = self.mach_display_cb.currentText()
        display_val = "./cncfusion_gui.py" if display_sel == "CNCFusion" else display_sel

        ini_content = [
            f"[EMC]", f"MACHINE = {name}", f"DEBUG = 0", f"VERSION = 1.1", f"",
            f"[DISPLAY]", f"DISPLAY = {display_val}", f"POSITION_OFFSET = RELATIVE",
            f"POSITION_FEEDBACK = ACTUAL", f"MAX_FEED_OVERRIDE = {self.mach_max_feed_ovr.value():.6f}",
            f"MAX_SPINDLE_OVERRIDE = {self.mach_max_spindle_ovr.value():.6f}", f"MIN_SPINDLE_OVERRIDE = {self.mach_min_spindle_ovr.value():.6f}",
            f"DEFAULT_SPINDLE_0_SPEED = {self.glob_spindle_max.value() * 0.5:.0f}",
            f"MIN_SPINDLE_0_SPEED = {self.glob_spindle_min.value():.0f}", f"MAX_SPINDLE_0_SPEED = {self.glob_spindle_max.value():.0f}",
            f"INTRO_GRAPHIC = linuxcnc.gif", f"INTRO_TIME = 5",
            f"PROGRAM_PREFIX = ~/linuxcnc/nc_files",
            f"INCREMENTS = 5mm 1mm .5mm .1mm .05mm .01mm .005mm", f"",
            f"[KINS]", f"JOINTS = {joints}", f"KINEMATICS = {k_str}", f"",
            f"[TRAJ]", f"COORDINATES = {coords}", f"LINEAR_UNITS = {units}",
            f"ANGULAR_UNITS = degree", f"DEFAULT_LINEAR_VELOCITY = {self.axis_inputs['X']['vel'].value() * 0.1:.2f}",
            f"MAX_LINEAR_VELOCITY = {self.axis_inputs['X']['vel'].value():.2f}", f"",
            f"[HAL]", f"HALUI = halui", f"HALFILE = {name}.hal", f"HALFILE = {name}_blueprint.hal", f"POSTGUI_HALFILE = custom_postgui.hal", f""]

        # Set the startup code to match the selected units, ensuring G-code is interpreted correctly.
        startup_code = "G21" if units == "mm" else "G20"
        ini_content.extend([
            f"[HAL]", f"HALUI = halui", f"HALFILE = {name}.hal", f"HALFILE = {name}_blueprint.hal", f"POSTGUI_HALFILE = custom_postgui.hal", f"",
            f"[RS274NGC]", f"PARAMETER_FILE = linuxcnc.var", f"RS274NGC_STARTUP_CODE = {startup_code} G40 G90 G94 G97 G64 P0.025", f"",
            f"[TASK]", f"TASK = milltask", f"CYCLE_TIME = 0.010", f"",
            f"[EMCIO]", f"EMCIO = io", f"CYCLE_TIME = 0.100", f"TOOL_TABLE = tool.tbl", f"",
            f"[EMCMOT]", f"EMCMOT = motmod", f"COMM_TIMEOUT = 1.0", f"SERVO_PERIOD = 1000000", f"" # Base thread for stepper/servo
        ])
        
        for axis in axes_list:
            a_min = self.axis_inputs[axis]['min'].value()
            a_max = self.axis_inputs[axis]['max'].value()
            a_vel = self.axis_inputs[axis]['vel'].value()
            a_acc = self.axis_inputs[axis]['accel'].value()
            ini_content.extend([
                f"[AXIS_{axis}]", f"MAX_VELOCITY = {a_vel:.2f}", f"MAX_ACCELERATION = {a_acc:.2f}",
                f"MIN_LIMIT = {a_min:.3f}", f"MAX_LIMIT = {a_max:.3f}", f""
            ])
            
        for j_idx, j_axis in enumerate(joint_axes):
            j_type = "ANGULAR" if j_axis in ['A', 'B', 'C'] else "LINEAR"
            is_y2 = (kinematics == "XYYZ (Gantry 3-Axis)" and j_idx == 2)
            
            a_min = self.axis_inputs[j_axis]['min'].value()
            a_max = self.axis_inputs[j_axis]['max'].value()
            a_vel = self.axis_inputs[j_axis]['vel'].value()
            a_acc = self.axis_inputs[j_axis]['accel'].value()
            
            # Unit-aware scale calculation
            units_per_rev = self.axis_inputs[j_axis]['units'].value()
            if units == "inch": units_per_rev /= 25.4
            a_scale = self.axis_inputs[j_axis]['steps'].value() / max(0.001, units_per_rev)
            
            backlash = self.axis_inputs[j_axis].get('backlash', None)
            backlash_val = backlash.value() if backlash else 0.0

            if is_y2:
                if self.axis_inputs['Y2']['invert'].isChecked():
                    a_scale = -a_scale
            else:
                if self.axis_inputs[j_axis]['invert'].isChecked():
                    a_scale = -a_scale

            stepgen_a = a_acc * 1.25
            
            # Expand the physical joint limits by 5mm so homing overshoot doesn't trigger a fatal soft-limit!
            over_travel = 5.0 if units == "mm" else 0.2
            j_min = a_min - over_travel
            j_max = a_max + over_travel
            h_pos = self.home_inputs[j_axis]['home'].value()
            h_off = self.home_inputs['Y2 (Squaring)']['offset'].value() if is_y2 else self.home_inputs[j_axis]['offset'].value()
            h_sv = self.home_inputs[j_axis]['search'].value()
            h_lv = self.home_inputs[j_axis]['latch'].value()
            h_seq = self.home_inputs[j_axis]['seq'].value()
            if kinematics == "XYYZ (Gantry 3-Axis)" and j_axis == 'Y':
                h_seq = -abs(h_seq) if h_seq != 0 else 0
            
            ini_content.extend([
                f"[JOINT_{j_idx}]", f"TYPE = {j_type}", f"HOME = {h_pos:.3f}",
                f"BACKLASH = {backlash_val:.4f}",
                f"MIN_LIMIT = {j_min:.3f}", f"MAX_LIMIT = {j_max:.3f}",
                f"MAX_VELOCITY = {a_vel:.2f}", f"MAX_ACCELERATION = {a_acc:.2f}",
                f"STEPGEN_MAXVEL = {a_vel * 1.20:.2f}", f"STEPGEN_MAXACCEL = {stepgen_a:.2f}",
                f"SCALE = {a_scale:.3f}", f"FERROR = {self.glob_ferror.value():.3f}", 
                f"MIN_FERROR = {self.glob_min_ferror.value():.3f}", f"HOME_OFFSET = {h_off:.3f}",
                f"HOME_SEARCH_VEL = {h_sv:.3f}", f"HOME_LATCH_VEL = {h_lv:.3f}",
                f"HOME_FINAL_VEL = {abs(h_sv):.3f}", f"HOME_USE_INDEX = NO",
                f"HOME_IGNORE_LIMITS = YES", f"HOME_SEQUENCE = {int(h_seq)}",
                f"DIRSETUP = {int(self.glob_dirsetup.value())}", f"DIRHOLD = {int(self.glob_dirhold.value())}",
                f"STEPLEN = {int(self.glob_steplen.value())}", f"STEPSPACE = {int(self.glob_stepspace.value())}",
                f"P = 0.0", f"I = 0.0", f"D = 0.0", f"FF0 = 0.0", f"FF1 = 1.0", f"FF2 = 0.0",
                f"BIAS = 0.0", f"DEADBAND = 0.0", f"MAX_OUTPUT = 0.0", f""
            ])
            
        ini_content.extend([
            f"[SPINDLE_0]", 
            f"P = {self.pid_p.value():.4f}", f"I = {self.pid_i.value():.4f}", 
            f"D = {self.pid_d.value():.4f}", f"FF0 = {self.pid_ff0.value():.4f}", 
            f"FF1 = {self.pid_ff1.value():.4f}", f"FF2 = 0.0", f"BIAS = 0.0", f"DEADBAND = 0.0",
            f"MAX_OUTPUT = {self.glob_spindle_max.value():.0f}", f"OUTPUT_SCALE = {self.glob_spindle_max.value():.0f}",
            f"OUTPUT_MIN_LIMIT = 0", f"OUTPUT_MAX_LIMIT = {self.glob_spindle_max.value():.0f}", f""
        ])
            
        board_ip = self.sm.get_setting("MESA", "ip_address", "10.10.10.10")

        # Smart Serial Mode 2 unlocks the hidden MPG encoders (enc0 and enc1) on TB5!
        config_str = f"num_encoders=1 num_pwmgens=0 num_stepgens={joints}"
        if "7i76" in board_model:
            config_str += " sserial_port_0=2xxxx"

        hal_content = [
            f"# Core HAL config for {name}",
            f"loadrt [KINS]KINEMATICS",
            f"loadrt [EMCMOT]EMCMOT servo_period_nsec=[EMCMOT]SERVO_PERIOD num_joints=[KINS]JOINTS",
            f"loadrt hostmot2",
            f"loadrt hm2_eth board_ip={board_ip} config=\"{config_str}\""
        ]
        
        if self.chk_spindle_closed_loop.isChecked():
            hal_content.append(f"loadrt pid names=pid.s")
            hal_content.append("loadrt abs count=1")
            
        if self.chk_enable_mpg_incr.isChecked():
            hal_content.append("loadrt mux16 names=jogincr")
            
        if pin_mpg_a and pin_mpg_b:
            hal_content.append("loadrt encoder num_chan=1")
            
        if or2_count > 0:
            hal_content.append(f"loadrt or2 count={or2_count}")
        if lut5_count > 0:
            hal_content.append(f"loadrt lut5 count={lut5_count}")
        if and2_count > 0:
            hal_content.append(f"loadrt and2 count={and2_count}")
            
        hal_content.extend([
            f"setp hm2_{board_model}.0.watchdog.timeout_ns 25000000",
            f"addf hm2_{board_model}.0.read servo-thread",
            f"addf motion-command-handler servo-thread",
            f"addf motion-controller servo-thread"
        ])
        
        if self.chk_spindle_closed_loop.isChecked():
            hal_content.append(f"addf pid.s.do-pid-calcs servo-thread")
            hal_content.append("addf abs.0 servo-thread")
            
        if self.chk_enable_mpg_incr.isChecked():
            hal_content.append("addf jogincr servo-thread")
            
        if pin_mpg_a and pin_mpg_b:
            hal_content.append("addf encoder.update-counters servo-thread")
            hal_content.append("addf encoder.capture-position servo-thread")
            
        if or2_count > 0:
            for i in range(or2_count):
                hal_content.append(f"addf or2.{i} servo-thread")
        if lut5_count > 0:
            for i in range(lut5_count):
                hal_content.append(f"addf lut5.{i} servo-thread")
        if and2_count > 0:
            for i in range(and2_count):
                hal_content.append(f"addf and2.{i} servo-thread")
            
        hal_content.extend([
            f"addf hm2_{board_model}.0.write servo-thread",
            f""
        ])
        
        if self.chk_spindle_closed_loop.isChecked():
            hal_content.extend([
                f"# --- Spindle PID Configuration ---",
                f"setp pid.s.Pgain [SPINDLE_0]P",
                f"setp pid.s.Igain [SPINDLE_0]I",
                f"setp pid.s.Dgain [SPINDLE_0]D",
                f"setp pid.s.FF0 [SPINDLE_0]FF0",
                f"setp pid.s.FF1 [SPINDLE_0]FF1",
                f""
            ])
        
        blueprint_hal = [
            f"# --- Auto-Generated Logic Blueprint for {name} ---",
            ""
        ]
        
        assigned_out_pins = {}
        def net_hw_input(signal_name, hw_pin, linuxcnc_pins):
            if not hw_pin or not linuxcnc_pins: return
            if hw_pin in assigned_out_pins:
                actual_sig = assigned_out_pins[hw_pin]
                blueprint_hal.append(f"net {actual_sig} => {linuxcnc_pins}")
            else:
                assigned_out_pins[hw_pin] = signal_name
                blueprint_hal.append(f"net {signal_name} {hw_pin} => {linuxcnc_pins}")
        
        for j_idx, j_axis in enumerate(joint_axes):
            stepgen_idx = f"{j_idx:02d}"
            
            blueprint_hal.extend([
                f"# --- {j_axis} Axis / Joint {j_idx} ---",
                f"setp hm2_{board_model}.0.stepgen.{stepgen_idx}.dirsetup [JOINT_{j_idx}]DIRSETUP",
                f"setp hm2_{board_model}.0.stepgen.{stepgen_idx}.dirhold [JOINT_{j_idx}]DIRHOLD",
                f"setp hm2_{board_model}.0.stepgen.{stepgen_idx}.steplen [JOINT_{j_idx}]STEPLEN",
                f"setp hm2_{board_model}.0.stepgen.{stepgen_idx}.stepspace [JOINT_{j_idx}]STEPSPACE",
                f"setp hm2_{board_model}.0.stepgen.{stepgen_idx}.position-scale [JOINT_{j_idx}]SCALE",
                f"setp hm2_{board_model}.0.stepgen.{stepgen_idx}.maxvel [JOINT_{j_idx}]STEPGEN_MAXVEL",
                f"setp hm2_{board_model}.0.stepgen.{stepgen_idx}.maxaccel [JOINT_{j_idx}]STEPGEN_MAXACCEL",
                f"setp hm2_{board_model}.0.stepgen.{stepgen_idx}.control-type 0",
                f"setp hm2_{board_model}.0.stepgen.{stepgen_idx}.step_type 0",
                f"net j{j_idx}-pos-cmd joint.{j_idx}.motor-pos-cmd => hm2_{board_model}.0.stepgen.{stepgen_idx}.position-cmd",
                f"net j{j_idx}-pos-fb hm2_{board_model}.0.stepgen.{stepgen_idx}.position-fb => joint.{j_idx}.motor-pos-fb",
                f"net j{j_idx}-enable joint.{j_idx}.amp-enable-out => hm2_{board_model}.0.stepgen.{stepgen_idx}.enable",
            ])
            
            h_pin = get_mapped_pin(f'joint_{j_idx}_home')
            n_pin = get_mapped_pin(f'joint_{j_idx}_neg')
            p_pin = get_mapped_pin(f'joint_{j_idx}_pos')
            
            if h_pin: net_hw_input(f"j{j_idx}-home-sw", h_pin, f"joint.{j_idx}.home-sw-in")
            if n_pin: net_hw_input(f"j{j_idx}-neg-limit", n_pin, f"joint.{j_idx}.neg-lim-sw-in")
            if p_pin: net_hw_input(f"j{j_idx}-pos-limit", p_pin, f"joint.{j_idx}.pos-lim-sw-in")
            
            blueprint_hal.append("")
            
        blueprint_hal.extend([
            "# --- Spindle ---",
            f"setp hm2_{board_model}.0.7i76.0.0.spinout-minlim {self.glob_spindle_min.value():.0f}",
            f"setp hm2_{board_model}.0.7i76.0.0.spinout-maxlim {self.glob_spindle_max.value():.0f}",
            f"setp hm2_{board_model}.0.7i76.0.0.spinout-scalemax {self.glob_spindle_max.value():.0f}"
        ])
        
        blueprint_hal.extend([
            f"net spindle-enable spindle.0.on => hm2_{board_model}.0.7i76.0.0.spinena",
            f"net spindle-dir spindle.0.reverse => hm2_{board_model}.0.7i76.0.0.spindir"
        ])
        
        if self.chk_spindle_closed_loop.isChecked():
            blueprint_hal.extend([
                "net spindle-enable => pid.s.enable",
                "net spindle-cmd spindle.0.speed-out => pid.s.command",
                "net spindle-pid-out pid.s.output => abs.0.in",
                f"net spindle-pid-abs abs.0.out => hm2_{board_model}.0.7i76.0.0.spinout",
                f"net spindle-fb hm2_{board_model}.0.encoder.00.velocity => pid.s.feedback",
                f"net spindle-fb hm2_{board_model}.0.encoder.00.velocity => spindle.0.speed-in"
            ])
        else:
            blueprint_hal.extend([
                f"net spindle-cmd spindle.0.speed-out-abs => hm2_{board_model}.0.7i76.0.0.spinout"
            ])
        
        if self.chk_enable_spindle_at_speed.isChecked():
            pin = get_mapped_pin('spindle_at_speed')
            if pin: net_hw_input("spindle-at-speed", pin, "spindle.0.at-speed")
        if self.chk_enable_spindle_fault.isChecked():
            pin = get_mapped_pin('spindle_fault')
            if pin: net_hw_input("spindle-fault", pin, "spindle.0.amp-fault-in")
            
        blueprint_hal.append("")
        
        if self.chk_enable_tool_change.isChecked():
            blueprint_hal.extend([
                "# --- Tool Change (Manual Loopback) ---",
                "net tool-change-loop iocontrol.0.tool-change => iocontrol.0.tool-changed",
                "net tool-prepare-loop iocontrol.0.tool-prepare => iocontrol.0.tool-prepared",
                ""
            ])
            
        if self.chk_enable_coolant.isChecked():
            m_pin = get_mapped_pin('coolant_mist')
            f_pin = get_mapped_pin('coolant_flood')
            blueprint_hal.extend([
                "# --- Coolant Outputs ---"
            ])
            if m_pin: blueprint_hal.append(f"net coolant-mist iocontrol.0.coolant-mist => {m_pin}")
            if f_pin: blueprint_hal.append(f"net coolant-flood iocontrol.0.coolant-flood => {f_pin}")
            blueprint_hal.append("")
            
        blueprint_hal.extend([
            "# --- Hardware Buttons ---"
        ])
        pin_start = get_mapped_pin('btn_cycle_start')
        if pin_start: net_hw_input("cycle-start-btn", pin_start, "halui.program.run")
        pin_stop = get_mapped_pin('btn_cycle_stop')
        if pin_stop: net_hw_input("cycle-stop-btn", pin_stop, "halui.program.stop")
        pin_pause = get_mapped_pin('btn_cycle_pause')
        if pin_pause: net_hw_input("cycle-pause-btn", pin_pause, "halui.program.pause")
        pin_resume = get_mapped_pin('btn_cycle_resume')
        if pin_resume: net_hw_input("cycle-resume-btn", pin_resume, "halui.program.resume")
        
        if pin_feed_btn and pin_overtravel:
            blueprint_hal.extend([
                f"net combined-feed-hold or2.{feed_or2}.out => motion.feed-hold"
            ])
            net_hw_input("feed-hold-btn", pin_feed_btn, f"or2.{feed_or2}.in0")
            net_hw_input("toolsetter-overtravel", pin_overtravel, f"or2.{feed_or2}.in1")
        elif pin_feed_btn:
            net_hw_input("feed-hold-btn", pin_feed_btn, "motion.feed-hold")
        elif pin_overtravel:
            net_hw_input("toolsetter-overtravel", pin_overtravel, "motion.feed-hold")
        blueprint_hal.append("")
        
        blueprint_hal.extend([
            "# --- External Pendant Jogging ---"
        ])
        for ax in axes_list:
            ax_lower = ax.lower()
            pin_pos = get_mapped_pin(f'jog_{ax_lower}_pos')
            if pin_pos: net_hw_input(f"jog-{ax_lower}-pos", pin_pos, f"halui.axis.{ax_lower}.plus")
            pin_neg = get_mapped_pin(f'jog_{ax_lower}_neg')
            if pin_neg: net_hw_input(f"jog-{ax_lower}-neg", pin_neg, f"halui.axis.{ax_lower}.minus")
        
        if pin_mpg_a and pin_mpg_b:
            blueprint_hal.extend([
                "# --- Software Encoder for MPG ---",
            ])
            net_hw_input("mpg-a", pin_mpg_a, "encoder.0.phase-A")
            net_hw_input("mpg-b", pin_mpg_b, "encoder.0.phase-B")
            mpg_nets = [f"axis.{ax.lower()}.jog-counts" for ax in axes_list]
            blueprint_hal.append(f"net mpg-count encoder.0.counts => " + " ".join(mpg_nets))
        elif pin_mpg_count:
            mpg_nets = [f"axis.{ax.lower()}.jog-counts" for ax in axes_list]
            net_hw_input("mpg-count", pin_mpg_count, " ".join(mpg_nets))
            
        if self.chk_enable_mpg_incr.isChecked():
            blueprint_hal.extend([
                "# --- MPG Jog Increments ---"
            ])
            pin_ja = get_mapped_pin('jog_incr_a')
            if pin_ja: net_hw_input("jog-incr-a", pin_ja, "jogincr.sel0")
            pin_jb = get_mapped_pin('jog_incr_b')
            if pin_jb: net_hw_input("jog-incr-b", pin_jb, "jogincr.sel1")
            pin_jc = get_mapped_pin('jog_incr_c')
            if pin_jc: net_hw_input("jog-incr-c", pin_jc, "jogincr.sel2")
            pin_jd = get_mapped_pin('jog_incr_d')
            if pin_jd: net_hw_input("jog-incr-d", pin_jd, "jogincr.sel3")
            
            blueprint_hal.extend([
                f"setp jogincr.debounce-time {self.mpg_debounce_input.value():.6f}",
                f"setp jogincr.use-graycode {'True' if self.chk_mpg_graycode.isChecked() else 'False'}",
                f"setp jogincr.suppress-no-input {'True' if self.chk_mpg_suppress.isChecked() else 'False'}"
            ])
            
            increments = [x.strip() for x in self.mpg_increments_input.text().split(',')]
            for i in range(16):
                val = increments[i] if i < len(increments) else increments[-1] if increments else "0.0"
                try: val_float = float(val)
                except: val_float = 0.0
                blueprint_hal.append(f"setp jogincr.in{i:02d} {val_float:.6f}")
                
            scale_nets = [f"axis.{ax.lower()}.jog-scale" for ax in axes_list]
            blueprint_hal.append(f"net selected-jog-incr jogincr.out-f => " + " ".join(scale_nets))
            blueprint_hal.append("")
        else:
            # If no MPG is enabled, do nothing, let default jog scale apply or be set elsewhere
            pass

        for ax in axes_list:
            ax_lower = ax.lower()
            pin_mpg_en = get_mapped_pin(f'mpg_{ax_lower}_en')
            if pin_mpg_en: net_hw_input(f"mpg-{ax_lower}-en", pin_mpg_en, f"axis.{ax_lower}.jog-enable")
        
        pin_feed_ovr = get_mapped_pin('feed_override_analog')
        if pin_feed_ovr:
            net_hw_input("feed-ovr-analog", pin_feed_ovr, "halui.feed-override.scale")
            blueprint_hal.extend([
                "setp halui.feed-override.direct-value true",
                "setp halui.feed-override.counts 1"
            ])
            
        pin_spindle_ovr = get_mapped_pin('spindle_override_analog')
        if pin_spindle_ovr:
            net_hw_input("spindle-ovr-analog", pin_spindle_ovr, "halui.spindle-override.scale")
            blueprint_hal.extend([
                "setp halui.spindle-override.direct-value true",
                "setp halui.spindle-override.counts 1"
            ])
            
        blueprint_hal.append("")
        
        blueprint_hal.extend([
            "# --- System & Safety ---"
        ])
        
        if self.chk_enable_estop.isChecked() and pin_estop:
            blueprint_hal.extend([
                f"net estop-user-out iocontrol.0.user-enable-out => and2.{estop_and2}.in0",
                f"net estop-safe-in and2.{estop_and2}.out => iocontrol.0.emc-enable-in"
            ])
            net_hw_input("estop-ext", pin_estop, f"and2.{estop_and2}.in1")
        else:
            blueprint_hal.append("net estop-loop iocontrol.0.user-enable-out => iocontrol.0.emc-enable-in")
            
        if self.chk_enable_probe.isChecked():
            if pin_probe and pin_plate:
                blueprint_hal.extend([
                    f"setp lut5.{probe_lut5}.function 0xCA",
                    f"net combined-probe lut5.{probe_lut5}.out => motion.probe-input",
                    f"# UI toggles lut5.{probe_lut5}.in-2 to switch between Plate (0) and 3D Probe (1)"
                ])
                net_hw_input("plate-setter", pin_plate, f"lut5.{probe_lut5}.in-0")
                net_hw_input("probe-tip", pin_probe, f"lut5.{probe_lut5}.in-1")
            elif pin_probe:
                net_hw_input("probe-in", pin_probe, "motion.probe-input")
            elif pin_plate:
                net_hw_input("plate-setter", pin_plate, "motion.probe-input")
            
        blueprint_hal.append("")
        
        postgui_hal = [
            f"# --- Custom Post-GUI HAL file for {name} ---",
            "# This file is loaded after the GUI has started.",
            "# It is automatically sourced by the POSTGUI_HALFILE entry in the INI.",
            "# Export your dashboard UI wires from the Logic Designer into this file!",
            ""
        ]
        
        tool_tbl = [
            "; Default Tool Table for CNC Fusion",
            "; T<tool> P<pocket> X<offset> Y<offset> Z<offset> A<offset> B<offset> C<offset> U<offset> V<offset> W<offset> D<diameter> I<frontangle> J<backangle> Q<orientation>",
            "T1 P1 X0 Y0 Z0.0 A0 B0 C0 U0 V0 W0 D0.0 I0 J0 Q0 ; Default Tool",
            ""
        ]
        
        var_content = [
            "5220 1.000000",
            ""
        ]
        
        cncfusion_gui_content = [
            "#!/usr/bin/env python3",
            "import time",
            "import sys",
            "try:",
            "    import hal",
            "    h = hal.component('cncfusion_gui')",
            "    h.ready()",
            "except ImportError:",
            "    pass",
            "try:",
            "    while True:",
            "        time.sleep(1)",
            "except KeyboardInterrupt:",
            "    pass"
        ]
        
        hal_content = self.align_hal_lines(hal_content)
        blueprint_hal = self.align_hal_lines(blueprint_hal)
        postgui_hal = self.align_hal_lines(postgui_hal)
        
        export_dir = os.path.join(os.getcwd(), "exports", name)
        os.makedirs(export_dir, exist_ok=True)
        
        ini_path = os.path.join(export_dir, f"{name}.ini")
        hal_path = os.path.join(export_dir, f"{name}.hal")
        blueprint_path = os.path.join(export_dir, f"{name}_blueprint.hal")
        postgui_path = os.path.join(export_dir, "custom_postgui.hal")
        tool_tbl_path = os.path.join(export_dir, "tool.tbl")
        var_path = os.path.join(export_dir, "linuxcnc.var")
        gui_path = os.path.join(export_dir, "cncfusion_gui.py")
        
        try:
            hal_composer = HALDocumentComposer(export_dir)
            existing_postgui = hal_composer.load_postgui()
            hal_artifacts = hal_composer.compose(
                "\n".join(hal_content),
                "\n".join(blueprint_hal),
                existing_postgui,
            )
            with open(ini_path, 'w', newline='\n') as f: 
                f.write("\n".join(ini_content))
            hal_composer.artifact_path(os.path.basename(hal_path)).write_text(
                hal_artifacts["core.hal"], encoding='utf-8', newline='\n'
            )
            hal_composer.artifact_path(os.path.basename(blueprint_path)).write_text(
                hal_artifacts["blueprint.hal"], encoding='utf-8', newline='\n'
            )
            hal_composer.artifact_path(os.path.basename(postgui_path)).write_text(
                hal_artifacts["custom_postgui.hal"], encoding='utf-8', newline='\n'
            )
            with open(tool_tbl_path, 'w', newline='\n') as f:
                f.write("\n".join(tool_tbl))
            with open(var_path, 'w', newline='\n') as f:
                f.write("\n".join(var_content))
            with open(gui_path, 'w', newline='\n') as f:
                f.write("\n".join(cncfusion_gui_content))
            
            QDesktopServices.openUrl(QUrl.fromLocalFile(export_dir))
            
            # Auto-Load Blueprint into Wiring Tab
            app = self.window()
            if hasattr(app, 'wiring_tab'):
                app.wiring_tab.clear_canvas()
                app.wiring_tab.import_hal(blueprint_path, auto_layout=True)
                app.tabs.setCurrentWidget(app.wiring_tab)
            
            reply = QMessageBox.question(self, "Success", 
                                         f"Base INI and HAL files successfully generated for '{name}'!\n\nThey have been saved to:\n{export_dir}\n\nWould you like to deploy these to your LinuxCNC machine now?", 
                                         QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if reply == QMessageBox.StandardButton.Yes:
                self.deploy_configs(export_dir, name)
                
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to generate files:\n{e}")

    def deploy_configs(self, local_dir, mach_name):
        ip = self.sm.get_setting("SSH", "ip_address", "")
        user = self.sm.get_setting("SSH", "username", "")
        password = self.sm.get_setting("SSH", "password", "")
        agent_client = client_from_settings(self.sm)

        if agent_client is not None:
            reply = QMessageBox.question(
                self,
                "Confirm Secure Configuration Deployment",
                f"Stage the reviewed configuration for '{mach_name}' through the secure machine agent?\n\n"
                "INI, HAL, tool-table, and variable files will be uploaded. Existing custom_postgui.hal will be preserved.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return
            allowed_suffixes = {".ini", ".hal", ".tbl", ".var"}
            try:
                uploaded = []
                for filename in sorted(os.listdir(local_dir)):
                    local_path = os.path.join(local_dir, filename)
                    if os.path.isfile(local_path) and os.path.splitext(filename)[1].lower() in allowed_suffixes:
                        agent_client.upload_file("config", f"{mach_name}/{filename}", local_path)
                        uploaded.append(filename)
                QMessageBox.information(
                    self,
                    "Deployment Staged",
                    "Securely staged files:\n\n" + "\n".join(uploaded) +
                    "\n\nThe operator must approve activation on the LinuxCNC machine.",
                )
            except Exception as error:
                QMessageBox.critical(self, "Deployment Failed", f"Secure configuration deployment failed:\n{error}")
            return
        
        if not ip or not user:
            QMessageBox.warning(self, "Deploy Failed", "SSH credentials are not configured. Please set them in the Mesa Utilities tab.")
            return
            
        remote_base = self.sm.get_setting("PATHS", "last_remote_config_dir", "~/linuxcnc/configs")
        remote_dir, ok = QInputDialog.getText(self, "CNC PC Directory", "Enter CNC PC config directory path:", text=f"{remote_base}/{mach_name}")
        
        if not ok or not remote_dir:
            return
            
        pending_dir = f"{remote_dir}/pending_updates"
        mkdir_cmd = f"mkdir -p {pending_dir}"
        
        try:
            if HAS_PARAMIKO:
                ssh = paramiko.SSHClient()
                ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                ssh.connect(ip, username=user, password=password, timeout=10, banner_timeout=15, auth_timeout=15, look_for_keys=not bool(password), allow_agent=not bool(password))
                
                # Wait for the directory creation to finish before uploading
                stdin, stdout, stderr = ssh.exec_command(mkdir_cmd)
                stdout.channel.recv_exit_status() 
                
                sftp_dir = pending_dir.replace("~/", f"/home/{user}/", 1) if pending_dir.startswith("~/") else pending_dir
                
                sftp = ssh.open_sftp()
                for filename in os.listdir(local_dir):
                    local_file = os.path.join(local_dir, filename)
                    if os.path.isfile(local_file):
                        sftp.put(local_file, f"{sftp_dir}/{filename}")
                
                sftp.close()
                ssh.exec_command(f"chmod +x {sftp_dir}/cncfusion_gui.py")
                ssh.close()
            else:
                subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", f"{user}@{ip}", mkdir_cmd], capture_output=True, timeout=10)
                for filename in os.listdir(local_dir):
                    local_file = os.path.join(local_dir, filename)
                    if os.path.isfile(local_file):
                        subprocess.run(["scp", "-o", "BatchMode=yes", local_file, f"{user}@{ip}:{pending_dir}/{filename}"], capture_output=True, timeout=10)
                subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", f"{user}@{ip}", f"chmod +x {pending_dir}/cncfusion_gui.py"], capture_output=True, timeout=10)
                        
            QMessageBox.information(self, "Deploy Success", f"Configuration successfully staged to {ip}:{pending_dir}\n\nThe operator will be prompted to approve these updates on the physical machine.")
        except Exception as e:
            QMessageBox.critical(self, "Deploy Error", f"Failed to deploy configuration files:\n{e}")

    def launch_linuxcnc(self):
        ip = self.sm.get_setting("SSH", "ip_address", "")
        user = self.sm.get_setting("SSH", "username", "")
        password = self.sm.get_setting("SSH", "password", "")
        agent_client = client_from_settings(self.sm)
        
        if agent_client is None and (not ip or not user):
            QMessageBox.warning(self, "Launch Failed", "SSH credentials are not configured. Please set them in the Mesa Utilities tab.")
            return

        name = self.mach_name_input.text().strip().replace(" ", "_")
        if not name: name = "CNCFusion"
        
        remote_base = self.sm.get_setting("PATHS", "last_remote_config_dir", "~/linuxcnc/configs")
        default_ini_path = f"{remote_base}/{name}/{name}.ini"

        ini_path, ok = QInputDialog.getText(self, "Launch LinuxCNC", "Enter CNC PC INI path to launch:", text=default_ini_path)
        if not ok or not ini_path:
            return
            
        # Ensure absolute path expansion for the shell command
        abs_ini_path = ini_path.replace("~/", f"/home/{user}/", 1) if ini_path.startswith("~/") else ini_path

        if agent_client is not None:
            try:
                lifecycle = LinuxCNCLifecycle(RemoteLinuxCNCBackend(agent_client))
                result = lifecycle.start(abs_ini_path)
                if result.success:
                    QMessageBox.information(self, "LinuxCNC Started", result.message)
                else:
                    QMessageBox.critical(self, "Launch Failed", result.message)
            except Exception as error:
                QMessageBox.critical(self, "Launch Failed", f"Machine agent request failed: {error}")
            return
        
        # Check if LinuxCNC is already running to avoid stealing control from physical operator
        check_cmd = "pgrep linuxcnc"
        is_running = False
        
        try:
            if is_local_ip(ip):
                res = subprocess.run(check_cmd, shell=True, capture_output=True)
                is_running = (res.returncode == 0)
            elif HAS_PARAMIKO:
                ssh = paramiko.SSHClient()
                ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                ssh.connect(ip, username=user, password=password, timeout=5, look_for_keys=not bool(password), allow_agent=not bool(password))
                stdin, stdout, stderr = ssh.exec_command(check_cmd)
                is_running = bool(stdout.read().decode().strip())
                ssh.close()
            else:
                res = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", f"{user}@{ip}", check_cmd], capture_output=True)
                is_running = (res.returncode == 0)
        except Exception as e:
            pass # Ignore pre-check failures and proceed to launch logic
            
        if is_running:
            reply = QMessageBox.question(self, "LinuxCNC is already running!", 
                                         "LinuxCNC is currently active on the CNC PC. This means the local operator might be using it.\n\n"
                                         "Launching it remotely will FORCE KILL their session and take control.\n\n"
                                         "Are you absolutely sure you want to take over?", 
                                         QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if reply != QMessageBox.StandardButton.Yes:
                return

        # Auto-kill previous instances to prevent the 'already running' GUI popup trap
        base_kill_cmd = "if pgrep linuxcnc > /dev/null; then python3 -c 'import linuxcnc; c=linuxcnc.command(); c.exit()' 2>/dev/null; for i in 1 2 3 4 5 6 7 8 9 10; do if ! pgrep linuxcnc > /dev/null; then break; fi; sleep 1; done; if pgrep linuxcnc > /dev/null; then killall -TERM linuxcnc linuxcncsvr milltask io 2>/dev/null; sleep 2; fi; if pgrep linuxcnc > /dev/null; then halrun -U 2>/dev/null; killall -9 linuxcnc linuxcncsvr milltask io halrun rtapi_app halcmd 2>/dev/null; rm -rf /tmp/linuxcnc.lock* /tmp/linuxcnc.print /tmp/linuxcnc.error 2>/dev/null; rm -f /dev/shm/linuxcnc* /dev/shm/rtapi* /dev/shm/hal* 2>/dev/null; fi; fi; pkill -TERM -f cncfusion_gui 2>/dev/null;"
            
        # We use a dedicated bash script to guarantee it inherits the exact environment
        # variables it needs, regardless of whether it's launched locally or remotely over SSH.
        script_content = f"""#!/bin/bash
{base_kill_cmd}
mkdir -p /home/{user}/linuxcnc/nc_files 2>/dev/null
echo '--- Starting LinuxCNC ---' > /tmp/linuxcnc_launch.log
export DISPLAY=:0
export WAYLAND_DISPLAY=wayland-0
export XDG_RUNTIME_DIR=/run/user/$(id -u {user})
export XAUTHORITY=/home/{user}/.Xauthority
export PATH=$PATH:/usr/bin:/usr/local/bin:/usr/sbin
# CRITICAL: Ensure the dummy GUI script is executable, otherwise LinuxCNC instantly crashes on boot!
chmod +x "$(dirname "{abs_ini_path}")/cncfusion_gui.py" 2>/dev/null
nohup linuxcnc "{abs_ini_path}" >> /tmp/linuxcnc_launch.log 2>&1 </dev/null &
sleep 2
cat /tmp/linuxcnc_launch.log
"""

        try:
            if is_local_ip(ip):
                self.terminal_view.append(f"\n--- Launching {ini_path} locally ---")
                with open("/tmp/launch_lcnc.sh", "w") as f:
                    f.write(script_content)
                os.chmod("/tmp/launch_lcnc.sh", 0o755)
                subprocess.Popen("/tmp/launch_lcnc.sh", shell=True, start_new_session=True)
                
                if self.chk_terminal_view.isChecked():
                    QTimer.singleShot(3000, lambda: self._read_local_log("/tmp/linuxcnc_launch.log"))
                else:
                    self.terminal_view.append("✅ (Started silently in background)")
                return

            # Remote SSH Launch
            b64_script = base64.b64encode(script_content.encode()).decode()
            cmd = f"echo {b64_script} | base64 -d > /tmp/launch_lcnc.sh && chmod +x /tmp/launch_lcnc.sh && bash /tmp/launch_lcnc.sh"

            out = ""
            err = ""
            if HAS_PARAMIKO:
                ssh = paramiko.SSHClient()
                ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                ssh.connect(ip, username=user, password=password, timeout=10, banner_timeout=15, auth_timeout=15, look_for_keys=not bool(password), allow_agent=not bool(password))
                stdin, stdout, stderr = ssh.exec_command(cmd)
                stdout.channel.settimeout(20.0) # Increase timeout to survive the base_kill_cmd 10-sec loop!
                out = stdout.read().decode()
                err = stderr.read().decode()
                ssh.close()
            else:
                res = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", f"{user}@{ip}", cmd], capture_output=True, text=True, timeout=25)
                out = res.stdout
                err = res.stderr
                
            full_log = (out.strip() + "\n" + err.strip()).strip()
            
            # Force a popup window with the log if the checkbox is ticked OR if there is an error
            if self.chk_terminal_view.isChecked() or "error" in full_log.lower() or "traceback" in full_log.lower():
                msg = QMessageBox(self)
                msg.setWindowTitle("LinuxCNC Launch Log")
                msg.setText("Here is the exact output from the CNC PC:")
                msg.setDetailedText(full_log if full_log else "(No output received. Check if it launched!)")
                msg.setStyleSheet("QLabel{min-width: 400px; font-weight: bold;}")
                msg.exec()
            
            if self.chk_terminal_view.isChecked():
                self.terminal_view.append(f"\n--- Launching {ini_path} ---")
                if "error" in full_log.lower() or "traceback" in full_log.lower() or "failed" in full_log.lower():
                    self.terminal_view.append("⚠️ WARNING: LinuxCNC may have encountered an error:")
                self.terminal_view.append(full_log if full_log else "(Started in background)")
                self.terminal_view.verticalScrollBar().setValue(self.terminal_view.verticalScrollBar().maximum())
            else:
                self.terminal_view.append(f"\n✅ Launch command sent silently for {ini_path}")
            
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to launch LinuxCNC:\n{e}")

    def _read_local_log(self, log_path):
        try:
            with open(log_path, 'r') as f:
                content = f.read().strip()
                
            msg = QMessageBox(self)
            msg.setWindowTitle("Local Launch Log")
            msg.setText("Here is the output from LinuxCNC:")
            msg.setDetailedText(content if content else "(File is empty)")
            msg.setStyleSheet("QLabel{min-width: 400px; font-weight: bold;}")
            msg.exec()
            
            if "error" in content.lower() or "traceback" in content.lower() or "failed" in content.lower():
                self.terminal_view.append("⚠️ WARNING: LinuxCNC may have encountered an error:")
            self.terminal_view.append(content if content else "(Started in background, log is empty)")
            self.terminal_view.verticalScrollBar().setValue(self.terminal_view.verticalScrollBar().maximum())
        except Exception as e:
            self.terminal_view.append(f"(Could not read local log: {e})")

    def pull_remote_log(self):
        ip = self.sm.get_setting("SSH", "ip_address", "")
        user = self.sm.get_setting("SSH", "username", "")
        password = self.sm.get_setting("SSH", "password", "")
        agent_client = client_from_settings(self.sm)

        if agent_client is not None:
            try:
                self.chk_terminal_view.setChecked(True)
                result = agent_client.read_launch_log()
                content = result.get("content", "")
                if result.get("error"):
                    content = f"[ERROR]\n{result['error']}"
                self.terminal_view.append("\n--- Secure Launch Log ---")
                self.terminal_view.append(content if content else "(File is empty or unavailable)")
                self.terminal_view.verticalScrollBar().setValue(self.terminal_view.verticalScrollBar().maximum())
            except Exception as error:
                QMessageBox.critical(self, "Error", f"Failed to pull secure launch log:\n{error}")
            return
        
        if not ip or not user:
            QMessageBox.warning(self, "Failed", "SSH credentials are not configured. Please set them in the Mesa Utilities tab.")
            return

        default_log_path = "/tmp/linuxcnc_launch.log"
        log_path, ok = QInputDialog.getText(self, "Pull CNC PC Log", "Enter CNC PC log file path:", text=default_log_path)
        if not ok or not log_path:
            return
        
        try:
            self.chk_terminal_view.setChecked(True)
            if is_local_ip(ip):
                self.terminal_view.append(f"\n--- Pulled Local Log: {log_path} ---")
                try:
                    with open(log_path, 'r') as f:
                        content = f.read().strip()
                    self.terminal_view.append(content if content else "(File is empty)")
                except Exception as e:
                    self.terminal_view.append(f"(Could not read file or it does not exist: {e})")
                self.terminal_view.verticalScrollBar().setValue(self.terminal_view.verticalScrollBar().maximum())
                return

            cmd = f"cat {log_path}"
            out = ""
            err = ""
            if HAS_PARAMIKO:
                ssh = paramiko.SSHClient()
                ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                ssh.connect(ip, username=user, password=password, timeout=10, banner_timeout=15, auth_timeout=15, look_for_keys=not bool(password), allow_agent=not bool(password))
                stdin, stdout, stderr = ssh.exec_command(cmd)
                out = stdout.read().decode()
                err = stderr.read().decode()
                ssh.close()
            else:
                res = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", f"{user}@{ip}", cmd], capture_output=True, text=True, timeout=10)
                out = res.stdout
                err = res.stderr
                
            full_log = (out.strip() + "\n" + err.strip()).strip()
            
            self.terminal_view.append(f"\n--- Pulled CNC PC Log: {log_path} ---")
            self.terminal_view.append(full_log if full_log else "(File is empty or does not exist)")
            self.terminal_view.verticalScrollBar().setValue(self.terminal_view.verticalScrollBar().maximum())
            
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to pull log file:\n{e}")

    def stop_linuxcnc(self):
        confirm = QMessageBox.question(self, "Confirm Stop", 
                                       "Are you sure you want to shut down LinuxCNC?\n\n"
                                       "The normal action requests a graceful shutdown so LinuxCNC can clean up safely.", 
                                       QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if confirm != QMessageBox.StandardButton.Yes:
            return

        ip = self.sm.get_setting("SSH", "ip_address", "")
        user = self.sm.get_setting("SSH", "username", "")
        password = self.sm.get_setting("SSH", "password", "")
        agent_client = client_from_settings(self.sm)
        
        if agent_client is None and (not ip or not user):
            QMessageBox.warning(self, "Failed", "SSH credentials are not configured. Please set them in the Mesa Utilities tab.")
            return

        if hasattr(self.window(), 'sys_monitor_lbl'):
            self.window().sys_monitor_lbl.setText("⏳ Shutting down LinuxCNC smoothly... please wait.")
            self.window().sys_monitor_lbl.setStyleSheet("color: #f39c12; font-weight: bold; font-size: 14px; padding-right: 15px;")
            QApplication.processEvents()

        if agent_client is not None:
            try:
                lifecycle = LinuxCNCLifecycle(RemoteLinuxCNCBackend(agent_client))
                result = lifecycle.graceful_shutdown(timeout=15.0)
                if result.success:
                    QMessageBox.information(self, "LinuxCNC Stopped", result.message)
                else:
                    QMessageBox.critical(self, "Shutdown Failed", result.message)
            except Exception as error:
                QMessageBox.critical(self, "Shutdown Failed", f"Machine agent request failed: {error}")
            return

        base_cmd = "if pgrep linuxcnc > /dev/null; then python3 -c 'import linuxcnc; c=linuxcnc.command(); c.exit()' 2>/dev/null; for i in 1 2 3 4 5 6 7 8 9 10; do if ! pgrep linuxcnc > /dev/null; then break; fi; sleep 1; done; if pgrep linuxcnc > /dev/null; then killall -TERM linuxcnc linuxcncsvr milltask io 2>/dev/null; sleep 2; fi; if pgrep linuxcnc > /dev/null; then halrun -U 2>/dev/null; killall -9 linuxcnc linuxcncsvr milltask io halrun rtapi_app halcmd 2>/dev/null; rm -rf /tmp/linuxcnc.lock* /tmp/linuxcnc.print /tmp/linuxcnc.error 2>/dev/null; rm -f /dev/shm/linuxcnc* /dev/shm/rtapi* /dev/shm/hal* 2>/dev/null; fi; fi; pkill -TERM -f cncfusion_gui 2>/dev/null"
            
        try:
            if is_local_ip(ip):
                subprocess.run(base_cmd, shell=True, capture_output=True)
            elif HAS_PARAMIKO:
                if password:
                    cmd = f"echo '{password}' | sudo -S bash -c \"{base_cmd}\""
                else:
                    cmd = f"sudo bash -c \"{base_cmd}\""
                    
                ssh = paramiko.SSHClient()
                ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                ssh.connect(ip, username=user, password=password, timeout=10, banner_timeout=15, auth_timeout=15, look_for_keys=not bool(password), allow_agent=not bool(password))
                stdin, stdout, stderr = ssh.exec_command(cmd)
                stdout.channel.recv_exit_status()
                ssh.close()
            else:
                if password:
                    cmd = f"echo '{password}' | sudo -S bash -c \"{base_cmd}\""
                else:
                    cmd = f"sudo bash -c \"{base_cmd}\""
                    
                subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", f"{user}@{ip}", cmd], capture_output=True, timeout=15)
                
            QMessageBox.information(self, "Success", f"Stop command sent to {ip}!")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to stop LinuxCNC:\n{e}")
        finally:
            if hasattr(self.window(), 'sys_monitor_lbl'):
                self.window().sys_monitor_lbl.setStyleSheet("color: #ecf0f1; font-weight: bold; padding-right: 15px;")
                self.window().sys_monitor_lbl.setText("System Diagnostics Active")

    def launch_vnc(self):
        ip = self.sm.get_setting("SSH", "ip_address", "")
        user = self.sm.get_setting("SSH", "username", "")
        password = self.sm.get_setting("SSH", "password", "")
        
        if not ip or not user:
            QMessageBox.warning(self, "Launch Failed", "SSH credentials are not configured. Please set them in the Mesa Utilities tab.")
            return

        # Start x11vnc on the remote machine in the background
        cmd = "killall x11vnc; nohup x11vnc -display :0 -auth guess -bg -nopw -xkb -q </dev/null > /dev/null 2>&1 &"
        
        try:
            if is_local_ip(ip):
                subprocess.Popen(cmd, shell=True, start_new_session=True)
            elif HAS_PARAMIKO:
                ssh = paramiko.SSHClient()
                ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                ssh.connect(ip, username=user, password=password, timeout=10, banner_timeout=15, auth_timeout=15, look_for_keys=not bool(password), allow_agent=not bool(password))
                ssh.exec_command(cmd)
                ssh.close()
            else:
                subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", f"{user}@{ip}", cmd], capture_output=True, timeout=10)
                
            vnc_url = f"vnc://{ip}:5900"
            msg = (f"Remote display server started at {ip}:5900!\n\nIf your computer doesn't automatically open a viewer, use RealVNC or TightVNC to connect.\n\n(Note: If connection fails, run 'sudo apt install x11vnc' on the Linux machine first.)")
                   
            if platform.system() == "Darwin":
                subprocess.run(["open", vnc_url])
            elif platform.system() == "Windows":
                QMessageBox.information(self, "VNC Started", msg)
            else:
                subprocess.run(["xdg-open", vnc_url])
                    
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to start remote VNC server:\n{e}")