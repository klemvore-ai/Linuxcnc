#!/usr/bin/env python3
"""Quick deploy script for agent files to the machine."""

import subprocess
import sys
import os

FILES = [
    "agent/policy.py",
    "agent/linuxcnc_runtime.py",
    "agent/server.py",
    "agent/hal_helper.py",
    "agent/status.py",
]

def main():
    machine = os.environ.get("CNCFUSION_SSH_TARGET")
    if not machine:
        print("ERROR: Set CNCFUSION_SSH_TARGET to user@machine-management-host before deploying.")
        sys.exit(1)
    
    print("Deploying agent files to machine...")
    
    for local_path in FILES:
        if not os.path.exists(local_path):
            print(f"SKIP: {local_path} not found")
            continue
            
        filename = os.path.basename(local_path)
        remote_tmp = f"/tmp/{filename}"
        remote_final = f"/opt/CNCFusion/agent/{filename}"
        
        # Copy to /tmp
        print(f"Copying {local_path}...")
        result = subprocess.run(
            ["scp", local_path, f"{machine}:{remote_tmp}"],
            capture_output=True,
            text=True
        )
        if result.returncode != 0:
            print(f"  ERROR: {result.stderr}")
            continue
            
        # Move to final location with sudo
        result = subprocess.run(
            ["ssh", machine, f"sudo cp {remote_tmp} {remote_final}"],
            capture_output=True,
            text=True
        )
        if result.returncode != 0:
            print(f"  ERROR: {result.stderr}")
            continue
            
        print(f"  OK: {filename}")
    
    # Restart services
    print("\nRestarting services...")
    subprocess.run(["ssh", machine, "sudo systemctl restart cncfusion-hal-helper"])
    subprocess.run(["ssh", machine, "sudo systemctl restart cncfusion-agent"])
    print("Done!")

if __name__ == "__main__":
    main()
