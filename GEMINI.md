# CNCFusion Pro - Gemini Development Context

This document provides a comprehensive overview of the CNCFusion Pro project for AI-assisted development.

## Project Overview

CNCFusion Pro is a sophisticated desktop application built with Python and PyQt6, designed to be an all-in-one control center and IDE for LinuxCNC systems, particularly those using Mesa Electronics hardware. It provides a graphical user interface for machine setup, real-time control, diagnostics, and the creation of custom operator panels.

**Core Purpose:** To simplify and centralize the entire workflow of configuring, operating, and diagnosing a CNC machine by replacing manual configuration file editing and fragmented tools with a unified, visually-driven environment.

**Key Technologies:**
*   **Backend & Application Logic:** Python
*   **GUI Framework:** PyQt6
*   **Remote Communication:** Paramiko (for SSH to the LinuxCNC machine)
*   **CNC Control System:** LinuxCNC
*   **Target Hardware:** Mesa Electronics FPGA cards (e.g., 7i76E)

**Architectural Structure:**
The application is architected in a modular, tab-based format. Each tab represents a distinct functional area of CNC control:
*   `main.py`: The application's main entry point. It initializes the main window, manages the lifecycle of all tabs, handles system-level monitoring (memory, threads), and orchestrates the graceful shutdown of the remote LinuxCNC instance.
*   `settings_manager.py`: A crucial class that manages persistence of all configuration data in a `settings.ini` file. This includes everything from machine axis limits and homing sequences to I/O pin mappings and UI preferences.
*   `tabs/`: This directory contains the source code for each major feature, such as the `Dashboard`, `SettingsTab`, `Logic Designer` (WiringTab), and `UI Designer` (LayoutEditorTab).
*   `core/`: Contains fundamental components like the visual canvas used in the Logic and UI designers.

## Building and Running

### Dependencies
The project relies on several key Python libraries. While a `requirements.txt` is not present, the primary dependencies can be inferred from the source code:
*   `PyQt6`: For the user interface.
*   `paramiko`: For SSH communication with the remote LinuxCNC machine.
*   `psutil`: For system monitoring (optional, but recommended).

To install the core dependencies, run:
```bash
pip install PyQt6 paramiko psutil
```

### Running the Application
The application is launched by running the main script:
```bash
python main.py
```

## Development Conventions

*   **GUI:** The UI is built using the PyQt6 framework. The UI is styled with a "Fusion" theme and custom stylesheets to achieve a dark, professional look.
*   **Modularity:** The application is highly modular, with each primary feature encapsulated within its own "tab" class in the `tabs/` directory. This separation of concerns is a core architectural pattern.
*   **Configuration:** All machine and application settings are managed through the `SettingsManager` class and persisted in the `settings.ini` file. This is the single source of truth for configuration.
*   **Remote Operations:** Actions on the remote LinuxCNC machine are performed over SSH using the `paramiko` library. Commands are often executed in background threads (`QThread` or `threading.Thread`) to keep the UI responsive.
*   **Operator vs. Developer Mode:** The application has a concept of an "Operator Mode" which simplifies the UI to show only the `DASHBOARD` tab. This mode can be toggled with the `Ctrl+Shift+O` shortcut.
