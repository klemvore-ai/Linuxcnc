# LinuxCNC Machine Setup

This guide prepares a LinuxCNC machine to receive updates and administration requests from LinuxCNC over the management network.

The development computer may be Windows. LinuxCNC does not need to be installed on the development computer.

## Before You Start

You need:

- The LinuxCNC computer powered on.
- A keyboard and screen connected to the LinuxCNC computer for the first setup.
- The LinuxCNC computer connected to the management network.
- The LinuxCNC computer's management IP address.
- The LinuxCNC project available on the LinuxCNC computer, or a USB drive containing it.
- An account on the LinuxCNC computer that can run `sudo`.

Do the first installation while the machine is safe and not cutting material.

## Where Commands Run

Commands in a gray code block are typed into the LinuxCNC computer's Terminal application unless the heading says **Windows development computer**.

Do not type the `$` character shown in examples. It represents the command prompt.

The words below are placeholders:

- `<MACHINE_IP>` means the LinuxCNC computer's management IP address.
- `<LINUX_USER>` means your normal Linux username. In `/home/<LINUX_USER>/linuxcnc`, replace only `<LINUX_USER>`; the second `linuxcnc` is normally the standard LinuxCNC folder name.
- `<PROJECT_FOLDER>` means the folder containing the LinuxCNC project.

Replace placeholders with your real values before pressing Enter.

## Computer Names

This guide uses these names to avoid ambiguity:

- **Development PC** means the Windows computer where you develop and stage updates.
- **CNC PC** means the Debian/LinuxCNC computer connected to the machine hardware.

The Development PC stages updates over the management network. The CNC PC operator approves and applies them locally. LinuxCNC motion, safety, real-time hardware traffic, and continuous jogging stay on the CNC PC.

## 1. Open a Linux Terminal

On the LinuxCNC computer:

1. Log in to the normal Linux desktop.
2. Open the application menu.
3. Search for **Terminal**.
4. Open Terminal.

Test that the terminal works:

```bash
whoami
hostname
```

Write down the username and machine name. You will use them later.

## 2. Find the Project Folder

If LinuxCNC is on a USB drive, copy the project to the LinuxCNC computer first. A simple location is the Linux home folder.

In Terminal, run:

```bash
ls
```

Look for the LinuxCNC project folder. Then enter it. For example:

```bash
cd ~/LinuxCNC
```

Verify that you are in the correct folder:

```bash
pwd
ls requirements.txt agent core
```

The last command should show `requirements.txt`, an `agent` folder, and a `core` folder.

If `requirements.txt` is missing, the project copy is incomplete. Copy the complete LinuxCNC project folder again before continuing.

## 3. Install Basic Linux Packages

These packages provide Python, virtual environments, certificates, and archive support:

```bash
sudo apt update
sudo apt install -y python3 python3-pip python3-venv openssl
```

If asked for a password, type your normal Linux login password. The cursor may not move while you type. Press Enter when finished.

## 4. Create the Agent Account

The machine agent should not run as the root user. Create its dedicated account:

```bash
sudo useradd --system --create-home --shell /usr/sbin/nologin linuxcnc
```

If Linux says the user already exists, that is okay. Continue.

## 5. Install LinuxCNC in a Standard Location

The service guide expects the project at `/opt/LinuxCNC`.

From the folder containing the project, run:

```bash
sudo mkdir -p /opt/LinuxCNC
sudo cp -a . /opt/LinuxCNC/
sudo chown -R root:root /opt/LinuxCNC
sudo find /opt/LinuxCNC -type d -exec chmod 755 {} +
sudo find /opt/LinuxCNC -type f -exec chmod 644 {} +
```

Verify the required files:

```bash
ls -l /opt/LinuxCNC/requirements.txt
ls -l /opt/LinuxCNC/agent /opt/LinuxCNC/core
```

## 6. Create the Agent Python Environment

Create a private Python environment for the agent:

```bash
sudo install -d -o linuxcnc -g linuxcnc -m 755 /opt/LinuxCNC/.venv
sudo -u linuxcnc python3 -m venv /opt/LinuxCNC/.venv
sudo -u linuxcnc /opt/LinuxCNC/.venv/bin/python -m pip install --upgrade pip
sudo -u linuxcnc /opt/LinuxCNC/.venv/bin/pip install -r /opt/LinuxCNC/requirements.txt
sudo chown -R root:root /opt/LinuxCNC/.venv
sudo find /opt/LinuxCNC/.venv -type d -exec chmod 755 {} +
sudo find /opt/LinuxCNC/.venv -type f -exec chmod 644 {} +
sudo chmod 755 /opt/LinuxCNC/.venv/bin/python /opt/LinuxCNC/.venv/bin/pip
```

The release tree is root-owned after installation. The `linuxcnc` service account and the normal LinuxCNC user can read and execute it but cannot modify application code. The current requirements file also contains desktop packages. That is acceptable for the first installation. A smaller machine-only requirements file can be added later.

## 7. Check LinuxCNC and Mesa Commands

The agent needs access to the LinuxCNC Python modules and Mesa tools. Run:

```bash
python3 -c "import linuxcnc; print('linuxcnc Python module: OK')"
python3 -c "import hal; print('hal Python module: OK')"
command -v linuxcnc
command -v halcmd
command -v mesaflash
```

Each check should print a result. If either Python import fails, or a command is not found, stop here and ask for help. Do not start the agent until these checks work.

## 8. Create Agent Directories

Run:

```bash
sudo mkdir -p /etc/linuxcnc
sudo mkdir -p /var/lib/linuxcnc/updates
sudo chown -R linuxcnc:linuxcnc /var/lib/linuxcnc
sudo chmod 755 /etc/linuxcnc
```

Use your actual LinuxCNC user's home folder for the configuration directories. Replace only `<LINUX_USER>` below. Keep the second `linuxcnc` because it is the standard LinuxCNC folder name:

```bash
sudo mkdir -p /home/<LINUX_USER>/linuxcnc/configs
sudo mkdir -p /home/<LINUX_USER>/linuxcnc/nc_files
sudo chown -R <LINUX_USER>:<LINUX_USER> /home/<LINUX_USER>/linuxcnc/configs /home/<LINUX_USER>/linuxcnc/nc_files
sudo setfacl -R -m u:linuxcnc:rwX /home/<LINUX_USER>/linuxcnc/configs /home/<LINUX_USER>/linuxcnc/nc_files
sudo find /home/<LINUX_USER>/linuxcnc/configs /home/<LINUX_USER>/linuxcnc/nc_files -type d -exec setfacl -m d:u:linuxcnc:rwX {} +
```

LinuxCNC retains ownership of these files. The access-control lists grant the isolated `linuxcnc` agent only the required access below the two approved roots, including new files created there. If `setfacl` is unavailable, install the Debian `acl` package first. For example, if your username is `operator`, use `/home/operator/linuxcnc/configs`. Do not change the second `linuxcnc` unless you intentionally use a different LinuxCNC configuration folder.

## 9. Install the Service Template

Copy the service template:

```bash
sudo cp /opt/LinuxCNC/agent/linuxcnc-agent.service /etc/systemd/system/linuxcnc-agent.service
sudo cp /opt/LinuxCNC/agent/linuxcnc-hal-helper.service /etc/systemd/system/linuxcnc-hal-helper.service
sudo cp /opt/LinuxCNC/agent/linuxcnc-apply-update.service /etc/systemd/system/linuxcnc-apply-update.service
```

`linuxcnc-agent` is the network-facing HTTPS/mTLS service and runs as `linuxcnc`. `linuxcnc-hal-helper` has no network listener; it runs as the LinuxCNC owner and exposes fixed read-only HAL pin/component inspections, bounded pin-value snapshots, and LinuxCNC status polling over a local Unix socket. The provided helper template uses `darrell`; change its `User=` value to your actual LinuxCNC login if it differs.

`linuxcnc-apply-update` is a root-owned, local-only one-shot service. It is not enabled or callable through the network agent. It applies a previously staged application update transactionally and restarts the two normal services afterward.

Create its environment file:

```bash
sudo cp /opt/LinuxCNC/agent/agent.env.example /etc/linuxcnc/agent.env
sudo nano /etc/linuxcnc/agent.env
```

Change these values:

```text
LINUXCNC_AGENT_BIND=<MANAGEMENT_IP>
LINUXCNC_AGENT_PORT=9443
LINUXCNC_CLIENT_FINGERPRINT=<CLIENT_CERTIFICATE_FINGERPRINT>
```

`LINUXCNC_AGENT_BIND` must be the LinuxCNC computer's management Ethernet or Wi-Fi address. Do not use the Mesa or EtherCAT control-network address.

Do not use the control NIC address for the LinuxCNC agent.

To save in `nano`:

1. Press `Ctrl+O`.
2. Press Enter.
3. Press `Ctrl+X`.

Protect the file:

```bash
sudo chown root:linuxcnc /etc/linuxcnc/agent.env
sudo chmod 640 /etc/linuxcnc/agent.env
```

The certificate files must be installed before starting the service:

```text
/etc/linuxcnc/ca.crt
/etc/linuxcnc/server.crt
/etc/linuxcnc/server.key
```

Do not invent certificate contents. They must be created by the LinuxCNC enrollment process or supplied as part of the approved certificate setup.

## 9a. Create Certificates for a First Test

If you do not already have certificates, that is expected. The service cannot start until they are created. Perform these commands on the Debian/LinuxCNC computer. Keep the private-key files secret.

Create a private certificate folder:

```bash
sudo mkdir -p /etc/linuxcnc
sudo chown linuxcnc:linuxcnc /etc/linuxcnc
sudo chmod 700 /etc/linuxcnc
cd /etc/linuxcnc
```

Create a private certificate authority. This authority will sign the agent and LinuxCNC client certificates:

```bash
sudo -u linuxcnc openssl genrsa -out ca.key 4096
sudo -u linuxcnc openssl req -x509 -new -nodes -key ca.key -sha256 -days 3650 -out ca.crt -subj "/CN=LinuxCNC Test Authority"
```

Create the LinuxCNC agent server certificate. Replace `<MANAGEMENT_IP>` with the management IP address of this Debian computer:

```bash
sudo -u linuxcnc openssl genrsa -out server.key 4096
sudo -u linuxcnc openssl req -new -key server.key -out server.csr -subj "/CN=<MANAGEMENT_IP>"
printf "subjectAltName=IP:<MANAGEMENT_IP>\nextendedKeyUsage=serverAuth\n" | sudo tee /tmp/linuxcnc-server-ext.cnf >/dev/null
sudo -u linuxcnc openssl x509 -req -in server.csr -CA ca.crt -CAkey ca.key -CAcreateserial -out server.crt -days 825 -sha256 -extfile /tmp/linuxcnc-server-ext.cnf
```

Create the LinuxCNC desktop client certificate:

```bash
sudo -u linuxcnc openssl genrsa -out client.key 4096
sudo -u linuxcnc openssl req -new -key client.key -out client.csr -subj "/CN=LinuxCNC Desktop"
printf "extendedKeyUsage=clientAuth\n" | sudo tee /tmp/linuxcnc-client-ext.cnf >/dev/null
sudo -u linuxcnc openssl x509 -req -in client.csr -CA ca.crt -CAkey ca.key -CAcreateserial -out client.crt -days 825 -sha256 -extfile /tmp/linuxcnc-client-ext.cnf
```

The generated `ca.crt`, `server.crt`, and `server.key` are already in `/etc/linuxcnc/` because the commands above created them there. Set the permissions expected by the service:

```bash
sudo chown root:linuxcnc /etc/linuxcnc/ca.crt /etc/linuxcnc/server.crt /etc/linuxcnc/server.key
sudo chmod 640 /etc/linuxcnc/ca.crt /etc/linuxcnc/server.crt /etc/linuxcnc/server.key
```

The `ca.key`, `server.key`, `client.key`, CSR files, and serial files are private or temporary. Do not upload them to a public repository or paste their contents into chat. For a first test, keep the CA files in `/etc/linuxcnc`; later, move the CA private key to a separate protected administration location.

Show the client certificate fingerprint. This prints public certificate information, not the private key:

```bash
sudo openssl x509 -in /etc/linuxcnc/client.crt -noout -fingerprint -sha256
```

Use the fingerprint value in `LINUXCNC_CLIENT_FINGERPRINT`. Remove the `SHA256 Fingerprint=` label; colons are optional. The `sudo` is needed because the certificate files are protected for the `linuxcnc` service account. Copy `ca.crt`, `client.crt`, and `client.key` to the Windows development computer using a protected transfer method. The Windows client needs those three files; the Debian agent needs `ca.crt`, `server.crt`, and `server.key`. Do not copy `ca.key` to Windows or leave it in a normal project folder.

To make the client files available to your normal Debian login for one protected transfer, run:

```bash
sudo cp /etc/linuxcnc/ca.crt /tmp/ca.crt
sudo cp /etc/linuxcnc/client.crt /tmp/client.crt
sudo cp /etc/linuxcnc/client.key /tmp/client.key
sudo chown <LINUX_USER>:<LINUX_USER> /tmp/ca.crt /tmp/client.crt /tmp/client.key
sudo chmod 644 /tmp/ca.crt /tmp/client.crt
sudo chmod 600 /tmp/client.key
```

Replace both occurrences of `<LINUX_USER>` with your Debian login username, such as `darrell`. Transfer the files to Windows, then immediately remove the temporary Debian copies:

```bash
rm -f /tmp/ca.crt /tmp/client.crt /tmp/client.key
```

## 9b. Rotate a Desktop Certificate

Keep the existing desktop certificate working until a replacement has been verified. Create and transfer a new client certificate using the same client-certificate steps above, then display its fingerprint:

```bash
sudo openssl x509 -in /etc/linuxcnc/client.crt -noout -fingerprint -sha256
```

Add the replacement fingerprint alongside the current one in `/etc/linuxcnc/agent.env` as a comma-separated list. For example:

```text
LINUXCNC_CLIENT_FINGERPRINT=<CURRENT_FINGERPRINT>,<REPLACEMENT_FINGERPRINT>
```

Restart the agent and verify **Test Machine-Agent Connection** from the replacement desktop certificate. Remove the old fingerprint only after the replacement connection succeeds.

SSH is for first installation, certificate enrollment or rotation, and explicit recovery only. Once the machine agent is enrolled, use the secure machine-agent controls for normal monitoring, deployment, diagnostics, and approved named operations.

## 10. Start the Agent

Run:

```bash
sudo systemctl daemon-reload
sudo systemctl enable linuxcnc-hal-helper
sudo systemctl enable linuxcnc-agent
sudo systemctl start linuxcnc-hal-helper
sudo systemctl start linuxcnc-agent
```

Check the result:

```bash
sudo systemctl status linuxcnc-agent --no-pager
sudo systemctl status linuxcnc-hal-helper --no-pager
```

Look for `active (running)`.

If it is not running, show the recent error:

```bash
sudo journalctl -u linuxcnc-agent -n 50 --no-pager
```

Do not repeatedly restart a failing service. Save the error text for troubleshooting.

## Applying a Staged Application Update

The **Stage App Update on Remote Machine** button uploads a verified archive but does not install it remotely. After reviewing the update at the LinuxCNC computer, approve it locally:

```bash
sudo systemctl start linuxcnc-apply-update
sudo systemctl status linuxcnc-apply-update --no-pager
sudo systemctl status linuxcnc-agent linuxcnc-hal-helper --no-pager
```

The installer replaces `/opt/LinuxCNC`, moves its existing `.venv` into the new application tree without copying it, removes the staged archive after success, atomically records a timestamped `applied`, `failed`, or `rolled_back` result with the applied archive SHA-256 in `/var/lib/linuxcnc/updates/last-apply.json`, and restarts the normal services. A `rolled_back` result means the previous application tree was restored after an update error. Do not run this while changing machine configuration or when you cannot access the LinuxCNC computer locally.

After an approved update, start the local desktop application with the generated launcher:

```bash
/opt/LinuxCNC/linuxcnc-launcher.sh
```

Run it as your normal graphical LinuxCNC user, not with `sudo`.

## 11. Open the Firewall Port

Only allow the management computer to reach the agent. Replace `<DEV_MACHINE_IP>` with the Windows development computer's management IP address:

```bash
sudo ufw allow from <DEV_MACHINE_IP> to any port 9443 proto tcp
sudo ufw status
```

If `ufw` is not installed or your machine uses a different firewall, do not change firewall settings blindly. Ask for help using the machine's current network setup.

## 12. Configure LinuxCNC on Windows

### Windows development computer

In the LinuxCNC project `settings.ini`, add the certificate references:

```ini
[AGENT]
base_url = https://<MACHINE_IP>:9443
ca_file = C:\Users\<WINDOWS_USER>\.linuxcnc\ca.crt
client_cert = C:\Users\<WINDOWS_USER>\.linuxcnc\client.crt
client_key = C:\Users\<WINDOWS_USER>\.linuxcnc\client.key
timeout = 5
```

The three certificate files must exist on the Windows development computer. Keep the private key protected and never paste it into `settings.ini`.

## 13. Test the Connection

In LinuxCNC:

1. Open **Mesa Utilities**.
2. Confirm that the credential status says the OS credential manager is available.
3. Click **Test Machine-Agent Connection**. It tests the secure machine agent when agent settings are configured.
4. Confirm that the result shows the agent version and capabilities.
5. Test Mesa identification.
6. Test HAL pin and component inspection.
7. Open the Dashboard and confirm telemetry.

Start with monitoring only. Test uploads, tuning, configuration activation, restore, and firmware operations later and only while the machine is safe.

## 13a. Real-Machine Acceptance Checklist

Perform this checklist with the spindle disabled, the machine clear of workholding, and a person at the local E-stop. Use the Windows desktop application after every staged application update and confirm the Debian agent reports the expected version and capabilities.

1. Confirm the services are healthy locally:

	```bash
	sudo systemctl status linuxcnc-agent linuxcnc-hal-helper --no-pager
	```

2. From Windows, use **Test Machine-Agent Connection**. Confirm the version, role mode, and capability list are displayed.
3. Test read-only operations: telemetry, HAL pins, HAL components, HAL Scope, tool-table read, G-code list/read, and the Mesa HMID read.
4. With LinuxCNC powered but idle, test one named low-risk operation at a time: coolant on/off, Manual/Auto mode, spindle stop, a bounded tool-setter or park move, and a named tool change. Verify the physical result before continuing.
5. With a known safe test program loaded, verify Cycle Start, Pause/Resume, and Cycle Stop. Keep the local E-stop reachable throughout the test.
6. Confirm Dashboard jogging remains unavailable through the secure management connection. Jog only from local LinuxCNC controls or another local real-time input device.
7. Stage a configuration or application update, inspect the staged status, then perform the local approval step. Verify both normal services restart and the desktop launcher opens LinuxCNC afterward.

Do not test tool-height calibration, edge finding, or automatic tool-table workflows through the secure management connection. Those workflows remain local-only until their complete approach, contact, retract, persistence, and partial-completion behavior is implemented as reviewed typed operations. The secure **Raw Z Probe (No Offset)** and **Touch Plate Z Offset** controls are separate bounded workflows; use them only after the local probe source has been selected and verified.

## 14. Important Network Rule

If the LinuxCNC computer has two network interfaces:

- Management Ethernet or Wi-Fi is for LinuxCNC administration.
- Control Ethernet is for Mesa or EtherCAT real-time traffic.
- The control interface should not have a default gateway.
- The agent should bind only to the management address.
- Wi-Fi must never carry E-stop, limit, servo, Mesa, EtherCAT, or real-time motion traffic.

## 15. Stop and Uninstall

To stop the agent without deleting files:

```bash
sudo systemctl stop linuxcnc-agent
```

To prevent it from starting automatically:

```bash
sudo systemctl disable linuxcnc-agent
```

Do not delete the LinuxCNC configuration, certificates, or machine files unless you have a separate backup.

## Common Problems

### `requirements.txt` not found

You are either in the wrong folder or the project copy is incomplete. Run:

```bash
pwd
ls
find /opt/LinuxCNC -maxdepth 2 -name requirements.txt -print
```

Use the full path when installing:

```bash
sudo -u linuxcnc /opt/LinuxCNC/.venv/bin/pip install -r /opt/LinuxCNC/requirements.txt
```

### `sudo` asks for a password

Use the normal Linux login password. Nothing is displayed while typing it.

### The service will not start

First look at the `ExecStart` line in the status output. It should contain:

```text
ExecStart=/opt/LinuxCNC/.venv/bin/python -m agent
```

If it contains `/usr/bin/python3 -m agent`, the installed service is an older copy. The project service file must be copied into `/etc/systemd/system/` again before restarting. Restarting without copying the updated file will continue using the old command.

If the log contains a path such as:

```text
Failed to set up mount namespacing: /home/<LINUX_USER>/linuxcnc/configs: No such file or directory
status=226/NAMESPACE
```

the service is trying to protect a folder that does not exist. Create the folders, replacing only `<LINUX_USER>`:

```bash
sudo mkdir -p /home/<LINUX_USER>/linuxcnc/configs
sudo mkdir -p /home/<LINUX_USER>/linuxcnc/nc_files
sudo chown -R <LINUX_USER>:<LINUX_USER> /home/<LINUX_USER>/linuxcnc/configs
sudo chown -R <LINUX_USER>:<LINUX_USER> /home/<LINUX_USER>/linuxcnc/nc_files
```

The service template uses the agent virtual environment at `/opt/LinuxCNC/.venv/bin/python`. Verify it exists:

```bash
ls -l /opt/LinuxCNC/.venv/bin/python
```

If it is missing, recreate the environment and install dependencies:

```bash
sudo -u linuxcnc python3 -m venv /opt/LinuxCNC/.venv
sudo -u linuxcnc /opt/LinuxCNC/.venv/bin/python -m pip install --upgrade pip
sudo -u linuxcnc /opt/LinuxCNC/.venv/bin/pip install -r /opt/LinuxCNC/requirements.txt
sudo chown -R root:root /opt/LinuxCNC/.venv
sudo find /opt/LinuxCNC/.venv -type d -exec chmod 755 {} +
sudo find /opt/LinuxCNC/.venv -type f -exec chmod 644 {} +
sudo chmod 755 /opt/LinuxCNC/.venv/bin/python /opt/LinuxCNC/.venv/bin/pip
```

After correcting the folders or Python environment, copy the updated service template again and restart it:

```bash
sudo cp /opt/LinuxCNC/agent/linuxcnc-agent.service /etc/systemd/system/linuxcnc-agent.service
sudo systemctl daemon-reload
sudo systemctl reset-failed linuxcnc-agent
sudo systemctl restart linuxcnc-agent
sudo systemctl status linuxcnc-agent --no-pager
```

Confirm the corrected Python path:

```bash
sudo systemctl status linuxcnc-agent --no-pager
```

Read the `ExecStart` line. It must show `/opt/LinuxCNC/.venv/bin/python`, not `/usr/bin/python3`.

### `FileNotFoundError` while loading TLS certificates

If the journal ends with a message like:

```text
FileNotFoundError: [Errno 2] No such file or directory
context.load_verify_locations(cafile=ca_file)
```

the agent is running, but one of its certificate files is missing. The first file it tried to open is normally:

```text
/etc/linuxcnc/ca.crt
```

Check all three required files:

```bash
sudo ls -l /etc/linuxcnc/ca.crt
sudo ls -l /etc/linuxcnc/server.crt
sudo ls -l /etc/linuxcnc/server.key
```

All three files must exist before the service can start. Do not create empty files or type random certificate text. These files must come from the approved LinuxCNC certificate-enrollment process or from the certificate authority used for this machine.

If the certificates are stored somewhere else, copy them into the expected directory. Replace `<CERTIFICATE_SOURCE>` with the real source location:

```bash
sudo mkdir -p /etc/linuxcnc
sudo cp <CERTIFICATE_SOURCE>/ca.crt /etc/linuxcnc/ca.crt
sudo cp <CERTIFICATE_SOURCE>/server.crt /etc/linuxcnc/server.crt
sudo cp <CERTIFICATE_SOURCE>/server.key /etc/linuxcnc/server.key
sudo chown root:linuxcnc /etc/linuxcnc/ca.crt /etc/linuxcnc/server.crt /etc/linuxcnc/server.key
sudo chmod 640 /etc/linuxcnc/ca.crt /etc/linuxcnc/server.crt /etc/linuxcnc/server.key
```

Verify that the service account can read them without displaying private-key contents:

```bash
sudo -u linuxcnc test -r /etc/linuxcnc/ca.crt && echo "CA certificate readable"
sudo -u linuxcnc test -r /etc/linuxcnc/server.crt && echo "Server certificate readable"
sudo -u linuxcnc test -r /etc/linuxcnc/server.key && echo "Server private key readable"
```

Then restart the service:

```bash
sudo systemctl reset-failed linuxcnc-agent
sudo systemctl restart linuxcnc-agent
sudo systemctl status linuxcnc-agent --no-pager
```

If the next error names `server.crt` or `server.key`, apply the same check to that specific file. The CA certificate verifies client certificates; the server certificate and private key identify the LinuxCNC machine. They are different files and all three are required.

### `OSError: [Errno 99] Cannot assign requested address`

If the journal contains:

```text
OSError: [Errno 99] Cannot assign requested address
```

the agent is trying to listen on an IP address that is not assigned to this Debian computer. This is usually a wrong value in `LINUXCNC_AGENT_BIND`.

Stop the automatic restart loop:

```bash
sudo systemctl stop linuxcnc-agent
```

Show the IP addresses assigned to this Debian computer:

```bash
hostname -I
ip -br address
```

Choose the address on the management Ethernet or Wi-Fi interface. Do not choose the Mesa or EtherCAT control-network address. Confirm the value currently configured:

```bash
sudo grep '^LINUXCNC_AGENT_BIND=' /etc/linuxcnc/agent.env
```

Edit the environment file:

```bash
sudo nano /etc/linuxcnc/agent.env
```

Set `LINUXCNC_AGENT_BIND` to an address shown by `hostname -I`. For example, if `hostname -I` shows `<MANAGEMENT_IP>`, use:

```text
LINUXCNC_AGENT_BIND=<MANAGEMENT_IP>
```

Do not include angle brackets or quotation marks. If you are unsure which interface is the management interface, temporarily use the address connected to the same network as the Windows development computer, not the address connected to the Mesa board.

Then restart the service:

```bash
sudo systemctl reset-failed linuxcnc-agent
sudo systemctl restart linuxcnc-agent
sudo systemctl status linuxcnc-agent --no-pager
```

If the address is correct but the error continues, check whether another service already uses the port:

```bash
sudo ss -ltnp | grep ':9443'
```

If another service is using port `9443`, choose a different unused port in `/etc/linuxcnc/agent.env`, then use the same port in the Windows LinuxCNC `[AGENT]` settings.

Run:

```bash
sudo systemctl status linuxcnc-agent --no-pager
sudo journalctl -u linuxcnc-agent -n 50 --no-pager
```

### LinuxCNC cannot connect

### `rtapi_shmem_new ... Permission denied` when showing HAL pins

If **Show HAL Pins** returns:

```text
rtapi_shmem_new failed due to shmget(...): Permission denied
rtapi_init: could not open shared memory for uuid
HAL: ERROR: could not initialize RTAPI
halcmd: hal_init() failed
```

the secure connection is working, but the agent process cannot access LinuxCNC's local real-time shared memory. This usually happens because LinuxCNC is running as your normal desktop user while the agent is running as the separate `linuxcnc` service user. It can also mean LinuxCNC's RTAPI kernel module is not loaded.

First check whether LinuxCNC itself is running and who owns it:

```bash
ps -eo user,pid,comm,args | grep '[l]inuxcnc'
lsmod | grep rtapi
```

Test HAL access as the logged-in LinuxCNC user. Replace `<LINUX_USER>` with the username shown by the first command:

```bash
sudo -u <LINUX_USER> halcmd show pin
```

If this command also fails, start LinuxCNC normally and resolve the local LinuxCNC/RTAPI problem first. Do not change shared-memory permissions blindly.

If it works as `<LINUX_USER>` but fails for `linuxcnc`, install the read-only HAL helper and use the isolated agent service. The helper must run as the same Linux user that owns the RTAPI segment:

```bash
sudo cp /opt/LinuxCNC/agent/linuxcnc-hal-helper.service /etc/systemd/system/linuxcnc-hal-helper.service
sudo sed -i 's/^User=.*/User=<LINUX_USER>/' /etc/systemd/system/linuxcnc-hal-helper.service
sudo systemctl daemon-reload
sudo systemctl enable --now linuxcnc-hal-helper
sudo systemctl restart linuxcnc-agent
sudo systemctl status linuxcnc-hal-helper --no-pager
sudo systemctl status linuxcnc-agent --no-pager
```

This keeps the network-facing service under `linuxcnc`; only the local helper can read the LinuxCNC user's owner-only RTAPI shared memory. If either service fails, inspect its individual journal with `sudo journalctl -u <SERVICE_NAME> -n 50 --no-pager`.

Do not solve this by making `/dev/shm` world-writable, disabling Linux security controls, or running every agent operation as root.

If LinuxCNC reports:

```text
Secure machine-agent connection successful.
LinuxCNC running: True
Machine-agent connection failed: Remote end closed connection without response
```

the first connection succeeded, but the agent closed the second status request while trying to read LinuxCNC state. Update the project on Debian so the agent includes the resilient status handler, then restart the service:

```bash
cd ~/LinuxCNC
sudo cp -a agent /opt/LinuxCNC/
sudo chown -R root:root /opt/LinuxCNC/agent
sudo find /opt/LinuxCNC/agent -type d -exec chmod 755 {} +
sudo find /opt/LinuxCNC/agent -type f -exec chmod 644 {} +
sudo systemctl restart linuxcnc-agent
sudo systemctl status linuxcnc-agent --no-pager
```

Then check the recent log:

```bash
sudo journalctl -u linuxcnc-agent -n 30 --no-pager
```

The agent status response should now remain connected even if LinuxCNC state is temporarily unavailable. LinuxCNC may report `state_available: false`; this is a diagnostic state, and motion or tuning actions should remain disabled until LinuxCNC state is available.

Check, in order:

1. The LinuxCNC computer is powered on.
2. The management IP address is correct.
3. The agent service says `active (running)`.
4. The firewall allows port `9443` from the development computer.
5. The Windows certificate paths are correct.
6. The client certificate fingerprint is authorized.
7. The agent is bound to the management interface.

### The machine is unreachable after a network change

Use the LinuxCNC computer's local screen and keyboard. Do not make repeated remote changes to network settings. Restore the known management configuration locally, then test again.

## Safety Reminder

The agent is an administration and monitoring service. LinuxCNC's real-time motion and safety behavior must remain local to the LinuxCNC computer and its control hardware. Never use Wi-Fi as a substitute for E-stop wiring, limit wiring, Mesa real-time traffic, or EtherCAT traffic.
