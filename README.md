# Bootstrap Device

An Ansible automation project for bootstrapping and configuring Raspberry Pi devices with development and infrastructure tools.

## Overview

This project automates the setup of Raspberry Pi systems with a consistent configuration including remote desktop access, development tools, container orchestration, and shell customization.

## Features

- **Custom MOTD**: Personalized message-of-the-day for Raspberry Pi
- **xRDP Remote Desktop**: Remote desktop access with SSL certificate configuration
- **Development Tools**: Git and other essential development packages
- **Snapd**: Snap package manager with proper PATH configuration
- **Oh-My-ZSH**: Popular ZSH shell framework for enhanced terminal experience
- **MicroK8s**: Lightweight Kubernetes distribution for container orchestration
- **Machine Learning**: Additional ML-related tooling and setup

## Prerequisites

- One or more Raspberry Pi devices running a Debian-based OS
- Ansible installed on your control machine
- SSH access to your Raspberry Pi devices
- Ansible inventory file with hosts in the `pis` group

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

3. Configure your inventory file with your Raspberry Pi hosts in the `pis` group

4. Run the playbook:
   ```bash
   ansible-playbook -i inventory main.yml
   ```

## What Gets Configured

### System Setup
- Git version control system
- Snapd package manager with PATH configuration
- Colord user permissions for graphical applications

### Remote Access
- xRDP server for remote desktop connections
- User added to ssl-cert group for certificate access

### Development Environment
- Oh-My-ZSH shell framework
- MicroK8s Kubernetes cluster

## Dependencies

- **Ansible Collections**:
  - `community.general` (>= 7.0.0)

## Project Structure

```
.
├── main.yml                    # Main playbook orchestrating all configurations
├── requirements.yml            # Ansible collection dependencies
├── files/
│   └── 45-allow-colord.pkla   # Polkit policy for Colord
└── playbooks/
    ├── raspberry-pi-motd.yml   # Custom MOTD configuration
    ├── oh-my-zsh.yml          # ZSH shell setup
    ├── microk8s.yml           # Kubernetes setup
    └── machine-learning.yml    # ML tools and libraries
```

## License

MIT

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.
