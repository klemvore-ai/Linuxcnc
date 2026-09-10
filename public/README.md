# Public CNCFusion Scope

This directory represents the public, user-facing CNCFusion code path.

The public project should stay focused on:

- LinuxCNC GUI and dashboard functionality
- G-code loading and management
- tool-table handling
- basic machine state and telemetry
- safe, bounded machine operations
- configuration assistance and local machine setup guidance

The public scope intentionally excludes:

- service installation
- certificate management
- systemd unit deployment
- privileged recovery logic
- firmware flashing
- remote administrative control planes
- machine-side lifecycle management

LinuxCNC remains the runtime owner of the machine configuration and controller state. CNCFusion is the interface and configuration layer around it.
