# SPDX-License-Identifier: Apache-2.0
"""PBS hook for managing quantum resources via QRMI.
This hook services the following events:
- execjob_end

Releases the quantum resources acquired by the qrmi_acquire (execjob_begin) hook,
on the first execution host of the job (mother superior), when the job ends or
is deleted. The acquisition tokens are read from the file saved by qrmi_acquire
($PBS_HOME/mom_priv/qrmi/<job id>.json), which is removed afterwards.

The settings of the quantum resources (e.g. API keys) are read from the hook
configuration file of this hook, which the PBS server distributes to the
execution hosts:
    qmgr -c "import hook qrmi_release application/x-config default qrmi_config.json"
If no hook configuration file is imported, $PBS_HOME/mom_priv/qrmi_config.json
is used.
"""

# pylint: disable=broad-exception-caught,import-outside-toplevel

import os
import sys
import json
import traceback
import pbs

# Prefix of the log messages of this hook ("grep qrmi_release" in the PBS logs)
HOOK_NAME = "qrmi_release"


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

# Directory where QRMI is installed for PBS hooks
# (pip install --target /opt/pbs/python/site-packages "qrmi[ibm]")
QRMI_SITE_PACKAGES = "/opt/pbs/python/site-packages"

# Default PBS_HOME
DEFAULT_PBS_HOME = "/var/spool/pbs"


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


def build_qrmi_config(name, cfg, variable_list):
    """Returns the configuration passed to QRMI (keys without the "{name}_" prefix):
    the settings in the QRMI configuration, overridden by the job's "{name}_QRMI_*"
    variables (e.g. the user's own API token specified with qsub -v)."""
    config = {k: str(v) for k, v in (cfg or {}).get("environment", {}).items()}
    prefix = f"{name}_"
    for key, value in variable_list.items():
        if key.startswith(f"{prefix}QRMI_"):
            config[key[len(prefix):]] = value
    return config


def state_path(job_id):
    """Returns the path of the file holding the acquisition tokens of the job"""
    return os.path.join(get_pbs_home(), "mom_priv", "qrmi", f"{job_id}.json")


def log_unexpected(context, err):
    """Logs an unexpected (non-QRMI) error with its traceback, on one log line.
    context: e.g. the resource name, or None for the whole hook"""
    tb = traceback.format_exc().strip().replace("\n", " | ")
    prefix = f"{context}: " if context else ""
    log(
        pbs.LOG_ERROR,
        f"{prefix}unexpected error (possibly a bug in the hook or invalid settings): "
        f"{type(err).__name__}: {err} | {tb}",
    )


e = pbs.event()
j = e.job
LOG_JOB = j.id

try:
    # execjob_end runs on every host of the job. The resources are acquired and
    # released only on the primary execution host (mother superior).
    if not j.in_ms_mom():
        e.accept()

    path = state_path(j.id)
    if not os.path.exists(path):
        # This is not QPU job, or no resource was acquired for it
        e.accept()

    with open(path, "r", encoding="utf8") as f:
        state = json.load(f)

    # QRMI is imported only for jobs that acquired quantum resources
    if QRMI_SITE_PACKAGES not in sys.path:
        sys.path.insert(0, QRMI_SITE_PACKAGES)
    import qrmi as qrmi_module
    from qrmi import QuantumResource, ResourceType


    # Keep this in sync with qrmi_check.PY, qrmi_acquire.PY and qrmi_validate.PY
    RES_TYPE_MAP = {
        "ibm-quantum-system": ResourceType.IBMQuantumSystem,
        "ibm-quantum-compute-service": ResourceType.IBMQuantumComputeService,
        "qiskit-runtime-service": ResourceType.IBMQiskitRuntimeService,
        "pasqal-cloud": ResourceType.PasqalCloud,
        "pasqal-local": ResourceType.PasqalLocal,
        "alice-bob-felis": ResourceType.AliceBobFelis,
        "iqm-server": ResourceType.IQMServer,
        "oqtopus": ResourceType.OQTOPUS,
    }

    try:
        cfg_by_name = load_qrmi_config()
    except Exception as err:
        # Continue with the values in the job attributes only
        log(pbs.LOG_ERROR, f"Cannot read the QRMI configuration on this host: {err}")
        cfg_by_name = {}

    released = set()
    for entry in state.get("resources", []):
        name = entry["name"]
        type_name = entry["type"]
        token = entry["token"]

        # Release each resource only once, even if it is listed more than once
        if name in released:
            continue
        released.add(name)

        res_type = RES_TYPE_MAP.get(type_name)
        if res_type is None:
            log(
                pbs.LOG_ERROR,
                f"{name}: unknown resource type '{type_name}'. Not released (token {token}).",
            )
            continue

        try:
            qrmi_config = build_qrmi_config(name, cfg_by_name.get(name), j.Variable_List)
            qrmi_config["QRMI_JOB_ACQUISITION_TOKEN"] = token
            qrmi = QuantumResource.from_config(name, res_type, qrmi_config)
            qrmi.release(token)
            log(LOG_TRACE, f"{name}, {type_name} released = {token}")

        except qrmi_module.QrmiError as err:
            # The remaining resources are still released. The token is logged so
            # that the administrator can release the resource manually.
            log(
                pbs.LOG_ERROR,
                f"{name}, {type_name} release failed (token {token}): {type(err).__name__}: {err}",
            )

        except Exception as err:
            # Not a QRMI error: probably a bug or invalid settings
            log_unexpected(f"{name} (token {token})", err)

    # The job has ended; the tokens are not needed any more
    os.remove(path)

    e.accept()

except Exception as ex:
    log_unexpected(None, ex)
    e.reject(f"execjob_end failed: {ex}")
