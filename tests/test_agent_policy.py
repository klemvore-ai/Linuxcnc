import tempfile
import unittest
from pathlib import Path

from agent.policy import AgentPolicyError, validate_action, validate_upload, validate_activation, validate_restart_activation, validate_mesa_identification, validate_tool_table_path, validate_gcode_path, validate_override, validate_pid, validate_firmware, validate_machine_command, validate_spindle_command, validate_tool_change, validate_home_axis, validate_probe_operation, validate_touch_off_z, validate_tool_setter_move, validate_hal_value_request


class AgentPolicyTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name) / "configs"
        self.root.mkdir()
        self.ini_file = self.root / "machine.ini"
        self.ini_file.write_text("[EMC]\nMACHINE = Test\n", encoding="utf-8")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_accepts_graceful_shutdown_without_extra_fields(self):
        self.assertEqual(
            validate_action({"action": "graceful_shutdown"}, [self.root]),
            ("graceful_shutdown", None),
        )

    def test_accepts_ini_inside_approved_root(self):
        action, path = validate_action(
            {"action": "start", "ini_path": str(self.ini_file)},
            [self.root],
        )
        self.assertEqual(action, "start")
        self.assertEqual(path, str(self.ini_file.resolve()))

    def test_rejects_ini_outside_approved_root(self):
        outside = Path(self.temp_dir.name) / "outside.ini"
        outside.write_text("[EMC]\n", encoding="utf-8")
        with self.assertRaises(AgentPolicyError):
            validate_action({"action": "start", "ini_path": str(outside)}, [self.root])

    def test_rejects_non_ini_and_unknown_actions(self):
        with self.assertRaises(AgentPolicyError):
            validate_action({"action": "start", "ini_path": str(self.root)}, [self.root])
        with self.assertRaises(AgentPolicyError):
            validate_action({"action": "run_shell", "command": "id"}, [self.root])

    def test_rejects_extra_fields_on_shutdown(self):
        with self.assertRaises(AgentPolicyError):
            validate_action({"action": "graceful_shutdown", "force": True}, [self.root])

    def test_accepts_named_coolant_machine_commands(self):
        self.assertEqual(
            validate_machine_command({"command": "coolant_mist_on"}),
            "coolant_mist_on",
        )
        self.assertEqual(
            validate_machine_command({"command": "coolant_flood_on"}),
            "coolant_flood_on",
        )

    def test_accepts_named_program_machine_commands(self):
        self.assertEqual(
            validate_machine_command({"command": "program_start"}),
            "program_start",
        )
        self.assertEqual(
            validate_machine_command({"command": "program_pause_resume"}),
            "program_pause_resume",
        )
        self.assertEqual(
            validate_machine_command({"command": "mode_manual"}),
            "mode_manual",
        )
        self.assertEqual(
            validate_machine_command({"command": "home_all"}),
            "home_all",
        )

    def test_validates_bounded_named_spindle_commands(self):
        self.assertEqual(validate_spindle_command({"action": "forward", "rpm": 12000}), ("forward", 12000.0))
        self.assertEqual(validate_spindle_command({"action": "stop", "rpm": 0}), ("stop", 0.0))
        with self.assertRaises(AgentPolicyError):
            validate_spindle_command({"action": "forward", "rpm": 0})
        with self.assertRaises(AgentPolicyError):
            validate_spindle_command({"action": "forward", "rpm": 100001})

    def test_validates_bounded_tool_change(self):
        self.assertEqual(validate_tool_change({"tool": 42}), 42)
        with self.assertRaises(AgentPolicyError):
            validate_tool_change({"tool": 10000})

    def test_validates_bounded_unique_hal_value_pin_list(self):
        self.assertEqual(
            validate_hal_value_request({"pins": ["joint.0.pos-fb", "hm2_7i76e.0.input-00"]}),
            ["joint.0.pos-fb", "hm2_7i76e.0.input-00"],
        )
        with self.assertRaises(AgentPolicyError):
            validate_hal_value_request({"pins": ["joint.0.pos-fb", "joint.0.pos-fb"]})
        with self.assertRaises(AgentPolicyError):
            validate_hal_value_request({"pins": ["joint.0.pos-fb;id"]})

    def test_accepts_config_upload_inside_root(self):
        kind, destination = validate_upload(
            {
                "kind": "config",
                "relative_path": "machine.hal",
                "sha256": "a" * 64,
                "content": "Y29uZmln",
            },
            self.root,
            self.root / "nc_files",
        )
        self.assertEqual(kind, "config")
        self.assertEqual(destination, (self.root / "machine.hal").resolve())

    def test_accepts_gcode_only_in_gcode_root(self):
        gcode_root = self.root / "nc_files"
        kind, destination = validate_upload(
            {
                "kind": "gcode",
                "relative_path": "job.ngc",
                "sha256": "b" * 64,
                "content": "Z28=",
            },
            self.root,
            gcode_root,
        )
        self.assertEqual(kind, "gcode")
        self.assertEqual(destination, (gcode_root / "job.ngc").resolve())

    def test_rejects_upload_traversal_and_wrong_file_class(self):
        base_payload = {
            "kind": "gcode",
            "relative_path": "../machine.hal",
            "sha256": "c" * 64,
            "content": "aA==",
        }
        with self.assertRaises(AgentPolicyError):
            validate_upload(base_payload, self.root, self.root / "nc_files")
        base_payload["relative_path"] = "machine.hal"
        with self.assertRaises(AgentPolicyError):
            validate_upload(base_payload, self.root, self.root / "nc_files")

    def test_accepts_distinct_pending_and_active_ini_files(self):
        pending = self.root / "settings_pending.ini"
        active = self.root / "settings.ini"
        pending.write_text("pending", encoding="utf-8")
        active.write_text("active", encoding="utf-8")

        pending_path, active_path = validate_activation(
            {
                "action": "activate_pending",
                "pending_path": "settings_pending.ini",
                "active_path": "settings.ini",
            },
            self.root,
        )

        self.assertEqual(pending_path, pending.resolve())
        self.assertEqual(active_path, active.resolve())

    def test_accepts_activation_and_restart_action(self):
        pending = self.root / "settings_pending.ini"
        pending.write_text("pending", encoding="utf-8")
        active = self.root / "settings.ini"
        active.write_text("active", encoding="utf-8")

        pending_path, active_path = validate_restart_activation(
            {
                "action": "activate_and_restart",
                "pending_path": "settings_pending.ini",
                "active_path": "settings.ini",
            },
            self.root,
        )

        self.assertEqual(pending_path, pending.resolve())
        self.assertEqual(active_path, active.resolve())

    def test_rejects_activation_of_missing_or_outside_pending_file(self):
        with self.assertRaises(AgentPolicyError):
            validate_activation(
                {
                    "action": "activate_pending",
                    "pending_path": "missing.ini",
                    "active_path": "settings.ini",
                },
                self.root,
            )
        with self.assertRaises(AgentPolicyError):
            validate_activation(
                {
                    "action": "activate_pending",
                    "pending_path": "../settings_pending.ini",
                    "active_path": "settings.ini",
                },
                self.root,
            )

    def test_validates_mesa_identification_inputs(self):
        self.assertEqual(
            validate_mesa_identification({"board": "7i76e", "address": "10.10.10.10"}),
            ("7i76e", "10.10.10.10"),
        )
        with self.assertRaises(AgentPolicyError):
            validate_mesa_identification({"board": "7i76e;id", "address": "10.10.10.10"})
        with self.assertRaises(AgentPolicyError):
            validate_mesa_identification({"board": "7i76e", "address": "not-an-ip"})

    def test_tool_table_path_must_be_relative_tbl_file(self):
        self.assertEqual(
            validate_tool_table_path("TestMill/tool.tbl", self.root),
            (self.root / "TestMill/tool.tbl").resolve(),
        )
        with self.assertRaises(AgentPolicyError):
            validate_tool_table_path("../tool.tbl", self.root)
        with self.assertRaises(AgentPolicyError):
            validate_tool_table_path("TestMill/tool.ini", self.root)

    def test_gcode_path_must_exist_inside_gcode_root(self):
        gcode_root = self.root / "nc_files"
        gcode_root.mkdir()
        job = gcode_root / "job.ngc"
        job.write_text("G0 X1\n", encoding="utf-8")

        self.assertEqual(validate_gcode_path("job.ngc", gcode_root), job.resolve())
        with self.assertRaises(AgentPolicyError):
            validate_gcode_path("../machine.ini", gcode_root)
        with self.assertRaises(AgentPolicyError):
            validate_gcode_path("missing.ngc", gcode_root)

    def test_override_policy_allows_bounded_runtime_values(self):
        self.assertEqual(validate_override({"kind": "feed", "value": 1.25}), ("feed", 1.25))
        self.assertEqual(validate_override({"kind": "spindle", "value": 0.5}), ("spindle", 0.5))
        with self.assertRaises(AgentPolicyError):
            validate_override({"kind": "feed", "value": 2.1})
        with self.assertRaises(AgentPolicyError):
            validate_override({"kind": "shell", "value": 1.0})

    def test_pid_policy_allows_one_nonnegative_gain(self):
        payload = {field: field == "p" for field in ("p", "i", "d", "ff0", "ff1")}
        payload["value"] = 2.5
        self.assertEqual(validate_pid(payload), ("p", 2.5))
        payload["p"] = False
        payload["ff0"] = True
        payload["value"] = -1.0
        self.assertEqual(validate_pid(payload), ("ff0", -1.0))
        with self.assertRaises(AgentPolicyError):
            validate_pid({**payload, "ff1": True})
        payload["ff0"] = False
        payload["p"] = True
        payload["value"] = -1.0
        with self.assertRaises(AgentPolicyError):
            validate_pid(payload)

    def test_firmware_policy_requires_safe_filename_and_valid_board(self):
        payload = {
            "board": "7i76e",
            "address": "10.10.10.10",
            "filename": "firmware.bit",
            "sha256": "a" * 64,
            "content": "YmluYXJ5",
        }
        self.assertEqual(validate_firmware(payload), ("7i76e", "10.10.10.10", "firmware.bit"))
        payload["filename"] = "../firmware.bit"
        with self.assertRaises(AgentPolicyError):
            validate_firmware(payload)
        payload["filename"] = "firmware.txt"
        with self.assertRaises(AgentPolicyError):
            validate_firmware(payload)

    def test_machine_command_policy_allows_named_controls_only(self):
        self.assertEqual(validate_machine_command({"command": "power_on"}), "power_on")
        self.assertEqual(validate_machine_command({"command": "estop"}), "estop")
        with self.assertRaises(AgentPolicyError):
            validate_machine_command({"command": "mdi G0 X1"})

    def test_home_axis_policy_allows_standard_axis_names_only(self):
        self.assertEqual(validate_home_axis({"axis": "z"}), "Z")
        with self.assertRaises(AgentPolicyError):
            validate_home_axis({"axis": "G0 X1"})

    def test_probe_policy_allows_bounded_numeric_operation_only(self):
        self.assertEqual(
            validate_probe_operation({"operation": "probe_z", "target": -10, "velocity": 50}),
            (-10.0, 50.0),
        )
        with self.assertRaises(AgentPolicyError):
            validate_probe_operation({"operation": "mdi", "target": 0, "velocity": 50})
        with self.assertRaises(AgentPolicyError):
            validate_probe_operation({"operation": "probe_z", "target": 0, "velocity": 0})
        with self.assertRaises(AgentPolicyError):
            validate_probe_operation({"operation": "probe_z", "target": 1, "velocity": 50})

    def test_touch_off_policy_requires_bounded_downward_probe_and_retract(self):
        self.assertEqual(
            validate_touch_off_z({"target": -10, "velocity": 50, "plate_thickness": 6.35, "retract": 2}),
            (-10.0, 50.0, 6.35, 2.0),
        )
        with self.assertRaises(AgentPolicyError):
            validate_touch_off_z({"target": 1, "velocity": 50, "plate_thickness": 6.35, "retract": 2})
        with self.assertRaises(AgentPolicyError):
            validate_touch_off_z({"target": -10, "velocity": 50, "plate_thickness": 6.35, "retract": 0})

    def test_tool_setter_move_requires_bounded_coordinates(self):
        self.assertEqual(
            validate_tool_setter_move({"x": 10, "y": 20, "z": -5}),
            (10.0, 20.0, -5.0),
        )
        with self.assertRaises(AgentPolicyError):
            validate_tool_setter_move({"x": 10, "y": 20, "z": "G0 X1"})


if __name__ == "__main__":
    unittest.main()