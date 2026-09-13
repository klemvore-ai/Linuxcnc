# Secure Agent Setup

The LinuxCNC secure agent is public source code. It uses mutual TLS (mTLS), so every
machine installation must create and manage its own certificate authority, server
identity, workstation client identity, and allowed client fingerprint.

## Safety First

The agent can request bounded machine operations, but it is not a safety system.
Before enabling it, verify the physical E-stop, limits, probe wiring, homing, and
LinuxCNC configuration locally. Review every O-code macro and test probing with the
machine clear, at low velocity, and with an operator ready to use the physical E-stop.
Stop and investigate any unexpected motion.

## Create Local Configuration

1. Copy [settings.example.ini](../settings.example.ini) to `settings.ini` on the workstation.
2. Replace all placeholders with the management-network address and paths for that installation.
3. Keep `settings.ini` local. It is ignored by Git because it can contain machine addresses, calibration data, and credential paths.

## Install and Enroll

On the LinuxCNC host, use the installer to create the `linuxcnc` service account,
local runtime directories, certificate authority, server certificate, client
certificate, and agent service configuration:

```bash
sudo python3 private/installer/install.py --source /path/to/LinuxCNC
```

The installer prints the SHA-256 fingerprint of the generated client certificate and
writes it to the local agent environment file. Copy only `ca.crt`, `client.crt`, and
`client.key` to the trusted workstation over a secure local process. Do not copy the
CA private key or server private key off the LinuxCNC host.

Set the local `[AGENT]` values in `settings.ini` to the workstation copies of those
three files and the HTTPS management address. The agent must bind only to a
management network, never the Mesa or EtherCAT control network.

## What Not To Publish

Never commit or share:

- `settings.ini`, `agent.env`, or `paths.conf` created for a real machine
- CA, server, or client private keys and certificate requests
- production certificates, enrolled client fingerprints, passwords, or tokens
- real management/control-network addresses, HAL pin maps, travel limits, and calibration data

Use [agent.env.example](../agent/agent.env.example) and
[settings.example.ini](../settings.example.ini) as templates instead.