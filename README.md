# Bootstrap Device

An Ansible automation project for bootstrapping and configuring Raspberry Pi devices with development and infrastructure tools.

## Overview

This project automates the setup of Raspberry Pi systems with a consistent configuration including remote desktop access, development tools, container orchestration, and shell customization.

## Features

- **Custom MOTD**: Distinct message-of-the-day for Raspberry Pi workers and the control-plane VM
- **xRDP Remote Desktop**: Remote desktop access with SSL certificate configuration (workers)
- **Development Tools**: Git and other essential development packages
- **Oh-My-ZSH**: Popular ZSH shell framework for enhanced terminal experience (control plane + workers)
- **Machine Learning**: Additional ML-related tooling and setup for designated worker(s)

Kubernetes bootstrap (kubeadm, containerd, kube-vip) is being layered in as roles — see project notes for current status.

## Prerequisites

- One Ubuntu Server 24.04 control-plane VM and one or more Raspberry Pi 5 workers running Raspberry Pi OS (64-bit)
- Ansible installed on your control machine
- SSH access to all devices
- An inventory defining `control_plane` and `workers` groups (see `inventory/hosts.yml`)

## Installation

1. Clone this repository:
   ```bash
   git clone <repository-url>
   cd bootstrap-device
   ```

2. Install required Ansible collections:
   ```bash
   ansible-galaxy collection install -r requirements.yml
   ```

3. Edit `inventory/hosts.yml` with your real hostnames/IPs for the `control_plane` and `workers` groups (and `ml` for any worker running ML workloads)

4. Run the playbook:
   ```bash
   ansible-playbook -i inventory/hosts.yml main.yml
   ```

## What Gets Configured

### System Setup
- Git version control system
- Colord user permissions for graphical applications (workers)

### Remote Access
- xRDP server for remote desktop connections (workers)
- User added to ssl-cert group for certificate access (workers)

### Development Environment
- Oh-My-ZSH shell framework (control plane + workers)

## Dependencies

- **Ansible Collections**:
  - `community.general` (>= 7.0.0)

## Project Structure

```
.
├── main.yml                      # Main playbook orchestrating all configurations
├── requirements.yml              # Ansible collection dependencies
├── inventory/
│   └── hosts.yml                 # control_plane / workers / ml groups
├── files/
│   └── 45-allow-colord.pkla     # Polkit policy for Colord
└── playbooks/
    ├── raspberry-pi-motd.yml     # Worker MOTD
    ├── control-plane-motd.yml    # Control-plane MOTD
    ├── oh-my-zsh.yml            # ZSH shell setup
    └── machine-learning.yml      # ML tools and libraries
```

## License

MIT

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.
