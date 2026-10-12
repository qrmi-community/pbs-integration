# SPDX-License-Identifier: Apache-2.0
"""PBS hook for managing quantum resources via QRMI.
This hook services the following events:
- execjob_begin

Acquires the quantum resources checked by the qrmi_check (runjob) hook, on the
first execution host of the job (mother superior), when the job starts there.
Because the resources are acquired only after the execution host has received
the job, the qrmi_release (execjob_end) hook always runs afterwards and releases
them, even if the job is deleted before it is launched.

The acquisition tokens are saved in a file on the execution host that only root
can read ($PBS_HOME/mom_priv/qrmi/<job id>.json). They are passed to the job by
the qrmi_launch (execjob_launch) hook and used by qrmi_release. They are not
stored in the job attributes.

The settings of the quantum resources (e.g. API keys) are read from the hook
configuration file of this hook, which the PBS server distributes to the
execution hosts:
    qmgr -c "import hook qrmi_acquire application/x-config default qrmi_config.json"
If no hook configuration file is imported, $PBS_HOME/mom_priv/qrmi_config.json
is used.
"""

# pylint: disable=broad-exception-caught,import-outside-toplevel

import os
import sys
import json
import traceback
import pbs

# Prefix of the log messages of this hook ("grep qrmi_acquire" in the PBS logs)
HOOK_NAME = "qrmi_acquire"


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

# Delimiter for list string (default; the value set by qrmi_check is preferred)
QRMI_LIST_DELIMITER = ":"

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


def load_state(path):
    """Returns the saved acquisition state, or an empty state"""
    if os.path.exists(path):
        with open(path, "r", encoding="utf8") as f:
            return json.load(f)
    return {"resources": []}


def save_state(path, state):
    """Saves the acquisition state in a file readable by root only"""
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    tmp_path = f"{path}.tmp"
    fd = os.open(tmp_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf8") as f:
        json.dump(state, f)
    os.replace(tmp_path, path)


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


def unavailable_reason(status, slots):
    """Returns why the quantum resource cannot be used now, or None if it can:
    not online, not healthy, busy, or fewer free slots than requested.
    healthy, busy and capacity are None for resource types that do not support
    them (QRMI >= 0.26.0); status_reason, if reported, is added to the reason.
    ResourceStatusCode is imported from qrmi (only for QPU jobs) before this is called.
    Keep this in sync between qrmi_check.PY and qrmi_acquire.PY."""
    reason_txt = f" (reason: {status.status_reason})" if status.status_reason is not None else ""
    if status.status != ResourceStatusCode.Online:
        # Maintenance, calibration, or other operational activities
        return f"not online{reason_txt}"
    if status.healthy is False:
        return f"not healthy{reason_txt}"
    if status.busy is True:
        return f"busy{reason_txt}"
    capacity = status.capacity  # ResourceCapacity, or None (no slots)
    if capacity is not None and capacity.available_slots < slots:
        return f"{slots} slots requested, {capacity.available_slots} available{reason_txt}"
    return None


class AcquireFailed(Exception):
    """Raised when some quantum resources could not be acquired"""


def release_acquired(acquired):
    """Releases the quantum resources acquired in this hook run"""
    for name, resource, token in acquired:
        try:
            resource.release(token)
            log(pbs.LOG_WARNING, f"{name} released because the job was rejected")
        except Exception as err:
            log(pbs.LOG_ERROR, f"Failed to release {name} (token {token}): {err}")


e = pbs.event()
j = e.job
LOG_JOB = j.id

# Resources acquired in this hook run: (name, QuantumResource, token).
# Defined before "try" so that they can be released on any error.
acquired = []
path = None
state = None

try:
    # execjob_begin runs on every host of the job. Acquire the quantum resources
    # only once, on the primary execution host (mother superior).
    if not j.in_ms_mom():
        e.accept()

    resnames = j.Variable_List.get("QRMI_JOB_QPU_RESOURCES")
    restypes = j.Variable_List.get("QRMI_JOB_QPU_TYPES")
    if not resnames or not restypes:
        # This is not QPU job. Returns immediately
        e.accept()

    # QRMI is imported only for QPU jobs, so that a problem with the QRMI
    # installation does not affect jobs that do not use quantum resources.
    if QRMI_SITE_PACKAGES not in sys.path:
        sys.path.insert(0, QRMI_SITE_PACKAGES)
    import qrmi as qrmi_module
    from qrmi import QuantumResource, ResourceType, ResourceStatusCode

    # Keep this in sync with qrmi_check.PY, qrmi_release.PY and qrmi_validate.PY
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

    delimiter = j.Variable_List.get("QRMI_LIST_DELIMITER") or QRMI_LIST_DELIMITER
    pairs = list(
        dict.fromkeys(zip(resnames.split(delimiter), restypes.split(delimiter)))
    )


    cfg_by_name = load_qrmi_config()

    # Resources already acquired for this job on this host (e.g. if this hook
    # runs again for the same job) are reused instead of being acquired again.
    path = state_path(j.id)
    state = load_state(path)
    already = {r["name"] for r in state["resources"]}

    failures = []
    for name, type_name in pairs:
        if name in already:
            log(LOG_TRACE, f"{name} is already acquired for this job")
            continue

        res_type = RES_TYPE_MAP.get(type_name)
        if res_type is None:
            failures.append(f"{name}: unknown resource type '{type_name}'")
            continue

        try:
            qrmi_config = build_qrmi_config(name, cfg_by_name.get(name), j.Variable_List)
            qrmi = QuantumResource.from_config(name, res_type, qrmi_config)
            # Check the status again: the resource may have become unavailable
            # since qrmi_check checked it on the server. Then the job goes back
            # to the queue instead of acquiring a resource that cannot be used.
            slots = int(j.Variable_List.get(f"{name}_QRMI_JOB_QPU_SLOTS") or 1)
            why = unavailable_reason(qrmi.status(), slots)
            if why is not None:
                log(pbs.LOG_WARNING, f"{name} is unavailable: {why}. The job goes back to the queue.")
                failures.append(f"{name}: {why}")
                continue
            token = qrmi.acquire()
            acquired.append((name, qrmi, token))
            # Save after each acquisition, so that the token is not lost even if
            # this hook is interrupted (e.g. by its alarm)
            state["resources"].append({"name": name, "type": type_name, "token": token})
            save_state(path, state)
            log(LOG_TRACE, f"{name}, {type_name} acquired: {token}")

        except qrmi_module.QrmiError as err:
            # QRMI error, e.g. the resource was taken by another job after
            # qrmi_check confirmed that it was available
            log(pbs.LOG_WARNING, f"{name}: QRMI error at acquisition ({type(err).__name__}: {err})")
            failures.append(f"{name}: {type(err).__name__}: {err}")

        except Exception as err:
            # Not a QRMI error: probably a bug or invalid settings
            log_unexpected(name, err)
            failures.append(f"{name}: unexpected error ({type(err).__name__})")

    if failures:
        # The resource may have become unavailable, or been taken by another job,
        # after qrmi_check confirmed that it was available. Release what was
        # acquired in this run and reject the job: the MOM discards the job and
        # the server puts it back in the queue (execjob_end does not run).
        raise AcquireFailed(f"Failed to acquire quantum resources: {'; '.join(failures)}")

    e.accept()

except Exception as err:
    if isinstance(err, AcquireFailed):
        # The job goes back to the queue and is retried. Unexpected errors among
        # the failures have been logged above as errors.
        log(pbs.LOG_WARNING, f"failed: {err}")
    else:
        log_unexpected(None, err)
    release_acquired(acquired)
    # Remove the released resources from the saved state
    if path is not None and state is not None and acquired:
        released_names = {name for name, _, _ in acquired}
        state["resources"] = [r for r in state["resources"] if r["name"] not in released_names]
        try:
            if state["resources"]:
                save_state(path, state)
            elif os.path.exists(path):
                os.remove(path)
        except Exception as save_err:
            log(pbs.LOG_ERROR, f"Failed to update {path}: {save_err}")
    e.reject(str(err))
