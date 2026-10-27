"""
Tool Change & Tool Setter Tab for LinuxCNC Pro.

Provides a dedicated graphical interface for:
- Machine Tool Setter physical location setup (Machine G53 X, Y, Z)
- Empty Spindle Nose height calibration (Z_gauge / baseline reference)
- Fixed Tool Setter switch height calibration (Z_setter)
- Mobile Workpiece Touch Plate thickness integration
- Dynamic tool length measurement: L = Z_touch - Z_gauge
- Automated Tool Table update (G10 L1 / G10 L10) & G43 tool offset apply
- Ergonomic Tool Change position management (M6 helper)
- Live CAD drafting diagram with spindle nose line, tool holder, cutter, setter, and contact status
"""

import math
import os
import time
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QPushButton,
    QGroupBox, QLineEdit, QDoubleSpinBox, QSpinBox, QCheckBox, QComboBox,
    QScrollArea, QFrame, QMessageBox, QTableWidget, QTableWidgetItem, QHeaderView,
    QSizePolicy
)
from PyQt6.QtCore import Qt, QTimer, pyqtSignal, QPointF, QRectF, QThread
from PyQt6.QtGui import QPainter, QColor, QPen, QBrush, QFont, QPolygonF, QLinearGradient
from core.machine_agent import client_from_settings


class ToolSetterDiagramWidget(QWidget):
    """
    Visual CAD drafting diagram displaying:
    - Spindle head casting and bearings
    - Empty spindle nose reference plane (Z_gauge)
    - Tool collet chuck and cutting tool
    - Tool Length dimension line (L = Z_touch - Z_gauge)
    - Fixed Tool Setter with base, column, and precision contact anvil
    - Live contact status LED (Ready vs Tripped)
    - Machine G53 Z=0 top limit and Safe Z Clearance plane
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(420, 360)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        # Dimension and coordinate parameters
        self.setter_x = 0.0
        self.setter_y = 0.0
        self.setter_z = 0.0
        self.spindle_nose_z = -140.0
        self.tool_length = 45.0
        self.tool_num = 1
        self.safe_z = 0.0
        self.gauge_block_h = 0.0
        self.setter_body_h = 50.0
        self.touch_plate_h = 15.0
        self.is_tripped = False
        self.has_tool = True
        self.mode = "idle"  # idle, measuring, spindle_calib, tool_change
        self.live_z = -140.0

    def update_parameters(self, **kwargs):
        for k, v in kwargs.items():
            if hasattr(self, k):
                setattr(self, k, v)
        self.update()

    def set_probe_status(self, tripped: bool):
        if self.is_tripped != tripped:
            self.is_tripped = tripped
            self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = float(self.width()), float(self.height())
        cx = w / 2.0

        # 1. Background Drafting Grid
        painter.fillRect(QRectF(0, 0, w, h), QColor("#1a1e24"))
        pen_grid = QPen(QColor("#262d35"), 1.0)
        painter.setPen(pen_grid)
        grid_step = 25
        for gx in range(0, int(w), grid_step):
            painter.drawLine(QPointF(gx, 0), QPointF(gx, h))
        for gy in range(0, int(h), grid_step):
            painter.drawLine(QPointF(0, gy), QPointF(w, gy))

        # 2. Machine Bed & Table Fixture (Bottom)
        y_bed = h - 45.0
        painter.setPen(QPen(QColor("#57606f"), 1.5))
        painter.setBrush(QBrush(QColor("#2f3542")))
        painter.drawRect(QRectF(15, y_bed, w - 30, 36))
        
        # T-Slots on bed
        painter.setPen(QPen(QColor("#1e272e"), 1.2))
        for ts_x in (cx - 150, cx - 75, cx + 75, cx + 150):
            if 25 < ts_x < w - 25:
                painter.drawRect(QRectF(ts_x - 8, y_bed, 16, 12))

        # 3. Fixed Tool Setter Unit on Table
        # Base plate
        painter.setPen(QPen(QColor("#747d8c"), 1.5))
        painter.setBrush(QBrush(QColor("#1e272e")))
        painter.drawRoundedRect(QRectF(cx - 38, y_bed - 12, 76, 12), 2, 2)
        
        # Setter column body
        grad_setter = QLinearGradient(cx - 24, 0, cx + 24, 0)
        grad_setter.setColorAt(0.0, QColor("#1e272e"))
        grad_setter.setColorAt(0.5, QColor("#2f3640"))
        grad_setter.setColorAt(1.0, QColor("#1e272e"))
        painter.setBrush(QBrush(grad_setter))
        painter.drawRect(QRectF(cx - 24, y_bed - 62, 48, 50))

        # Setter Anvil / Ground Contact Disc
        y_anvil = y_bed - 74.0
        anvil_brush = QBrush(QColor("#ff4757")) if self.is_tripped else QBrush(QColor("#ecf0f1"))
        painter.setPen(QPen(QColor("#dfe4ea"), 1.5))
        painter.setBrush(anvil_brush)
        painter.drawRoundedRect(QRectF(cx - 18, y_anvil, 36, 12), 2, 2)

        # Contact Sensor Trip LED on Setter
        led_color = QColor("#ff4757") if self.is_tripped else QColor("#2ecc71")
        painter.setPen(QPen(QColor("#ffffff"), 1.0))
        painter.setBrush(QBrush(led_color))
        painter.drawEllipse(QPointF(cx, y_bed - 37), 5.0, 5.0)

        # Tool Setter Reference Plane Line
        painter.setPen(QPen(QColor("#00adb5"), 1.3, Qt.PenStyle.DashLine))
        painter.drawLine(QPointF(25, y_anvil), QPointF(w - 25, y_anvil))
        painter.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
        painter.setPen(QColor("#00adb5"))
        painter.drawText(QPointF(30, y_anvil - 5), f"Tool Setter Plane: Z = {self.setter_z:+.4f} mm (G53)")

        # 4. Spindle Head / Quill (Top Center)
        y_nose = 145.0
        # Spindle Casting Body
        grad_spindle = QLinearGradient(cx - 50, 0, cx + 50, 0)
        grad_spindle.setColorAt(0.0, QColor("#2c3e50"))
        grad_spindle.setColorAt(0.5, QColor("#3d4e5d"))
        grad_spindle.setColorAt(1.0, QColor("#2c3e50"))
        painter.setPen(QPen(QColor("#7f8c8d"), 1.5))
        painter.setBrush(QBrush(grad_spindle))
        painter.drawRect(QRectF(cx - 50, 32, 100, y_nose - 48))

        # Cooling fins
        painter.setPen(QPen(QColor("#1e272e"), 1.0))
        for fy in range(45, int(y_nose) - 25, 12):
            painter.drawLine(QPointF(cx - 48, fy), QPointF(cx + 48, fy))

        # Spindle Nose Ring / Flange
        painter.setPen(QPen(QColor("#bdc3c7"), 1.5))
        painter.setBrush(QBrush(QColor("#7f8c8d")))
        painter.drawRect(QRectF(cx - 36, y_nose - 16, 72, 16))

        # 5. Spindle Nose Reference Gauge Plane
        painter.setPen(QPen(QColor("#f39c12"), 1.5, Qt.PenStyle.DashLine))
        painter.drawLine(QPointF(25, y_nose), QPointF(w - 25, y_nose))
        painter.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
        painter.setPen(QColor("#f39c12"))
        painter.drawText(QPointF(30, y_nose - 5), f"Spindle Nose Baseline (Z_gauge = {self.spindle_nose_z:+.4f} mm)")

        # 6. Mounted Tool & Length Dimension
        if self.has_tool:
            # Collet Nut
            painter.setPen(QPen(QColor("#2f3542"), 1.5))
            painter.setBrush(QBrush(QColor("#1e272e")))
            painter.drawPolygon(QPolygonF([
                QPointF(cx - 18, y_nose),
                QPointF(cx + 18, y_nose),
                QPointF(cx + 14, y_nose + 22),
                QPointF(cx - 14, y_nose + 22)
            ]))

            # Tool Shank & Cutter
            y_tip = y_anvil - 2.0 if self.mode == "measuring" else y_anvil - 35.0
            painter.setPen(QPen(QColor("#bdc3c7"), 1.5))
            painter.setBrush(QBrush(QColor("#ecf0f1")))
            painter.drawRect(QRectF(cx - 6, y_nose + 22, 12, max(10.0, y_tip - (y_nose + 22))))

            # Spiral flute accents on tool
            painter.setPen(QPen(QColor("#7f8c8d"), 1.0))
            for flute_y in range(int(y_nose + 26), int(y_tip) - 4, 8):
                painter.drawLine(QPointF(cx - 5, flute_y), QPointF(cx + 5, flute_y + 4))

            # Tool Length Dimension Line (Emerald Green)
            dim_x = cx + 65.0
            painter.setPen(QPen(QColor("#9ad58d"), 1.5))
            painter.drawLine(QPointF(dim_x, y_nose), QPointF(dim_x, y_tip))
            # Arrow heads
            self._draw_dim_ticks(painter, dim_x, y_nose, y_tip, QColor("#9ad58d"))
            painter.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
            painter.setPen(QColor("#9ad58d"))
            painter.drawText(QPointF(dim_x + 8, (y_nose + y_tip) / 2 + 4), f"Tool Length L = {self.tool_length:.4f} mm")

            # Trajectory Arrows during measurement
            if self.mode == "measuring":
                self._draw_motion_arrow(painter, cx, y_nose + 15, cx, y_anvil - 8, QColor("#00adb5"))

        else:
            # Gauge block calibration view
            if self.gauge_block_h > 0:
                gb_h = 30.0
                painter.setPen(QPen(QColor("#f39c12"), 1.5))
                painter.setBrush(QBrush(QColor("#d35400")))
                painter.drawRect(QRectF(cx - 15, y_anvil - gb_h, 30, gb_h))
                painter.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
                painter.setPen(QColor("#ffffff"))
                painter.drawText(QRectF(cx - 30, y_anvil - gb_h, 60, gb_h), Qt.AlignmentFlag.AlignCenter, f"Gauge\n{self.gauge_block_h:.1f}")

        # 7. Safe Z Clearance Line (Top Limit)
        y_safe = 50.0
        painter.setPen(QPen(QColor("#2ecc71"), 1.0, Qt.PenStyle.DashDotLine))
        painter.drawLine(QPointF(25, y_safe), QPointF(w - 25, y_safe))
        painter.setFont(QFont("Segoe UI", 8))
        painter.setPen(QColor("#2ecc71"))
        painter.drawText(QPointF(w - 180, y_safe - 4), f"Safe Travel Plane: Z = {self.safe_z:+.1f} mm")

        # 8. Top Status Summary Banner
        painter.setPen(QPen(QColor("#3d3d3d"), 1.0))
        painter.setBrush(QBrush(QColor("#111111")))
        painter.drawRoundedRect(QRectF(10, 6, w - 20, 24), 3, 3)
        painter.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
        status_txt = (
            f"Setter: ({self.setter_x:.1f}, {self.setter_y:.1f}) | "
            f"Spindle Gauge: {self.spindle_nose_z:+.3f} | "
            f"Tool: T{self.tool_num} (L={self.tool_length:.3f} mm) | "
            f"Sensor: {'TRIPPED (CONTACT)' if self.is_tripped else 'CLEAR (READY)'}"
        )
        painter.setPen(QColor("#ff4757") if self.is_tripped else QColor("#ecf0f1"))
        painter.drawText(QRectF(15, 7, w - 30, 22), Qt.AlignmentFlag.AlignCenter, status_txt)

    def _draw_dim_ticks(self, painter, x, y1, y2, color):
        painter.save()
        painter.setPen(QPen(color, 1.5))
        painter.drawLine(QPointF(x - 5, y1), QPointF(x + 5, y1))
        painter.drawLine(QPointF(x - 5, y2), QPointF(x + 5, y2))
        # Top arrow
        painter.setBrush(QBrush(color))
        painter.drawPolygon(QPolygonF([QPointF(x, y1), QPointF(x - 3, y1 + 6), QPointF(x + 3, y1 + 6)]))
        # Bottom arrow
        painter.drawPolygon(QPolygonF([QPointF(x, y2), QPointF(x - 3, y2 - 6), QPointF(x + 3, y2 - 6)]))
        painter.restore()

    def _draw_motion_arrow(self, painter, x1, y1, x2, y2, color):
        painter.save()
        pen = QPen(color, 2.0, Qt.PenStyle.SolidLine)
        painter.setPen(pen)
        painter.drawLine(QPointF(x1, y1), QPointF(x2, y2))
        angle = math.atan2(y2 - y1, x2 - x1)
        p1 = QPointF(x2 - 6 * math.cos(angle - math.pi / 6), y2 - 6 * math.sin(angle - math.pi / 6))
        p2 = QPointF(x2 - 6 * math.cos(angle + math.pi / 6), y2 - 6 * math.sin(angle + math.pi / 6))
        painter.setBrush(QBrush(color))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawPolygon(QPolygonF([QPointF(x2, y2), p1, p2]))
        painter.restore()


class ToolSetterMotionWorker(QThread):
    """
    Background worker that runs multi-step G-code motion sequences (e.g. Move to Setter,
    Auto-Probe Baseline, Measure Tool) and waits for machine completion between moves
    so LinuxCNC never rejects MDI with 'interp_state != INTERP_IDLE'.
    """
    step_started = pyqtSignal(str)
    step_progress = pyqtSignal(int, int)
    finished = pyqtSignal(bool, str, dict)

    def __init__(self, mode, steps, payload=None, dashboard=None):
        super().__init__()
        self.mode = mode
        self.steps = steps
        self.payload = payload or {}
        self.dashboard = dashboard
        self._is_aborted = False

    def abort(self):
        self._is_aborted = True

    def run(self):
        try:
            import linuxcnc
            c = linuxcnc.command()
            s = linuxcnc.stat()
            e = getattr(linuxcnc, 'error_channel', lambda: None)()
        except Exception:
            c = None
            s = None
            e = None

        def wait_idle(timeout=180):
            if not s:
                time.sleep(0.05)
                return True
            start = time.time()
            time.sleep(0.08)
            while time.time() - start < timeout:
                if self._is_aborted:
                    if c:
                        try: c.abort()
                        except Exception: pass
                    return False
                s.poll()
                if e:
                    err = e.poll()
                    if err:
                        raise RuntimeError(str(err[1]))
                if s.task_state == linuxcnc.STATE_ESTOP:
                    raise RuntimeError("Machine entered E-STOP state.")
                if s.interp_state == linuxcnc.INTERP_IDLE:
                    return True
                time.sleep(0.04)
            raise TimeoutError("Motion timed out waiting for machine completion.")

        total_steps = len(self.steps)
        probed_z = None

        try:
            for idx, step_info in enumerate(self.steps):
                if self._is_aborted:
                    self.finished.emit(False, "Motion aborted by operator.", self.payload)
                    return

                cmd = step_info.get('cmd', '')
                desc = step_info.get('desc', cmd)
                is_probe = step_info.get('is_probe', False)

                self.step_started.emit(f"Step {idx+1}/{total_steps}: {desc}")
                self.step_progress.emit(idx + 1, total_steps)

                if c and s:
                    s.poll()
                    if s.task_mode != linuxcnc.MODE_MDI:
                        c.mode(linuxcnc.MODE_MDI)
                        wait_idle(5)
                    c.mdi(cmd)
                    wait_idle(180)

                    if is_probe:
                        s.poll()
                        if not s.probe_tripped:
                            raise RuntimeError("Probe moved full distance without making contact with the tool setter.")
                        probed_z = s.probed_position[2]
                elif self.dashboard and hasattr(self.dashboard, 'execute_mdi'):
                    self.dashboard.execute_mdi(cmd)
                    if is_probe and hasattr(self.dashboard, 'mock_coords'):
                        probed_z = float(self.dashboard.mock_coords.get('ABSOLUTE_Z', -90.0))

                time.sleep(0.04)

            self.payload['probed_z'] = probed_z
            self.finished.emit(True, "Sequence completed successfully.", self.payload)
        except Exception as error:
            self.finished.emit(False, f"Motion failed: {error}", self.payload)


class ToolChangeTab(QWidget):
    """
    Dedicated Tool Change & Tool Setter Management Tab.

    Provides setup and calibration for:
    1. Tool Setter Position (Machine G53 X, Y, Z)
    2. Empty Spindle Nose Gauge Calibration (Z_gauge)
    3. Tool Length Measurement (L = Z_touch - Z_gauge)
    4. Safe G53 Rapid Traversal & Ergonomic M6 Tool Change Location
    5. Automatic Tool Table Updates (G10 L1) and G43 Tool Offset Application
    """

    tool_measured = pyqtSignal(int, float)

    def __init__(self, settings, dashboard=None, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.dashboard = dashboard
        self.is_measuring = False
        self.is_moving = False
        self.motion_worker = None
        self.last_measured_tool = 1
        self.last_measured_length = 0.0
        self._is_loading = False

        self.init_ui()
        self.load_settings()

        # Telemetry update timer
        self.poll_timer = QTimer(self)
        self.poll_timer.timeout.connect(self._poll_telemetry)
        self.poll_timer.start(100)

    def init_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(12, 10, 12, 10)
        main_layout.setSpacing(10)

        self.setStyleSheet("""
            QWidget { background-color: #242424; color: #e0e0e0; font-family: 'Segoe UI', Arial, sans-serif; }
            QGroupBox { font-weight: bold; border: 1px solid #3d3d3d; border-radius: 6px; margin-top: 8px; }
            QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 5px; color: #9ad58d; font-size: 11px; }
            QDoubleSpinBox, QSpinBox, QLineEdit {
                background-color: #151515; color: white; border: 1px solid #3d3d3d; padding: 4px 6px; border-radius: 3px; font-weight: bold;
            }
            QDoubleSpinBox:focus, QLineEdit:focus { border: 1px solid #00adb5; }
        """)

        # 1. TOP HEADER & HIGH-CONTRAST DIGITAL DRO BAR
        header_frame = QFrame()
        header_frame.setStyleSheet("background-color: #1a1a1a; border: 1px solid #333333; border-radius: 6px; padding: 4px;")
        header_layout = QHBoxLayout(header_frame)
        header_layout.setContentsMargins(10, 6, 10, 6)
        header_layout.setSpacing(12)

        lbl_title = QLabel("🔄 TOOL CHANGE & TOOL SETTER SETUP")
        lbl_title.setStyleSheet("font-size: 14px; font-weight: bold; color: #9ad58d; letter-spacing: 0.5px;")
        header_layout.addWidget(lbl_title)

        header_layout.addStretch()

        # Machine Coordinates DROs
        dro_box = QHBoxLayout()
        dro_box.setSpacing(6)
        self.dro_mach_x = QLabel("G53 X: +000.0000")
        self.dro_mach_y = QLabel("G53 Y: +000.0000")
        self.dro_mach_z = QLabel("G53 Z: +000.0000")
        for lbl in (self.dro_mach_x, self.dro_mach_y, self.dro_mach_z):
            lbl.setStyleSheet("""
                background-color: #111111; color: #00adb5; font-family: 'Consolas', monospace;
                font-size: 13px; font-weight: bold; padding: 4px 8px; border: 1px solid #2a2a2a; border-radius: 4px;
            """)
            dro_box.addWidget(lbl)
        header_layout.addLayout(dro_box)

        # Probe Source Toggle (Tool Setter vs 3D Probe)
        self.btn_probe_source = QPushButton("🎯 Source: Tool Setter")
        self.btn_probe_source.setStyleSheet(self._btn_style("#1b4332", "#2d6a4f", "#9ad58d"))
        self.btn_probe_source.setToolTip("Toggle active probe hardware between Fixed Tool Setter (lut5.0.in-2=0) and 3D Probe (lut5.0.in-2=1)")
        self.btn_probe_source.clicked.connect(self.toggle_probe_source)
        header_layout.addWidget(self.btn_probe_source)

        # Invert Sensor LED logic (for NC vs NO switches)
        self.chk_invert_led = QCheckBox("Invert LED (NC)")
        self.chk_invert_led.setToolTip("Check if your tool setter switch is Normally Closed (NC), uncheck if Normally Open (NO)")
        self.chk_invert_led.setStyleSheet("color: #bbbbbb; font-size: 11px; font-weight: bold;")
        self.chk_invert_led.stateChanged.connect(self._on_param_changed)
        header_layout.addWidget(self.chk_invert_led)

        # Contact Sensor Trip Status Badge
        self.lbl_sensor_badge = QLabel("🟢 SETTER READY")
        self.lbl_sensor_badge.setStyleSheet("""
            background-color: #163b2f; color: #2ecc71; font-weight: bold; font-size: 12px;
            padding: 5px 12px; border: 1px solid #2ecc71; border-radius: 4px;
        """)
        header_layout.addWidget(self.lbl_sensor_badge)

        main_layout.addWidget(header_frame)

        # Real-time Motion & Sequencing Status Banner
        self.lbl_motion_status = QLabel("🟢 System Ready — Spindle & Tool Setter Idle")
        self.lbl_motion_status.setStyleSheet("""
            background-color: #16241b; color: #9ad58d; font-family: 'Consolas', monospace;
            font-size: 11px; font-weight: bold; padding: 5px 10px; border: 1px solid #234d31; border-radius: 4px;
        """)
        main_layout.addWidget(self.lbl_motion_status)

        # 2. BODY LAYOUT (3 Columns: Parameters & Setup / Center Diagram / Operations & Results)
        body_layout = QHBoxLayout()
        body_layout.setSpacing(10)

        # === COLUMN 1: SETUP & CALIBRATION PARAMETERS ===
        col_left = QWidget()
        left_layout = QVBoxLayout(col_left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(8)

        scroll_left = QScrollArea()
        scroll_left.setWidgetResizable(True)
        scroll_left.setStyleSheet("QScrollArea { border: none; background: transparent; }")
        scroll_content = QWidget()
        scroll_layout = QVBoxLayout(scroll_content)
        scroll_layout.setContentsMargins(0, 0, 0, 0)
        scroll_layout.setSpacing(8)

        # --- Section 1: Tool Setter Physical Location (G53) ---
        grp_setter_loc = QGroupBox("1. Fixed Tool Setter Location (G53)")
        loc_grid = QGridLayout(grp_setter_loc)
        loc_grid.setSpacing(6)

        self.spin_setter_x = QDoubleSpinBox()
        self.spin_setter_x.setRange(-10000.0, 10000.0)
        self.spin_setter_x.setDecimals(4)
        self.spin_setter_x.valueChanged.connect(self._on_param_changed)

        self.spin_setter_y = QDoubleSpinBox()
        self.spin_setter_y.setRange(-10000.0, 10000.0)
        self.spin_setter_y.setDecimals(4)
        self.spin_setter_y.valueChanged.connect(self._on_param_changed)

        self.spin_setter_z = QDoubleSpinBox()
        self.spin_setter_z.setRange(-10000.0, 10000.0)
        self.spin_setter_z.setDecimals(4)
        self.spin_setter_z.valueChanged.connect(self._on_param_changed)

        loc_grid.addWidget(QLabel("Machine X:"), 0, 0); loc_grid.addWidget(self.spin_setter_x, 0, 1)
        loc_grid.addWidget(QLabel("Machine Y:"), 1, 0); loc_grid.addWidget(self.spin_setter_y, 1, 1)
        loc_grid.addWidget(QLabel("Approach Z:"), 2, 0); loc_grid.addWidget(self.spin_setter_z, 2, 1)

        btn_cap_xy = QPushButton("🎯 Capture Current Machine X/Y")
        btn_cap_xy.setStyleSheet(self._btn_style("#1b4332", "#2d6a4f", "#9ad58d"))
        btn_cap_xy.clicked.connect(self.capture_setter_xy)
        loc_grid.addWidget(btn_cap_xy, 3, 0, 1, 2)

        btn_cap_z = QPushButton("🎯 Capture Current Machine Z")
        btn_cap_z.setStyleSheet(self._btn_style("#1b4332", "#2d6a4f", "#9ad58d"))
        btn_cap_z.clicked.connect(self.capture_setter_z)
        loc_grid.addWidget(btn_cap_z, 4, 0, 1, 2)

        self.btn_go_setter = QPushButton("🚀 Move to Tool Setter (Safe G53)")
        self.btn_go_setter.setStyleSheet(self._btn_style("#1a365d", "#2b4c7e", "#63b3ed"))
        self.btn_go_setter.clicked.connect(self.move_to_setter)
        loc_grid.addWidget(self.btn_go_setter, 5, 0, 1, 2)

        scroll_layout.addWidget(grp_setter_loc)

        # --- Section 2: Spindle Nose Baseline Calibration (Z_gauge) ---
        grp_spindle_cal = QGroupBox("2. Empty Spindle Nose Baseline (Z_gauge)")
        spindle_grid = QGridLayout(grp_spindle_cal)
        spindle_grid.setSpacing(6)

        self.spin_spindle_nose_z = QDoubleSpinBox()
        self.spin_spindle_nose_z.setRange(-10000.0, 10000.0)
        self.spin_spindle_nose_z.setDecimals(4)
        self.spin_spindle_nose_z.setValue(-140.0)
        self.spin_spindle_nose_z.valueChanged.connect(self._on_param_changed)

        self.spin_gauge_block_h = QDoubleSpinBox()
        self.spin_gauge_block_h.setRange(0.0, 500.0)
        self.spin_gauge_block_h.setDecimals(4)
        self.spin_gauge_block_h.setValue(0.0)
        self.spin_gauge_block_h.valueChanged.connect(self._on_param_changed)

        self.spin_setter_body_h = QDoubleSpinBox()
        self.spin_setter_body_h.setRange(0.0, 500.0)
        self.spin_setter_body_h.setDecimals(4)
        self.spin_setter_body_h.setValue(50.0)
        self.spin_setter_body_h.valueChanged.connect(self._on_param_changed)

        spindle_grid.addWidget(QLabel("Spindle Nose Z:"), 0, 0); spindle_grid.addWidget(self.spin_spindle_nose_z, 0, 1)
        spindle_grid.addWidget(QLabel("Gauge Block / Feeler:"), 1, 0); spindle_grid.addWidget(self.spin_gauge_block_h, 1, 1)
        spindle_grid.addWidget(QLabel("Setter Body H:"), 2, 0); spindle_grid.addWidget(self.spin_setter_body_h, 2, 1)

        btn_set_spindle = QPushButton("📏 Set Current Z as Spindle Nose")
        btn_set_spindle.setStyleSheet(self._btn_style("#6e3c0e", "#b07d1a", "#f39c12"))
        btn_set_spindle.clicked.connect(self.capture_spindle_nose_z)
        spindle_grid.addWidget(btn_set_spindle, 3, 0, 1, 2)

        self.btn_probe_spindle = QPushButton("🔍 Auto-Probe Spindle Baseline")
        self.btn_probe_spindle.setStyleSheet(self._btn_style("#1b4332", "#2d6a4f", "#9ad58d"))
        self.btn_probe_spindle.clicked.connect(self.auto_probe_spindle_baseline)
        spindle_grid.addWidget(self.btn_probe_spindle, 4, 0, 1, 2)

        scroll_layout.addWidget(grp_spindle_cal)

        # --- Section 3: Motion & Travel Limits ---
        grp_motion = QGroupBox("3. Motion Velocities & Safe Clearance")
        motion_grid = QGridLayout(grp_motion)
        motion_grid.setSpacing(6)

        self.spin_safe_z = QDoubleSpinBox()
        self.spin_safe_z.setRange(-1000.0, 1000.0)
        self.spin_safe_z.setDecimals(2)
        self.spin_safe_z.setValue(0.0)
        self.spin_safe_z.valueChanged.connect(self._on_param_changed)

        self.spin_search_feed = QDoubleSpinBox()
        self.spin_search_feed.setRange(1.0, 5000.0)
        self.spin_search_feed.setDecimals(1)
        self.spin_search_feed.setValue(300.0)
        self.spin_search_feed.valueChanged.connect(self._on_param_changed)

        self.spin_latch_feed = QDoubleSpinBox()
        self.spin_latch_feed.setRange(1.0, 1000.0)
        self.spin_latch_feed.setDecimals(1)
        self.spin_latch_feed.setValue(25.0)
        self.spin_latch_feed.valueChanged.connect(self._on_param_changed)

        self.spin_retract_dist = QDoubleSpinBox()
        self.spin_retract_dist.setRange(0.5, 50.0)
        self.spin_retract_dist.setDecimals(2)
        self.spin_retract_dist.setValue(3.0)
        self.spin_retract_dist.valueChanged.connect(self._on_param_changed)

        self.spin_max_travel = QDoubleSpinBox()
        self.spin_max_travel.setRange(5.0, 500.0)
        self.spin_max_travel.setDecimals(1)
        self.spin_max_travel.setValue(60.0)
        self.spin_max_travel.valueChanged.connect(self._on_param_changed)

        motion_grid.addWidget(QLabel("Safe Clearance Z:"), 0, 0); motion_grid.addWidget(self.spin_safe_z, 0, 1)
        motion_grid.addWidget(QLabel("Search Feed:"), 1, 0); motion_grid.addWidget(self.spin_search_feed, 1, 1)
        motion_grid.addWidget(QLabel("Latch Feed:"), 2, 0); motion_grid.addWidget(self.spin_latch_feed, 2, 1)
        motion_grid.addWidget(QLabel("Retract Dist:"), 3, 0); motion_grid.addWidget(self.spin_retract_dist, 3, 1)
        motion_grid.addWidget(QLabel("Max Search Dist:"), 4, 0); motion_grid.addWidget(self.spin_max_travel, 4, 1)

        scroll_layout.addWidget(grp_motion)

        # --- Section 4: Ergonomic Tool Change Position ---
        grp_change_pos = QGroupBox("4. Manual Tool Change Location")
        change_grid = QGridLayout(grp_change_pos)
        change_grid.setSpacing(6)

        self.spin_change_x = QDoubleSpinBox(); self.spin_change_x.setRange(-10000.0, 10000.0); self.spin_change_x.setDecimals(4)
        self.spin_change_y = QDoubleSpinBox(); self.spin_change_y.setRange(-10000.0, 10000.0); self.spin_change_y.setDecimals(4)
        self.spin_change_z = QDoubleSpinBox(); self.spin_change_z.setRange(-10000.0, 10000.0); self.spin_change_z.setDecimals(4)
        for s in (self.spin_change_x, self.spin_change_y, self.spin_change_z):
            s.valueChanged.connect(self._on_param_changed)

        change_grid.addWidget(QLabel("Change X:"), 0, 0); change_grid.addWidget(self.spin_change_x, 0, 1)
        change_grid.addWidget(QLabel("Change Y:"), 1, 0); change_grid.addWidget(self.spin_change_y, 1, 1)
        change_grid.addWidget(QLabel("Change Z:"), 2, 0); change_grid.addWidget(self.spin_change_z, 2, 1)

        btn_cap_change = QPushButton("🎯 Current as Change Pos")
        btn_cap_change.setStyleSheet(self._btn_style("#1b4332", "#2d6a4f", "#9ad58d"))
        btn_cap_change.clicked.connect(self.capture_change_pos)
        change_grid.addWidget(btn_cap_change, 3, 0, 1, 2)

        self.btn_go_change = QPushButton("🚶 Move to Tool Change Position")
        self.btn_go_change.setStyleSheet(self._btn_style("#1a365d", "#2b4c7e", "#63b3ed"))
        self.btn_go_change.clicked.connect(self.move_to_change_pos)
        change_grid.addWidget(self.btn_go_change, 4, 0, 1, 2)

        self.chk_auto_m6 = QCheckBox("Automatically probe new tool on M6 change")
        self.chk_auto_m6.setStyleSheet("color: #9ad58d; font-size: 11px;")
        self.chk_auto_m6.stateChanged.connect(self._on_param_changed)
        change_grid.addWidget(self.chk_auto_m6, 5, 0, 1, 2)

        scroll_layout.addWidget(grp_change_pos)
        scroll_layout.addStretch()

        scroll_left.setWidget(scroll_content)
        left_layout.addWidget(scroll_left)
        col_left.setFixedWidth(330)
        body_layout.addWidget(col_left)

        # === COLUMN 2: CENTER CAD DRAFTING DIAGRAM & GUIDANCE ===
        center_group = QGroupBox("Tool Setter Visual Schematic")
        center_layout = QVBoxLayout(center_group)
        center_layout.setSpacing(8)

        self.diagram = ToolSetterDiagramWidget()
        center_layout.addWidget(self.diagram, 1)

        # Guided Step-by-Step Box
        guide_box = QFrame()
        guide_box.setStyleSheet("background-color: #1a1a1a; border: 1px solid #333333; border-radius: 4px; padding: 8px;")
        guide_layout = QVBoxLayout(guide_box)
        guide_layout.setSpacing(4)

        self.lbl_guide_title = QLabel("Initial Setup Sequence:")
        self.lbl_guide_title.setStyleSheet("color: #9ad58d; font-weight: bold; font-size: 12px;")
        guide_layout.addWidget(self.lbl_guide_title)

        self.lbl_guide_text = QLabel(
            "1. Jog empty spindle nose over Tool Setter and record X/Y.\n"
            "2. Touch empty spindle nose to setter face (or feeler block) and click 'Calibrate Spindle Nose Z'.\n"
            "3. Insert cutting tool into collet and click 'MEASURE CURRENT TOOL'.\n"
            "4. Tool Length is automatically calculated: L = Z_touch - Z_gauge and stored in Tool Table."
        )
        self.lbl_guide_text.setStyleSheet("color: #b0b0b0; font-size: 11px;")
        self.lbl_guide_text.setWordWrap(True)
        guide_layout.addWidget(self.lbl_guide_text)

        center_layout.addWidget(guide_box)
        body_layout.addWidget(center_group, 1)

        # === COLUMN 3: OPERATIONS, TOOL MEASUREMENT & LOG ===
        col_right = QWidget()
        right_layout = QVBoxLayout(col_right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(8)

        # Active Tool Specification
        grp_tool_spec = QGroupBox("Active Tool Specifications")
        tool_spec_grid = QGridLayout(grp_tool_spec)
        tool_spec_grid.setSpacing(6)

        self.spin_tool_num = QSpinBox()
        self.spin_tool_num.setRange(1, 999)
        self.spin_tool_num.setValue(1)
        self.spin_tool_num.valueChanged.connect(self._on_tool_num_changed)

        self.inp_tool_desc = QLineEdit("6mm 2-Flute Flat Endmill")
        self.inp_tool_diam = QDoubleSpinBox()
        self.inp_tool_diam.setRange(0.1, 100.0)
        self.inp_tool_diam.setDecimals(3)
        self.inp_tool_diam.setValue(6.0)

        tool_spec_grid.addWidget(QLabel("Tool Number (T#):"), 0, 0); tool_spec_grid.addWidget(self.spin_tool_num, 0, 1)
        tool_spec_grid.addWidget(QLabel("Description:"), 1, 0); tool_spec_grid.addWidget(self.inp_tool_desc, 1, 1)
        tool_spec_grid.addWidget(QLabel("Diameter (D):"), 2, 0); tool_spec_grid.addWidget(self.inp_tool_diam, 2, 1)

        right_layout.addWidget(grp_tool_spec)

        # Measured Length Readout Card
        card_len = QFrame()
        card_len.setStyleSheet("background-color: #111111; border: 1.5px solid #2ecc71; border-radius: 6px; padding: 8px;")
        card_len_layout = QVBoxLayout(card_len)
        card_len_layout.setSpacing(2)

        lbl_tlo_title = QLabel("CALCULATED TOOL LENGTH (L):")
        lbl_tlo_title.setStyleSheet("color: #888888; font-size: 10px; font-weight: bold;")
        card_len_layout.addWidget(lbl_tlo_title)

        self.lbl_length_display = QLabel("+000.0000 mm")
        self.lbl_length_display.setStyleSheet("color: #9ad58d; font-family: 'Consolas', monospace; font-size: 20px; font-weight: bold;")
        card_len_layout.addWidget(self.lbl_length_display)

        right_layout.addWidget(card_len)

        # Big Tactile Action Buttons
        self.btn_measure_tool = QPushButton("⚡ MEASURE CURRENT TOOL")
        self.btn_measure_tool.setStyleSheet("""
            QPushButton {
                background: qlineargradient(spread:pad, x1:0, y1:1, x2:0, y2:0, stop:0 #1b4332, stop:1 #2d6a4f);
                color: #ffffff; font-weight: bold; padding: 12px; border: 1.5px solid #9ad58d; border-radius: 5px; font-size: 13px;
            }
            QPushButton:hover { background: #2d6a4f; border-color: #52b788; }
            QPushButton:pressed { background: #081c15; }
        """)
        self.btn_measure_tool.clicked.connect(self.measure_tool_length)
        right_layout.addWidget(self.btn_measure_tool)

        btn_save_tool_table = QPushButton("💾 UPDATE TOOL TABLE (G10 L1)")
        btn_save_tool_table.setStyleSheet(self._btn_style("#1a365d", "#2b4c7e", "#63b3ed"))
        btn_save_tool_table.clicked.connect(self.save_tool_table_offset)
        right_layout.addWidget(btn_save_tool_table)

        btn_apply_g43 = QPushButton("🔄 APPLY G43 TOOL OFFSET")
        btn_apply_g43.setStyleSheet(self._btn_style("#6e3c0e", "#b07d1a", "#f39c12"))
        btn_apply_g43.clicked.connect(self.apply_g43_offset)
        right_layout.addWidget(btn_apply_g43)

        self.btn_abort = QPushButton("⏹ ABORT MOTION")
        self.btn_abort.setStyleSheet("""
            QPushButton {
                background: qlineargradient(spread:pad, x1:0, y1:1, x2:0, y2:0, stop:0 #5c1d1d, stop:1 #8b2626);
                color: #ffffff; font-weight: bold; padding: 8px; border: 1px solid #e74c3c; border-radius: 4px; font-size: 12px;
            }
            QPushButton:hover { background: #a82828; }
        """)
        self.btn_abort.clicked.connect(self.abort_motion)
        right_layout.addWidget(self.btn_abort)

        # Workpiece Touch Plate Sync Section
        grp_workpiece = QGroupBox("Workpiece Reference Sync")
        wp_grid = QGridLayout(grp_workpiece)
        wp_grid.setSpacing(6)

        self.spin_touch_plate_h = QDoubleSpinBox()
        self.spin_touch_plate_h.setRange(0.0, 200.0)
        self.spin_touch_plate_h.setDecimals(4)
        self.spin_touch_plate_h.setValue(15.0)
        self.spin_touch_plate_h.valueChanged.connect(self._on_param_changed)

        wp_grid.addWidget(QLabel("Touch Plate Thickness:"), 0, 0); wp_grid.addWidget(self.spin_touch_plate_h, 0, 1)

        btn_touch_wp = QPushButton("📐 Touch Off Workpiece Z0")
        btn_touch_wp.setStyleSheet(self._btn_style("#4a154b", "#6c1d70", "#e056fd"))
        btn_touch_wp.clicked.connect(self.touch_off_workpiece)
        wp_grid.addWidget(btn_touch_wp, 1, 0, 1, 2)

        right_layout.addWidget(grp_workpiece)

        # Measurement History Table Log
        grp_log = QGroupBox("Measurement History")
        log_layout = QVBoxLayout(grp_log)
        log_layout.setContentsMargins(4, 6, 4, 4)

        self.tbl_log = QTableWidget(0, 4)
        self.tbl_log.setHorizontalHeaderLabels(["Tool", "Length (mm)", "Touch Z", "Time"])
        self.tbl_log.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.tbl_log.setStyleSheet("""
            QTableWidget { background-color: #151515; gridline-color: #2a2a2a; color: #ecf0f1; font-size: 11px; }
            QHeaderView::section { background-color: #222222; color: #9ad58d; font-weight: bold; border: 1px solid #333333; }
        """)
        log_layout.addWidget(self.tbl_log)
        right_layout.addWidget(grp_log, 1)

        col_right.setFixedWidth(320)
        body_layout.addWidget(col_right)

        main_layout.addLayout(body_layout, 1)

    def _btn_style(self, bg0, bg1, border):
        return f"""
            QPushButton {{
                background: qlineargradient(spread:pad, x1:0, y1:1, x2:0, y2:0, stop:0 {bg0}, stop:1 {bg1});
                color: #ffffff; font-weight: bold; padding: 8px; border: 1px solid {border}; border-radius: 4px; font-size: 11px;
            }}
            QPushButton:hover {{ border-color: #ffffff; }}
            QPushButton:pressed {{ background: #111111; }}
        """

    def _on_param_changed(self):
        if getattr(self, '_is_loading', False):
            return
        self._sync_diagram()
        self.save_settings()

    def _on_tool_num_changed(self, tool_num):
        self.diagram.tool_num = tool_num
        self.diagram.update()

    def _sync_diagram(self):
        self.diagram.update_parameters(
            setter_x=self.spin_setter_x.value(),
            setter_y=self.spin_setter_y.value(),
            setter_z=self.spin_setter_z.value(),
            spindle_nose_z=self.spin_spindle_nose_z.value(),
            safe_z=self.spin_safe_z.value(),
            gauge_block_h=self.spin_gauge_block_h.value(),
            setter_body_h=self.spin_setter_body_h.value(),
            touch_plate_h=self.spin_touch_plate_h.value(),
            tool_num=self.spin_tool_num.value(),
            tool_length=self.last_measured_length
        )

    def select_tool_setter_hardware(self):
        """Ensure multiplexer lut5.0.in-2 is set to 0 (Tool Setter source)."""
        self.active_probe_source = "setter"
        if hasattr(self, 'btn_probe_source'):
            self.btn_probe_source.setText("🎯 Source: Tool Setter (Active)")
            self.btn_probe_source.setStyleSheet(self._btn_style("#1b4332", "#2d6a4f", "#9ad58d"))
        if self.dashboard:
            if hasattr(self.dashboard, 'switch_probe_hardware'):
                try:
                    self.dashboard.switch_probe_hardware(0)
                except Exception:
                    pass
            elif hasattr(self.dashboard, 'send_halcmd_setp'):
                try:
                    self.dashboard.send_halcmd_setp("lut5.0.in-2", "0")
                except Exception:
                    pass
        try:
            import subprocess
            subprocess.run(["halcmd", "setp", "lut5.0.in-2", "0"], capture_output=True, timeout=1)
        except Exception:
            pass

    def toggle_probe_source(self):
        """Toggle active probe source between Tool Setter (0) and 3D Probe (1)."""
        if getattr(self, 'active_probe_source', 'setter') == 'setter':
            self.active_probe_source = 'probe'
            if hasattr(self, 'btn_probe_source'):
                self.btn_probe_source.setText("🟠 Source: 3D Touch Probe")
                self.btn_probe_source.setStyleSheet(self._btn_style("#6e3c0e", "#b07d1a", "#f39c12"))
            if self.dashboard and hasattr(self.dashboard, 'switch_probe_hardware'):
                self.dashboard.switch_probe_hardware(1)
            elif self.dashboard and hasattr(self.dashboard, 'send_halcmd_setp'):
                self.dashboard.send_halcmd_setp("lut5.0.in-2", "1")
            try:
                import subprocess
                subprocess.run(["halcmd", "setp", "lut5.0.in-2", "1"], capture_output=True, timeout=1)
            except Exception:
                pass
        else:
            self.select_tool_setter_hardware()

    def showEvent(self, event):
        super().showEvent(event)
        self.select_tool_setter_hardware()

    def _get_z_limits(self):
        """Retrieve configured Z axis machine limits (default -134.0 min, +5.0 max)."""
        z_min = -134.0
        z_max = 5.0
        if self.settings:
            try:
                z_min = float(self.settings.get_setting("AXIS_Z", "min", "-134.0"))
                z_max = float(self.settings.get_setting("AXIS_Z", "max", "5.0"))
            except (ValueError, TypeError):
                pass
        return z_min, z_max

    def _calc_safe_probe_travel(self, current_mach_z, requested_travel):
        """
        Calculate safe downward probe travel bounded by Joint 3 (Z) negative limit.
        Guarantees that (current_mach_z - safe_travel) >= z_min + 1.0mm,
        preventing 'Probe move on line 0 would exceed joint 3's negative limit'.
        """
        z_min, _ = self._get_z_limits()
        # Remaining distance to soft limit with 1.0mm safety cushion
        remaining_travel = current_mach_z - (z_min + 1.0)
        if remaining_travel <= 0.5:
            return 0.0, z_min
        safe_travel = min(requested_travel, remaining_travel)
        return safe_travel, z_min

    def _poll_telemetry(self):
        """Poll machine telemetry and update DROs, diagram, and sensor badge."""
        raw_val = 0
        if self.dashboard and hasattr(self.dashboard, 'mock_coords'):
            coords = self.dashboard.mock_coords
            mx = coords.get('ABSOLUTE_X', coords.get('X', 0.0))
            my = coords.get('ABSOLUTE_Y', coords.get('Y', 0.0))
            mz = coords.get('ABSOLUTE_Z', coords.get('Z', 0.0))

            self.dro_mach_x.setText(f"G53 X: {float(mx):+08.4f}")
            self.dro_mach_y.setText(f"G53 Y: {float(my):+08.4f}")
            self.dro_mach_z.setText(f"G53 Z: {float(mz):+08.4f}")

            # Contact sensor tripped status from telemetry
            raw_val = int(coords.get('PROBE_VAL', 0) or (1 if coords.get('probe_tripped', False) else 0))

        # Local LinuxCNC fallback if available and not yet tripped
        if not raw_val:
            if not hasattr(self, '_local_lcnc_stat'):
                try:
                    import linuxcnc
                    self._local_lcnc_stat = linuxcnc.stat()
                except Exception:
                    self._local_lcnc_stat = None
            if self._local_lcnc_stat:
                try:
                    self._local_lcnc_stat.poll()
                    raw_val = getattr(self._local_lcnc_stat, 'probe_val', 0)
                except Exception:
                    pass

        invert = getattr(self, 'chk_invert_led', None) and self.chk_invert_led.isChecked()
        tripped = bool(not raw_val if invert else raw_val)
        self.diagram.set_probe_status(tripped)

        if tripped:
            self.lbl_sensor_badge.setText("🔴 SETTER TRIPPED")
            self.lbl_sensor_badge.setStyleSheet("""
                background-color: #3b1010; color: #ff4757; font-weight: bold; font-size: 12px;
                padding: 5px 12px; border: 1px solid #ff4757; border-radius: 4px;
            """)
        else:
            self.lbl_sensor_badge.setText("🟢 SETTER READY")
            self.lbl_sensor_badge.setStyleSheet("""
                background-color: #163b2f; color: #2ecc71; font-weight: bold; font-size: 12px;
                padding: 5px 12px; border: 1px solid #2ecc71; border-radius: 4px;
            """)

    def load_settings(self):
        """Load persistent tool setter and calibration parameters."""
        if not self.settings:
            return

        self._is_loading = True
        try:
            def _get_f(key, default, fallback_section="PROBING"):
                val = self.settings.get_setting("TOOL_SETTER", key, "")
                if not val:
                    val = self.settings.get_setting(fallback_section, key, str(default))
                try:
                    return float(val)
                except ValueError:
                    return default

            self.spin_setter_x.setValue(_get_f("tool_setter_x", 0.0))
            self.spin_setter_y.setValue(_get_f("tool_setter_y", 0.0))
            self.spin_setter_z.setValue(_get_f("tool_setter_z", 0.0))
            self.spin_spindle_nose_z.setValue(_get_f("spindle_nose_z", -140.0))
            self.spin_gauge_block_h.setValue(_get_f("gauge_block_h", 0.0))
            self.spin_setter_body_h.setValue(_get_f("setter_body_h", 50.0))
            self.spin_touch_plate_h.setValue(_get_f("touch_plate_h", 15.0))
            self.spin_safe_z.setValue(_get_f("safe_z", 0.0))
            self.spin_search_feed.setValue(_get_f("search_feed", 300.0, fallback_section="PROBING"))
            self.spin_latch_feed.setValue(_get_f("latch_feed", 25.0, fallback_section="PROBING"))
            self.spin_retract_dist.setValue(_get_f("retract_dist", 3.0))
            self.spin_max_travel.setValue(_get_f("max_travel", 60.0))

            self.spin_change_x.setValue(_get_f("change_x", 0.0))
            self.spin_change_y.setValue(_get_f("change_y", 0.0))
            self.spin_change_z.setValue(_get_f("change_z", 0.0))

            auto_m6 = self.settings.get_setting("TOOL_SETTER", "auto_measure_on_m6", "False") == "True"
            self.chk_auto_m6.setChecked(auto_m6)

            inv_led_str = self.settings.get_setting("TOOL_SETTER", "invert_setter_led", "")
            if inv_led_str != "":
                self.chk_invert_led.setChecked(inv_led_str == "True")
            else:
                io_inv = self.settings.get_setting("IO", "plate_setter_inv", "False") == "True"
                self.chk_invert_led.setChecked(io_inv)

            try:
                self.last_measured_length = float(self.settings.get_setting("TOOL_SETTER", "last_measured_len", "0.0"))
                self.lbl_length_display.setText(f"{self.last_measured_length:+08.4f} mm")
            except ValueError:
                pass
        finally:
            self._is_loading = False

        self._sync_diagram()

    def save_settings(self):
        """Save parameters to settings.ini under [TOOL_SETTER] and [PROBING]."""
        if not self.settings:
            return

        sx = str(self.spin_setter_x.value())
        sy = str(self.spin_setter_y.value())
        sz = str(self.spin_setter_z.value())

        # Save to TOOL_SETTER section
        self.settings.save_setting("TOOL_SETTER", "tool_setter_x", sx)
        self.settings.save_setting("TOOL_SETTER", "tool_setter_y", sy)
        self.settings.save_setting("TOOL_SETTER", "tool_setter_z", sz)
        self.settings.save_setting("TOOL_SETTER", "spindle_nose_z", str(self.spin_spindle_nose_z.value()))
        self.settings.save_setting("TOOL_SETTER", "gauge_block_h", str(self.spin_gauge_block_h.value()))
        self.settings.save_setting("TOOL_SETTER", "setter_body_h", str(self.spin_setter_body_h.value()))
        self.settings.save_setting("TOOL_SETTER", "touch_plate_h", str(self.spin_touch_plate_h.value()))
        self.settings.save_setting("TOOL_SETTER", "safe_z", str(self.spin_safe_z.value()))
        self.settings.save_setting("TOOL_SETTER", "search_feed", str(self.spin_search_feed.value()))
        self.settings.save_setting("TOOL_SETTER", "latch_feed", str(self.spin_latch_feed.value()))
        self.settings.save_setting("TOOL_SETTER", "retract_dist", str(self.spin_retract_dist.value()))
        self.settings.save_setting("TOOL_SETTER", "max_travel", str(self.spin_max_travel.value()))
        self.settings.save_setting("TOOL_SETTER", "change_x", str(self.spin_change_x.value()))
        self.settings.save_setting("TOOL_SETTER", "change_y", str(self.spin_change_y.value()))
        self.settings.save_setting("TOOL_SETTER", "change_z", str(self.spin_change_z.value()))
        self.settings.save_setting("TOOL_SETTER", "auto_measure_on_m6", str(self.chk_auto_m6.isChecked()))
        self.settings.save_setting("TOOL_SETTER", "invert_setter_led", str(self.chk_invert_led.isChecked()))
        self.settings.save_setting("TOOL_SETTER", "last_measured_len", str(self.last_measured_length))

        # Mirror core coordinates into PROBING section for compatibility
        self.settings.save_setting("PROBING", "tool_setter_x", sx)
        self.settings.save_setting("PROBING", "tool_setter_y", sy)
        self.settings.save_setting("PROBING", "tool_setter_z", sz)

    # --- Calibration & Capture Handlers ---

    def capture_setter_xy(self):
        """Capture current machine G53 X and Y as tool setter coordinates."""
        if not self.dashboard or not hasattr(self.dashboard, 'mock_coords'):
            return
        mx = self.dashboard.mock_coords.get('ABSOLUTE_X', self.dashboard.mock_coords.get('X', 0.0))
        my = self.dashboard.mock_coords.get('ABSOLUTE_Y', self.dashboard.mock_coords.get('Y', 0.0))
        self.spin_setter_x.setValue(float(mx))
        self.spin_setter_y.setValue(float(my))
        self.save_settings()
        self._add_log_entry(f"Captured Setter Pos: X={float(mx):.4f}, Y={float(my):.4f}")
        QMessageBox.information(self, "Setter Position Captured", f"Tool Setter coordinates saved:\nX = {float(mx):.4f}\nY = {float(my):.4f}")

    def capture_setter_z(self):
        """Capture current machine G53 Z as tool setter approach Z."""
        if not self.dashboard or not hasattr(self.dashboard, 'mock_coords'):
            return
        mz = self.dashboard.mock_coords.get('ABSOLUTE_Z', self.dashboard.mock_coords.get('Z', 0.0))
        self.spin_setter_z.setValue(float(mz))
        self.save_settings()
        self._add_log_entry(f"Captured Setter Z: {float(mz):.4f}")
        QMessageBox.information(self, "Setter Z Captured", f"Tool Setter Approach Z saved:\nZ = {float(mz):.4f}")

    def capture_spindle_nose_z(self):
        """Calibrate empty spindle nose Z baseline from current machine Z minus gauge block."""
        if not self.dashboard or not hasattr(self.dashboard, 'mock_coords'):
            return
        mz = float(self.dashboard.mock_coords.get('ABSOLUTE_Z', self.dashboard.mock_coords.get('Z', 0.0)))
        gauge_h = self.spin_gauge_block_h.value()
        spindle_nose_z = mz - gauge_h
        self.spin_spindle_nose_z.setValue(spindle_nose_z)
        self.save_settings()
        self._add_log_entry(f"Spindle Baseline Set: Z={spindle_nose_z:.4f} (Mach Z={mz:.4f}, Gauge={gauge_h:.4f})")
        QMessageBox.information(
            self,
            "Spindle Baseline Calibrated",
            f"Spindle Nose Baseline (Z_gauge) recorded:\nZ = {spindle_nose_z:.4f} mm\n\n"
            f"(Machine Z: {mz:.4f} - Gauge Block: {gauge_h:.4f})"
        )

    def capture_change_pos(self):
        """Capture current machine G53 position as tool change location."""
        if not self.dashboard or not hasattr(self.dashboard, 'mock_coords'):
            return
        coords = self.dashboard.mock_coords
        mx = float(coords.get('ABSOLUTE_X', coords.get('X', 0.0)))
        my = float(coords.get('ABSOLUTE_Y', coords.get('Y', 0.0)))
        mz = float(coords.get('ABSOLUTE_Z', coords.get('Z', 0.0)))
        self.spin_change_x.setValue(mx)
        self.spin_change_y.setValue(my)
        self.spin_change_z.setValue(mz)
        self.save_settings()
        self._add_log_entry(f"Captured Change Pos: X={mx:.4f}, Y={my:.4f}, Z={mz:.4f}")
        QMessageBox.information(self, "Tool Change Position Set", f"Ergonomic Tool Change Position set to:\nX: {mx:.4f}\nY: {my:.4f}\nZ: {mz:.4f}")

    # --- Motion & Execution Methods ---

    def _check_safety_interlocks(self):
        """Verify machine is on, homed, and not estopped."""
        if self.dashboard:
            if getattr(self.dashboard, 'is_estopped', False):
                QMessageBox.warning(self, "E-STOP Active", "Machine is in E-STOP state. Release E-STOP first.")
                return False
            if hasattr(self.dashboard, 'mock_coords'):
                if not self.dashboard.mock_coords.get('MACHINE_ON', False):
                    QMessageBox.warning(self, "Power Off", "Machine power is OFF. Please turn ON machine power first.")
                    return False
                if self.dashboard.mock_coords.get('STATUS') in ('RUNNING', 'PAUSED'):
                    QMessageBox.warning(self, "Interpreter Busy", "Cannot move while a program is running or paused.")
                    return False
        return True

    def _is_machine_busy(self):
        """Check if machine or background motion worker is currently active."""
        if hasattr(self, 'motion_worker') and self.motion_worker and self.motion_worker.isRunning():
            return True
        if self.dashboard and hasattr(self.dashboard, 'mock_coords'):
            status = self.dashboard.mock_coords.get('STATUS', 'IDLE')
            if status in ('RUNNING', 'PAUSED'):
                return True
        return False

    def _set_buttons_enabled(self, enabled):
        """Enable or disable motion trigger buttons during active movement."""
        if hasattr(self, 'btn_go_setter'): self.btn_go_setter.setEnabled(enabled)
        if hasattr(self, 'btn_probe_spindle'): self.btn_probe_spindle.setEnabled(enabled)
        if hasattr(self, 'btn_measure_tool'): self.btn_measure_tool.setEnabled(enabled)
        if hasattr(self, 'btn_go_change'): self.btn_go_change.setEnabled(enabled)

    def _run_motion_sequence(self, mode, steps, payload, on_complete_func):
        """
        Execute a multi-step motion sequence safely.
        In test mode / Mock, executes synchronously.
        On live machine, runs in background ToolSetterMotionWorker with wait_idle().
        """
        from unittest.mock import Mock
        is_mock_dashboard = self.dashboard and (isinstance(self.dashboard, Mock) or isinstance(getattr(self.dashboard, 'execute_mdi', None), Mock))
        if getattr(self, '_is_test_mode', False) or is_mock_dashboard:
            for step in steps:
                self._execute_mdi(step['cmd'])
            touch_z = float(self.dashboard.mock_coords.get('ABSOLUTE_Z', -90.0)) if self.dashboard and hasattr(self.dashboard, 'mock_coords') else -90.0
            payload['probed_z'] = touch_z
            on_complete_func(True, "Test sequence completed.", payload)
            return

        self._set_buttons_enabled(False)
        first_desc = steps[0].get('desc', 'Starting motion...') if steps else 'Starting motion...'
        self.lbl_motion_status.setText(f"🚀 Running: {first_desc}")
        self.lbl_motion_status.setStyleSheet("""
            background-color: #1a365d; color: #63b3ed; font-family: 'Consolas', monospace;
            font-size: 11px; font-weight: bold; padding: 5px 10px; border: 1px solid #2b4c7e; border-radius: 4px;
        """)

        self.motion_worker = ToolSetterMotionWorker(mode, steps, payload=payload, dashboard=self.dashboard)
        self.motion_worker.step_started.connect(lambda desc: self.lbl_motion_status.setText(f"🚀 {desc}"))
        self.motion_worker.finished.connect(lambda success, msg, res: self._on_motion_sequence_finished(success, msg, res, on_complete_func))
        self.motion_worker.start()

    def _on_motion_sequence_finished(self, success, msg, res, on_complete_func):
        self._set_buttons_enabled(True)
        self.diagram.mode = "idle"
        self.diagram.update()

        if success:
            self.lbl_motion_status.setText("🟢 System Ready — Spindle & Tool Setter Idle")
            self.lbl_motion_status.setStyleSheet("""
                background-color: #16241b; color: #9ad58d; font-family: 'Consolas', monospace;
                font-size: 11px; font-weight: bold; padding: 5px 10px; border: 1px solid #234d31; border-radius: 4px;
            """)
            on_complete_func(True, msg, res)
        else:
            self.lbl_motion_status.setText(f"⚠️ {msg}")
            self.lbl_motion_status.setStyleSheet("""
                background-color: #3b1010; color: #ff4757; font-family: 'Consolas', monospace;
                font-size: 11px; font-weight: bold; padding: 5px 10px; border: 1px solid #ff4757; border-radius: 4px;
            """)
            QMessageBox.warning(self, "Motion Error", msg)
            self._add_log_entry(f"Error: {msg}")

    def move_to_setter(self):
        """Rapid to safe clearance Z and position directly over the tool setter."""
        if not self._check_safety_interlocks():
            return
        if self._is_machine_busy():
            QMessageBox.warning(self, "Machine Busy", "Machine is currently moving. Please wait for the current move to finish.")
            return

        sx = self.spin_setter_x.value()
        sy = self.spin_setter_y.value()
        sz = self.spin_setter_z.value()
        safe_z = self.spin_safe_z.value()
        z_min, _ = self._get_z_limits()

        confirm = QMessageBox.question(
            self,
            "Confirm Move to Setter",
            f"Retract to safe Z ({safe_z:.2f}) and rapid to Tool Setter (X:{sx:.2f}, Y:{sy:.2f})?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return

        steps = [
            {"cmd": f"G53 G0 Z{safe_z:.4f}", "desc": f"Lifting Z to Safe Height (G53 Z{safe_z:.2f})..."},
            {"cmd": f"G53 G0 X{sx:.4f} Y{sy:.4f}", "desc": f"Moving to Tool Setter (G53 X{sx:.2f} Y{sy:.2f})..."}
        ]
        if sz < safe_z and sz > (z_min + 5.0):
            steps.append({"cmd": f"G53 G0 Z{sz:.4f}", "desc": f"Lowering to Approach Z (G53 Z{sz:.2f})..."})

        def _on_done(ok, msg, res):
            if ok:
                self._add_log_entry(f"Moved to Tool Setter (X={sx:.2f}, Y={sy:.2f})")

        self._run_motion_sequence("move_setter", steps, {}, _on_done)

    def move_to_change_pos(self):
        """Move to ergonomic manual tool change position."""
        if not self._check_safety_interlocks():
            return
        if self._is_machine_busy():
            QMessageBox.warning(self, "Machine Busy", "Machine is currently moving. Please wait for the current move to finish.")
            return

        cx = self.spin_change_x.value()
        cy = self.spin_change_y.value()
        cz = self.spin_change_z.value()
        safe_z = self.spin_safe_z.value()

        steps = [
            {"cmd": f"G53 G0 Z{safe_z:.4f}", "desc": f"Lifting Z to Safe Height (G53 Z{safe_z:.2f})..."},
            {"cmd": f"G53 G0 X{cx:.4f} Y{cy:.4f}", "desc": f"Moving to Tool Change Pos (G53 X{cx:.2f} Y{cy:.2f})..."}
        ]
        if cz != safe_z:
            steps.append({"cmd": f"G53 G0 Z{cz:.4f}", "desc": f"Lowering to Tool Change Z (G53 Z{cz:.2f})..."})

        def _on_done(ok, msg, res):
            if ok:
                self._add_log_entry(f"Moved to Tool Change Position (X={cx:.2f}, Y={cy:.2f})")
                QMessageBox.information(
                    self,
                    "Tool Change Position Reached",
                    "Spindle is at manual tool change position.\n\n"
                    "1. Change the cutter in collet chuck.\n"
                    "2. Tighten collet nut to specified torque.\n"
                    "3. Click OK, then click 'MEASURE CURRENT TOOL'."
                )

        self._run_motion_sequence("move_change", steps, {}, _on_done)

    def auto_probe_spindle_baseline(self):
        """Execute automated slow probe downward with bare spindle nose/gauge."""
        if not self._check_safety_interlocks():
            return
        if self._is_machine_busy():
            QMessageBox.warning(self, "Machine Busy", "Machine is currently moving. Please wait for the current move to finish.")
            return

        confirm = QMessageBox.question(
            self,
            "Auto-Probe Spindle Baseline",
            "This routine will lower the bare spindle nose (or gauge block) to contact the tool setter.\n\n"
            "Ensure the setter circuit is verified before running.\n\n"
            "Do you want to proceed?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return

        # Ensure tool setter hardware input is selected
        self.select_tool_setter_hardware()

        sx = self.spin_setter_x.value()
        sy = self.spin_setter_y.value()
        sz = self.spin_setter_z.value()
        safe_z = self.spin_safe_z.value()
        latch_f = self.spin_latch_feed.value()
        z_min, _ = self._get_z_limits()

        # Check probe starting elevation
        probe_start_z = sz if (sz < safe_z and sz > (z_min + 5.0)) else safe_z
        safe_travel, _ = self._calc_safe_probe_travel(probe_start_z, self.spin_max_travel.value())
        if safe_travel <= 0.5:
            QMessageBox.warning(
                self,
                "Travel Limit Exceeded",
                f"Cannot probe downward from Z ({probe_start_z:.2f} mm).\n\n"
                f"Machine Z negative limit is {z_min:.2f} mm.\n"
                f"Jog Z upwards or adjust Approach Z before calibrating."
            )
            return

        self.diagram.mode = "spindle_calib"
        self.diagram.has_tool = False
        self.diagram.update()

        steps = [
            {"cmd": f"G53 G0 Z{safe_z:.4f}", "desc": f"Lifting Z to Safe Clearance (G53 Z{safe_z:.2f})..."},
            {"cmd": f"G53 G0 X{sx:.4f} Y{sy:.4f}", "desc": f"Positioning over Setter (G53 X{sx:.2f} Y{sy:.2f})..."}
        ]
        if sz < safe_z and sz > (z_min + 5.0):
            steps.append({"cmd": f"G53 G0 Z{sz:.4f}", "desc": f"Lowering to Approach Z (G53 Z{sz:.2f})..."})

        steps.append({
            "cmd": f"G91 G38.2 Z-{safe_travel:.4f} F{latch_f:.1f}",
            "desc": f"Probing Spindle Baseline (G38.2 Z-{safe_travel:.2f} F{latch_f:.0f})...",
            "is_probe": True
        })
        steps.append({"cmd": f"G90 G53 G0 Z{safe_z:.4f}", "desc": f"Retracting to Safe Height (G53 Z{safe_z:.2f})..."})

        def _on_done(ok, msg, res):
            if ok:
                probed_z = res.get('probed_z', sz)
                gauge_h = self.spin_gauge_block_h.value()
                spindle_z = probed_z - gauge_h
                self.spin_spindle_nose_z.setValue(spindle_z)
                self.save_settings()
                self._add_log_entry(f"Auto-Probed Spindle Baseline: Z={spindle_z:.4f} mm (Probed Z={probed_z:.4f})")
                QMessageBox.information(
                    self,
                    "Spindle Baseline Calibrated",
                    f"Spindle Baseline (Z_gauge) calibrated successfully:\n\n"
                    f"Z_gauge = {spindle_z:.4f} mm\n"
                    f"(Probed Z: {probed_z:.4f} mm, Gauge Offset: {gauge_h:.4f} mm)"
                )

        self._run_motion_sequence("probe_baseline", steps, {}, _on_done)

    def measure_tool_length(self):
        """
        Execute full precision tool setter measurement with limit guards:
        1. Select Tool Setter probe source (lut5.0.in-2 = 0)
        2. Rapid to Safe Z (G53 Z)
        3. Rapid to Setter (X, Y)
        4. Optional rapid to Approach Z if configured
        5. Incremental fast search (G91 G38.2 F_search) clamped to soft limit
        6. Retract back-off (G91 G0 Z)
        7. Incremental slow latch (G91 G38.2 F_latch)
        8. Calculate tool length: L = Z_touch - Z_spindle_nose
        9. Retract to safe Z (G90 G53 G0 Z)
        """
        if not self._check_safety_interlocks():
            return
        if self._is_machine_busy():
            QMessageBox.warning(self, "Machine Busy", "Machine is currently moving. Please wait for the current move to finish.")
            return

        # Ensure tool setter hardware input is selected
        self.select_tool_setter_hardware()

        tool_num = self.spin_tool_num.value()
        sx = self.spin_setter_x.value()
        sy = self.spin_setter_y.value()
        sz = self.spin_setter_z.value()
        safe_z = self.spin_safe_z.value()
        search_f = self.spin_search_feed.value()
        latch_f = self.spin_latch_feed.value()
        retract_d = self.spin_retract_dist.value()
        max_travel = self.spin_max_travel.value()
        spindle_nose_z = self.spin_spindle_nose_z.value()
        z_min, _ = self._get_z_limits()

        # Check probe starting elevation
        probe_start_z = sz if (sz < safe_z and sz > (z_min + 5.0)) else safe_z
        safe_travel, _ = self._calc_safe_probe_travel(probe_start_z, max_travel)
        if safe_travel <= 0.5:
            QMessageBox.warning(
                self,
                "Travel Limit Exceeded",
                f"Cannot probe downward from Z ({probe_start_z:.2f} mm).\n\n"
                f"Machine Z negative limit is {z_min:.2f} mm.\n"
                f"Jog Z upwards or adjust Approach Z before measuring."
            )
            return

        self.is_measuring = True
        self.diagram.mode = "measuring"
        self.diagram.has_tool = True
        self.diagram.tool_num = tool_num
        self.diagram.update()

        steps = [
            {"cmd": f"G53 G0 Z{safe_z:.4f}", "desc": f"Lifting Z to Safe Height (G53 Z{safe_z:.2f})..."},
            {"cmd": f"G53 G0 X{sx:.4f} Y{sy:.4f}", "desc": f"Positioning over Setter (G53 X{sx:.2f} Y{sy:.2f})..."}
        ]
        if sz < safe_z and sz > (z_min + 5.0):
            steps.append({"cmd": f"G53 G0 Z{sz:.4f}", "desc": f"Lowering to Approach Z (G53 Z{sz:.2f})..."})

        steps.append({
            "cmd": f"G91 G38.2 Z-{safe_travel:.4f} F{search_f:.1f}",
            "desc": f"Fast Probe Search (G38.2 Z-{safe_travel:.2f} F{search_f:.0f})...",
            "is_probe": True
        })
        steps.append({"cmd": f"G91 G0 Z{retract_d:.4f}", "desc": f"Backing off {retract_d:.2f}mm..."})
        latch_travel = min(retract_d * 2.5, safe_travel)
        steps.append({
            "cmd": f"G91 G38.2 Z-{latch_travel:.4f} F{latch_f:.1f}",
            "desc": f"Slow Precision Latch (G38.2 F{latch_f:.0f})...",
            "is_probe": True
        })
        steps.append({"cmd": f"G90 G53 G0 Z{safe_z:.4f}", "desc": f"Retracting to Safe Z (G53 Z{safe_z:.2f})..."})

        def _on_done(ok, msg, res):
            self.is_measuring = False
            self.diagram.mode = "idle"
            self.diagram.update()
            if ok:
                touch_z = res.get('probed_z', sz - 5.0)
                tool_length = touch_z - spindle_nose_z
                self.last_measured_length = tool_length
                self.last_measured_tool = tool_num
                self.lbl_length_display.setText(f"{tool_length:+08.4f} mm")
                self.diagram.tool_length = tool_length
                self.diagram.update()
                self.save_settings()
                self._add_log_entry(f"Measured Tool T{tool_num}: L={tool_length:.4f} mm (Touch Z={touch_z:.4f})")
                self.tool_measured.emit(tool_num, tool_length)
                QMessageBox.information(
                    self,
                    "Tool Measured Successfully",
                    f"Tool T{tool_num} measured:\n\n"
                    f"Length (L): {tool_length:.4f} mm\n"
                    f"Touch Plane (Z): {touch_z:.4f} mm\n"
                    f"Spindle Baseline (Z_gauge): {spindle_nose_z:.4f} mm\n\n"
                    "Click 'UPDATE TOOL TABLE' to store this length in LinuxCNC."
                )

        self._run_motion_sequence("measure_tool", steps, {}, _on_done)

    def save_tool_table_offset(self):
        """Update LinuxCNC Tool Table with measured length using G10 L1."""
        tool_num = self.spin_tool_num.value()
        length = self.last_measured_length
        diam = self.inp_tool_diam.value()
        desc = self.inp_tool_desc.text().strip()

        cmd = f"G10 L1 P{tool_num} Z{length:.4f} D{diam:.4f}"
        self._execute_mdi(cmd)
        self._add_log_entry(f"Updated Tool Table: T{tool_num} Z={length:.4f} D={diam:.4f}")
        QMessageBox.information(self, "Tool Table Updated", f"Tool T{tool_num} offset updated in tool table:\n\n{cmd}")

    def apply_g43_offset(self):
        """Apply active tool length offset using G43 H<tool_num>."""
        tool_num = self.spin_tool_num.value()
        cmd = f"G43 H{tool_num}"
        self._execute_mdi(cmd)
        self._add_log_entry(f"Applied G43 Offset: {cmd}")
        QMessageBox.information(self, "G43 Offset Applied", f"Active tool offset applied:\n{cmd}")

    def touch_off_workpiece(self):
        """Set Workpiece Z0 using mobile touch plate and sync with tool setter."""
        plate_h = self.spin_touch_plate_h.value()
        cmd = f"G10 L20 P0 Z{plate_h:.4f}"
        self._execute_mdi(cmd)
        self._add_log_entry(f"Workpiece Z0 set with touch plate ({plate_h:.4f} mm): {cmd}")
        QMessageBox.information(self, "Workpiece Z0 Set", f"Workpiece Z0 offset established:\n{cmd}\n(Touch plate thickness: {plate_h:.4f} mm)")

    def abort_motion(self):
        """Emergency motion abort."""
        if self.dashboard and hasattr(self.dashboard, 'stop_machine'):
            self.dashboard.stop_machine()
        elif self.dashboard and hasattr(self.dashboard, 'execute_mdi'):
            self.dashboard.execute_mdi("M2")
        self.is_measuring = False
        self.diagram.mode = "idle"
        self.diagram.update()
        self._add_log_entry("Aborted Tool Setter motion.")

    def _execute_mdi(self, cmd):
        """Dispatch MDI command through dashboard or local API."""
        if self.dashboard and hasattr(self.dashboard, 'execute_mdi'):
            self.dashboard.execute_mdi(cmd)

    def _add_log_entry(self, message):
        """Append entry to measurement history table."""
        timestamp = time.strftime("%H:%M:%S")
        row = self.tbl_log.rowCount()
        self.tbl_log.insertRow(row)
        self.tbl_log.setItem(row, 0, QTableWidgetItem(f"T{self.spin_tool_num.value()}"))
        self.tbl_log.setItem(row, 1, QTableWidgetItem(f"{self.last_measured_length:+.4f}"))
        self.tbl_log.setItem(row, 2, QTableWidgetItem(f"{self.spin_setter_z.value():+.4f}"))
        self.tbl_log.setItem(row, 3, QTableWidgetItem(timestamp))
        self.tbl_log.scrollToBottom()

    def cleanup(self):
        """Clean up timers and background threads."""
        if hasattr(self, 'poll_timer') and self.poll_timer.isActive():
            self.poll_timer.stop()

    def closeEvent(self, event):
        self.cleanup()
        super().closeEvent(event)
