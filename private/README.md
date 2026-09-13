# Machine-Admin Components

This directory contains public LinuxCNC machine-agent and administrative implementation code.
It is licensed under GPL-3.0-or-later with the rest of LinuxCNC; it does not contain
credentials or machine-specific runtime configuration.

This scope includes:

- machine-side agent services
- mTLS and certificate configuration
- LinuxCNC owner helper processes
- systemd deployment and lifecycle logic
- firmware and recovery tooling
- privileged config activation or rollback workflows
- deployment-specific templates and example settings

Do not commit generated certificates, private keys, enrolled client fingerprints,
machine addresses, or working environment files. See [Secure Agent Setup](../docs/SECURE_AGENT_SETUP.md).
