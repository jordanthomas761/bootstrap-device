#!/usr/bin/env python3
"""Idempotently wire an EncryptionConfiguration into kubeadm's kube-apiserver
static pod manifest.

Adds three things, each only if absent:
  * the --encryption-provider-config flag on the kube-apiserver command
  * a read-only volumeMount for the directory holding that file
  * a hostPath volume backing it

Exit codes are the ansible contract for this script:
  0  already correct, nothing written
  2  manifest changed and rewritten
  1  refused to touch it (unreadable, unexpected shape)

Writes via a temp file + os.replace so the kubelet, which watches this
directory, never observes a half-written manifest and kills the apiserver.
"""

import os
import shutil
import sys
import tempfile

try:
    import yaml
except ImportError:
    sys.stderr.write(
        "PyYAML not available — install python3-yaml on the control plane\n"
    )
    sys.exit(1)

MANIFEST = "/etc/kubernetes/manifests/kube-apiserver.yaml"
# Deliberately OUTSIDE /etc/kubernetes/manifests. The kubelet treats every
# non-hidden file in that directory as a static pod manifest, so a backup kept
# next to the original is parsed as a SECOND Pod named kube-apiserver -- one
# still carrying the pre-patch, unencrypted spec. The kubelet then flaps
# between the two and can settle on the backup, leaving the apiserver running
# without --encryption-provider-config even though the real manifest is
# correctly patched. (Observed exactly that: the encrypted pod was created,
# then torn down and replaced by one with no encryption-config volume.)
BACKUP = "/etc/kubernetes/kube-apiserver.yaml.pre-encryption"
FLAG = "--encryption-provider-config"
VOLUME_NAME = "encryption-config"


def fail(msg):
    sys.stderr.write(msg + "\n")
    sys.exit(1)


def main():
    if len(sys.argv) != 2:
        fail("usage: kube-apiserver-encryption-patch.py <encryption-config-path>")
    config_path = sys.argv[1]
    mount_dir = os.path.dirname(config_path)

    if not os.path.isfile(MANIFEST):
        fail("no kube-apiserver manifest at %s — is this a control plane?" % MANIFEST)

    with open(MANIFEST) as fh:
        doc = yaml.safe_load(fh)

    if not isinstance(doc, dict) or doc.get("kind") != "Pod":
        fail("%s is not a Pod manifest — refusing to edit" % MANIFEST)

    containers = doc.get("spec", {}).get("containers") or []
    container = next(
        (c for c in containers if c.get("name") == "kube-apiserver"), None
    )
    if container is None:
        fail("no kube-apiserver container in %s — refusing to edit" % MANIFEST)

    changed = False

    # 1. the flag itself. Match on the flag name, not the whole string, so a
    #    run that changes the path corrects it instead of adding a second copy
    #    (the apiserver takes the last occurrence, but two is still wrong).
    command = container.setdefault("command", [])
    wanted_flag = "%s=%s" % (FLAG, config_path)
    existing = [i for i, a in enumerate(command) if a.startswith(FLAG + "=")]
    if not existing:
        # After the binary, so the manifest keeps kubeadm's shape.
        command.insert(1 if command else 0, wanted_flag)
        changed = True
    else:
        for i in existing[1:][::-1]:
            del command[i]
            changed = True
        if command[existing[0]] != wanted_flag:
            command[existing[0]] = wanted_flag
            changed = True

    # 2. the volumeMount, keyed by name so a changed path is corrected in place
    mounts = container.setdefault("volumeMounts", [])
    mount = next((m for m in mounts if m.get("name") == VOLUME_NAME), None)
    wanted_mount = {"mountPath": mount_dir, "name": VOLUME_NAME, "readOnly": True}
    if mount is None:
        mounts.append(wanted_mount)
        changed = True
    elif {k: mount.get(k) for k in wanted_mount} != wanted_mount:
        mount.update(wanted_mount)
        changed = True

    # 3. the hostPath volume behind it
    volumes = doc["spec"].setdefault("volumes", [])
    volume = next((v for v in volumes if v.get("name") == VOLUME_NAME), None)
    wanted_volume = {
        "hostPath": {"path": mount_dir, "type": "DirectoryOrCreate"},
        "name": VOLUME_NAME,
    }
    if volume is None:
        volumes.append(wanted_volume)
        changed = True
    elif volume.get("hostPath") != wanted_volume["hostPath"]:
        volume["hostPath"] = wanted_volume["hostPath"]
        changed = True

    if not changed:
        print("kube-apiserver manifest already references %s" % config_path)
        return 0

    # Keep a copy of whatever was working before this run, outside the
    # kubelet's static pod directory -- see the BACKUP comment above.
    shutil.copy2(MANIFEST, BACKUP)

    # The temp file must share a filesystem with MANIFEST for os.replace to be
    # atomic, so it stays in the manifests directory. Safe because the leading
    # dot makes the kubelet skip it -- unlike the backup above, which had no dot.
    directory = os.path.dirname(MANIFEST)
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".kube-apiserver-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as fh:
            yaml.safe_dump(doc, fh, default_flow_style=False, sort_keys=False)
        os.chmod(tmp, 0o600)
        os.replace(tmp, MANIFEST)
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise

    print("patched kube-apiserver manifest for %s" % config_path)
    return 2


if __name__ == "__main__":
    sys.exit(main())
