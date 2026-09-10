# CNCFusion LinuxCNC Agent

The agent provides a small HTTPS/mTLS administration endpoint for lifecycle operations. It is intended to run on the LinuxCNC machine under a dedicated service account.

The initial API is:

- `GET /v1/lifecycle` returns `{ "running": true|false }`.
- `GET /v1/hal/pins` runs the fixed HAL pin inspection operation and returns bounded output.
- `GET /v1/hal/components` runs the fixed HAL component inspection operation and returns bounded output.
- `POST /v1/hal/values` returns one read-only snapshot for 1 through 16 validated, unique HAL pin names.
- `POST /v1/mesa/identify` runs read-only Mesa HMID identification from a validated board identifier and IP address.
- `POST /v1/lifecycle/actions` accepts `graceful_shutdown`, `recovery_shutdown`, or `start` with an approved `ini_path`.
- `POST /v1/files` accepts bounded, checksum-verified `config` or `gcode` uploads using relative paths.
- `POST /v1/configuration/actions` accepts `activate_pending` for an explicit pending-to-active INI promotion.
- `POST /v1/configuration/actions` also accepts `activate_and_restart` for verified startup with rollback on failure.
- `GET /v1/configuration/bundles/<name>` reports staged files, checksums, completeness, approval, and restart requirement.
- `POST /v1/configuration/bundles/<name>/approve` records explicit approval for a complete bundle.

The agent does not accept arbitrary shell commands. `recovery_shutdown` is intentionally disabled until a separate recovery policy is implemented.

When the secure agent is enrolled, CNCFusion disables its arbitrary custom-shell command path. Machine operations must be added as narrowly scoped agent actions instead.

Configuration uploads are restricted to the configured configuration root and are atomically replaced with a backup of the previous file. G-code uploads are restricted to the configured G-code root and are never treated as machine configuration.

The Settings tab uses this endpoint to upload `settings_pending.ini`. Uploading stages the file only; it does not activate the configuration or restart LinuxCNC. Activation is a separate operator-approved action, requires LinuxCNC to be stopped, backs up the active INI, and atomically promotes the pending file. Restart remains a separate lifecycle operation with machine-state checks.

`activate_and_restart` is intended for a LinuxCNC machine configuration. It requires LinuxCNC to be stopped, backs up both active and pending files during the transaction, starts LinuxCNC, and restores the previous configuration if the process does not become visible before the startup timeout.

Bundle status and approval are separate from upload. A successful upload does not imply that the configuration is complete or approved. Approval is persisted as a machine-side marker and can be reviewed before a later activation request.

Secure log retrieval is limited to the configured LinuxCNC launch log. The secure UI does not accept an arbitrary remote file path; arbitrary path retrieval remains part of the legacy compatibility path only.

Tool-table access uses `GET /v1/tool-table/<relative-path>` for reads and the existing checksum-verified file upload endpoint for writes. Secure UI operations use `<machine-name>/tool.tbl` below the approved configuration root; arbitrary absolute paths remain legacy-only.

After a secure tool-table upload, CNCFusion calls `POST /v1/tool-table/reload`. The agent accepts no fields for this operation and requires LinuxCNC to be running and idle before reloading offsets.

Dashboard program loading uploads `active_program.ngc` and then calls `POST /v1/program/open` with that relative path. The agent verifies that the file exists inside the approved G-code root before passing it to LinuxCNC.

Runtime-safe tuning uses `POST /v1/tuning/override` with a bounded `feed` or `spindle` value from `0.0` through `2.0`. The agent calls LinuxCNC's command API directly; arbitrary Python and shell tuning commands are not accepted.

Spindle direction uses `POST /v1/spindle/command` with an allowlisted `forward`, `reverse`, or `stop` action. Forward and reverse require RPM from `1` through `100000`; the agent requires powered LinuxCNC and never accepts MDI text.

Tool loading uses `POST /v1/tool/change` with one integer tool number from `0` through `9999`. The agent requires powered, idle LinuxCNC and executes only the fixed `Tn M6 G43` sequence.

Machine state is available at `GET /v1/machine/state`. Program loading and PID tuning require LinuxCNC to be running and idle. Configuration activation, archive restore, and firmware flashing require LinuxCNC to be stopped.

Dashboard machine controls use `POST /v1/machine/command` with named actions for abort, power, E-stop, limit override, coolant, program start/pause/resume, Manual/Auto mode, and Home All/Unhome All. Each action is checked against LinuxCNC state on the machine. Arbitrary MDI strings are intentionally not accepted by the secure API.

Axis homing uses `POST /v1/machine/home-axis` with a validated axis name and requires powered LinuxCNC.

The secure probing operations include `POST /v1/machine/probe-z` for a bounded raw Z probe, `POST /v1/machine/touch-off-z` for a bounded Z touch-off, `POST /v1/machine/tool-setter` for bounded setter positioning, and `POST /v1/machine/tool-setter/calibrate` for the technician-only setter calibration worker. These operations require powered/idle LinuxCNC and use the locally selected probe source; they do not accept arbitrary MDI text.

`POST /v1/machine/touch-off-z` is the first complete touch-plate workflow. It accepts bounded downward travel, velocity, plate thickness, and retract values; requires a locally selected probe input to trip; checks LinuxCNC's local error channel after each motion and offset step; applies only `G10 L20 P0 Z<plate_thickness>`; and then retracts. If retract fails after the offset succeeds, the returned error explicitly reports that the Z work offset was already applied. It does not select probe hardware, calibrate tool length, or execute edge-finding sequences.

Advanced XY edge-finding, corner, boss, and hole workflows remain local-only until they have explicit bounded contracts for approach/retract motion, probe-source selection, contact/timeouts, offset mutation, and partial-completion reporting. The Dashboard keeps those legacy routines disabled in secure mode while the bounded Z and tool-setter operations remain available.

Tool-setter positioning uses `POST /v1/machine/tool-setter` with bounded absolute X/Y/Z coordinates and requires powered, idle LinuxCNC. The operation is technician-only; it does not execute arbitrary MDI.

Macro file management uses the typed G-code list/read endpoints and checksum-verified uploads. Macro execution is intentionally not exposed as a generic operation; a future named execution workflow must define its allowed programs, state checks, and confirmation policy.

Dashboard polling uses `GET /v1/telemetry` when the secure agent is enrolled. It returns one bounded operator snapshot per request, including position, distance-to-go, feed/spindle overrides, interpreter status, mode, program-loaded state, and the current motion line. The Dashboard reconnects after temporary Wi-Fi loss and does not use the management link as a real-time control loop.

Spindle PID tuning uses `POST /v1/tuning/pid` and accepts only one of `p`, `i`, `d`, `ff0`, or `ff1` per request. LinuxCNC must be running and idle; the agent rejects arbitrary HAL parameter names.

Mesa firmware flashing uses `POST /v1/mesa/firmware`. It accepts only a single `.bit` or `.bin` filename, a validated board/address, bounded base64 content, and a SHA-256 checksum. LinuxCNC must be stopped; the agent runs fixed `mesaflash` write and reload operations and removes its temporary image afterward.

Backup inventory uses `GET /v1/backups/config` and `GET /v1/backups/gcode`. These endpoints return bounded file manifests with sizes and SHA-256 values and exclude internal approval/backup metadata. Binary archive transfer and restore are separate operations and are not implied by manifest success.

Archive transfer uses `GET /v1/backups/config/archive` or `GET /v1/backups/gcode/archive`. Restore uses the matching `POST /restore` endpoint with a checksum and base64 archive. Archives are size-limited, reject traversal/links/devices, require LinuxCNC to be stopped, extract into a temporary directory, and replace the approved root transactionally.

Machine-agent roles are enforced per endpoint: `monitor` can inspect state, logs, HAL pins/components/values, and tool tables; `operator` can load programs and set feed/spindle overrides; `technician` can tune spindle PID; `administrator` can deploy, restore, flash firmware, and perform recovery operations. Administrators inherit lower-risk roles.

Application updates use `POST /v1/application/update` to stage a bounded, checksum-verified archive after validating its members. `GET /v1/application/update` reports whether an archive is staged plus the last local apply result. `GET /v1/agent/preflight` reports read-only prerequisites for application updates: the installed release tree, Python executable, update directory, and agent write access. Install `cncfusion-apply-update.service` once on the machine. A local administrator then approves a staged update with `sudo systemctl start cncfusion-apply-update`; before replacement it requires `main.py`, `requirements.txt`, and the agent package entry points including `agent/status.py`, then replaces `/opt/CNCFusion` transactionally, moves the existing `.venv` without copying it, removes the consumed archive, records `applied`, `failed`, or `rolled_back` together with the replacement release identity, and restarts the agent and HAL helper. The network endpoint cannot invoke this installation step.

Agent discovery uses `GET /v1/agent/status`, which returns the agent version, supported typed capabilities, role mode, and current LinuxCNC state. CNCFusion should use this response before enabling optional controls.

Capability discovery is a UI convenience only. The agent still enforces certificate roles and machine-state policy on every operation. Recent operator capabilities include `program_control`, `mode_control`, `coolant_control`, `spindle_control`, `tool_change`, and `home_all`; a missing capability means that an older agent deployment does not support the corresponding desktop control.

Example:

```text
python3 -m agent --bind 0.0.0.0 --port 9443 \
  --ca-file /etc/cncfusion/ca.crt \
  --certificate-file /etc/cncfusion/server.crt \
  --private-key-file /etc/cncfusion/server.key \
  --config-root /home/operator/linuxcnc/configs
```

The service should be bound to the management interface, protected by a firewall, and run with a private key readable only by its service account. The control-network interface should not be used as the administration endpoint.

## systemd deployment

`cncfusion-agent.service` is a hardened service template for the LinuxCNC host. `cncfusion-hal-helper.service` is its local-only companion for HAL inspection, bounded value snapshots, and LinuxCNC status polling: it runs as the LinuxCNC owner, accepts only `show_hal_pins`, `show_hal_components`, validated `read_hal_values`, and `linuxcnc_status` requests over `/run/cncfusion/hal-helper.sock`, and has no network listener. The network-facing agent remains `cncfusion` and cannot access RTAPI or LinuxCNC NML status directly. Copy both units into `/etc/systemd/system/`, copy `agent.env.example` to `/etc/cncfusion/agent.env`, and replace the example management bind address, port, client certificate fingerprint, and helper `User=` value. The environment file must be readable only by the service administrator and agent service account.

Before enabling the service, create the `cncfusion` service account, install the application under `/opt/CNCFusion`, create `/etc/cncfusion/`, install the CA/server certificate/private key with restrictive permissions, and verify that the configured bind address belongs to the management interface. Do not use the control NIC address as the agent bind address.

After installation, use the host's normal service-management procedure to reload units, enable the agent, start it, and inspect its status/logs. Configure the firewall to allow the agent port only from authorized management clients.

For production, configure one or more authorized desktop certificates with repeated `--client-fingerprint` options. Fingerprints may be SHA-256 hex with or without colon separators. An empty allowlist is a compatibility mode for development and should not be used when multiple client certificates share the CA.

```text
  --client-fingerprint 0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef
```

Use SSH only to bootstrap the agent, enroll or rotate certificates, and perform explicitly approved recovery. Once mTLS enrollment succeeds, normal monitoring, deployment, diagnostics, and named machine operations use the HTTPS agent. Rotate a desktop certificate by adding the new fingerprint to the agent allowlist, verifying the new client connection, and only then removing the old fingerprint.

## CNCFusion client settings

The desktop application opts into the agent when these non-secret references are configured in `settings.ini`:

```ini
[AGENT]
base_url = https://machine-management.example:9443
ca_file = C:\\Users\\operator\\.cncfusion\\ca.crt
client_cert = C:\\Users\\operator\\.cncfusion\\client.crt
client_key = C:\\Users\\operator\\.cncfusion\\client.key
timeout = 5
```

The certificate and private-key files are references only; private key material and passwords must not be placed in the INI file. When `AGENT` is fully configured, application shutdown uses the agent and does not silently fall back to SSH if the agent is unavailable. Without complete `AGENT` settings, the existing compatibility path remains available during migration.

The desktop credential storage dependency is declared in `requirements.txt`. Install it before migrating legacy SSH password storage; no plaintext fallback should be introduced when the operating-system keyring is unavailable.