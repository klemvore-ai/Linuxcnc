"""Guided LinuxCNC setup workflow built on the shared machine model."""

from PyQt6.QtWidgets import (
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QWizard,
    QWizardPage,
    QComboBox,
    QCheckBox,
    QTextEdit,
    QPushButton,
    QMessageBox,
    QFileDialog,
    QWidget,
)
from core.config_model import MachineConfig
from core.config_renderer import ConfigurationRenderError, LinuxCNCConfigRenderer
from core.machine_agent import MachineAgentClient, client_from_settings

class MachineBasicsPage(QWizardPage):
    """Collect machine identity, units, and kinematics choices."""

    def __init__(self, settings, parent=None):
        """Create the page and initialize controls from existing settings."""
        super().__init__(parent)
        self.settings = settings
        self.setTitle("Machine Basics")
        self.setSubTitle("Choose the basic LinuxCNC machine structure.")

        form = QFormLayout(self)
        self.name_input = QLineEdit(settings.get_setting("MACHINE_CONFIG", "name", "CNCFusion"))
        self.kinematics_input = QComboBox()
        self.kinematics_input.addItems([
            "XYZ (3-Axis)",
            "XYYZ (Gantry 3-Axis)",
            "XYZ A (4-Axis)",
            "XYZ AB (5-Axis)",
        ])
        self.kinematics_input.setCurrentText(
            settings.get_setting("MACHINE_CONFIG", "kinematics", "XYZ (3-Axis)")
        )
        self.units_input = QComboBox()
        self.units_input.addItems(["Metric (mm)", "Imperial (inch)"])
        self.units_input.setCurrentText(
            settings.get_setting("MACHINE_CONFIG", "units", "Metric (mm)")
        )
        form.addRow("Machine name:", self.name_input)
        form.addRow("Kinematics:", self.kinematics_input)
        form.addRow("Units:", self.units_input)

    def validatePage(self):
        """Reject a blank machine name before allowing the wizard to continue."""
        valid = bool(self.name_input.text().strip())
        if not valid:
            self.setSubTitle("Machine name is required.")
        return valid


class AxisLimitsPage(QWizardPage):
    """Collect basic travel, velocity, and acceleration values for each axis."""

    def __init__(self, settings, parent=None):
        """Create axis controls initialized from existing axis sections."""
        super().__init__(parent)
        self.settings = settings
        self.setTitle("Axis Limits and Motion")
        self.setSubTitle("Set safe travel and motion limits for the selected axes.")
        self.controls = {}
        form = QFormLayout(self)
        for axis in ("X", "Y", "Z", "A", "B"):
            minimum = self._number(form, axis, "min", "Minimum")
            maximum = self._number(form, axis, "max", "Maximum")
            velocity = self._number(form, axis, "vel", "Velocity")
            acceleration = self._number(form, axis, "accel", "Acceleration")
            self.controls[axis] = {
                "min": minimum,
                "max": maximum,
                "vel": velocity,
                "accel": acceleration,
            }

    def _number(self, form, axis, key, label):
        """Create one bounded numeric field for an axis setting."""
        field = QDoubleSpinBox()
        field.setRange(-100000.0, 100000.0)
        field.setDecimals(3)
        field.setValue(float(self.settings.get_setting(f"AXIS_{axis}", key, 0.0)))
        form.addRow(f"{axis} {label}:", field)
        return field

    def validatePage(self):
        """Reject invalid axis ranges and non-positive motion values."""
        for axis, controls in self.controls.items():
            if controls["min"].value() == 0 and controls["max"].value() == 0:
                continue
            if controls["min"].value() >= controls["max"].value():
                self.setSubTitle(f"Axis {axis} minimum must be below maximum.")
                return False
            if controls["vel"].value() <= 0 or controls["accel"].value() <= 0:
                self.setSubTitle(f"Axis {axis} velocity and acceleration must be positive.")
                return False
        return True


class HardwareNetworkPage(QWizardPage):
    """Collect Mesa identity and separate control/management network roles."""

    def __init__(self, settings, parent=None):
        """Create hardware and network fields from existing non-secret settings."""
        super().__init__(parent)
        self.settings = settings
        self.setTitle("Hardware and Network")
        self.setSubTitle("Choose the control hardware and administration network path.")
        form = QFormLayout(self)
        self.board_input = QLineEdit(settings.get_setting("MESA", "board_model", "7i76eu"))
        self.control_address_input = QLineEdit(settings.get_setting("MESA", "ip_address", "10.10.10.10"))
        self.network_role_input = QComboBox()
        self.network_role_input.addItems([
            "Dedicated control NIC + Wi-Fi management",
            "Dedicated control NIC + wired management",
            "Single interface for control and management",
        ])
        self.network_role_input.setCurrentText(
            settings.get_setting("NETWORK", "role", "Dedicated control NIC + Wi-Fi management")
        )
        self.isolation_warning = QCheckBox("I understand that control traffic must remain local and deterministic")
        self.isolation_warning.setChecked(
            settings.get_setting("NETWORK", "isolation_acknowledged", "False") == "True"
        )
        form.addRow("Mesa board:", self.board_input)
        form.addRow("Mesa/control address:", self.control_address_input)
        form.addRow("Network role:", self.network_role_input)
        form.addRow("Safety acknowledgement:", self.isolation_warning)

    def validatePage(self):
        """Require a board, an address, and explicit network-boundary acknowledgement."""
        if not self.board_input.text().strip() or not self.control_address_input.text().strip():
            self.setSubTitle("Mesa board and control address are required.")
            return False
        if not self.isolation_warning.isChecked():
            self.setSubTitle("Acknowledge the control and management network boundary to continue.")
            return False
        return True


class SecureAgentPage(QWizardPage):
    """Configure and verify the authenticated machine-agent connection."""

    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.setTitle("Secure Machine Agent")
        self.setSubTitle("Configure mTLS files and verify the Debian machine before continuing.")
        form = QFormLayout(self)
        self.base_url = QLineEdit(settings.get_setting("AGENT", "base_url", "https://"))
        self.ca_file = self._path_field(form, "CA certificate:", "ca_file")
        self.client_cert = self._path_field(form, "Client certificate:", "client_cert")
        self.client_key = self._path_field(form, "Client private key:", "client_key")
        form.insertRow(0, "Agent URL:", self.base_url)
        self.status = QLabel("Connection not tested")
        self.test_button = QPushButton("Test Secure Connection")
        self.test_button.clicked.connect(self.test_connection)
        form.addRow(self.test_button, self.status)
        roles = QLabel("Required roles: monitor, operator, technician, administrator")
        form.addRow("Agent roles:", roles)

    def _path_field(self, form, label, key):
        row = QLineEdit(self.settings.get_setting("AGENT", key, ""))
        button = QPushButton("Browse")
        button.clicked.connect(lambda: self._browse(row))
        container = QWidget()
        layout = QHBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(row)
        layout.addWidget(button)
        form.addRow(label, container)
        setattr(self, key, row)
        return row

    @staticmethod
    def _browse(field):
        path, _ = QFileDialog.getOpenFileName()
        if path:
            field.setText(path)

    def test_connection(self):
        try:
            client = MachineAgentClient(
                self.base_url.text().strip(),
                self.ca_file.text().strip(),
                self.client_cert.text().strip(),
                self.client_key.text().strip(),
                timeout=5,
            )
            status = client.agent_status()
            self.status.setText(
                f"Connected: agent {status.get('agent_version', 'unknown')}, "
                f"roles={status.get('role_mode', 'unknown')}"
            )
            self.status.setStyleSheet("color: green;")
        except Exception as error:
            self.status.setText(f"Connection failed: {error}")
            self.status.setStyleSheet("color: red;")

    def validatePage(self):
        values = (self.base_url.text().strip(), self.ca_file.text().strip(), self.client_cert.text().strip(), self.client_key.text().strip())
        if not all(values):
            self.setSubTitle("Agent URL, CA, client certificate, and private key are required.")
            return False
        for key, value in zip(("base_url", "ca_file", "client_cert", "client_key"), values):
            self.settings.save_setting("AGENT", key, value)
        return True


class DebianEnrollmentPage(QWizardPage):
    """Verify the Debian machine through the authenticated machine agent."""

    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.setTitle("Machine Agent Verification")
        self.setSubTitle("Verify the Debian machine through HTTPS before changing services.")
        form = QFormLayout(self)
        self.agent_url = QLineEdit(settings.get_setting("AGENT", "base_url", ""))
        self.agent_url.setReadOnly(True)
        form.addRow("Agent URL:", self.agent_url)
        self.status = QLabel("Agent check not run")
        self.check_button = QPushButton("Check Machine Agent")
        self.check_button.clicked.connect(self.check_agent)
        form.addRow(self.check_button, self.status)
        self.plan = QTextEdit()
        self.plan.setReadOnly(True)
        form.addRow("Enrollment plan:", self.plan)
        self._refresh_plan()

    def _refresh_plan(self):
        agent_url = self.agent_url.text().strip() or "<agent URL>"
        self.plan.setPlainText(
            "Review before applying:\n\n"
            f"1. Connect to {agent_url} using the configured mTLS identity.\n"
            "2. Verify the agent version, role mode, and LinuxCNC state.\n"
            "3. Verify the advertised helper-backed capabilities.\n"
            "4. Review any certificate or systemd changes before applying them.\n\n"
            "No service, certificate, or SSH changes are made by this check."
        )

    def check_agent(self):
        try:
            client = client_from_settings(self.settings)
            if client is None:
                raise ValueError("Complete [AGENT] settings are required.")
            status = client.agent_status()
            capabilities = len(status.get("capabilities", []))
            machine = status.get("machine", {})
            self.status.setText(
                f"Connected: agent {status.get('agent_version', 'unknown')}, "
                f"roles={status.get('role_mode', 'unknown')}, capabilities={capabilities}, "
                f"LinuxCNC={'running' if machine.get('running') else 'offline'}"
            )
            self.status.setStyleSheet("color: green;")
        except Exception as error:
            self.status.setText(f"Agent check failed: {error}")
            self.status.setStyleSheet("color: red;")

    def validatePage(self):
        if not self.agent_url.text().strip():
            self.setSubTitle("Configure the secure machine agent before continuing.")
            return False
        self._refresh_plan()
        return True


class IOSafetyPage(QWizardPage):
    """Collect essential home, limit, E-stop, probe, and auxiliary I/O signals."""

    def __init__(self, settings, parent=None):
        """Create signal fields from existing IO_MAPPING values."""
        super().__init__(parent)
        self.settings = settings
        self.setTitle("I/O and Safety")
        self.setSubTitle("Assign physical signals and confirm the safety inputs.")
        self.fields = {}
        form = QFormLayout(self)
        keys = [
            ("estop_ext", "External E-stop"),
            ("probe_in", "Probe input"),
            ("plate_setter", "Touch plate"),
            ("spindle_at_speed", "Spindle at-speed"),
            ("spindle_fault", "Spindle fault"),
            ("coolant_mist", "Coolant mist output"),
            ("coolant_flood", "Coolant flood output"),
            ("btn_cycle_start", "Cycle start"),
            ("btn_cycle_stop", "Cycle stop"),
            ("btn_feed_hold", "Feed hold"),
        ]
        for key, label in keys:
            field = QLineEdit(settings.get_setting("IO_MAPPING", key, ""))
            field.setPlaceholderText("Optional physical HAL signal")
            self.fields[key] = field
            form.addRow(f"{label}:", field)
        self.enable_estop = QCheckBox("Require external E-stop input")
        self.enable_estop.setChecked(settings.get_setting("MACHINE_CONFIG", "enable_estop", "True") == "True")
        self.enable_probe = QCheckBox("Enable probing")
        self.enable_probe.setChecked(settings.get_setting("MACHINE_CONFIG", "enable_probe", "True") == "True")
        form.addRow("Safety policy:", self.enable_estop)
        form.addRow("Probe policy:", self.enable_probe)

    def validatePage(self):
        """Require physical signals for enabled E-stop and probing features."""
        if self.enable_estop.isChecked() and not self.fields["estop_ext"].text().strip():
            self.setSubTitle("An external E-stop signal is required when enabled.")
            return False
        if self.enable_probe.isChecked() and not self.fields["probe_in"].text().strip():
            self.setSubTitle("A probe input is required when probing is enabled.")
            return False
        return True


class FeedbackPage(QWizardPage):
    """Collect semantic motor and axis feedback roles for future closed loops."""

    def __init__(self, settings, parent=None):
        """Create feedback controls initialized from the optional FEEDBACK section."""
        super().__init__(parent)
        self.settings = settings
        self.setTitle("Feedback and Dual-Loop Setup")
        self.setSubTitle("Describe what each encoder or linear sensor measures.")
        self.controls = {}
        form = QFormLayout(self)
        for axis in ("X", "Y", "Z", "A", "B"):
            mode = QComboBox()
            mode.addItems(["none", "motor", "axis", "dual"])
            mode.setCurrentText(settings.get_setting("FEEDBACK", f"{axis.lower()}_mode", "none"))
            motor = QLineEdit(settings.get_setting("FEEDBACK", f"{axis.lower()}_motor_signal", ""))
            axis_sensor = QLineEdit(settings.get_setting("FEEDBACK", f"{axis.lower()}_axis_signal", ""))
            motor.setPlaceholderText("Motor encoder HAL signal")
            axis_sensor.setPlaceholderText("Axis/linear sensor HAL signal")
            self.controls[axis] = {"mode": mode, "motor": motor, "axis": axis_sensor}
            form.addRow(f"{axis} feedback mode:", mode)
            form.addRow(f"{axis} motor feedback:", motor)
            form.addRow(f"{axis} axis feedback:", axis_sensor)

    def validatePage(self):
        """Require the signal associated with each selected feedback role."""
        for axis, controls in self.controls.items():
            mode = controls["mode"].currentText()
            if mode in {"motor", "dual"} and not controls["motor"].text().strip():
                self.setSubTitle(f"Motor feedback signal is required for axis {axis}.")
                return False
            if mode in {"axis", "dual"} and not controls["axis"].text().strip():
                self.setSubTitle(f"Axis feedback signal is required for axis {axis}.")
                return False
        return True


class ReviewPage(QWizardPage):
    """Preview generated LinuxCNC files and show unresolved model errors."""

    def __init__(self, settings, parent=None):
        """Create a read-only preview backed by the shared configuration model."""
        super().__init__(parent)
        self.settings = settings
        self.setTitle("Review Configuration")
        self.setSubTitle("Review the generated LinuxCNC files before completing setup.")
        self.preview = QTextEdit()
        self.preview.setReadOnly(True)
        self.agent_status = QLabel("Machine agent status: not checked")
        self.stage_button = QPushButton("Stage Reviewed Configuration Securely")
        self.stage_button.clicked.connect(self.stage_configuration)
        form = QFormLayout(self)
        form.addRow(self.agent_status)
        form.addRow(self.stage_button)
        form.addRow(self.preview)

    def initializePage(self):
        """Save the draft and refresh the INI/HAL preview when entering the page."""
        wizard = self.wizard()
        if wizard is not None:
            wizard._save_values()
        try:
            config = MachineConfig.from_settings(self.settings)
            agent_client = client_from_settings(self.settings)
            self.stage_button.setEnabled(agent_client is not None)
            if agent_client is None:
                self.agent_status.setText("Machine agent status: not configured")
            else:
                try:
                    status = agent_client.bundle_status(config.name)
                    self.agent_status.setText(
                        f"Machine agent: staged={len(status.get('files', []))}, "
                        f"complete={status.get('complete', False)}, "
                        f"approved={status.get('approved', False)}"
                    )
                except Exception as error:
                    self.agent_status.setText(f"Machine agent status unavailable: {error}")
            errors = config.validate()
            if errors:
                self.preview.setPlainText("Validation errors:\n\n" + "\n".join(errors))
                return
            renderer = LinuxCNCConfigRenderer()
            self.preview.setPlainText(
                "[ Generated INI ]\n\n"
                + renderer.render_ini(config)
                + "\n\n[ Generated HAL ]\n\n"
                + renderer.render_hal(config)
            )
        except (ConfigurationRenderError, TypeError, ValueError) as error:
            self.preview.setPlainText(f"Preview failed:\n\n{error}")

    def stage_configuration(self):
        """Upload the reviewed INI and HAL pair without activating the bundle."""
        agent_client = client_from_settings(self.settings)
        if agent_client is None:
            QMessageBox.warning(self, "Staging Unavailable", "Secure machine-agent settings are not configured.")
            return
        config = MachineConfig.from_settings(self.settings)
        errors = config.validate()
        if errors:
            QMessageBox.warning(self, "Staging Blocked", "\n".join(errors))
            return
        reply = QMessageBox.question(
            self,
            "Confirm Secure Staging",
            f"Stage the reviewed INI and HAL files for '{config.name}'?\n\n"
            "This uploads files only. It does not activate or restart LinuxCNC.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        try:
            renderer = LinuxCNCConfigRenderer()
            agent_client.upload_content("config", f"{config.name}/{config.name}.ini", renderer.render_ini(config).encode("utf-8"))
            agent_client.upload_content("config", f"{config.name}/{config.name}.hal", renderer.render_hal(config).encode("utf-8"))
            QMessageBox.information(self, "Configuration Staged", "The reviewed INI and HAL files were securely staged.")
            self.initializePage()
        except (ConfigurationRenderError, ValueError, OSError) as error:
            QMessageBox.critical(self, "Staging Failed", str(error))

    def validatePage(self):
        """Prevent completion while the saved model contains validation errors."""
        errors = MachineConfig.from_settings(self.settings).validate()
        if errors:
            self.setSubTitle("Resolve validation errors before completing setup.")
            return False
        return True


class SetupWizard(QWizard):
    """Guide setup while saving values into the existing SettingsManager schema."""

    def __init__(self, settings, parent=None):
        """Create the setup wizard backed by the supplied settings manager."""
        super().__init__(parent)
        self.settings = settings
        self.setWindowTitle("CNCFusion LinuxCNC Setup")
        self.setMinimumSize(640, 480)
        self.basics_page = MachineBasicsPage(settings)
        self.axis_page = AxisLimitsPage(settings)
        self.hardware_page = HardwareNetworkPage(settings)
        self.agent_page = SecureAgentPage(settings)
        self.enrollment_page = DebianEnrollmentPage(settings)
        self.io_page = IOSafetyPage(settings)
        self.feedback_page = FeedbackPage(settings)
        self.review_page = ReviewPage(settings)
        self.addPage(self.basics_page)
        self.addPage(self.axis_page)
        self.addPage(self.hardware_page)
        self.addPage(self.agent_page)
        self.addPage(self.enrollment_page)
        self.addPage(self.io_page)
        self.addPage(self.feedback_page)
        self.addPage(self.review_page)

    def accept(self):
        """Persist the pages completed so far for the next wizard phase."""
        self._save_values()
        super().accept()

    def _save_values(self):
        """Write wizard fields using the legacy settings sections for compatibility."""
        self.settings.save_setting("MACHINE_CONFIG", "name", self.basics_page.name_input.text().strip())
        self.settings.save_setting("MACHINE_CONFIG", "kinematics", self.basics_page.kinematics_input.currentText())
        self.settings.save_setting("MACHINE_CONFIG", "units", self.basics_page.units_input.currentText())
        self.settings.save_setting("MESA", "board_model", self.hardware_page.board_input.text().strip())
        self.settings.save_setting("MESA", "ip_address", self.hardware_page.control_address_input.text().strip())
        self.settings.save_setting("NETWORK", "role", self.hardware_page.network_role_input.currentText())
        self.settings.save_setting(
            "NETWORK",
            "isolation_acknowledged",
            str(self.hardware_page.isolation_warning.isChecked()),
        )
        self.settings.save_setting("MACHINE_CONFIG", "enable_estop", str(self.io_page.enable_estop.isChecked()))
        self.settings.save_setting("MACHINE_CONFIG", "enable_probe", str(self.io_page.enable_probe.isChecked()))
        for key, field in self.io_page.fields.items():
            self.settings.save_setting("IO_MAPPING", key, field.text().strip())
        for axis, controls in self.feedback_page.controls.items():
            self.settings.save_setting("FEEDBACK", f"{axis.lower()}_mode", controls["mode"].currentText())
            self.settings.save_setting("FEEDBACK", f"{axis.lower()}_motor_signal", controls["motor"].text().strip())
            self.settings.save_setting("FEEDBACK", f"{axis.lower()}_axis_signal", controls["axis"].text().strip())
        for axis, controls in self.axis_page.controls.items():
            for key, field in controls.items():
                self.settings.save_setting(f"AXIS_{axis}", key, field.value())