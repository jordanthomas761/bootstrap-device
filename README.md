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

The Dex OIDC client secret lives ansible-vault-encrypted in
`inventory/vault/argocd_dex.yml`. `inventory/vault/*.yml` is gitignored so a
real secret can never land in git unencrypted by accident; once a file is
actually encrypted it is committed deliberately with `git add -f`, which is
why `argocd_dex.yml` is tracked despite the ignore rule. The
`.yml.example` templates carry no secret and are tracked normally. It's also kept out of `group_vars`/`host_vars` on purpose, so
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

## etcd Encryption at Rest

By default Kubernetes stores Secrets in etcd as plain base64 — anyone with an
etcd snapshot, a backup, or the control-plane disk can read every Secret in the
cluster. `playbooks/etcd-encryption-at-rest.yml` turns on AES-CBC encryption for
Secrets:

```bash
ansible-playbook -i inventory/hosts.yml \
  -e @inventory/vault/etcd_encryption.yml \
  --vault-password-file .vault_pass.txt \
  playbooks/etcd-encryption-at-rest.yml
```

Like the Dex SSO playbook this is deliberately **not** in `main.yml` — it
restarts `kube-apiserver` and rewrites every Secret in the cluster. On this
single-control-plane cluster that restart is a brief full API outage (roughly
15-30s): running workloads keep running, but `kubectl` and ArgoCD will error
for that window.

What it does, in order: installs the 32-byte key from the vault file, writes an
`EncryptionConfiguration`, wires it into the `kube-apiserver` static pod
manifest, restarts the apiserver, rewrites every existing Secret so it actually
gets encrypted, and then verifies the result by reading a canary Secret
*straight out of etcd with `etcdctl`* and asserting the stored bytes carry the
`k8s:enc:aescbc:v1:key1` prefix. Reading it back through the apiserver would
prove nothing, since the apiserver decrypts on read.

The playbook is safe to re-run: it installs the same key from the vault every
time, and the manifest patcher is idempotent. That patcher keeps a
`.pre-encryption` backup of the manifest the first time it changes it, written
to `/etc/kubernetes/` — deliberately **outside** `/etc/kubernetes/manifests/`,
because the kubelet parses every non-hidden file in that directory as a static
pod. A backup left there becomes a second Pod named `kube-apiserver` carrying
the old unencrypted spec, and the kubelet will happily run it instead: the
apiserver comes up with no `--encryption-provider-config`, nothing errors, and
encryption is silently off.

### Where the key lives

The key is **not** generated on the control plane. It is the source of truth in
`inventory/vault/etcd_encryption.yml`, ansible-vault encrypted and tracked in
git, exactly like the Dex client secret above; the playbook pushes it out to
`/etc/kubernetes/enc/key1.b64` (mode 0600).

That is a deliberate reversal of the obvious instinct to keep the key off disk
and out of the repo. Minting it on the control plane means it exists in exactly
one place, so losing that single VM loses every Secret in the cluster with no
way back — a guaranteed total loss traded against a hypothetical one. Storing it
vault-encrypted means an off-host copy is a *property of where the key lives*
rather than a backup job someone has to keep running and remember to check, and
it makes rebuilding a control plane reproducible: the replacement gets the same
key and can read what is already in etcd.

The tradeoff is real and worth stating plainly: anyone holding **both** this
repo and `.vault_pass.txt` can decrypt an etcd snapshot. `.vault_pass.txt` stays
gitignored and out of band in a password manager, which is the whole basis of
that separation — the same trust model already accepted for the Dex secret.

### Provider order, and why `identity` stays

The `EncryptionConfiguration` lists `aescbc` first and `identity` second. The
first provider is the one the apiserver *writes* with; all providers are tried,
in order, on *read*. So this order means new writes are encrypted while anything
still sitting in etcd as plaintext remains readable — which is exactly what makes
it safe to enable on a cluster that already has Secrets in it.

Two ways to get this wrong, both destructive:

- Putting `identity` **first** silently decrypts everything on the next write.
- Removing `identity` **before** every Secret has been rewritten makes the
  not-yet-encrypted ones permanently unreadable.

### Rotating the key

Rotation has to keep the old key readable until everything has been rewritten
under the new one:

1. Add the new key as a **second** entry under `aescbc.keys` — order matters,
   the first key in the list is the one used for writes, later ones are still
   tried on read. Bump `etcd_encryption_key_name` and add the new secret ahead
   of the old one. Note this needs a template change, not just a new value in
   the vault file: overwriting `vault_etcd_encryption_key` in place replaces the
   only key the apiserver knows, which is precisely the failure this sequence
   exists to avoid.
2. Restart `kube-apiserver` so it picks the config up.
3. Rewrite every Secret so they are re-encrypted under the new key:
   `kubectl get secrets --all-namespaces -o json | kubectl replace -f -`
4. Only now drop the old key from the list and restart again.

### What this does and doesn't protect

It protects an etcd snapshot, a backup, or a stolen disk. It does **not**
protect against anyone who can reach the apiserver with cluster-admin — the
apiserver holds the key and decrypts on read. It is a defence against offline
access to the data, not against cluster access.

It also does nothing at all for **availability** — see the next section.
Encryption stops a stolen snapshot being readable; it does not help when the
control plane is simply gone.

## etcd Snapshots

Encryption and snapshots are the two halves of protecting etcd, and they solve
opposite problems. Encryption is confidentiality; snapshots are availability.
On a single-control-plane cluster losing that one VM loses all cluster state,
and a surviving encryption key does not help if there is nothing left to
decrypt.

`playbooks/etcd-snapshot-backup.yml` installs a systemd timer that takes a
verified snapshot and ships it off the host:

```bash
ansible-playbook -i inventory/hosts.yml playbooks/etcd-snapshot-backup.yml
```

Safe and boring to run, unlike the encryption playbook: it writes a script, a
service and a timer, and never touches the apiserver manifest. No API outage.

Three design choices worth knowing, because each one is about the backup still
working when things are broken:

- **It uses `crictl`, not `kubectl`.** `kubectl` needs a healthy apiserver, and
  the moment you most want a snapshot is the moment the apiserver is broken.
  `crictl` talks to containerd directly.
- **It is a systemd timer on the host, not a CronJob in the cluster.** A backup
  that needs the cluster healthy in order to run is not a backup.
- **It verifies before keeping.** `etcdctl snapshot status` parses the file and
  checks its hash, and the script refuses to keep a snapshot reporting zero
  keys. The difference between having a backup and having a file is whether
  anything ever checked.

Remote shipping goes over **NFS, not scp or rsync**. DSM only grants SSH to
accounts in the `administrators` group, so an SSH-based copy would mean parking
a key on the control plane that grants NAS admin — on the machine most exposed
to the cluster, and the same NAS that serves the directory. An NFS export
restricted to this host's IP needs no account and stores no credential at all.

rsync was considered and rejected for this specific job: its advantage is delta
transfer, and every snapshot is a new gzipped file with a fresh timestamped
name, so there is nothing to diff against. It would still need a password in a
secrets file, which is the thing NFS avoids.

Shipping **enables itself only once the export demonstrably works** — the
playbook mounts it and writes a probe file rather than merely checking the port
answers, because "NFS is running" says nothing about whether this host is
allowed in or the export is writable, and both fail silently at 06:00
otherwise. Until it passes, the playbook says so loudly and reports the probe's
exit code (10 = could not mount, 11/12 = mounted but not writable, usually the
export's squash setting).

### Setting up the export on the NAS

In DSM: Control Panel → File Services → NFS → **Enable NFS service**. Then on
the shared folder, Edit → NFS Permissions → Create:

- **Hostname/IP**: the control plane's address only
- **Privilege**: Read/Write
- **Squash**: `No mapping`. This is the setting that catches people — the
  default maps all users to guest, so the snapshot script runs as root and
  cannot write, which surfaces as probe exit 11 or 12 rather than a mount
  failure.

Snapshots are ~3 MB gzipped, so frequency is cheap. Defaults: every 6 hours,
3 kept locally as staging, 28 kept remotely (a week).

Note snapshot size tracks etcd's **file** size, not its live data — a snapshot
copies free pages too. This cluster once sat at 740 MiB `dbSize` against 15 MiB
of real data, which would have made every snapshot 50x larger than necessary.
Check with `etcdctl endpoint status --write-out=json` and compare `dbSize`
against `dbSizeInUse`; if the gap is large, `etcdctl defrag` reclaims it.

### Restoring

Snapshots restore into a *new* data directory — restore never writes over a
live one. To recover a control plane:

1. Get a snapshot: from `/var/backups/etcd/` if the host survives, otherwise
   from the remote copy. Decompress it: `gunzip etcd-<ts>.db.gz`.
2. Stop the apiserver and etcd by moving their manifests out of
   `/etc/kubernetes/manifests/` — **to somewhere outside that directory**. The
   kubelet parses every non-hidden file there, so a manifest "moved aside" but
   left in place still runs.
3. Restore into a fresh directory:
   ```bash
   etcdctl snapshot restore etcd-<ts>.db --data-dir=/var/lib/etcd-restored
   ```
4. Move the old data dir aside and put the restored one at `/var/lib/etcd`.
5. Move the manifests back. The kubelet restarts etcd and the apiserver against
   the restored data.
6. If encryption at rest is on, the restored data is still ciphertext — the
   apiserver needs the same key from `inventory/vault/etcd_encryption.yml` to
   read it. Restoring onto a rebuilt control plane means running the encryption
   playbook with that vault file **before** expecting Secrets to be readable.

You can rehearse step 3 without touching anything live: restore a snapshot to a
scratch `--data-dir` and confirm it produces a `member/` subdirectory. That is
worth doing occasionally — it is the only check that proves a snapshot is
genuinely restorable rather than merely well-formed.
## Longhorn Host Preparation

Longhorn is deployed by GitOps from `homelab-infra` (`apps/longhorn`), but it
depends on things that live below Kubernetes and cannot be expressed as a
manifest. `playbooks/longhorn-prereqs.yml` does those:

```bash
ansible-playbook -i inventory/hosts.yml playbooks/longhorn-prereqs.yml
```

Run it **before** syncing the Longhorn app. Skipping it does not produce a
clear error — the CSI plugin starts happily and volumes simply never attach,
which surfaces as pods stuck in `ContainerCreating` with nothing obvious in the
logs. Safe to re-run; the only service it starts is `iscsid`, and it never
touches the API server.

What it does:

- Installs `open-iscsi` (Longhorn attaches every volume as an iSCSI target on
  the node) and `nfs-common` (needed for ReadWriteMany volumes and an NFS
  backup target — neither in use yet, installed now so enabling either later
  is not a second round of host changes).
- Loads `iscsi_tcp` **and persists it** via `/etc/modules-load.d`. A node that
  silently loses iSCSI across a reboot takes its Longhorn volumes with it.
- Enables and starts `iscsid`.
- **Blacklists Longhorn's devices from `multipathd`** where multipath-tools is
  installed. multipathd claims those block devices out from under Longhorn; the
  symptom is volumes failing to mount or mounting and then corrupting, with
  errors about the device being busy. Only the control plane has the package
  here, but a node that gains it later would otherwise break quietly.
- Labels which nodes may host Longhorn disks.

### Why the control plane holds no data

`longhorn_storage_nodes` in `inventory/group_vars/all.yml` lists only the two
Pis. The control plane has roughly 1 GB of available RAM and 19 GB of disk,
against 400–860 GB of SSD on the Pis, so hosting replicas there would fill the
disk and starve the API server. Longhorn's own components still run on it; only
the storage is kept off.

The node labels drive Longhorn's `createDefaultDiskLabeledNodes` setting. Nodes
that should not hold data are labelled `false` rather than left unlabelled —
both behave the same today, but the explicit value makes the intent legible to
whoever later wonders why the control plane has no disk.

## Kubelet Resource Reservations

The control plane global-OOM'd and the kernel killed `cilium-operator`:

```
kubelet invoked oom-killer: ... global_oom
Out of memory: Killed process 31452 (cilium-operator)
```

`constraint=CONSTRAINT_NONE` means the whole node ran out of memory, not a
container exceeding its own limit. The cause was accounting, not capacity:
memory *requests* on that node totalled 410Mi while real usage was ~2.2 GB, so
the scheduler believed the node was nearly empty. Nothing was reserved for
systemd, sshd, the kubelet or containerd, and no eviction thresholds were set,
so the node had no way to shed load — the kernel's OOM killer acted instead and
picked a victim arbitrarily.

```bash
ansible-playbook -i inventory/hosts.yml playbooks/kubelet-reservations.yml
```

Reservations shrink `allocatable` so the scheduler stops overcommitting, and
eviction thresholds let the kubelet evict a pod *gracefully* before the kernel
kills one at random. Adding RAM alone does not fix this — it only moves the
cliff.

Values live in `inventory/group_vars/all.yml`. All three nodes are ~8 GB / 4
cores, so one set applies everywhere: roughly 1 GB held back plus a 500Mi hard
eviction headroom. Afterwards each node reports allocatable about 1524Mi below
capacity, and 3600m of CPU instead of 4.

Runs one host at a time — restarting every kubelet at once would take all three
NotReady together, and that includes the only control plane. Restarting a
kubelet does not stop running containers.

### The node-name trap

The playbook pins each kubelet to the name it was joined as, via
`--hostname-override` in `/etc/default/kubelet`, and this is **not** optional
housekeeping.

The kubelet defaults to `uname -n` for the name it registers under. Both
workers here were joined as `pi5-gpio` / `pi5-ml`, then had their hostnames
changed to `gpio.k8s.internal` and `ml.k8s.interna`. A *running* kubelet keeps
its original identity, so nothing looked wrong for as long as they stayed up. A
*restarted* one tries to claim the new hostname and is rejected by the node
authorizer, because its client certificate says otherwise:

```
leases.coordination.k8s.io "gpio.k8s.internal" is forbidden:
User "system:node:pi5-gpio" can only access node lease with the same name
as the requesting node
```

The node then goes `NotReady` with *"Kubelet stopped posting node status"*. Any
restart triggers it — this playbook, a package upgrade, a reboot. The override
is set unconditionally, even where the hostname currently matches, so a future
rename cannot reintroduce it.

Note the correct name is read from the kubelet's client certificate CN, not
from the hostname or the inventory — the certificate is the identity it
actually authenticates as.

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
│   └── vault/                    # ansible-vault-encrypted secrets, passed explicitly (not auto-loaded; gitignored by default, encrypted files committed with git add -f)
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
    ├── etcd-encryption-at-rest.yml # Encrypts Secrets at rest in etcd (run on demand, not via main.yml)
    ├── etcd-snapshot-backup.yml  # Installs the verified-snapshot systemd timer (safe to run, no API outage)
    ├── build-autoinstall-iso.yml # Ubuntu autoinstall seed ISO for the control-plane VM
    ├── files/                    # kube-apiserver manifest patcher used by the encryption playbook
    └── templates/                # user-data / meta-data / EncryptionConfiguration templates
```

## License

MIT

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.
