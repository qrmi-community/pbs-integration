#!/bin/bash
# Registers the QRMI hooks and their configuration on the PBS server.
# Run as root on the PBS server host (e.g. podman exec pbs-server bash /shared/qrmi-hooks/install_qrmi_hooks.sh).
# Safe to run again: existing hooks are updated.
#
#   HOOK_DIR     directory containing the *.PY hook files (default: the directory of this script)
#   QRMI_CONFIG  QRMI configuration file (default: $PBS_HOME/server_priv/qrmi_config.json)
#   QRMI_ALARM   timeout in seconds for the hooks that call QRMI (default: 120)
set -euo pipefail

. /etc/pbs.conf
HOOK_DIR="${HOOK_DIR:-$(cd "$(dirname "$0")" && pwd)}"
QRMI_CONFIG="${QRMI_CONFIG:-$PBS_HOME/server_priv/qrmi_config.json}"
QRMI_ALARM="${QRMI_ALARM:-120}"
QMGR="$PBS_EXEC/bin/qmgr"

[ -f "$QRMI_CONFIG" ] || { echo "ERROR: $QRMI_CONFIG not found" >&2; exit 1; }

# The configuration contains API keys: readable by root only
if [ "$QRMI_CONFIG" = "$PBS_HOME/server_priv/qrmi_config.json" ]; then
    chown root:root "$QRMI_CONFIG"
    chmod 600 "$QRMI_CONFIG"
else
    install -o root -g root -m 600 "$QRMI_CONFIG" "$PBS_HOME/server_priv/qrmi_config.json"
fi

# Custom resource for "qsub -l quantum_resources=..."
"$QMGR" -c "list resource quantum_resources" >/dev/null 2>&1 \
    || "$QMGR" -c "create resource quantum_resources type=string"

# One server-level consumable resource per quantum resource, counting its
# slots in use: qrmi_<name> (characters other than [A-Za-z0-9_] become "_").
# Keep the naming in sync with counter_resource() in qrmi_validate.PY.
# qrmi_validate sets the job's qrmi_<name> from "quantum_resources=<name>[:<N>]",
# and the scheduler runs a job only while enough slots are free.
# The limit should be max_slots of the resource (1 if it has no slots). It is set
# to 1 only if it is not set yet; change it with qmgr, e.g.
#   qmgr -c "set server resources_available.qrmi_ibm_kingston = 4"
SCHED_CONFIG="$PBS_HOME/sched_priv/sched_config"
COUNTERS=$(python3 -c '
import json, re, sys
for r in json.load(open(sys.argv[1]))["resources"]:
    print("qrmi_" + re.sub(r"[^A-Za-z0-9_]", "_", r["name"]))
' "$PBS_HOME/server_priv/qrmi_config.json")
sched_changed=false
for res in $COUNTERS; do
    "$QMGR" -c "list resource $res" >/dev/null 2>&1 \
        || "$QMGR" -c "create resource $res type=long, flag=q"
    if ! "$QMGR" -c "list server resources_available.$res" 2>/dev/null | grep -q "resources_available.$res ="; then
        "$QMGR" -c "set server resources_available.$res = 1"
    fi
    # The scheduler only enforces resources listed in the "resources:" line
    if ! grep -E '^resources:' "$SCHED_CONFIG" | grep -qw "$res"; then
        sed -i -E "s/^(resources:[[:space:]]*\".*)\"/\1, $res\"/" "$SCHED_CONFIG"
        sched_changed=true
    fi
    echo "concurrency counter: $res = $("$QMGR" -c "list server resources_available.$res" | awk -F'= ' '/resources_available/ {print $2}')"
done
if $sched_changed; then
    # Reload sched_config
    pkill -HUP -x pbs_sched 2>/dev/null || kill -HUP "$(cat "$PBS_HOME/sched_priv/sched.lock")"
fi

# Do not show other users' jobs in "qstat -f"
"$QMGR" -c "set server query_other_jobs = false"

register_hook() {  # register_hook NAME EVENTS
    local name=$1 events=$2
    "$QMGR" -c "list hook $name" >/dev/null 2>&1 || "$QMGR" -c "create hook $name"
    "$QMGR" -c "set hook $name event = \"$events\""
    "$QMGR" -c "import hook $name application/x-python default $HOOK_DIR/$name.PY"
    "$QMGR" -c "set hook $name enabled = true"
    echo "registered hook: $name ($events)"
}

register_hook qrmi_validate "queuejob,modifyjob"   # server: reject invalid requests at qsub/qalter
register_hook qrmi_check    "runjob"               # server: wait in the queue until the resources are available
register_hook qrmi_acquire  "execjob_begin"        # first execution host: acquire the resources
register_hook qrmi_launch   "execjob_launch"       # execution hosts: pass settings and tokens to the job
register_hook qrmi_release  "execjob_end"          # first execution host: release the resources

# The hooks on the execution hosts read the QRMI configuration from their hook
# configuration files, which the server distributes to the execution hosts.
for name in qrmi_acquire qrmi_launch qrmi_release; do
    "$QMGR" -c "import hook $name application/x-config default $PBS_HOME/server_priv/qrmi_config.json"
done

# The hooks that call the QRMI APIs may take longer than the default (30 seconds)
for name in qrmi_validate qrmi_check qrmi_acquire qrmi_release; do
    "$QMGR" -c "set hook $name alarm = $QRMI_ALARM"
done

echo
echo "Done. Restart the PBS server so that the server hooks are loaded cleanly:"
echo "  podman compose restart pbs-server"
echo "When qrmi_config.json is changed, run this script again."
