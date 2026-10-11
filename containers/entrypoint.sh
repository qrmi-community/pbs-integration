#!/bin/bash
# Entrypoint for the OpenPBS cluster
#   PBS_ROLE=server : pbs_server + pbs_sched + pbs_comm (does not run jobs)
#   PBS_ROLE=mom    : pbs_mom only (compute node)
# systemd is not used; the SysV init script /etc/init.d/pbs is called directly
set -uo pipefail

PBS_CONF=/etc/pbs.conf
HOST="$(uname -n)"
ROLE="${PBS_ROLE:-server}"
SERVER_HOST="${PBS_SERVER_HOST:-pbs-server}"
NODES="${PBS_NODES:-}"

log() { echo "[entrypoint:$ROLE] $*"; }

# On failure, do not exit; keep the container running so it can be inspected with podman exec
hold_for_debug() {
    log "ERROR: $*"
    log "Keeping the container running for inspection:  podman exec -it $HOST bash"
    log "--- recent logs ---"
    find /var/spool/pbs -type f \( -name '*.log' -o -path '*_logs/*' \) \
         -newer "$PBS_CONF" -exec tail -n 30 {} + 2>/dev/null
    sleep infinity &
    wait $!
    exit 1
}

set_conf() {  # set_conf KEY VALUE
    if grep -q "^$1=" "$PBS_CONF"; then
        sed -i "s|^$1=.*|$1=$2|" "$PBS_CONF"
    else
        echo "$1=$2" >> "$PBS_CONF"
    fi
}

# --- 1. Configure pbs.conf for the role ---------------------------------
case "$ROLE" in
    server)
        set_conf PBS_SERVER       "$HOST"
        set_conf PBS_START_SERVER 1
        set_conf PBS_START_SCHED  1
        set_conf PBS_START_COMM   1
        set_conf PBS_START_MOM    0
        ;;
    mom)
        set_conf PBS_SERVER       "$SERVER_HOST"
        set_conf PBS_START_SERVER 0
        set_conf PBS_START_SCHED  0
        set_conf PBS_START_COMM   0
        set_conf PBS_START_MOM    1
        ;;
    *)
        hold_for_debug "PBS_ROLE must be 'server' or 'mom' (got: $ROLE)"
        ;;
esac

# shellcheck disable=SC1090
. "$PBS_CONF"
export PATH="$PBS_EXEC/bin:$PBS_EXEC/sbin:$PATH"

log "hostname=$HOST  PBS_SERVER=$PBS_SERVER  PBS_HOME=$PBS_HOME"
log "OpenPBS commit: $(cat "$PBS_EXEC/OPENPBS_COMMIT" 2>/dev/null || echo unknown)"
log "pbs_python: $("$PBS_EXEC/bin/pbs_python" -c 'import sys; print(sys.version.split()[0], sys.executable)' 2>&1 | tail -n 1)"

# Check name resolution (this host, the server, and on the server each compute node)
for h in "$HOST" "$PBS_SERVER" $NODES; do
    getent hosts "$h" >/dev/null || hold_for_debug "cannot resolve hostname: $h"
done

# --- 2. Role-specific preparation ---------------------------------------
if [ "$ROLE" = server ]; then
    # Lock/socket directory for PostgreSQL (PBS datastore)
    install -d -o postgres -g postgres -m 775 /var/run/postgresql
else
    # MOM configuration (overwritten because pbs_postinstall wrote the build-time hostname)
    # $usecp: if the job output destination is under these paths, write it with cp
    #         instead of rcp/ssh (both /shared and /home are shared by all nodes)
    mkdir -p "$PBS_HOME/mom_priv"
    cat > "$PBS_HOME/mom_priv/config" <<EOF
\$clienthost $PBS_SERVER
\$usecp *:/shared/ /shared/
\$usecp *:/home/ /home/
EOF
fi

# --- 3. Shutdown handler (stop PBS cleanly on podman stop) ---------------
stop_pbs() {
    log "stopping PBS ..."
    /etc/init.d/pbs stop
    exit 0
}
trap stop_pbs TERM INT

# --- 4. Start PBS ----------------------------------------------------------
log "starting PBS ..."
/etc/init.d/pbs start || hold_for_debug "/etc/init.d/pbs start failed"

today="$(date +%Y%m%d)"

if [ "$ROLE" = server ]; then
    for _ in $(seq 1 60); do
        qstat -B >/dev/null 2>&1 && break
        sleep 2
    done
    qstat -B >/dev/null 2>&1 || hold_for_debug "pbs_server is not responding"

    # Initial configuration (safe to run on every start)
    qmgr -c "set server scheduling = true"
    qmgr -c "set server flatuid = true"
    # Keep finished jobs visible with qstat -x / qstat -fx
    qmgr -c "set server job_history_enable = true"
    # Make pbsuser a PBS manager (allows qmgr configuration changes without sudo)
    # Added only if not registered yet, to avoid duplicates on restart
    qmgr -c "list server managers" 2>/dev/null | grep -q "pbsuser@" \
        || qmgr -c "set server managers += pbsuser@*"
    for n in $NODES; do
        qmgr -c "list node $n" >/dev/null 2>&1 || qmgr -c "create node $n"
    done

    # Wait a little for the nodes to become free, then show the status (does not block startup)
    for _ in $(seq 1 30); do
        pbsnodes -a 2>/dev/null | grep -q "state = free" && break
        sleep 2
    done
    log "PBS server is up."
    qstat -B
    pbsnodes -aS

    tail -n 0 -F "$PBS_HOME/server_logs/$today" \
                 "$PBS_HOME/sched_logs/$today" \
                 "$PBS_HOME/comm_logs/$today" 2>/dev/null &
else
    sleep 2
    pgrep -x pbs_mom >/dev/null || hold_for_debug "pbs_mom is not running"
    log "pbs_mom is up (server: $PBS_SERVER)."

    tail -n 0 -F "$PBS_HOME/mom_logs/$today" 2>/dev/null &
fi

# --- 5. Follow the logs and wait ------------------------------------------
wait $!
