#!/usr/bin/env python3
"""Idempotently set OIDC flags on kubeadm's kube-apiserver static pod manifest.

Takes flags as arguments, e.g.
  kube-apiserver-oidc-patch.py --oidc-issuer-url=https://dex.example --oidc-client-id=x

Every --oidc-* flag currently on the command line that is NOT in the wanted
set is removed, and each wanted flag is added or corrected by name, so the
manifest ends up with exactly the OIDC flags passed and no duplicates.

Exit codes are the ansible contract for this script:
  0  already correct, nothing written
  2  manifest changed and rewritten
  1  refused to touch it (unreadable, unexpected shape, bad arguments)

Writes via a temp file + os.replace so the kubelet, which watches the
manifests directory, never observes a half-written manifest. The backup lives
OUTSIDE that directory for the reason documented in
kube-apiserver-encryption-patch.py.
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
BACKUP = "/etc/kubernetes/kube-apiserver.yaml.pre-oidc"
PREFIX = "--oidc-"


def fail(msg):
    sys.stderr.write(msg + "\n")
    sys.exit(1)


def main():
    wanted = sys.argv[1:]
    if not wanted:
        fail("usage: kube-apiserver-oidc-patch.py --oidc-flag=value ...")
    for w in wanted:
        if not w.startswith(PREFIX) or "=" not in w:
            fail("refusing %r: expected --oidc-<name>=<value>" % w)
    wanted_by_name = {w.split("=", 1)[0]: w for w in wanted}

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

    command = container.get("command") or []
    # Keep the binary and every non-OIDC flag exactly as they were, in order.
    rest = [a for a in command if not a.startswith(PREFIX)]
    current = [a for a in command if a.startswith(PREFIX)]
    desired = [wanted_by_name[n] for n in sorted(wanted_by_name)]
    if sorted(current) == sorted(desired):
        print("kube-apiserver manifest already has the wanted OIDC flags")
        return 0

    container["command"] = rest + desired

    shutil.copy2(MANIFEST, BACKUP)

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

    print("patched kube-apiserver manifest with %d OIDC flags" % len(desired))
    return 2


if __name__ == "__main__":
    sys.exit(main())
