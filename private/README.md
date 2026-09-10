# Private Machine-Admin Scope

This directory represents the private, machine-specific implementation layer.

This scope is reserved for:

- machine-side agent services
- mTLS and certificate configuration
- LinuxCNC owner helper processes
- systemd deployment and lifecycle logic
- firmware and recovery tooling
- privileged config activation or rollback workflows
- deployment-specific settings that should not ship in the public project

This layer is intentionally separate from the public CNCFusion GUI and should not be treated as the default runtime path for a generic CNC app.
