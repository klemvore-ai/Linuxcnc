# Multi-Tool Change and Probe Test

The test program is [projects/multi_tool_change_test.ngc](../projects/multi_tool_change_test.ngc).
It verifies the manual tool-change handshake, automatic tool-length probing, tool-table updates, tool-length activation, and commanded spindle telemetry without cutting material.

## Calibration First

Do this before running the multi-tool program:

1. Home the machine and verify the probe input.
2. In the Dashboard, set the tool setter X/Y location and setter velocity.
3. Use **Calibrate Tool Setter** in the Tool Setter panel.
4. Complete the reference-tool/workpiece-zero prompts.
5. Confirm the measured `REF_Z` value appears in LinuxCNC settings.
6. Open Settings -> Probing & Locations and click **Sync Probing to Machine INI**.
7. Approve and activate the staged configuration, then restart LinuxCNC.
8. Perform one single-tool probe check before testing multiple tools.

The O111/O112 files are reference routines and should not replace the bounded Dashboard calibration workflow until their behavior is separately verified on this machine.

## Prerequisites

- LinuxCNC is powered on, homed, idle, and out of E-stop.
- The tool setter X/Y position is calibrated.
- The tool setter reference Z is calibrated and present in the active LinuxCNC `[TOOL_SETTER] REF_Z`.
- `[TOOL_SETTER] MAX_PROBE_DEPTH` and `VEL` are conservative for the current setup.
- `PROBING setter_search_dist` is large enough to reach the setter from the safe approach height, but remains inside the Z soft limit. The current machine values use `100 mm` from `G53 Z0` toward a setter near `Z=-85.525`.
- `[PROBE] Z_SAFE` is a verified machine-coordinate safe height.
- The probe input is connected and tested.
- Tool table entries `T1`, `T2`, and `T3` exist.
- The machine contains the deployed `113.ngc` subroutine in the configured `SUBROUTINE_PATH`.
- The spindle and work area are clear before starting.

## Test Sequence

The program performs this sequence three times:

1. Requests `T1 M6`, `T2 M6`, or `T3 M6`.
2. The Dashboard prompts the operator to install the requested tool.
3. The operator acknowledges the tool change.
4. `O113 call [n]` moves to the setter and probes tool `n`.
5. `G10 L1` writes the measured length to the tool table.
6. `G43 Hn` activates the measured tool offset.
7. The spindle runs at a short commanded test speed for five seconds.
8. `M5` stops the spindle before the next tool change.

The test commands 3000 RPM for T1, 6000 RPM for T2, and 9000 RPM for T3. With an analog 0-10 V spindle and no encoder, the Dashboard RPM value is the commanded RPM, not measured feedback.

## Running It

1. Load `multi_tool_change_test.ngc` through the Dashboard.
2. Confirm the loaded program and verify the machine is powered and idle.
3. Start the cycle.
4. Install each requested tool when prompted, then acknowledge the tool change.
5. Watch the Dashboard position, commanded RPM, tool number, and error fields.
6. Stop the test immediately if probing does not contact the setter, a move is unexpected, or the spindle command is unsafe.

## Expected Results

- Each `M6` pauses and produces one tool-change prompt.
- Each acknowledgement resumes the program.
- Each `O113` completes without a probe or LinuxCNC error.
- The tool table receives a length value for T1, T2, and T3.
- The active offset changes to `H1`, `H2`, and `H3` in sequence.
- Commanded RPM reports approximately 3000, 6000, and 9000 during the three dwell periods.
- The spindle stops before each following tool change.
- The program ends normally at `M2`.

## Failure Handling

Use the Dashboard Abort control if a probe fails or motion is unsafe. Do not continue after a failed probe: a bad tool length can make the next motion unsafe. Record the active tool, current position, and LinuxCNC error message before resetting the machine.
