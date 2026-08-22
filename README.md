# Bootstrap Device

An Ansible automation project for bootstrapping a small kubeadm Kubernetes cluster — one Ubuntu Server 24.04 control-plane VM and two Raspberry Pi 5 workers (Raspberry Pi OS, 64-bit) — plus general device provisioning (shell setup, remote desktop, ML tooling).

## Overview

This project bootstraps a kubeadm-based Kubernetes cluster across a control-plane VM and Raspberry Pi worker nodes (containerd, kube-vip, Cilium, ArgoCD), and layers on general device configuration: remote desktop access, development tools, and shell customization.

## Features

- **Kubernetes cluster bootstrap**: kubeadm-based control plane (kube-vip for a stable VIP endpoint, Cilium as the CNI, ArgoCD as the GitOps controller for everything else) and worker nodes, via the `k8s_common`/`k8s_control_plane`/`k8s_worker` roles
- **Custom MOTD**: Distinct message-of-the-day for Raspberry Pi workers and the control-plane VM
- **xRDP Remote Desktop**: Remote desktop access with SSL certificate configuration (workers)
- **Development Tools**: Git and other essential development packages
- **Oh-My-ZSH**: Popular ZSH shell framework for enhanced terminal experience (control plane + workers)
- **Machine Learning**: Additional ML-related tooling and setup for designated worker(s)

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

4. (Optional, one-time) If your SSH user needs a password for `sudo`, every
   `become: true` task below will fail with `Missing sudo password` unless
   you either pass `-K` on every run or grant passwordless sudo once:
   ```bash
   ansible-playbook -i inventory/hosts.yml playbooks/passwordless-sudo.yml -K
   ```

5. Run the playbook:
   ```bash
   ansible-playbook -i inventory/hosts.yml main.yml
   ```

## Unattended VM Install (Control Plane)

For a UTM/QEMU control-plane VM, `playbooks/build-autoinstall-iso.yml` generates an Ubuntu Server 24.04 `autoinstall` seed ISO so the OS installation requires no manual TUI steps:

```bash
ansible-playbook playbooks/build-autoinstall-iso.yml
```

Attach the resulting `build/autoinstall-seed.iso` as a second CD-ROM drive on the VM (alongside the Ubuntu installer ISO) and boot — hostname, disk layout, OpenSSH, and your SSH public key are all pre-seeded. Override defaults with `-e control_plane_username=... -e ssh_pubkey_path=...`.

## ArgoCD

`main.yml` installs ArgoCD automatically (`roles/k8s_control_plane/tasks/argocd.yml`,
right after Cilium) via the official non-HA install manifest, pinned to
`argocd_version` in `inventory/group_vars/control_plane/vars.yml`. This is
imperative, like kube-vip and Cilium — ArgoCD can't manage its own initial
install. Immediately after, the same task applies `homelab-infra`'s
`root-app.yaml` (fetched from `homelab_infra_root_app_url`, its `main`
branch), which is the app-of-apps entry point — from that point on, ArgoCD
syncs and self-heals everything under `homelab-infra`'s `apps/` on its own
(Cilium, kube-vip, and ArgoCD itself stay imperative, since none of them can
manage their own bootstrap).

The install also sets `server.insecure` in `argocd-cmd-params-cm`. The gateway
in `homelab-infra` (`apps/gateway-config`) terminates TLS and forwards plain
HTTP to `argocd-server:80`; left at its default `argocd-server` does its own
TLS and answers every plaintext request with a 307 to `https://<host>/`, which
comes straight back through the same gateway and is forwarded as plaintext
again. That is an infinite redirect loop, and it looks like a broken
certificate when it isn't — the cert is fine, the redirect is the bug.

**Dex SSO login is the one deliberate exception, not automated**: once Dex is
up via GitOps, run `playbooks/argocd-dex-sso.yml` to patch
`argocd-cm`/`argocd-secret` in the `argocd` namespace and wire the ArgoCD
login page to Dex as an external OIDC provider. It's not part of `main.yml`'s
automatic chain since Dex itself doesn't exist until `root-app.yaml` has
synced, and the shared client secret still needs generating/sealing on both
sides first (see below).

### Ansible Vault

The Dex OIDC client secret is meant to live ansible-vault-encrypted in
`inventory/vault/argocd_dex.yml` — that path is gitignored (only
`inventory/vault/argocd_dex.yml.example`, a template with no real secret, is
tracked), so a real secret can never land in git unencrypted even by
accident. It's also kept out of `group_vars`/`host_vars` on purpose, so
unrelated playbook runs never need the vault password. One-time setup:

```bash
cp inventory/vault/argocd_dex.yml.example inventory/vault/argocd_dex.yml
openssl rand -hex 32                    # generate the shared secret, paste it into the copy above
echo -n 'your-chosen-password' > .vault_pass.txt
ansible-vault encrypt inventory/vault/argocd_dex.yml --vault-password-file .vault_pass.txt
```

Then run the SSO playbook:

```bash
ansible-playbook -i inventory/hosts.yml \
  -e @inventory/vault/argocd_dex.yml \
  --vault-password-file .vault_pass.txt \
  playbooks/argocd-dex-sso.yml
```

**Coordination with `homelab-infra`**: the same generated secret value must
also be sealed into that repo's
`apps/dex-config/manifests/oidc-client-secrets-sealedsecret.yaml` under
`ARGOCD_CLIENT_SECRET` — Dex and ArgoCD must agree on the client secret for
SSO to work. Generate it once here, then seal it there.

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
  - `kubernetes.core` (>= 3.0.0) — used by `playbooks/argocd-dex-sso.yml`;
    also requires the `python3-kubernetes` apt package on the control-plane
    host (installed automatically by that playbook)

## Project Structure

```
.
├── main.yml                      # Main playbook orchestrating all configurations
├── requirements.yml              # Ansible collection dependencies
├── inventory/
│   ├── hosts.yml                 # control_plane / workers / ml groups
│   ├── group_vars/               # cluster-wide + per-group version pins, VIP, CIDR
│   ├── host_vars/                # per-worker vars (e.g. ML workload labeling)
│   └── vault/                    # ansible-vault-encrypted secrets, passed explicitly (not auto-loaded, gitignored — only *.yml.example is tracked)
├── roles/
│   ├── k8s_common/                # swap, kernel modules, containerd, kubeadm/kubelet/kubectl
│   ├── k8s_control_plane/         # kube-vip, kubeadm init, Cilium CNI, ArgoCD install + server.insecure
│   └── k8s_worker/                # kubeadm join
├── files/
│   └── 45-allow-colord.pkla     # Polkit policy for Colord
└── playbooks/
    ├── passwordless-sudo.yml      # One-time: grants NOPASSWD sudo so -K isn't needed on later runs
    ├── raspberry-pi-motd.yml     # Worker MOTD
    ├── control-plane-motd.yml    # Control-plane MOTD
    ├── oh-my-zsh.yml            # ZSH shell setup
    ├── machine-learning.yml      # ML tools and libraries
    ├── argocd-dex-sso.yml        # Patches argocd-cm/argocd-secret for Dex SSO (run on demand, not via main.yml)
    ├── build-autoinstall-iso.yml # Ubuntu autoinstall seed ISO for the control-plane VM
    └── templates/                # user-data / meta-data templates for the above
```

## License

MIT

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.
