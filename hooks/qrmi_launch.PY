# SPDX-License-Identifier: Apache-2.0
"""PBS hook for managing quantum resources via QRMI.
This hook services the following events:
- execjob_launch

Adds the settings of the quantum resources (e.g. endpoints and API keys) from
the QRMI configuration, and the acquisition tokens saved by the qrmi_acquire
(execjob_begin) hook, to the environment of the job processes, just before
they are launched on the execution host.

The settings are passed only to the launched processes and are not stored in
the job attributes (Variable_List), so they do not appear in "qstat -f", the
PBS datastore or the job history.

The QRMI configuration is read from the hook configuration file of this hook,
which the PBS server distributes to the execution hosts:
    qmgr -c "import hook qrmi_launch application/x-config default qrmi_config.json"
If no hook configuration file is imported, $PBS_HOME/mom_priv/qrmi_config.json
is used.
"""

# pylint: disable=broad-exception-caught

import os
import json
import pbs

# Prefix of the log messages of this hook ("grep qrmi_launch" in the PBS logs)
HOOK_NAME = "qrmi_launch"


# Log levels used by the QRMI hooks:
#   pbs.LOG_ERROR   -- the administrator must act: a bug, broken settings, a held
#                      job, or quantum resources that may remain acquired
#   pbs.LOG_WARNING -- a request was rejected, or the job is retried (once per event)
#   LOG_TRACE       -- normal progress, and the reasons why a job waits (repeated at
#                      every scheduling cycle while it waits)
# pbs.LOG_ERROR, pbs.LOG_WARNING and pbs.LOG_DEBUG are all logged with event class
# 0x0006 (always logged); they only differ in the syslog severity. LOG_TRACE uses
# event class 0x0400 (pbs.EVENT_DEBUG3), which is not logged by default. To see it:
#   server: qmgr -c "set server log_events = 1535"   (default 511)
#   MOM:    $logevent 0x4ff in mom_priv/config, then SIGHUP pbs_mom
#           (do not enable 0x0100: pbs_mom logs the job environment with it)
LOG_TRACE = pbs.EVENT_DEBUG3

# Job shown in the log messages (set after the event is read)
LOG_JOB = None


def log(level, msg):
    """Writes a message to the PBS log (server_logs or mom_logs), prefixed with the
    hook name and the job"""
    prefix = f"{HOOK_NAME}: {LOG_JOB}: " if LOG_JOB else f"{HOOK_NAME}: "
    pbs.logmsg(level, prefix + msg)

# Default PBS_HOME
DEFAULT_PBS_HOME = "/var/spool/pbs"

# Delimiter for list string (default; the value set by qrmi_acquire is preferred)
QRMI_LIST_DELIMITER = ":"


def get_pbs_home():
    """Returns PBS_HOME value"""
    pbs_conf = pbs.get_pbs_conf()
    if "PBS_HOME" in pbs_conf:
        return pbs_conf["PBS_HOME"]

    return DEFAULT_PBS_HOME  # fallback


def load_qrmi_config():
    """Returns the QRMI configuration as {resource name: resource config}"""
    cfg_path = getattr(pbs, "hook_config_filename", None)
    if not cfg_path:
        cfg_path = os.path.join(get_pbs_home(), "mom_priv", "qrmi_config.json")
    with open(cfg_path, "r", encoding="utf8") as f:
        cfg_json = json.load(f)
    return {r["name"]: r for r in cfg_json["resources"]}


def state_path(job_id):
    """Returns the path of the file holding the acquisition tokens of the job"""
    return os.path.join(get_pbs_home(), "mom_priv", "qrmi", f"{job_id}.json")


e = pbs.event()
j = e.job
LOG_JOB = j.id

try:
    resnames = j.Variable_List.get("QRMI_JOB_QPU_RESOURCES")
    if not resnames:
        # This is not QPU job. Returns immediately
        e.accept()

    delimiter = j.Variable_List.get("QRMI_LIST_DELIMITER") or QRMI_LIST_DELIMITER
    names = list(dict.fromkeys(n for n in resnames.split(delimiter) if n))

    cfg_by_name = load_qrmi_config()

    for name in names:
        cfg = cfg_by_name.get(name)
        if cfg is None:
            log(pbs.LOG_ERROR, f"{name} not found in the QRMI configuration on this host.")
            continue

        added = []
        for key, value in cfg.get("environment", {}).items():
            env_name = f"{name}_{key}"
            if j.Variable_List.get(env_name) is not None:
                # The user specified this variable (e.g. the user's own API token);
                # it is already in the job's environment. Do not overwrite it.
                continue
            e.env[env_name] = str(value)
            added.append(env_name)

        # Log only the names, never the values (they may contain API keys)
        log(LOG_TRACE, f"{name}: added to the job environment: {', '.join(added)}")

    # Acquisition tokens saved by qrmi_acquire. The file exists only on the first
    # execution host of the job, where the job script runs.
    path = state_path(j.id)
    if os.path.exists(path):
        with open(path, "r", encoding="utf8") as f:
            state = json.load(f)
        for entry in state.get("resources", []):
            e.env[f"{entry['name']}_QRMI_JOB_ACQUISITION_TOKEN"] = entry["token"]
        log(LOG_TRACE, "acquisition tokens added to the job environment")

    e.accept()

except Exception as err:
    # Do not stop the job here. The job runs without the settings, and the
    # QRMI calls in the job fail with an error the user can see.
    log(pbs.LOG_ERROR, f"failed: {err}")
    e.accept()
