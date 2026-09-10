# CNCFusion Scope Boundary

## Goal

Keep CNCFusion honest: it is a LinuxCNC frontend and configuration tool, not a full machine operating system or remote-control platform.

## Boundary

### LinuxCNC owns the machine runtime

LinuxCNC owns:

- the real machine INI and HAL configuration
- controller state and motion behavior
- machine safety and limits
- the actual runtime control loop

### CNCFusion owns the GUI and workflow layer

CNCFusion may own:

- the dashboard UI
- G-code management
- tool-table handling
- operator controls and status views
- config-generation assistance for LinuxCNC
- safe, bounded commands to the machine

### Private/admin functionality is separate

The private layer may own:

- service installation and lifecycle jobs
- certificate and trust configuration
- recovery and rollback scripting
- firmware flashing
- privileged configuration activation
- machine-side agent services and helpers

These features should not be treated as normal public CNCFusion functionality and should remain out of the default public release path.
