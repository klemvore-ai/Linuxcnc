# CNC Fusion Pro - Complete Instruction Manual

Welcome to **CNC Fusion Pro**, the ultimate integrated development environment (IDE) and control center for LinuxCNC and Mesa Electronics hardware. 

This application bridges the gap between machine configuration, logic wiring, UI design, and live operator control.

For maintainers and contributors, see the [Architecture Guide](docs/ARCHITECTURE.md) for the service boundaries, security model, lifecycle rules, and future Mesa/EtherCAT extension path.

---

## Table of Contents
1. Initial Machine Setup (The Wizard)
2. Mesa Utilities & Diagnostics
3. Logic Designer (Visual HAL)
4. UI Designer (Operator Panels)
5. Live Dashboard & Control
6. Tool Table Management
7. HAL Monitor & Oscilloscope

---

## 1. Initial Machine Setup (The Wizard)
The **⚙️ Settings Tab** is the brains of the operation. It houses the Setup Wizard which automatically generates complete `INI` and `HAL` files.

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
- **Probing:** The Offsets & Probing tabs allow you to trigger standard Plate Z-Zeroing, Edge Finding, or Tool Height Setter routines directly via MDI subroutines.

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

*Designed and Built for CNC Fusion Pro.*