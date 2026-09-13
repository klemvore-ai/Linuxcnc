#!/usr/bin/env python3
"""LinuxCNC complete installer for LinuxCNC machines.

This script sets up:
- Directories and permissions
- Users and groups
- TLS certificates (CA, server, client)
- Systemd services
- Configuration files
- LinuxCNC INI integration

Run as root: sudo python3 installer/install.py
"""

import argparse
import hashlib
import os
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
from pathlib import Path


class LinuxCNCInstaller:
    """Complete LinuxCNC installation for LinuxCNC machines."""

    def __init__(self, config_path=None, dry_run=False):
        self.dry_run = dry_run
        self.paths = self._load_paths(config_path)
        self.hostname = socket.gethostname()
        self.machine_ip = self._get_machine_ip()

    def _load_paths(self, config_path):
        """Load paths from config file or use defaults."""
        default_paths = {
            "LINUXCNC_CONFIG_ROOT": "/home/operator/linuxcnc/configs",
            "LINUXCNC_GCODE_ROOT": "/home/operator/linuxcnc/nc_files",
            "LINUXCNC_AGENT_ROOT": "/opt/LinuxCNC",
            "LINUXCNC_STAGING_DIR": "/var/lib/linuxcnc/updates",
            "LINUXCNC_CERTS_DIR": "/etc/linuxcnc",
            "LINUXCNC_LINUXCNC_USER": "operator",
            "LINUXCNC_AGENT_USER": "linuxcnc",
            "LINUXCNC_AGENT_GROUP": "linuxcnc",
            "LINUXCNC_AGENT_BIND": "0.0.0.0",
            "LINUXCNC_AGENT_PORT": "9443",
        }

        if config_path and os.path.exists(config_path):
            with open(config_path) as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        key, value = line.split("=", 1)
                        default_paths[key.strip()] = value.strip()

        return default_paths

    def _get_machine_ip(self):
        """Get the machine's primary IP address."""
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except Exception:
            return "127.0.0.1"

    def _run(self, cmd, check=True, shell=False):
        """Run a command, respecting dry-run mode."""
        if self.dry_run:
            print(f"[DRY RUN] {' '.join(cmd) if isinstance(cmd, list) else cmd}")
            return True
        try:
            if isinstance(cmd, str):
                result = subprocess.run(cmd, shell=True, capture_output=True, text=True, check=check)
            else:
                result = subprocess.run(cmd, capture_output=True, text=True, check=check)
            return result.returncode == 0
        except subprocess.CalledProcessError as e:
            print(f"Error running {cmd}: {e.stderr}")
            return False

    def _write_file(self, path, content, mode=0o644, owner=None, group=None):
        """Write a file with permissions."""
        if self.dry_run:
            print(f"[DRY RUN] Write {path} ({len(content)} bytes)")
            return

        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(content)
        os.chmod(path, mode)
        if owner or group:
            shutil.chown(path, owner, group)
        print(f"Created: {path}")

    def _copy_file(self, src, dst, mode=0o644, owner=None, group=None):
        """Copy a file with permissions."""
        if self.dry_run:
            print(f"[DRY RUN] Copy {src} -> {dst}")
            return

        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(src, dst)
        os.chmod(dst, mode)
        if owner or group:
            shutil.chown(dst, owner, group)
        print(f"Copied: {dst}")

    # === Installation Steps ===

    def create_users(self):
        """Create required users and groups."""
        print("\n=== Creating Users and Groups ===")

        agent_user = self.paths["LINUXCNC_AGENT_USER"]
        agent_group = self.paths["LINUXCNC_AGENT_GROUP"]
        linuxcnc_user = self.paths["LINUXCNC_LINUXCNC_USER"]

        # Create agent group
        self._run(["groupadd", "-f", agent_group])

        # Create agent user (system user, no login)
        self._run([
            "useradd", "--system", "--no-create-home",
            "--shell", "/usr/sbin/nologin",
            "--gid", agent_group,
            agent_user
        ], check=False)  # May fail if user exists

        # Add agent user to linuxcnc group (for helper socket access)
        self._run(["usermod", "-aG", linuxcnc_user, agent_user])

        # Add linuxcnc user to agent group (for socket access)
        self._run(["usermod", "-aG", agent_group, linuxcnc_user])

        print(f"Created: {agent_user} user, {agent_group} group")

    def create_directories(self):
        """Create required directories with permissions."""
        print("\n=== Creating Directories ===")

        dirs = [
            (self.paths["LINUXCNC_AGENT_ROOT"], 0o755, "root", "root"),
            (self.paths["LINUXCNC_CERTS_DIR"], 0o700, "root", "root"),
            (self.paths["LINUXCNC_STAGING_DIR"], 0o755, self.paths["LINUXCNC_AGENT_USER"], self.paths["LINUXCNC_AGENT_GROUP"]),
            (os.path.join(self.paths["LINUXCNC_STAGING_DIR"], "tmp"), 0o755, self.paths["LINUXCNC_AGENT_USER"], self.paths["LINUXCNC_AGENT_GROUP"]),
            ("/run/linuxcnc", 0o755, self.paths["LINUXCNC_LINUXCNC_USER"], self.paths["LINUXCNC_AGENT_GROUP"]),
        ]

        for path, mode, owner, group in dirs:
            if self.dry_run:
                print(f"[DRY RUN] mkdir -p {path} (mode={oct(mode)}, owner={owner}:{group})")
            else:
                os.makedirs(path, exist_ok=True)
                os.chmod(path, mode)
                shutil.chown(path, owner, group)
                print(f"Created: {path}")

    def generate_certificates(self):
        """Generate CA and server/client certificates."""
        print("\n=== Generating Certificates ===")

        certs_dir = self.paths["LINUXCNC_CERTS_DIR"]
        ca_key = os.path.join(certs_dir, "ca.key")
        ca_crt = os.path.join(certs_dir, "ca.crt")
        server_key = os.path.join(certs_dir, "server.key")
        server_crt = os.path.join(certs_dir, "server.crt")
        client_key = os.path.join(certs_dir, "client.key")
        client_crt = os.path.join(certs_dir, "client.crt")

        # Generate CA
        print("Generating CA certificate...")
        self._run([
            "openssl", "req", "-x509", "-newkey", "rsa:4096",
            "-keyout", ca_key, "-out", ca_crt,
            "-days", "3650", "-nodes",
            "-subj", f"/CN=LinuxCNC-CA-{self.hostname}"
        ])

        # Generate server certificate
        print("Generating server certificate...")
        server_csr = os.path.join(certs_dir, "server.csr")
        self._run([
            "openssl", "req", "-newkey", "rsa:2048",
            "-keyout", server_key, "-out", server_csr,
            "-nodes", "-subj", f"/CN={self.hostname}"
        ])
        self._run([
            "openssl", "x509", "-req", "-in", server_csr,
            "-CA", ca_crt, "-CAkey", ca_key, "-CAcreateserial",
            "-out", server_crt, "-days", "3650"
        ])

        # Generate client certificate
        print("Generating client certificate...")
        client_csr = os.path.join(certs_dir, "client.csr")
        self._run([
            "openssl", "req", "-newkey", "rsa:2048",
            "-keyout", client_key, "-out", client_csr,
            "-nodes", "-subj", f"/CN=linuxcnc-client-{self.hostname}"
        ])
        self._run([
            "openssl", "x509", "-req", "-in", client_csr,
            "-CA", ca_crt, "-CAkey", ca_key, "-CAcreateserial",
            "-out", client_crt, "-days", "3650"
        ])

        # Set permissions (skip in dry-run since files don't exist)
        if not self.dry_run:
            for f in [ca_key, server_key, client_key]:
                os.chmod(f, 0o600)
                shutil.chown(f, "root", "root")

            for f in [ca_crt, server_crt, client_crt]:
                os.chmod(f, 0o644)
                shutil.chown(f, "root", "root")

            # Cleanup CSRs
            for f in [server_csr, client_csr]:
                if os.path.exists(f):
                    os.unlink(f)

            # Calculate client fingerprint for config
            with open(client_crt, "rb") as f:
                cert_der = ssl.PEM_cert_to_DER_cert(f.read().decode())
                fingerprint = hashlib.sha256(cert_der).hexdigest()
        else:
            # Dry-run: use a placeholder fingerprint
            fingerprint = "dry-run-fingerprint-would-go-here"

        print(f"Client certificate fingerprint: {fingerprint}")
        print(f"Save this fingerprint for the dev machine settings!")

        return fingerprint

    def install_agent(self, source_dir):
        """Install the agent application."""
        print("\n=== Installing Agent Application ===")

        agent_root = self.paths["LINUXCNC_AGENT_ROOT"]

        # Copy agent files
        agent_files = [
            "agent/__init__.py",
            "agent/__main__.py",
            "agent/server.py",
            "agent/policy.py",
            "agent/linuxcnc_runtime.py",
            "agent/hal_helper.py",
            "agent/status.py",
            "agent/authorization.py",
            "agent/state_policy.py",
            "agent/bundles.py",
            "agent/backups.py",
            "agent/archive_transfer.py",
        ]

        for rel_path in agent_files:
            src = os.path.join(source_dir, rel_path)
            dst = os.path.join(agent_root, rel_path)
            if os.path.exists(src):
                self._copy_file(src, dst, mode=0o644, owner="root", group="root")

        # Create __init__.py for agent package
        init_path = os.path.join(agent_root, "agent", "__init__.py")
        if not os.path.exists(init_path):
            self._write_file(init_path, '"""LinuxCNC machine agent package."""\n', mode=0o644, owner="root", group="root")

        print(f"Installed agent to {agent_root}")

    def install_services(self, source_dir):
        """Install systemd service files."""
        print("\n=== Installing Systemd Services ===")

        # Agent service
        agent_service = f"""[Unit]
Description=LinuxCNC Machine Agent
After=network-online.target
Wants=network-online.target
Requires=linuxcnc-hal-helper.service
After=linuxcnc-hal-helper.service

[Service]
Type=simple
User={self.paths["LINUXCNC_AGENT_USER"]}
Group={self.paths["LINUXCNC_AGENT_GROUP"]}
WorkingDirectory={self.paths["LINUXCNC_AGENT_ROOT"]}
EnvironmentFile={self.paths["LINUXCNC_CERTS_DIR"]}/agent.env
Environment=TMPDIR={self.paths["LINUXCNC_STAGING_DIR"]}/tmp
ExecStart={self.paths["LINUXCNC_AGENT_ROOT"]}/.venv/bin/python -m agent \\
    --bind ${{LINUXCNC_AGENT_BIND}} \\
    --port ${{LINUXCNC_AGENT_PORT}} \\
    --ca-file {self.paths["LINUXCNC_CERTS_DIR"]}/ca.crt \\
    --certificate-file {self.paths["LINUXCNC_CERTS_DIR"]}/server.crt \\
    --private-key-file {self.paths["LINUXCNC_CERTS_DIR"]}/server.key \\
    --config-root {self.paths["LINUXCNC_CONFIG_ROOT"]} \\
    --gcode-root {self.paths["LINUXCNC_GCODE_ROOT"]} \\
    --hal-helper-socket /run/linuxcnc/hal-helper.sock \\
    --client-fingerprint ${{LINUXCNC_CLIENT_FINGERPRINT}}
Restart=on-failure
RestartSec=2
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=read-only
ReadWritePaths={self.paths["LINUXCNC_STAGING_DIR"]} {self.paths["LINUXCNC_STAGING_DIR"]}/tmp {self.paths["LINUXCNC_CONFIG_ROOT"]} {self.paths["LINUXCNC_GCODE_ROOT"]}

[Install]
WantedBy=multi-user.target
"""

        # Helper service
        helper_service = f"""[Unit]
Description=LinuxCNC HAL Helper
Before=linuxcnc-agent.service

[Service]
Type=simple
User={self.paths["LINUXCNC_LINUXCNC_USER"]}
Group={self.paths["LINUXCNC_AGENT_GROUP"]}
WorkingDirectory={self.paths["LINUXCNC_AGENT_ROOT"]}
ExecStart={self.paths["LINUXCNC_AGENT_ROOT"]}/.venv/bin/python -m agent.hal_helper --socket /run/linuxcnc/hal-helper.sock
Restart=on-failure
RestartSec=2
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=read-only
ReadWritePaths=/run/linuxcnc

[Install]
WantedBy=multi-user.target
"""

        # Apply update service
        apply_service = f"""[Unit]
Description=LinuxCNC Application Update Applier
After=local-fs.target

[Service]
Type=oneshot
User=root
Group=root
WorkingDirectory={self.paths["LINUXCNC_AGENT_ROOT"]}
Environment=TMPDIR={self.paths["LINUXCNC_STAGING_DIR"]}/tmp
ExecStart={self.paths["LINUXCNC_AGENT_ROOT"]}/.venv/bin/python -m agent.apply_update \\
    --staging-dir {self.paths["LINUXCNC_STAGING_DIR"]} \\
    --agent-root {self.paths["LINUXCNC_AGENT_ROOT"]}
TimeoutStartSec=300

[Install]
WantedBy=multi-user.target
"""

        services_dir = "/etc/systemd/system"
        self._write_file(os.path.join(services_dir, "linuxcnc-agent.service"), agent_service, mode=0o644)
        self._write_file(os.path.join(services_dir, "linuxcnc-hal-helper.service"), helper_service, mode=0o644)
        self._write_file(os.path.join(services_dir, "linuxcnc-apply-update.service"), apply_service, mode=0o644)

        # Reload systemd
        self._run(["systemctl", "daemon-reload"])

        print("Installed systemd services")

    def generate_agent_env(self, fingerprint):
        """Generate the agent environment file."""
        print("\n=== Generating Agent Configuration ===")

        env_content = f"""# LinuxCNC Agent Environment
# Generated by installer

LINUXCNC_AGENT_BIND={self.paths["LINUXCNC_AGENT_BIND"]}
LINUXCNC_AGENT_PORT={self.paths["LINUXCNC_AGENT_PORT"]}
LINUXCNC_CLIENT_FINGERPRINT={fingerprint}

# Paths
LINUXCNC_CONFIG_ROOT={self.paths["LINUXCNC_CONFIG_ROOT"]}
LINUXCNC_GCODE_ROOT={self.paths["LINUXCNC_GCODE_ROOT"]}
LINUXCNC_AGENT_ROOT={self.paths["LINUXCNC_AGENT_ROOT"]}
"""

        env_path = os.path.join(self.paths["LINUXCNC_CERTS_DIR"], "agent.env")
        self._write_file(env_path, env_content, mode=0o600, owner="root", group="root")

        print(f"Generated: {env_path}")

    def update_linuxcnc_ini(self):
        """Update LinuxCNC INI with correct paths."""
        print("\n=== Updating LinuxCNC INI ===")

        # Find the active INI
        config_root = self.paths["LINUXCNC_CONFIG_ROOT"]
        gcode_root = self.paths["LINUXCNC_GCODE_ROOT"]

        # Look for INI files in config root
        for root, dirs, files in os.walk(config_root):
            for file in files:
                if file.endswith(".ini"):
                    ini_path = os.path.join(root, file)
                    print(f"Found INI: {ini_path}")

                    # Read and update
                    with open(ini_path) as f:
                        content = f.read()

                    # Update SUBROUTINE_PATH
                    if "SUBROUTINE_PATH" in content:
                        lines = content.split("\n")
                        for i, line in enumerate(lines):
                            if line.strip().startswith("SUBROUTINE_PATH"):
                                lines[i] = f"SUBROUTINE_PATH = {gcode_root}"
                                break
                        content = "\n".join(lines)

                        # Backup and write
                        shutil.copy2(ini_path, ini_path + ".bak")
                        with open(ini_path, "w") as f:
                            f.write(content)
                        print(f"Updated: {ini_path}")

    def create_helper_socket_dir(self):
        """Create the helper socket directory with correct permissions."""
        print("\n=== Setting Up Helper Socket ===")

        socket_dir = "/run/linuxcnc"
        linuxcnc_user = self.paths["LINUXCNC_LINUXCNC_USER"]
        agent_group = self.paths["LINUXCNC_AGENT_GROUP"]

        if self.dry_run:
            print(f"[DRY RUN] mkdir -p {socket_dir}, owner={linuxcnc_user}:{agent_group}")
        else:
            os.makedirs(socket_dir, exist_ok=True)
            os.chmod(socket_dir, 0o755)
            shutil.chown(socket_dir, linuxcnc_user, agent_group)
            print(f"Created: {socket_dir} (owner={linuxcnc_user}:{agent_group})")

    def enable_services(self):
        """Enable and start services."""
        print("\n=== Enabling Services ===")

        services = ["linuxcnc-hal-helper", "linuxcnc-agent"]
        for svc in services:
            self._run(["systemctl", "enable", svc])
            self._run(["systemctl", "start", svc])
            print(f"Enabled and started: {svc}")

    def print_summary(self, fingerprint):
        """Print installation summary."""
        print("\n" + "=" * 60)
        print("LinuxCNC Installation Complete!")
        print("=" * 60)
        print()
        print("Machine Information:")
        print(f"  Hostname: {self.hostname}")
        print(f"  IP Address: {self.machine_ip}")
        print(f"  Agent URL: https://{self.machine_ip}:{self.paths['LINUXCNC_AGENT_PORT']}")
        print()
        print("Certificate Locations:")
        print(f"  CA Certificate: {self.paths['LINUXCNC_CERTS_DIR']}/ca.crt")
        print(f"  Client Certificate: {self.paths['LINUXCNC_CERTS_DIR']}/client.crt")
        print(f"  Client Key: {self.paths['LINUXCNC_CERTS_DIR']}/client.key")
        print()
        print("Client Fingerprint (save for dev machine):")
        print(f"  {fingerprint}")
        print()
        print("Next Steps:")
        print("  1. Copy ca.crt, client.crt, client.key to your dev machine")
        print("  2. Add to dev machine settings.ini:")
        print(f"     [AGENT]")
        print(f"     base_url = https://{self.machine_ip}:{self.paths['LINUXCNC_AGENT_PORT']}")
        print(f"     ca_file = C:\\Users\\<user>\\.linuxcnc\\ca.crt")
        print(f"     client_cert = C:\\Users\\<user>\\.linuxcnc\\client.crt")
        print(f"     client_key = C:\\Users\\<user>\\.linuxcnc\\client.key")
        print("  3. Test connection from dev machine")
        print()

    def install(self, source_dir):
        """Run the complete installation."""
        print("LinuxCNC Installer")
        print("=" * 60)
        print(f"Source: {source_dir}")
        print(f"Target: {self.paths['LINUXCNC_AGENT_ROOT']}")
        print(f"Mode: {'DRY RUN' if self.dry_run else 'LIVE'}")
        print()

        # Check for root on Unix-like systems
        if hasattr(os, 'geteuid') and os.geteuid() != 0 and not self.dry_run:
            print("ERROR: This installer must be run as root (use sudo)")
            sys.exit(1)

        try:
            self.create_users()
            self.create_directories()
            fingerprint = self.generate_certificates()
            self.install_agent(source_dir)
            self.install_services(source_dir)
            self.generate_agent_env(fingerprint)
            self.create_helper_socket_dir()
            self.update_linuxcnc_ini()
            self.enable_services()
            self.print_summary(fingerprint)

        except Exception as e:
            print(f"\nERROR: Installation failed: {e}")
            import traceback
            traceback.print_exc()
            sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description="LinuxCNC Machine Installer")
    parser.add_argument("--source", "-s", default=".", help="Source directory containing agent files")
    parser.add_argument("--config", "-c", help="Path to paths.conf configuration file")
    parser.add_argument("--dry-run", "-n", action="store_true", help="Show what would be done without doing it")
    args = parser.parse_args()

    installer = LinuxCNCInstaller(config_path=args.config, dry_run=args.dry_run)
    installer.install(args.source)


if __name__ == "__main__":
    main()
