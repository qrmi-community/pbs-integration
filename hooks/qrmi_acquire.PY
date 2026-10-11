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


def qrmi_base_errors(qrmi_module):
    """Returns the QRMI error classes (qrmi.QrmiError, the base class of all QRMI errors)"""
    base = getattr(qrmi_module, "QrmiError", None)
    if isinstance(base, type) and issubclass(base, BaseException):
        return (base,)
    pbs.logmsg(pbs.LOG_WARNING, "qrmi.QrmiError not found; QRMI errors are logged as unexpected errors")
    return ()


def log_unexpected(context, err):
    """Logs an unexpected (non-QRMI) error with its traceback, on one log line"""
    tb = traceback.format_exc().strip().replace("\n", " | ")
    pbs.logmsg(
        pbs.LOG_ERROR,
        f"{context}: unexpected error (possibly a bug in the hook or invalid settings): "
        f"{type(err).__name__}: {err} | {tb}",
    )


class AcquireFailed(Exception):
    """Raised when some quantum resources could not be acquired"""


def release_acquired(acquired):
    """Releases the quantum resources acquired in this hook run"""
    for name, resource, token in acquired:
        try:
            resource.release(token)
            pbs.logmsg(pbs.LOG_WARNING, f"{name} released because the job was rejected")
        except Exception as err:
            pbs.logmsg(pbs.LOG_ERROR, f"Failed to release {name} (token {token}): {err}")


e = pbs.event()
j = e.job

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
    from qrmi import QuantumResource, ResourceType

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

    qrmi_errors = qrmi_base_errors(qrmi_module)

    cfg_by_name = load_qrmi_config()

    # Resources already acquired for this job on this host (e.g. if this hook
    # runs again for the same job) are reused instead of being acquired again.
    path = state_path(j.id)
    state = load_state(path)
    already = {r["name"] for r in state["resources"]}

    failures = []
    for name, type_name in pairs:
        if name in already:
            pbs.logmsg(pbs.LOG_DEBUG, f"{name} is already acquired for this job")
            continue

        res_type = RES_TYPE_MAP.get(type_name)
        if res_type is None:
            failures.append(f"{name}: unknown resource type '{type_name}'")
            continue

        try:
            qrmi_config = build_qrmi_config(name, cfg_by_name.get(name), j.Variable_List)
            qrmi = QuantumResource.from_config(name, res_type, qrmi_config)
            token = qrmi.acquire()
            acquired.append((name, qrmi, token))
            # Save after each acquisition, so that the token is not lost even if
            # this hook is interrupted (e.g. by its alarm)
            state["resources"].append({"name": name, "type": type_name, "token": token})
            save_state(path, state)
            pbs.logmsg(pbs.LOG_DEBUG, f"{name}, {type_name} acquired: {token}")

        except qrmi_errors as err:
            # QRMI error, e.g. the resource was taken by another job after
            # qrmi_check confirmed that it was available
            pbs.logmsg(pbs.LOG_WARNING, f"{name}: QRMI error at acquisition ({type(err).__name__}: {err})")
            failures.append(f"{name}: {type(err).__name__}: {err}")

        except Exception as err:
            # Not a QRMI error: probably a bug or invalid settings
            log_unexpected(f"{name}: qrmi_acquire", err)
            failures.append(f"{name}: unexpected error ({type(err).__name__})")

    if failures:
        # The resource may have been taken by another job after qrmi_check
        # confirmed that it was available. Release what was acquired in this run
        # and reject the job.
        raise AcquireFailed(f"Failed to acquire quantum resources: {'; '.join(failures)}")

    e.accept()

except Exception as err:
    if isinstance(err, AcquireFailed):
        pbs.logmsg(pbs.LOG_ERROR, f"qrmi_acquire failed: {err}")
    else:
        log_unexpected("qrmi_acquire", err)
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
            pbs.logmsg(pbs.LOG_ERROR, f"Failed to update {path}: {save_err}")
    e.reject(str(err))
