#!/usr/bin/env python3
"""Idempotently merge resource reservations into kubelet's config.yaml.

Reads the desired settings as a JSON blob on argv[1] and merges them into
/var/lib/kubelet/config.yaml, leaving every other key kubeadm wrote untouched.

Exit codes are the ansible contract for this script:
  0  already correct, nothing written
  2  config changed and rewritten
  1  refused to touch it (unreadable, unexpected shape)

Writes via a temp file + os.replace so a crash mid-write cannot leave the
kubelet with a truncated config it will refuse to start from.
"""

import json
import os
import shutil
import sys
import tempfile

try:
    import yaml
except ImportError:
    sys.stderr.write("PyYAML not available — install python3-yaml\n")
    sys.exit(1)

CONFIG = "/var/lib/kubelet/config.yaml"
BACKUP = "/var/lib/kubelet/config.yaml.pre-reservations"


def fail(msg):
    sys.stderr.write(msg + "\n")
    sys.exit(1)


def main():
    if len(sys.argv) != 2:
        fail("usage: kubelet-reservations-patch.py '<json settings>'")

    try:
        wanted = json.loads(sys.argv[1])
    except ValueError as exc:
        fail("argv[1] is not valid JSON: %s" % exc)

    if not isinstance(wanted, dict) or not wanted:
        fail("expected a non-empty JSON object of kubelet settings")

    if not os.path.isfile(CONFIG):
        fail("no kubelet config at %s — is this a joined node?" % CONFIG)

    with open(CONFIG) as fh:
        doc = yaml.safe_load(fh)

    # Refuse rather than guess if this is not the file we think it is. A
    # kubelet that fails to parse its config does not start, and a node whose
    # kubelet does not start takes every pod on it down.
    if not isinstance(doc, dict) or doc.get("kind") != "KubeletConfiguration":
        fail("%s is not a KubeletConfiguration — refusing to edit" % CONFIG)

    # evictionSoft is only valid when every signal it names also has a grace
    # period; the kubelet exits at startup otherwise. Checked here rather than
    # trusted from the caller, because the failure lands on a node that then
    # will not come back.
    soft = wanted.get("evictionSoft") or {}
    grace = wanted.get("evictionSoftGracePeriod") or {}
    missing = [k for k in soft if k not in grace]
    if missing:
        fail(
            "evictionSoft signals without an evictionSoftGracePeriod: %s — "
            "the kubelet would refuse to start" % ", ".join(sorted(missing))
        )

    changed = False
    for key, value in wanted.items():
        if doc.get(key) != value:
            doc[key] = value
            changed = True

    if not changed:
        print("kubelet config already has the wanted reservations")
        return 0

    if not os.path.exists(BACKUP):
        shutil.copy2(CONFIG, BACKUP)

    directory = os.path.dirname(CONFIG)
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".kubelet-config-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as fh:
            yaml.safe_dump(doc, fh, default_flow_style=False, sort_keys=False)
        os.chmod(tmp, 0o644)
        os.replace(tmp, CONFIG)
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise

    print("kubelet config updated with reservations")
    return 2


if __name__ == "__main__":
    sys.exit(main())
