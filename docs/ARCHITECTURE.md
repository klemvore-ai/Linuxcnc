# CNCFusion Architecture Guide

This document describes where behavior belongs and how the application is intended to evolve. The project is being refactored incrementally; existing Qt screens and LinuxCNC file formats remain supported while services are extracted underneath them.

## Product lifecycle

The application follows three modes:

1. **Setup** creates or imports a standard LinuxCNC machine configuration.
2. **Tune** applies approved runtime-safe values and records tuning results.
3. **Run** presents the Dashboard as the operator control interface.

Setup, tuning, deployment, diagnostics, and recovery are technician workflows. The Dashboard should remain focused on normal operation.

## Runtime boundary

The LinuxCNC host owns the deterministic loop:

- Motion timing and HAL execution
- Mesa or future EtherCAT communication
- E-stop, limits, watchdogs, and fault behavior
- Motor and axis feedback

CNCFusion communicates over the management interface, which may be Wi-Fi or a second NIC. Management traffic covers monitoring, G-code upload, configuration deployment, diagnostics, and approved runtime-safe tuning. Loss of Wi-Fi must not compromise the local control loop.

## Layers

```text
Qt screens (Dashboard, Settings, diagnostics)
        |
Application services and typed models
        |
Local backend | HTTPS/mTLS machine-agent backend
        |
LinuxCNC local runtime and real-time hardware
```

### Existing application layer

- [main.py](../main.py) creates the Qt application, settings manager, tabs, timers, and application shutdown flow.
- [tabs/dashboard.py](../tabs/dashboard.py) is the operator-facing Dashboard and currently contains legacy machine-command workers that are being migrated behind services.
- [tabs/settings_tab.py](../tabs/settings_tab.py) contains the current configuration form, file generation, deployment controls, and lifecycle controls. New configuration and deployment logic should move out of this tab.
- [tabs/setup_wizard.py](../tabs/setup_wizard.py) contains the additive setup wizard. Its current pages collect machine basics, axis motion limits, Mesa identity, control/management network role, essential I/O/safety signals, and a generated INI/HAL review preview, then save them through the existing settings schema. Future pages will add feedback and deployment.
- [tabs/mesa_util_tab.py](../tabs/mesa_util_tab.py) contains Mesa discovery and diagnostics. Mesa-specific mapping belongs in a hardware backend, not in the generic machine model.
- [tabs/wiring_tab.py](../tabs/wiring_tab.py) manages visual HAL components, pins, wires, and project persistence.
- [tabs/layout_editor_tab.py](../tabs/layout_editor_tab.py) manages operator layouts and `.fusionui` persistence.
- [settings_manager.py](../settings_manager.py) owns INI settings persistence and migration. Credentials must not remain plaintext in this file.

### Shared lifecycle services

- [core/lifecycle.py](../core/lifecycle.py) contains `LifecycleState`, `LifecycleResult`, the `LifecycleBackend` protocol, and `LinuxCNCLifecycle`. It owns state transitions, graceful shutdown policy, timeouts, and recovery escalation policy.
- [core/local_lifecycle.py](../core/local_lifecycle.py) implements the backend for a LinuxCNC process on the local host. It uses argument-based subprocess calls and LinuxCNC's own graceful exit API.
- [core/remote_lifecycle.py](../core/remote_lifecycle.py) translates lifecycle operations into structured machine-agent requests.

Normal shutdown must use LinuxCNC's graceful exit. Forceful recovery is a separate, explicit operation and must never be the default button behavior.

### Configuration model and renderer

- [core/config_model.py](../core/config_model.py) defines `AxisConfig`, `JointConfig`, `FeedbackConfig`, `HardwareConfig`, and `MachineConfig`. These classes describe machine meaning and capabilities without embedding Mesa pin names or EtherCAT PDO addresses in the wizard.
- [core/config_renderer.py](../core/config_renderer.py) validates a `MachineConfig` and renders standard LinuxCNC INI and HAL text. Mesa is the first implemented renderer backend; EtherCAT must not be rendered until its backend has been implemented and tested.

`MachineConfig.from_settings()` is the compatibility bridge from the existing `settings.ini` schema. It allows the current Settings tab to adopt the model incrementally while preserving existing settings files and UI controls.

The first wizard slice is intentionally incomplete: it does not claim a machine is ready to run until hardware, safety, and validation pages are completed. The existing expert Settings form and generator remain available during this transition.

`IOSafetyPage` requires an external E-stop signal when that policy is enabled and a probe input when probing is enabled. Optional spindle, coolant, and cycle-control fields remain explicit mappings and are not guessed from board type.

`ReviewPage` previews the model-rendered standard INI/HAL and reports secure agent bundle status when configured. Its staging action uploads the reviewed pair only after confirmation; activation and restart remain separate operations.

The existing Settings tab currently exposes this bridge through **Validate Machine Model**. The established generator remains available until renderer output has been compared against known-good machine configurations.

The current compatibility checkpoint covers the standard INI sections, gantry joint structure, and Mesa `7i76eu` naming. The Generate button must continue using the established generator until the new HAL backend also represents project I/O wiring, MPG/encoder behavior, spindle options, safety signals, and blueprint output.

`IOMapping` is the first explicit HAL wiring layer. The settings bridge currently imports joint home/limit signals and external E-stop mappings into semantic LinuxCNC targets, and the Mesa renderer emits those mappings with inversion handling. Blueprint wiring, MPG, spindle, and richer safety logic remain separate migration slices so their behavior can be verified against known-good HAL files.

Generated net names use stable hyphenated identifiers derived from the semantic target; the target pin remains the LinuxCNC pin name. This keeps signal naming separate from physical Mesa addresses and leaves room for EtherCAT mappings later.

`HALDocumentComposer` keeps `core.hal`, generated `blueprint.hal`, and user-maintained `custom_postgui.hal` as independent artifacts. Regeneration can load the existing post-GUI file and write generated artifacts without folding over operator-authored logic. Future export code should use this composer rather than overwriting an entire configuration directory.

`compose_blueprint()` normalizes one non-empty generated blueprint artifact and rejects empty content. It is intentionally independent from `load_postgui()`, so generated visual wiring and user-authored post-GUI logic cannot be confused during regeneration.

The Wiring tab's explicit **Export HAL** action writes the file selected by the user through the composer and may replace that selected custom artifact by intent. Automatic configuration regeneration must use `load_postgui()` and preserve the existing user-maintained file instead.

`SpindleConfig` owns spindle limits and closed-loop intent. The Mesa renderer emits spindle scaling/control nets and optional PID loading. `IOMapping.direction` distinguishes hardware inputs from LinuxCNC outputs, so coolant outputs are rendered in the correct direction instead of being treated like limit inputs.

`PendantConfig` owns optional MPG phase inputs, count distribution, axis jog-enable inputs, increment selectors, and selector behavior. This is a control-interface encoder, not yet motor or linear-axis feedback. Future dual-loop feedback should use `FeedbackConfig` with explicit motor-side and axis-side roles and a backend-specific control topology.

`AxisFeedbackConfig` and the wizard's `FeedbackPage` record whether an axis is open-loop, motor-feedback, axis-feedback, or dual-feedback, along with semantic signal names. The current Mesa renderer validates and preserves these choices but does not generate dual-loop control logic until the specific encoder/scaling/control topology has been tested on real hardware.

`SafetyConfig` owns the external E-stop interlock. When enabled, the Mesa renderer requires an explicit hardware signal and emits an `and2` chain combining LinuxCNC's internal enable output with that signal before `iocontrol.0.emc-enable-in`. Home and limit inputs remain explicit semantic mappings. Missing safety hardware is a model validation error, not a renderer default.

`ProbingConfig` owns the probe and optional touch-plate signals. The Mesa renderer uses a `lut5` combination and sends the selected result to `motion.probe-input`; probe-only configurations set the selector explicitly false. Tool-setter motion and calibration behavior remain a separate workflow and must not be inferred from a raw input mapping.

Hardware cycle controls are represented as semantic `IOMapping` targets such as `halui.program.run`, `halui.program.stop`, `halui.program.pause`, `halui.program.resume`, and `motion.feed-hold`. Optional tool-change loopback is controlled by `MachineConfig.tool_change_loopback`, keeping the operator controls separate from physical Mesa addresses.

The intended extension path is `MachineConfig -> hardware backend mapping -> LinuxCNC renderer`. Add new hardware support in the backend layer rather than adding board-specific conditionals throughout the Qt screens. Feedback roles such as `motor_encoder` and `axis_encoder` are semantic configuration data and can later drive dual-loop templates.

### Secure machine agent

- [core/machine_agent.py](../core/machine_agent.py) is the desktop HTTPS/mTLS client. It validates the HTTPS requirement, verifies the CA, loads the client identity, bounds responses, and uploads checksummed files.
- [agent/policy.py](../agent/policy.py) validates agent actions, approved INI roots, upload classes, file types, checksums, and path containment.
- [agent/server.py](../agent/server.py) exposes the bounded JSON API over a client-certificate-authenticated TLS socket.
- [agent/linuxcnc_runtime.py](../agent/linuxcnc_runtime.py) performs approved local process and atomic file operations on the LinuxCNC host.
- [agent/__main__.py](../agent/__main__.py) is the service entry point used by deployment tooling.

The current API supports lifecycle status/actions and configuration or G-code uploads. It does not accept arbitrary shell commands. Configuration uploads are staged and backed up; activation and restart remain separate operations.

HAL inspection is exposed as the fixed `GET /v1/hal/pins` operation. It accepts no command text, captures bounded `halcmd show pin` output on the LinuxCNC host, and is the first typed diagnostic replacement for legacy SSH shell execution.

Mesa identification is exposed as `POST /v1/mesa/identify`. The agent validates the board identifier and IP address, then invokes the fixed read-only `mesaflash --device ... --addr ... --readhmid` argument list. The UI uses this endpoint in secure mode and keeps legacy SSH only for unenrolled machines.

Launch-log retrieval is exposed as the fixed `GET /v1/logs/launch` operation. It returns bounded content from the configured launch log and does not accept a remote path, preventing arbitrary file disclosure through the secure interface.

Tool-table transfer uses a validated relative `.tbl` path under the configuration root. Secure Tool Table load reads `<machine-name>/tool.tbl`, and save uses the checksum-verified atomic upload path; the legacy UI path prompt is retained only for unenrolled machines.

Secure Tool Table save follows the upload with `POST /v1/tool-table/reload`. This is an operator-role, idle-only operation that accepts no arbitrary command or path.

Secure Dashboard program loading is a two-step operation: upload `active_program.ngc`, then request `open_program` with the same relative G-code path. The agent validates existence and root containment before calling LinuxCNC's `program_open` API; it accepts no Python or shell text.

Dashboard machine controls use `POST /v1/machine/command` for named `abort`, power, E-stop, and limit-override actions under the `operator` role. Secure mode does not forward arbitrary MDI text; future motion workflows must define named operations, argument bounds, machine-state checks, and explicit confirmation.

The first secure tuning operations are feed and spindle override through `POST /v1/tuning/override`. Values are bounded to `0.0` through `2.0`, and the Dashboard uses these typed operations when the agent is enrolled. More sensitive tuning, including PID and dual-loop parameters, requires additional machine-state and authorization policy.

`GET /v1/machine/state` provides the typed state snapshot used by operation guards. The current state matrix is: monitoring requires reachability; program loading and PID tuning require running/idle LinuxCNC; configuration activation, archive restore, and firmware flashing require stopped LinuxCNC. These checks execute on the machine, not only in the desktop UI.

Dashboard telemetry is exposed through `GET /v1/telemetry` and requires the `monitor` role. Secure `PositionFetcherThread` instances poll this endpoint over the management network; legacy local/SSH streaming remains only as a compatibility path. The network cannot become the deterministic motion loop.

Spindle PID tuning is exposed separately as `POST /v1/tuning/pid`. Only `p`, `i`, `d`, `ff0`, and `ff1` may be changed, one at a time, with bounded values. The LinuxCNC runtime must be running and idle. The Settings tab presents this as an explicit technician action and reports partial application if a later field fails.

Firmware flashing is a separate high-risk operation at `POST /v1/mesa/firmware`. Secure mode requires a stopped LinuxCNC process, validates the board/address and image filename, verifies the checksum, uses fixed `mesaflash` argv lists, and reloads the FPGA only after a successful write. The legacy SSH path remains available only for unenrolled machines.

Backup inventory is exposed through fixed configuration and G-code manifest endpoints. Secure Mesa Utilities actions save these manifests locally for audit and verification. A later archive-transfer/restore implementation must add bounded archive handling, safe extraction, and machine-state checks; manifest status alone does not restore files.

Archive transfer is now exposed through fixed configuration and G-code archive endpoints. Restore validates checksum and archive members, rejects links/devices/traversal, requires LinuxCNC to be stopped, extracts to temporary storage, and swaps the approved root with rollback to the previous root if replacement fails.

Application updates use a separate administrator operation, `POST /v1/application/update`, which stages a bounded validated archive under the agent's update directory without extracting or executing it. Local installation, service replacement, and rollback require a later explicit approval workflow.

The existing Settings deployment workflow uses the agent when enrolled and stages only `.ini`, `.hal`, `.tbl`, and `.var` artifacts under the machine subdirectory. `custom_postgui.hal` is intentionally excluded from that batch because it is user-maintained. The legacy SSH deployment remains available only for machines not yet enrolled with the agent.

The agent exposes bundle status and approval separately from upload. A bundle reports its file names, SHA-256 values, completeness, approval marker, and restart requirement. The operator must explicitly approve a complete bundle; upload success alone is never treated as authorization to activate it.

## Hardware-neutral machine model

The future configuration model should represent meaning rather than physical addresses:

- Axis and joint
- Commanded position
- Motor encoder feedback
- Axis or linear-scale feedback
- Home, limit, E-stop, and fault signals
- Spindle and coolant capabilities
- Control-network interface

Backend adapters map these concepts to Mesa pins, EtherCAT PDOs, or another LinuxCNC-supported device. EtherCAT and other deterministic control traffic must stay on a local control network; Wi-Fi is a supervisory transport only.

## Configuration files

Generated output must remain usable by LinuxCNC without CNCFusion:

- `.ini` machine configuration
- `.hal` core and hardware logic
- `custom_postgui.hal`
- `tool.tbl`
- `linuxcnc.var`
- G-code files under `nc_files/`

CNCFusion project metadata may preserve wizard state, but LinuxCNC must not require that metadata to start. Generated and user-maintained content must eventually be tracked separately so regeneration does not overwrite custom HAL logic.

## Security rules

- Use HTTPS with mutual TLS for normal agent communication.
- Store desktop secrets in OS-native credential storage, not `settings.ini`.
- [core/credentials.py](../core/credentials.py) provides the desktop credential boundary through `keyring`, declared in [requirements.txt](../requirements.txt).
- [agent/authorization.py](../agent/authorization.py) can restrict the mTLS client to an explicit SHA-256 certificate fingerprint allowlist; production deployments must configure the allowlist.
- Keep private keys readable only by the owning account or service.
- Use separate management and control network interfaces when available.
- Do not send real-time motion or safety traffic over Wi-Fi.
- Do not accept arbitrary remote shell commands in the agent.
- Enrolled secure-agent mode disables the legacy custom-shell command UI; add typed agent operations for supported diagnostics instead.
- Validate paths, file types, payload sizes, checksums, and machine state.
- Keep startup, graceful shutdown, recovery, deployment, and motion authorization distinct.

Axis homing uses the named `POST /v1/machine/home-axis` operation. It accepts only a standard axis identifier and requires powered LinuxCNC on the machine. Arbitrary homing or MDI strings are not sent through the secure agent.

The first typed probe operation is `POST /v1/machine/probe-z`, restricted to the `technician` role and powered/idle LinuxCNC. Existing tool-setter and edge-finding workflows contain multiple motion steps and remain legacy until they are decomposed into reviewed, bounded operations.

Tool-setter positioning is the first decomposed calibration operation at `POST /v1/machine/tool-setter`. It accepts bounded coordinates, requires powered/idle LinuxCNC, and uses fixed local MDI commands. Full tool-setter calibration remains separate because it includes multi-step probing and result calculation.

Secure Macro Editor file management uses the approved G-code list/read endpoints and checksum-verified uploads. It does not execute macro text through the agent. Macro execution requires a future named operation with explicit program allowlisting, machine-state checks, role authorization, and confirmation.

Legacy SSH consumers remain compatible during migration. When `keyring` is installed, SettingsManager migrates existing plaintext SSH passwords and refuses future plaintext password writes; the Mesa Utilities UI still needs an explicit enrollment/error state before the legacy path can be retired.

`SettingsManager` now migrates an existing SSH password to the OS keyring when available, returns the keyring value through the existing `get_setting("SSH", "password")` API, and clears plaintext on future password writes. This keeps legacy SSH consumers compatible while the UI is migrated away from direct credential persistence.

The machine agent applies role checks in its request handler. Certificates may be mapped with `--client-role FINGERPRINT=role,role`; the operation matrix is `monitor` for inspection, `operator` for program loading and overrides, `technician` for PID tuning, and `administrator` for deployment, restore, firmware, and recovery. The desktop UI is not the security boundary; the agent repeats these checks on the LinuxCNC host.

The LinuxCNC host deployment template is [agent/cncfusion-agent.service](../agent/cncfusion-agent.service), with deployment variables shown in [agent/agent.env.example](../agent/agent.env.example). The agent should bind to the management interface, while the control NIC remains dedicated to Mesa/EtherCAT real-time traffic. Service hardening and firewall configuration are required before production use.

`tests/test_agent_http.py` provides an in-process HTTP harness with an injected peer certificate and fake runtime. It verifies request routing, certificate allowlisting, combined monitor/operator roles, and machine-state rejection without requiring LinuxCNC or TLS certificates on the development machine.

`GET /v1/agent/status` provides version, capability, role-mode, and machine-state discovery. UI controls should be enabled from this response rather than assuming every agent or LinuxCNC host supports every operation.

Mesa Utilities applies discovered capabilities to its action buttons after a secure connection test. This prevents stale clients from offering unsupported actions, but the server-side role and state checks remain authoritative.

## Documentation convention

Public classes, functions, protocols, and data objects in new service code require concise docstrings describing their responsibility and contract. Legacy UI code should receive targeted documentation at ownership boundaries rather than comments on every local variable. Tests document edge cases and are the executable reference for safety policy.

## Validation

Run the focused suite from the repository root:

```text
python -m compileall -q agent core tests main.py tabs/dashboard.py tabs/settings_tab.py
python -m unittest tests.test_agent_policy tests.test_lifecycle tests.test_remote_lifecycle
```

Before enabling remote deployment on a machine, test certificate enrollment, firewall/interface selection, graceful shutdown, staged upload, rollback, and recovery on a non-production LinuxCNC host.