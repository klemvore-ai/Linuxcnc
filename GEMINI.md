# LinuxCNC Pro - Gemini Development Context

This document provides a detailed architectural and functional overview of the LinuxCNC Pro project. It serves as a foundational "memory" to ensure accurate and context-aware code assistance.

## Project Overview

LinuxCNC Pro is a sophisticated desktop application built with Python and PyQt6. It is designed as an all-in-one control center and IDE for LinuxCNC systems, particularly those using Mesa Electronics hardware. It provides a graphical user interface for machine setup, real-time control, diagnostics, and the creation of custom operator panels.

**Core Purpose:** To simplify and centralize the entire workflow of configuring, operating, and diagnosing a CNC machine by replacing manual configuration file editing and fragmented tools with a unified, visually-driven environment.

**Key Technologies:**
*   **Backend & Application Logic:** Python
*   **GUI Framework:** PyQt6
*   **Remote Communication:** Paramiko (for SSH to the LinuxCNC machine)
*   **CNC Control System:** LinuxCNC
*   **Target Hardware:** Mesa Electronics FPGA cards (e.g., 7i76E)
*   **System Monitoring:** `psutil` (optional)

## Core Architecture & Data Flow

The application is architected in a modular, tab-based format managed by the main window.

### 1. Main Application (`main.py`)
*   **Role:** The central orchestrator.
*   **Responsibilities:**
    *   Initializes the `QMainWindow` and the main `QTabWidget`.
    *   Instantiates all tab modules (`MachineDashboard`, `SettingsTab`, etc.).
    *   Manages the "Operator Mode" (`Ctrl+Shift+O`), which hides all tabs except the dashboard.
    *   Handles the graceful shutdown sequence, including an option to remotely stop the LinuxCNC service.
    *   Performs an initial "auto-sync" to pull the latest `settings.ini` from the remote machine on startup.
    *   Manages the system monitor in the status bar.

### 2. Settings Manager (`settings_manager.py`)
*   **Role:** The single source of truth for all configuration.
*   **Responsibilities:**
    *   Provides a simple API (`get_setting`, `save_setting`) for reading from and writing to the `settings.ini` file.
    *   All other modules should interact with this manager, not directly with the INI file, to ensure consistency.

### 3. Key Tabs & Their Interactions

#### `tabs/settings_tab.py` (The "Brain")
*   **Purpose:** The primary configuration wizard. This is where all machine parameters are defined.
*   **Key Function:** `generate_base_configs()` reads all the user inputs from the UI (axis limits, I/O pins, etc.) and compiles them into a complete set of LinuxCNC configuration files (`.ini` and `.hal`).
*   **Remote Interaction:** Can `deploy_configs` (push generated files), `launch_linuxcnc`, and `stop_linuxcnc` via SSH.
*   **Communication:** Emits the `settings_changed` signal when settings are saved, notifying other tabs (like the Dashboard) to update their internal state.

#### `tabs/dashboard.py` (The "Face")
*   **Purpose:** The main operator control panel for live machine interaction.
*   **Live Data:**
    *   Uses a `PositionFetcherThread` to run a persistent Python script on the remote machine.
    *   This remote script polls `linuxcnc.stat()` at ~10Hz and streams JSON-formatted data (positions, status, overrides) back to the dashboard.
    *   The dashboard receives this data via the `position_fetched` signal and updates the UI (DROs, LEDs, tool marker) in the `update_status()` method.
*   **Command Execution:**
    *   Most actions (jogging, MDI commands, homing) are sent to the remote machine as Base64-encoded Python scripts.
    *   The `_send_b64_py_cmd` function packages the script.
    *   A dedicated `_cmd_worker` thread (`QThread`) processes a queue of these commands, executing them over SSH via `paramiko` to prevent UI freezing.
*   **UI System:** Features a `QStackedWidget` to switch between a built-in default UI and a dynamically loaded custom UI from a `.linuxcncui` file.

#### `tabs/mesa_util_tab.py`
*   **Purpose:** A frontend for the `mesaflash` command-line utility.
*   **Functionality:** Allows reading hardware info (`--readhmid`), backing up firmware (`--read`), and flashing new firmware (`--write`) to the Mesa card over SSH.
*   **Integration:** The `parse_hmid_to_library` function can convert the output of a `mesaflash` scan directly into a new component in the `component_library.json` file for use in the Logic Designer.

#### `tabs/wiring_tab.py` (Logic Designer)
*   **Purpose:** A visual replacement for writing HAL `net` commands.
*   **Workflow:** Users drag components from the library (defined in `component_library.json`) onto a canvas and draw wires between I/O pins.
*   **Output:** Exports the visual wiring diagram into a valid `custom_postgui.hal` file, which is loaded by LinuxCNC after the main GUI starts. This is how custom UI elements are linked to machine logic.

#### `tabs/layout_editor_tab.py` (UI Designer)
*   **Purpose:** A drag-and-drop editor for creating custom operator panels.
*   **Workflow:** Users place and style widgets (Buttons, DROs, LEDs) on a canvas.
*   **Output:** Saves the layout as a `.linuxcncui` JSON file. This file is what the `MachineDashboard` loads to render the custom UI. It also generates a corresponding `.mcomp` file, which defines the HAL pins for the custom widgets that can then be used in the Logic Designer.

## Development Conventions & Patterns

*   **Remote Commands:** Long-running or critical commands are executed in background threads (`QThread` or `threading.Thread`) to keep the UI responsive. Short, non-blocking commands (like a button press) are often sent via a fire-and-forget `exec_command`.
*   **UI Updates:** UI updates triggered by background threads or timers are done via PyQt's signal/slot mechanism (e.g., `position_fetched.emit()`) to ensure thread safety.
*   **Error Handling:** Remote command execution includes capturing `stderr` and emitting signals (e.g., `error_signal`) to display `QMessageBox` warnings to the user. Robust `wait_idle` functions with timeouts are used in probing scripts to prevent hangs.
*   **No-Scroll Widgets:** Custom `NoScroll...Box` widgets are used to prevent accidental value changes when scrolling through the UI, a common UX issue in settings-heavy applications.
*   **State Caching:** The `update_status` method in the dashboard uses a `_last_update_state` dictionary to cache the last drawn values, preventing unnecessary and flickering UI redraws on every 100ms timer tick.

## Development Conventions

*   **GUI:** The UI is built using the PyQt6 framework. The UI is styled with a "Fusion" theme and custom stylesheets to achieve a dark, professional look.
*   **Modularity:** The application is highly modular, with each primary feature encapsulated within its own "tab" class in the `tabs/` directory. This separation of concerns is a core architectural pattern.
*   **Configuration:** All machine and application settings are managed through the `SettingsManager` class and persisted in the `settings.ini` file. This is the single source of truth for configuration.
*   **Remote Operations:** Actions on the remote LinuxCNC machine are performed over SSH using the `paramiko` library. Commands are often executed in background threads (`QThread` or `threading.Thread`) to keep the UI responsive.
*   **Operator vs. Developer Mode:** The application has a concept of an "Operator Mode" which simplifies the UI to show only the `DASHBOARD` tab. This mode can be toggled with the `Ctrl+Shift+O` shortcut.
