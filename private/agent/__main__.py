"""Command-line entry point for the LinuxCNC machine agent service."""

import argparse

from private.agent.linuxcnc_runtime import LinuxCNCRuntime
from private.agent.server import MachineAgentServer, create_server_tls_context
from private.agent.authorization import parse_role_mapping


def parse_args():
    """Parse TLS, bind-address, and approved-directory configuration."""
    parser = argparse.ArgumentParser(description="LinuxCNC machine agent")
    parser.add_argument("--bind", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9443)
    parser.add_argument("--ca-file", required=True)
    parser.add_argument("--certificate-file", required=True)
    parser.add_argument("--private-key-file", required=True)
    parser.add_argument("--config-root", action="append", required=True)
    parser.add_argument("--gcode-root", required=True)
    parser.add_argument("--hal-helper-socket")
    parser.add_argument("--client-fingerprint", action="append", default=[])
    parser.add_argument("--client-role", action="append", default=[])
    return parser.parse_args()


def main():
    """Create the configured agent and run it until interrupted."""
    args = parse_args()
    client_roles = parse_role_mapping(args.client_role)
    fingerprints = [
        fingerprint.strip()
        for entry in args.client_fingerprint
        for fingerprint in entry.split(",")
        if fingerprint.strip()
    ]
    tls_context = create_server_tls_context(
        args.ca_file,
        args.certificate_file,
        args.private_key_file,
    )
    server = MachineAgentServer(
        (args.bind, args.port),
        tls_context,
        LinuxCNCRuntime(hal_helper_socket=args.hal_helper_socket),
        args.config_root,
        args.gcode_root,
        fingerprints,
        client_roles,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()


if __name__ == "__main__":
    main()
