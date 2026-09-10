# Dashboard control safety checklist

This checklist reflects the current LinuxCNC/CNCFusion boundary: LinuxCNC owns the real machine runtime and safety loop, while CNCFusion remains a bounded operator UI and workflow layer.

## Rule of thumb

- Safe: monitor-only UI actions, editing, and display work.
- Needs agent: any action that changes machine state, motion, or offsets.
- Unsafe: arbitrary MDI, raw shell execution, or freeform G-code input without a typed policy gate.

## Safe controls

These do not change machine state and may remain in the public UI layer.

- DRO display / status LEDs / telemetry panels
- 3D toolpath preview / file viewer
- G-code editor load, merge, save, clear, clean, and rapid-fix helpers
- G-code upload preview and local file operations
- reading machine status, homing state, current mode, and tool data
- reading macro and capability lists from the secure agent
- local settings inspection and display-only calibration values

## Needs agent / typed machine action

These should be bound to the secure machine-agent contract, with machine-state checks and bounded parameters.

- Raw Z probe: `probe_z(target, velocity)`
- Touch plate Z offset: `touch_off_z(target, velocity, plate_thickness)`
- Home All / axis homing
- Unhome All
- Program start / pause / stop / cycle control
- Mode selection: Manual / Auto
- Spindle start/stop (M3 / M4 / M5)
- Coolant on/off (M7 / M8 / M9)
- Tool load / tool change flow
- Tool-setter move / park actions
- Tool-setter calibration
- Safe park machine action
- Approved O-code subroutine execution
- Any button that writes work offsets, tool offsets, machine mode, or axis motion

## Unsafe controls

These must be blocked or removed from the secure operation surface.

- Arbitrary MDI entry and Execute
- Freeform G-code execution from the dashboard without verification and allowlisting
- Remote shell commands or SSH-driven machine operation in the operator UI
- Any unvalidated machine command that takes freeform strings from the UI
- `G10`, `G92`, raw `G-code` or motion commands entered directly by the operator without a typed contract

## Per-button classification for the current dashboard

### Probing / offsets

- `Set Z Zero` — Needs agent or local typed contract; this writes a work offset
- `Fast Z Probe` — Needs agent; it is a validated probe workflow
- `Raw Z Probe (No Offset)` — Needs agent; this is the explicit no-offset probe surface
- `Touch Plate Z Offset` — Needs agent; this is the typed offset-setting workflow
- `Probe Z` in the touch tab — Needs agent; same validated probe contract
- XY edge / inside / outside probing buttons — Needs agent or local-only advanced geometry workflow; not approved for unrestricted execution
- `Probe Tool Height` — Needs agent or controlled local-only workflow; not a freeform MDI path

### Manual motion / home

- `Home X / Y / Z` and `Home All` — Needs agent or local-only LinuxCNC authority, never arbitrary user MDI
- `Jog` buttons — Needs agent or local-only machine authority with safe state checks; not arbitrary freeform motion
- `Zero X / Y / Z` — Needs agent or typed locked operation; it changes the machine coordinate context

### Program control

- `MANUAL` — Needs agent if used to change controller mode
- `AUTO` — Needs agent if used to change controller mode
- `CYCLE START` — Needs agent
- `PAUSE` — Needs agent
- `STOP` — Needs agent

### Spindle / coolant / macros

- `M3 / M4 / M5` — Needs agent
- `M7 / M8 / M9` — Needs agent
- `Run Subroutine` — Needs agent with allowlisted O-code / secure macro contract

### UI-only controls

- `Load`, `Merge`, `Save`, `Clear`, `Clean G-Code`, `Fix Fusion Rapids`, `Save Screenshot` — Safe
- `Load Tool` — Needs agent
- `Move to Tool Change / Setter` — Needs agent
- `Calibrate Tool Setter` — Needs agent
- `Park Machine` — Needs agent

## Acceptance rule

A control is approved for the public UI only when it is:

1. read-only, or
2. implemented through a typed, validated, machine-agent operation, or
3. running on the LinuxCNC host under direct local control with explicit safety checks and no arbitrary shell/MDI strings.

If a button could send arbitrary G-code or shell commands, it is unsafe and should be disabled in secure mode.
