"""Public LinuxCNC settings manager for user-facing machine configuration.

This file owns normal INI settings for the GUI and operator workflow.
Private machine-admin secrets and system-critical credential handling remain
isolated behind the credential store boundary used by the private layer.
"""

import configparser
import os
import json
from core.credentials import CredentialStore, CredentialStoreError


class SettingsManager:
    def __init__(self, filename="settings.ini", credential_store=None):
        self.filename = os.fspath(filename)
        self.credential_store = credential_store
        self.config = configparser.ConfigParser()
        self._migrate_from_json()
        self._ensure_file_exists()
        self.config.read(self.filename)
        self._migrate_legacy_ssh_password()

    @staticmethod
    def _secret_account(section, key):
        """Return the stable keyring account used for one settings secret."""
        return f"{section.lower()}_{key.lower()}"

    def _get_credential_store(self):
        """Return the injected store or construct the default OS-backed credential store.

        Public config code may read SSH credentials from the system keyring, but the
        machine-admin credential policy is intentionally kept separate from the rest
        of the public app config surface.
        """
        return self.credential_store or CredentialStore()

    def _migrate_legacy_ssh_password(self):
        """Move a legacy plaintext SSH password into the OS keyring once."""
        legacy_password = self.config.get("SSH", "password", fallback="")
        if not legacy_password:
            return
        try:
            store = self._get_credential_store()
            account = self._secret_account("SSH", "password")
            if store.get(account) is None:
                store.save(account, legacy_password)
            self.config["SSH"]["password"] = ""
            self._write_config()
        except CredentialStoreError:
            # Keep the legacy value readable until secure storage is available.
            return

    def save_secret(self, account, secret):
        """Store a secret outside the INI file using the OS credential store."""
        CredentialStore().save(account, secret)

    def get_secret(self, account):
        """Retrieve a secret from the OS credential store, if enrolled."""
        return CredentialStore().get(account)

    def delete_secret(self, account):
        """Delete a secret from the OS credential store."""
        CredentialStore().delete(account)

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

    def reload(self):
        """Force re-read the configuration from disk."""
        self.config.clear()
        self.config.read(self.filename)
        self._ensure_file_exists()

    def _ensure_file_exists(self):
        if not os.path.exists(self.filename):
            self.config['MACHINE_CONFIG'] = {'name': 'LinuxCNC', 'kinematics': 'XYZ (3-Axis)', 'units': 'Metric (mm)', 'drive_type': 'Stepper', 'display_ui': 'LinuxCNC', 'min_spindle_override': '0.5', 'max_spindle_override': '1.2', 'max_feed_override': '2.0', 'enable_probe': 'True', 'enable_spindle_at_speed': 'True', 'enable_spindle_fault': 'True', 'enable_tool_change': 'True', 'enable_coolant': 'True', 'enable_estop': 'True', 'spindle_closed_loop': 'False', 'shared_home_min_limit': 'False'}
            self.config['GLOBAL_TIMINGS'] = {'steplen': '1000', 'stepspace': '1000', 'dirsetup': '200', 'dirhold': '200', 'ferror': '10.0', 'min_ferror': '1.0', 'spindle_max_rpm': '24000', 'spindle_min_rpm': '100'}
            self.config['AXIS_X'] = {'min': '0.0', 'max': '1298.0', 'vel': '75.0', 'accel': '600.0', 'steps': '1000', 'units': '2.0'}
            self.config['AXIS_Y'] = {'min': '0.0', 'max': '1298.0', 'vel': '75.0', 'accel': '600.0', 'steps': '1000', 'units': '2.0'}
            self.config['AXIS_Z'] = {'min': '-125.0', 'max': '0.0', 'vel': '30.0', 'accel': '150.0', 'steps': '5000', 'units': '4.0'}
            self.config['AXIS_A'] = {'min': '-360.0', 'max': '360.0', 'vel': '180.0', 'accel': '600.0', 'steps': '5000', 'units': '360.0'}
            self.config['AXIS_B'] = {'min': '-360.0', 'max': '360.0', 'vel': '180.0', 'accel': '600.0', 'steps': '5000', 'units': '360.0'}
            self.config['HOMING'] = {
                'x_home': '5.0', 'x_offset': '5.0', 'x_search': '-5.0', 'x_latch': '1.0', 'x_seq': '2',
                'y_home': '5.0', 'y_offset': '5.0', 'y_search': '-5.0', 'y_latch': '1.0', 'y_seq': '-3',
                'y2_offset': '2.52',
                'z_home': '-0.5', 'z_offset': '-0.5', 'z_search': '3.0', 'z_latch': '-0.5', 'z_seq': '1',
                'a_home': '0.0', 'a_offset': '0.0', 'a_search': '0.0', 'a_latch': '0.0', 'a_seq': '0',
                'b_home': '0.0', 'b_offset': '0.0', 'b_search': '0.0', 'b_latch': '0.0', 'b_seq': '0'
            }
            self.config['JOG_SETTINGS'] = {'speed': '100', 'step': '0.1', 'turbo_multiplier': '5.0', 'enable_mpg_increments': 'False', 'mpg_debounce_time': '0.2', 'mpg_use_graycode': 'False', 'mpg_suppress_no_input': 'False', 'mpg_increments': '0.000, 0.001, 0.010, 0.100, 0.125'}
            self.config['UI_PREFERENCES'] = {'theme': 'dark', 'language': 'en', 'auto_save_interval_minutes': '5'}
            self.config['PROBING'] = {'tool_setter_x': '0.0', 'tool_setter_y': '0.0', 'probe_vel': '100.0', 'setter_vel': '150.0'}
            self.config['SPINDLE_PID'] = {'p': '0.0', 'i': '0.0', 'd': '0.0', 'ff0': '1.0', 'ff1': '0.0'}
            self.config['IO_MAPPING'] = {
                'joint_0_home': 'hm2_7i76e.0.7i76.0.0.input-00', 'joint_0_home_inv': 'True',
                'joint_0_neg': 'hm2_7i76e.0.7i76.0.0.input-10', 'joint_0_neg_inv': 'True',
                'joint_0_pos': 'hm2_7i76e.0.7i76.0.0.input-20', 'joint_0_pos_inv': 'True',
                'joint_1_home': 'hm2_7i76e.0.7i76.0.0.input-01', 'joint_1_home_inv': 'True',
                'joint_1_neg': 'hm2_7i76e.0.7i76.0.0.input-11', 'joint_1_neg_inv': 'True',
                'joint_1_pos': 'hm2_7i76e.0.7i76.0.0.input-21', 'joint_1_pos_inv': 'True',
                'joint_2_home': 'hm2_7i76e.0.7i76.0.0.input-02', 'joint_2_home_inv': 'True',
                'joint_2_neg': 'hm2_7i76e.0.7i76.0.0.input-12', 'joint_2_neg_inv': 'True',
                'joint_2_pos': 'hm2_7i76e.0.7i76.0.0.input-22', 'joint_2_pos_inv': 'True',
                'joint_3_home': 'hm2_7i76e.0.7i76.0.0.input-07', 'joint_3_home_inv': 'True',
                'joint_3_neg': 'hm2_7i76e.0.7i76.0.0.input-13', 'joint_3_neg_inv': 'True',
                'joint_3_pos': 'hm2_7i76e.0.7i76.0.0.input-23', 'joint_3_pos_inv': 'True',
                'joint_4_home': 'hm2_7i76e.0.7i76.0.0.input-08', 'joint_4_home_inv': 'True',
                'joint_4_neg': 'hm2_7i76e.0.7i76.0.0.input-14', 'joint_4_neg_inv': 'True',
                'joint_4_pos': 'hm2_7i76e.0.7i76.0.0.input-24', 'joint_4_pos_inv': 'True',
                'probe_in': 'hm2_7i76e.0.7i76.0.0.input-03', 'probe_in_inv': 'False',
                'plate_setter': '', 'plate_setter_inv': 'False',
                'setter_overtravel': '', 'setter_overtravel_inv': 'False',
                'spindle_at_speed': 'hm2_7i76e.0.7i76.0.0.input-04', 'spindle_at_speed_inv': 'False',
                'spindle_fault': 'hm2_7i76e.0.7i76.0.0.input-05', 'spindle_fault_inv': 'True',
                'estop_ext': 'hm2_7i76e.0.7i76.0.0.input-06', 'estop_ext_inv': 'True',
                'coolant_mist': 'hm2_7i76e.0.7i76.0.0.output-00', 'coolant_mist_inv': 'False',
                'coolant_flood': 'hm2_7i76e.0.7i76.0.0.output-01', 'coolant_flood_inv': 'False',
                'btn_cycle_start': '', 'btn_cycle_start_inv': 'False',
                'btn_cycle_stop': '', 'btn_cycle_stop_inv': 'False',
                'btn_cycle_pause': '', 'btn_cycle_pause_inv': 'False',
                'btn_cycle_resume': '', 'btn_cycle_resume_inv': 'False',
                'btn_feed_hold': '', 'btn_feed_hold_inv': 'False',
                'jog_x_pos': '', 'jog_x_pos_inv': 'False',
                'jog_x_neg': '', 'jog_x_neg_inv': 'False',
                'jog_y_pos': '', 'jog_y_pos_inv': 'False',
                'jog_y_neg': '', 'jog_y_neg_inv': 'False',
                'jog_z_pos': '', 'jog_z_pos_inv': 'False',
                'jog_z_neg': '', 'jog_z_neg_inv': 'False',
                'jog_a_pos': '', 'jog_a_pos_inv': 'False',
                'jog_a_neg': '', 'jog_a_neg_inv': 'False',
                'jog_b_pos': '', 'jog_b_pos_inv': 'False',
                'jog_b_neg': '', 'jog_b_neg_inv': 'False',
                'mpg_count': '', 'mpg_count_inv': 'False',
                'mpg_a': '', 'mpg_a_inv': 'False',
                'mpg_b': '', 'mpg_b_inv': 'False',
                'mpg_x_en': '', 'mpg_x_en_inv': 'False',
                'mpg_y_en': '', 'mpg_y_en_inv': 'False',
                'mpg_z_en': '', 'mpg_z_en_inv': 'False',
                'mpg_a_en': '', 'mpg_a_en_inv': 'False',
                'mpg_b_en': '', 'mpg_b_en_inv': 'False',
                'jog_incr_a': '', 'jog_incr_a_inv': 'False',
                'jog_incr_b': '', 'jog_incr_b_inv': 'False',
                'jog_incr_c': '', 'jog_incr_c_inv': 'False',
                'jog_incr_d': '', 'jog_incr_d_inv': 'False',
                'feed_override_analog': '', 'feed_override_analog_inv': 'False',
                'spindle_override_analog': '', 'spindle_override_analog_inv': 'False'
            }
            with open(self.filename, 'w') as f:
                self.config.write(f)

    def get_setting(self, section, key, default=None):
        if section.upper() == "SSH" and key.lower() == "password":
            try:
                secret = self._get_credential_store().get(self._secret_account(section, key))
                if secret is not None:
                    return secret
            except CredentialStoreError:
                pass
        try:
            return self.config[section][key]
        except KeyError:
            return default

    def save_setting(self, section, key, value):
        if section.upper() == "SSH" and key.lower() == "password":
            try:
                self._get_credential_store().save(self._secret_account(section, key), str(value))
                value = ""
            except CredentialStoreError as error:
                raise RuntimeError("Unable to store SSH password securely.") from error
        if section not in self.config:
            self.config[section] = {}
        self.config[section][key] = str(value)
        self._write_config()

    def _write_config(self):
        """Write the non-secret INI configuration to disk."""
        with open(self.filename, 'w') as f:
            self.config.write(f)