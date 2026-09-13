import configparser
import os
import json

class SettingsManager:
    def __init__(self, filename="settings.ini"):
        self.filename = filename
        self.config = configparser.ConfigParser()
        self._migrate_from_json()
        self._ensure_file_exists()
        self.config.read(self.filename)

    def _migrate_from_json(self):
        json_file = self.filename.replace('.ini', '.json')
        if os.path.exists(json_file):
            with open(json_file, 'r') as f:
                data = json.load(f)
                for section, values in data.items():
                    if section not in self.config:
                        self.config[section] = {}
                    for key, value in values.items():
                        self.config[section][key] = str(value)
            with open(self.filename, 'w') as f:
                self.config.write(f)
            os.rename(json_file, json_file + '.old')

    def _ensure_file_exists(self):
        if not os.path.exists(self.filename):
            self.config['MACHINE_CONFIG'] = {'name': 'LinuxCNC', 'kinematics': 'XYZ (3-Axis)', 'units': 'Metric (mm)', 'drive_type': 'Stepper', 'max_velocity': '250.0', 'max_acceleration': '750.0', 'min_spindle_override': '0.5', 'max_spindle_override': '1.2', 'max_feed_override': '2.0', 'enable_probe': 'True', 'enable_spindle_at_speed': 'True', 'enable_spindle_fault': 'True', 'enable_tool_change': 'True', 'enable_coolant': 'True', 'enable_estop': 'True', 'spindle_closed_loop': 'False'}
            self.config['MACHINE_LIMITS'] = {'x_min': '0.0', 'x_max': '500.0', 'y_min': '0.0', 'y_max': '500.0', 'z_min': '-150.0', 'z_max': '0.0'}
            self.config['JOG_SETTINGS'] = {'speed': '100', 'step': '0.1'}
            self.config['UI_PREFERENCES'] = {'theme': 'dark', 'language': 'en', 'auto_save_interval_minutes': '5'}
            with open(self.filename, 'w') as f:
                self.config.write(f)

    def get_setting(self, section, key, default=None):
        try:
            return self.config[section][key]
        except KeyError:
            return default

    def save_setting(self, section, key, value):
        if section not in self.config:
            self.config[section] = {}
        self.config[section][key] = str(value)
        with open(self.filename, 'w') as f:
            self.config.write(f)