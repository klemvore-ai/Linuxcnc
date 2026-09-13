# LinuxCNC Pro - Complete Instruction Manual

Welcome to **LinuxCNC Pro**, the ultimate integrated development environment (IDE) and control center for LinuxCNC and Mesa Electronics hardware. 

This application bridges the gap between machine configuration, logic wiring, UI design, and live operator control.

For maintainers and contributors, see the [Architecture Guide](docs/ARCHITECTURE.md) for the service boundaries, security model, lifecycle rules, and future Mesa/EtherCAT extension path.

For first-time LinuxCNC machine setup, follow the [Beginner LinuxCNC Setup Guide](docs/LINUXCNC_BEGINNER_SETUP.md). It explains where commands run, how to verify each step, and how to avoid mixing the management network with the Mesa/EtherCAT control network.

## License, Safety, and Local Configuration

LinuxCNC, including the secure machine agent, installer, deployment tooling, and documentation, is released under the GNU General Public License v3.0 or later. See [LICENSE](LICENSE) for the project license.

**LinuxCNC is not safety-rated machine-control software.** LinuxCNC, the physical E-stop, limits, interlocks, and other independent safety systems remain responsible for safe machine operation. Review every generated G-code program and O-code macro, test with the machine clear at low speed, and stop immediately when any motion is unexpected. Never rely on LinuxCNC as the sole safety control.

Do not commit a working `settings.ini`, certificates, private keys, fingerprints, passwords, machine IP addresses, HAL pin maps, or calibration values. Start from [settings.example.ini](settings.example.ini) and create your own local `settings.ini`; it is intentionally ignored by Git.

The secure agent is public source code. Each installation must generate its own CA, server certificate, and client certificate and enroll the client fingerprint before accepting network requests. Follow [Secure Agent Setup](docs/SECURE_AGENT_SETUP.md) before enabling agent control on a machine.

---

## Table of Contents
1. [Quick Start Installation](#quick-start-installation)
2. [Initial Machine Setup (The Wizard)](#1-initial-machine-setup-the-wizard)
3. [Secure Agent Architecture](#2-secure-agent-architecture)
4. [Mesa Utilities & Diagnostics](#3-mesa-utilities--diagnostics)
5. [Logic Designer (Visual HAL)](#4-logic-designer-visual-hal)
6. [UI Designer (Operator Panels)](#5-ui-designer-operator-panels)
7. [Live Dashboard & Control](#6-live-dashboard--control)
8. [Tool Table Management](#7-tool-table-management)
9. [HAL Monitor & Oscilloscope](#8-hal-monitor--oscilloscope)
10. [Macro & Subroutine System](#9-macro--subroutine-system)
11. [Application Updates](#10-application-updates)
12. [Path Configuration](#11-path-configuration)

---

## Quick Start Installation

### Machine-Side (Debian/LinuxCNC)

Run the complete installer as root:

```bash
sudo python3 private/installer/install.py --source /path/to/LinuxCNC
```

The installer sets up:
- **Users & Groups**: `linuxcnc` service account with proper permissions
- **Directories**: `/opt/LinuxCNC`, `/etc/linuxcnc`, `/var/lib/linuxcnc/updates`
- **TLS Certificates**: CA, server, and client certificates with auto-generated fingerprint
- **Systemd Services**: `linuxcnc-agent`, `linuxcnc-hal-helper`, `linuxcnc-apply-update`
- **Configuration**: Environment files with configurable paths
- **LinuxCNC Integration**: Updates INI with correct `SUBROUTINE_PATH`

**Output includes the client certificate fingerprint** — save this for the dev machine.

### Dev Workstation (Windows)

1. Copy certificates from the machine:
   ```powershell
   scp user@machine:/etc/linuxcnc/ca.crt $HOME\.linuxcnc\
   scp user@machine:/etc/linuxcnc/client.crt $HOME\.linuxcnc\
   scp user@machine:/etc/linuxcnc/client.key $HOME\.linuxcnc\
   ```

2. Configure `settings.ini`:
   ```ini
   [AGENT]
   base_url = https://<MACHINE_MANAGEMENT_IP>:9443
   ca_file = C:\Users\<user>\.linuxcnc\ca.crt
   client_cert = C:\Users\<user>\.linuxcnc\client.crt
   client_key = C:\Users\<user>\.linuxcnc\client.key
   ```

3. Launch LinuxCNC — the title bar shows **"DEVELOPER WORKSTATION"**

### Custom Paths

For non-standard installations, create `/etc/linuxcnc/paths.conf`:

```ini
LINUXCNC_CONFIG_ROOT=/home/operator/linuxcnc/configs
LINUXCNC_GCODE_ROOT=/home/operator/linuxcnc/nc_files
LINUXCNC_AGENT_ROOT=/opt/LinuxCNC
LINUXCNC_LINUXCNC_USER=operator
```

Then run: `sudo python3 installer/setup_paths.py`

---

## 1. Initial Machine Setup (The Wizard)
The **⚙️ Settings Tab** is the brains of the operation. It houses the Setup Wizard which automatically generates complete `INI` and `HAL` files.

### Mode Indicators
The window title shows which mode you're running:
- **"LinuxCNC Pro - DEVELOPER WORKSTATION"** — Windows dev machine
- **"LinuxCNC Pro - MACHINE CONTROL"** — LinuxCNC machine

### Recommended Workflow:
1. **Establish Comms:** Go to the *Mesa Utilities* tab, enter your Linux Machine IP, Username, and Password, and click **Save Credentials**. Click **Test SSH Connection** to ensure it works.
2. **Configure Limits:** In the Settings tab, set your Machine Name, Kinematics (e.g., 3-Axis vs Gantry), Max Velocities, and X/Y/Z physical limits.
3. **Map Hardware I/O:** In the *Hardware I/O Pin Mapping* section, assign your physical Mesa pins to the machine logic (e.g., Home Switches, Limits, E-Stop). 
   - *Tip: Use the "Invert" checkbox for normally-closed safety switches.*
4. **Generate:** Click **✨ Generate Base INI & HAL Files**. The app will compile your settings into standard LinuxCNC config files and generate a visual logic `blueprint.hal`.
5. **Deploy & Launch:** Click "Yes" when prompted to deploy the files to the remote machine via SSH. Then click **🚀 Launch LinuxCNC Remotely** to spin up the actual machine!

---

## 2. Secure Agent Architecture

LinuxCNC uses a **mutual TLS (mTLS) secure agent** for machine control instead of SSH. This provides:
- **Encrypted communication** between dev workstation and machine
- **Certificate-based authentication** (no passwords over the network)
- **Role-based access control** (monitor, operator, technician, administrator)
- **Audit logging** of all machine operations

### Architecture

```
Dev Workstation (Windows)          CNC Machine (Debian/LinuxCNC)
┌─────────────────────┐           ┌─────────────────────────────┐
│  LinuxCNC GUI      │◄─────────►│  linuxcnc-agent (HTTPS)    │
│  - Dashboard        │  mTLS     │  - Port 9443                │
│  - Settings         │  9443     │  - Policy validation        │
│  - Macro Editor     │           │  - Role enforcement         │
└─────────────────────┘           └─────────────┬───────────────┘
                                                │
                                                │ Unix Socket
                                                ▼
                                      ┌─────────────────────────┐
                                      │  linuxcnc-hal-helper   │
                                      │  - Runs as operator     │
                                      │  - LinuxCNC API access  │
                                      │  - HAL pin control      │
                                      └─────────────────────────┘
```

### Security Features
- **No arbitrary shell commands** — operations are typed and validated, with the public repo intentionally excluding machine secrets
- **No MDI injection** — G-code execution uses allowlisted O-code subroutines only
- **State validation** — the machine must be in the correct safe state before motion commands run
- **Certificate rotation** — client certs can be rotated without exposing machine credentials
- **Management-network isolation** — the agent binds only to the approved management interface, not the Mesa/EtherCAT control network

---

### Recommended Workflow:
1. **Establish Comms:** Go to the *Mesa Utilities* tab, enter your Linux Machine IP, Username, and Password, and click **Save Credentials**. Click **Test SSH Connection** to ensure it works.
2. **Configure Limits:** In the Settings tab, set your Machine Name, Kinematics (e.g., 3-Axis vs Gantry), Max Velocities, and X/Y/Z physical limits.
3. **Map Hardware I/O:** In the *Hardware I/O Pin Mapping* section, assign your physical Mesa pins to the machine logic (e.g., Home Switches, Limits, E-Stop). 
   - *Tip: Use the "Invert" checkbox for normally-closed safety switches.*
4. **Generate:** Click **✨ Generate Base INI & HAL Files**. The app will compile your settings into standard LinuxCNC config files and generate a visual logic `blueprint.hal`.
5. **Deploy & Launch:** Click "Yes" when prompted to deploy the files to the remote machine via SSH. Then click **🚀 Launch LinuxCNC Remotely** to spin up the actual machine!

---

## 2. Mesa Utilities & Diagnostics
The **🛠️ MESA UTILITIES** tab provides deep hardware-level access without ever opening a terminal.

- **Read HMID (Mesaflash):** Queries the actual FPGA on your Mesa card and auto-discovers all available hardware pins.
- **Convert Scan to Library:** Converts the discovered pins into modular blocks for the Logic Designer.
- **Flash Firmware:** Safely upload `.bit` files directly to the Mesa card.
- **Backups:** One-click backup of your entire remote `~/linuxcnc/configs` folder, or your `nc_files` G-Code directory.
- **Upload G-Code:** Push `.ngc` files directly from your computer to the remote machine.

---

## 3. Logic Designer (Visual HAL)
The **🔌 LOGIC DESIGNER** tab replaces typing raw `net` commands with a beautiful, magnetic drag-and-drop wire interface.

### Controls:
- **Add Components:** Select a component from the sidebar dropdown and click **Add Component**.
- **Wire Pins:** Left-click an Output pin, then click an Input pin to snap a wire between them.
- **Change Wire Color:** Double-click any drawn wire to pick a custom color.
- **Delete:** Right-click a wire to delete it, or select a component and press `Delete` on your keyboard.
- **Pan/Zoom:** Click and drag with the Middle Mouse Button to pan the canvas. Use the scroll wheel to zoom.
- **Auto-Group Zones:** Click this in the sidebar to automatically draw bounding boxes around connected clusters of components.

### Deploying Custom UI:
When you build a custom dashboard (see UI Designer), it generates virtual pins. Drop those virtual pins onto this canvas, wire them to the Mesa hardware pins, and click **📤 Export to .HAL**. It will automatically save as `custom_postgui.hal` which is loaded live into LinuxCNC!

---

## 4. UI Designer (Operator Panels)
The **🎨 UI DESIGNER** tab allows you (or a dedicated graphic artist) to build completely custom, hardware-linked interfaces.

### Controls:
- **Place Components:** Select a Button, LED, DRO, or G-Code Editor from the toolbox and click **Place Component**.
- **Edit Properties:** Double-click a component to open the Property Editor. You can change colors, assign background images, round the borders, and add drop shadows.
- **Resize:** Drag the bottom-right corner of any block.
- **Duplicate / Lock:** Right-click a block to access advanced options like Duplication, Z-Index sorting (Front/Back), and Locking.

### Sharing Layouts:
Click **📤 Export for Sharing**. This packages your layout, your custom images, and the generated Logic Block definition into a `.zip` folder so an artist can easily send it to the machine integrator!

---

## 5. Live Dashboard & Control
The **🖥️ DASHBOARD** tab is the primary operator interface. It streams live positional data from the machine at 10Hz.

### Features:
- **Live DRO:** Shows X, Y, Z coordinates alongside real-time Feedrate and Spindle RPM.
- **Keyboard Jogging:** Use the Arrow Keys (X/Y) and PageUp/PageDown (Z) to physically jog the machine. Hold `Shift` for Turbo speed!
- **G-Code Editor:** Load a file directly from the machine. 
  - *Magic Shortcut:* **Double-Click** any line of code to instantly prompt the machine to "Run From Line"!
  - The editor automatically scrolls to highlight the active line of code as the machine runs.
- **3D Toolpath Viewer:** Instantly plots the loaded G-Code. 
  - Left-Click and Drag to pan. Scroll to zoom.
  - A red **Live Tool Marker** actively traces the path as the physical machine moves.
  - **Double-Click** any line in the 3D drawing to instantly jump the G-Code Editor to that specific coordinate!
  - Click **📸 Save Screenshot** to export the render to a `.png`.
- **Probing:** The secure Offsets & Probing controls support bounded raw Z probing, touch-plate Z offset, tool-setter positioning, and technician-only setter calibration. The directional XY probing family (`O114`–`O129`) is also supported for machine-specific corner and center routines, with each macro following the configured axis convention and safe retract logic.

---

## 6. Tool Table Management
The **🧰 TOOL TABLE** tab allows you to manage offsets without manually editing text files.

- **Load / Save:** Pulls the active `tool.tbl` from LinuxCNC directly into a clean, editable grid.
- **Highlight Active:** Clicks the machine via SSH to find out what tool is currently in the spindle and highlights it in the table.

---

## 7. HAL Monitor & Oscilloscope
When configuring PID loops or diagnosing faulty limit switches, these tabs are invaluable.

### 🔎 HAL Monitor:
- Click **Start Live Monitor** to stream every single pin state from the machine.
- Click any green/red LED in the "Value / State" column to physically force a `halcmd setp` command to change the pin's state live!
- Double-click any numeric value to type in a new number (great for manually overriding PID values on the fly).

### 📈 HAL Oscilloscope:
- Provides a high-speed, auto-scaling visual graph of pin values over time.
- **How to use:** Select a pin from the dropdown (e.g., `pid.s.feedback`), choose a trace color, and click "Add Channel". Click **▶ Run Scope** to watch the data plot in real-time.
- Essential for tuning Spindle RPM overshoots or StepGen following errors!

---

## 9. Macro & Subroutine System

LinuxCNC includes a complete **O-code subroutine system** for automated machine operations. All macros run through the secure agent — no arbitrary MDI allowed.

### Available Macros

| O-Code | Function | Description |
|--------|----------|-------------|
| **O100** | Spindle Warmup | Steps spindle 3000→20000 RPM, 10s per step |
| **O101** | Tool Measure | Probes tool setter, writes offset to tool table |
| **O102** | Park Machine | Retracts Z, moves to park position |
| **O103** | Touch Plate Z | Probes Z down to plate, sets work offset |
| **O104** | Outside Corner | Probes X- and Y-, sets corner as origin |
| **O105** | Inside Corner | Probes X+ and Y+, sets corner as origin |
| **O106** | Boss Center | Probes 4 sides of boss, sets center as origin |
| **O107** | Hole Center | Probes 4 sides of hole, sets center as origin |
| **O108** | Probe X Edge | Single-axis X probe, sets X origin |
| **O109** | Probe Y Edge | Single-axis Y probe, sets Y origin |
| **O110** | Auto Tool Change | Full cycle: probe → store offset → return → resume |
| **O111** | Calibrate Setter | Establish tool setter surface with known reference |
| **O112** | Calibrate Spindle Nose | Probe bare spindle nose as absolute reference |
| **O113** | Quick Tool Change | Simplified probe for in-program use after `T# M6` |
| **O114** | Outside Back-Left Corner | Safe outside-corner routine using machine-specific sign conventions |
| **O115** | Outside Back Edge | Probe along the rear edge and zero the machine corner |
| **O116** | Outside Front-Left Corner | Outside-left probing sequence for a front-left reference |
| **O117** | Outside Front Edge | Front-edge probing routine |
| **O118** | Outside Front-Right Corner | Outside front-right corner routine |
| **O119** | Outside Right Edge | Right-edge probing routine |
| **O120** | Outside Back-Right Corner | Back-right outside corner routine |
| **O121** | Inside Back-Left Corner | Inside back-left corner routine |
| **O122** | Inside Back Edge | Inside rear-edge probing routine |
| **O123** | Inside Front-Left Corner | Front-left inside-corner sequence |
| **O124** | Inside Front Edge | Inside front-edge routine |
| **O125** | Inside Front-Right Corner | Front-right inside-corner sequence |
| **O126** | Inside Right Edge | Right-side inside probing routine |
| **O127** | Inside Back-Right Corner | Back-right inside-corner probing |
| **O128** | Boss Center | Four-side boss center estimate, then zero the work offset |
| **O129** | Hole Center | Four-side hole center estimate, then zero the work offset |

### Using Macros

**Dashboard Picker:**
- The **Run Subroutine** dropdown shows only macros installed on the machine
- Select a macro, click **Run Subroutine**, confirm the operation
- Macro runs in MDI mode with full state validation

**In G-Code Programs:**
```gcode
(Program with automatic tool change)
T1 M6                    (Load tool 1)
O113 call [1]            (Probe tool 1, store offset)
G43 H1                   (Activate offset)
M3 S12000                (Start spindle)
... cutting moves ...
T2 M6                    (Load tool 2)
O113 call [2]            (Probe tool 2, store offset)
G43 H2                   (Activate new offset)
```

### Creating Custom Macros

Use the **Macros** tab:
1. Click **➕ New Macro**
2. Select from allowlisted O-codes (O100-O113)
3. Edit the template with your logic
4. Click **💾 Save to LinuxCNC** — uploads securely via agent

**Requirements:**
- File must be named `O###.ngc` matching the O-code number
- Must contain `O### sub` and `O### endsub` markers
- Content is validated before upload
- For machine-specific probing routines, the axis sign convention must be checked against the real machine orientation before use

### Macro Configuration

Macros read settings from the LinuxCNC INI:

```ini
[TOOL_SETTER]
X = 1208.0900          ; Tool setter X position
Y = 26.2000            ; Tool setter Y position
VEL = 50.00            ; Probe velocity (mm/min)
REF_Z = -85.5250       ; Calibrated setter reference Z
MAX_PROBE_DEPTH = 50.0 ; Max probe travel

[PROBE]
PLATE_THICKNESS = 5.0  ; Touch plate thickness
VEL = 50.00            ; Probe velocity
SEARCH_DIST = 10.0     ; Max probe search distance
XY_CLEARANCE = 5.0     ; XY clearance after probe
Z_DROP = 5.0           ; Z drop before probing
DIAMETER = 6.0         ; Probe tip diameter
Z_SAFE = 0.0           ; Safe retract height

[LOCATIONS]
PARK_X = 0.0           ; Park position X
PARK_Y = 0.0           ; Park position Y
PARK_Z = 0.0           ; Park position Z
```

**Sync Settings to Machine:**
- Set values in **Settings → Probing & Locations**
- Click **🔄 Sync Probing to Machine INI**
- Approve and activate the configuration on the machine
- Treat the `VEL` and travel settings as a machine-specific safety contract; verify them at low speed before production probing

---

## 10. Application Updates

LinuxCNC supports **secure staged updates** with operator approval.

### Update Flow

```
Dev Workstation                    CNC Machine
---------------                    -----------
Click "Deploy" ──────────────────► Update staged to
                                   /var/lib/linuxcnc/updates/
                                          │
                                          ▼
                              ┌─────────────────────┐
                              │ "⚡ Apply Staged    │
                              │  Update" button     │
                              │  appears (green)    │
                              └─────────────────────┘
                                          │
                              Operator clicks button
                                          │
                              LinuxCNC stopped? ──No──► Warn
                                          │ Yes
                                          ▼
                              Confirm dialog
                                          │
                                          ▼
                              sudo systemctl start
                                linuxcnc-apply-update
                                          │
                                          ▼
                              Services restarted
                              Update complete!
```

### Safety Features
- **Two-stage process**: Dev stages, operator approves on machine
- **LinuxCNC check**: Warns if LinuxCNC is running (optional override)
- **Transactional**: Old version preserved until new version verified
- **Automatic rollback**: If startup fails, previous version restored
- **Service restart**: Agent and helper automatically restarted

### Update Status

Click **🔎 View Update Status** to see:
- Whether an update is staged
- Staged file size and SHA-256
- Last apply result (success/failed/rolled back)
- Applied vs live agent version comparison

---

## 11. Path Configuration

LinuxCNC supports **configurable paths** for flexible installation.

### Configuration Files

| File | Purpose |
|------|---------|
| `/etc/linuxcnc/paths.conf` | Master path configuration (installer) |
| `/etc/linuxcnc/agent.env` | Agent environment variables |
| `core/paths.py` | Runtime path resolution |

### Configurable Paths

| Setting | Default | Description |
|---------|---------|-------------|
| `LINUXCNC_CONFIG_ROOT` | `/home/operator/linuxcnc/configs` | LinuxCNC config directory |
| `LINUXCNC_GCODE_ROOT` | `/home/operator/linuxcnc/nc_files` | G-code/O-code directory |
| `LINUXCNC_AGENT_ROOT` | `/opt/LinuxCNC` | Agent installation directory |
| `LINUXCNC_LINUXCNC_USER` | `operator` | LinuxCNC user account |
| `LINUXCNC_AGENT_USER` | `linuxcnc` | Agent service account |

### Changing Paths

**Option 1: Re-run installer with config**
```bash
sudo python3 installer/install.py --config /path/to/paths.conf
```

**Option 2: Update paths only**
```bash
sudo python3 installer/setup_paths.py
sudo systemctl daemon-reload
sudo systemctl restart linuxcnc-hal-helper
sudo systemctl restart linuxcnc-agent
```

### LinuxCNC INI Integration

The installer automatically updates:
- `PROGRAM_PREFIX` → Points to G-code directory
- `SUBROUTINE_PATH` → Points to O-code directory (same as G-code)

This ensures O-code subroutines are found regardless of where LinuxCNC is launched from.

---

*Designed and Built for LinuxCNC Pro.*