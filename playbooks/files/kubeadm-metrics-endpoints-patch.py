#!/usr/bin/env python3
"""Idempotently persist the "expose component metrics" intent into the two
ConfigMaps that `kubeadm upgrade apply` regenerates control-plane config from.

A vanilla `kubeadm init` binds every control-plane component's metrics endpoint
to loopback:

  * kube-controller-manager  --bind-address=127.0.0.1        (:10257)
  * kube-scheduler           --bind-address=127.0.0.1        (:10259)
  * etcd                     --listen-metrics-urls=...127.0.0.1:2381
  * kube-proxy               metricsBindAddress: ""  -> 127.0.0.1:10249

Prometheus runs on a worker, so it cannot reach any of them and
kube-prometheus-stack's kube-controller-manager / kube-scheduler / kube-etcd /
kube-proxy targets are permanently down (with a scary-but-false
etcdInsufficientMembers among the alerts).

This script edits:
  * configmap/kubeadm-config  .data.ClusterConfiguration
      controllerManager.extraArgs  += bind-address=0.0.0.0
      scheduler.extraArgs          += bind-address=0.0.0.0
      etcd.local.extraArgs         += listen-metrics-urls=http://0.0.0.0:2381
  * configmap/kube-proxy      .data["config.conf"]
      metricsBindAddress = 0.0.0.0:10249

so the next `kubeadm upgrade` regenerates the manifests already correct. The
calling playbook flips the same flag in each *live* manifest for immediate
effect and restarts kube-proxy.

Pass --dry-run to report what would change without writing (exit 0 always
unless it would refuse).

Exit codes are the ansible contract for this script:
  0  both ConfigMaps already correct, nothing written
  2  something changed (stdout lists what; "kube-proxy" appears iff that CM changed)
  1  refused (kubectl missing, unreadable, unexpected shape)
"""

import os
import subprocess
import sys

try:
    import yaml
except ImportError:
    sys.stderr.write(
        "PyYAML not available -- install python3-yaml on the control plane\n"
    )
    sys.exit(1)

# Root on the control plane can read admin.conf; allow an override so the script
# is runnable from anywhere with a working kubeconfig for a dry check.
KUBECONFIG = os.environ.get("KUBECONFIG") or "/etc/kubernetes/admin.conf"
NS = "kube-system"

CC_WANT = {
    "controllerManager": ("bind-address", "0.0.0.0"),
    "scheduler": ("bind-address", "0.0.0.0"),
}
ETCD_WANT = ("listen-metrics-urls", "http://0.0.0.0:2381")
PROXY_METRICS_BIND = "0.0.0.0:10249"

DRY_RUN = "--dry-run" in sys.argv[1:]


def fail(msg):
    sys.stderr.write(msg + "\n")
    sys.exit(1)


def kubectl(*args, stdin=None):
    try:
        proc = subprocess.run(
            ["kubectl", "--kubeconfig", KUBECONFIG, *args],
            input=stdin,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        fail("kubectl not found on PATH")
    if proc.returncode != 0:
        fail("kubectl %s failed: %s" % (" ".join(args), proc.stderr.strip()))
    return proc.stdout


def ensure_extra_arg(component, name, value):
    """component is a dict like {'extraArgs': [{'name':..., 'value':...}]}.
    Returns True if it had to add or correct the arg."""
    args = component.setdefault("extraArgs", [])
    for arg in args:
        if arg.get("name") == name:
            if arg.get("value") == value:
                return False
            arg["value"] = value
            return True
    args.append({"name": name, "value": value})
    return True


def patch_kubeadm_config():
    raw = kubectl("-n", NS, "get", "configmap", "kubeadm-config", "-o", "yaml")
    cm = yaml.safe_load(raw)
    cc_text = cm.get("data", {}).get("ClusterConfiguration")
    if not cc_text:
        fail("configmap/kubeadm-config has no ClusterConfiguration key")
    cc = yaml.safe_load(cc_text)
    if not isinstance(cc, dict) or cc.get("kind") != "ClusterConfiguration":
        fail("ClusterConfiguration is not the shape we expected -- refusing to edit")

    changed = []
    for key, (name, value) in CC_WANT.items():
        section = cc.get(key)
        if not isinstance(section, dict):
            section = {}
            cc[key] = section
        if ensure_extra_arg(section, name, value):
            changed.append("kubeadm-config: %s %s=%s" % (key, name, value))

    etcd_local = cc.setdefault("etcd", {}).setdefault("local", {})
    if not isinstance(etcd_local, dict):
        fail("etcd.local is not a mapping -- refusing to edit")
    name, value = ETCD_WANT
    if ensure_extra_arg(etcd_local, name, value):
        changed.append("kubeadm-config: etcd.local %s=%s" % (name, value))

    if not changed:
        return []

    # keep the document's key order stable so the diff stays readable
    if not DRY_RUN:
        cm["data"]["ClusterConfiguration"] = yaml.safe_dump(
            cc, default_flow_style=False, sort_keys=False
        )
        kubectl("replace", "-f", "-", stdin=yaml.safe_dump(cm))
    return changed


def patch_kube_proxy():
    raw = kubectl("-n", NS, "get", "configmap", "kube-proxy", "-o", "yaml")
    cm = yaml.safe_load(raw)
    conf_text = cm.get("data", {}).get("config.conf")
    if not conf_text:
        fail("configmap/kube-proxy has no config.conf key")
    conf = yaml.safe_load(conf_text)
    if not isinstance(conf, dict) or conf.get("kind") != "KubeProxyConfiguration":
        fail("kube-proxy config.conf is not the shape we expected -- refusing to edit")

    if conf.get("metricsBindAddress") == PROXY_METRICS_BIND:
        return []

    if not DRY_RUN:
        conf["metricsBindAddress"] = PROXY_METRICS_BIND
        cm["data"]["config.conf"] = yaml.safe_dump(
            conf, default_flow_style=False, sort_keys=False
        )
        kubectl("replace", "-f", "-", stdin=yaml.safe_dump(cm))
    return ["kube-proxy: metricsBindAddress=%s" % PROXY_METRICS_BIND]


def main():
    changed = patch_kubeadm_config() + patch_kube_proxy()
    if not changed:
        print("kubeadm-config and kube-proxy ConfigMaps already expose metrics")
        return 0
    verb = "would change" if DRY_RUN else "changed"
    for line in changed:
        print("%s: %s" % (verb, line))
    return 0 if DRY_RUN else 2


if __name__ == "__main__":
    sys.exit(main())
