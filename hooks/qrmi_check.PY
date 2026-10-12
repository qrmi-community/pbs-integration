# SPDX-License-Identifier: Apache-2.0
"""PBS hook for managing quantum resources via QRMI.
This hook services the following events:
- runjob

Checks that every quantum resource requested by the job is online, healthy and
not busy (healthy and busy are used only if the resource type reports them),
and, for resources that report their capacity (status.capacity), that at least
the requested number of slots ("<name>:<N>" in quantum_resources, 1 by default)
is available, just before the job is sent to the execution hosts. If a resource is not
available, the job is rejected and goes back to the queue, so it waits there
without using any compute node, and the scheduler retries it later.

If QRMI reports an error that does not go away by waiting (for example invalid
or revoked credentials), the job is put on hold (system hold) instead, with the
reason in its comment, so that it does not wait in the queue forever. After the
problem is fixed, the administrator releases it with "qrls -h s <job id>".

This hook does not acquire the resources. They are acquired on the first
execution host by the qrmi_acquire (execjob_begin) hook, so that they are always
released by the qrmi_release (execjob_end) hook, even if the job is deleted.
"""

# pylint: disable=broad-exception-caught,import-outside-toplevel

import os
import sys
import json
import traceback
import pbs

# Prefix of the log messages of this hook ("grep qrmi_check" in the PBS logs)
HOOK_NAME = "qrmi_check"


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

# Default is 6 hours
DEFAULT_WALLTIME = pbs.duration(21600)

# Default PBS_HOME
DEFAULT_PBS_HOME = "/var/spool/pbs"

# Delimiter for list string
QRMI_LIST_DELIMITER = ":"

# Delimiter between a quantum resource name and its count in quantum_resources
# ("ibm_kingston:2"). Keep this in sync with qrmi_validate.PY.
QRMI_COUNT_DELIMITER = ":"

# Environment variable names containing these words are masked in the logs
SECRET_HINTS = ("TOKEN", "KEY", "SECRET", "PASSWORD", "CREDENTIAL", "AUTH")

# QRMI errors that do not go away by waiting (qrmi.<name>, subclasses of qrmi.QrmiError),
# with the explanation shown to the user.
# Keep this in sync with qrmi_validate.PY.
QRMI_PERMANENT_ERRORS = {
    "AuthenticationFailedError": "authentication failed (invalid, expired or revoked credentials)",
    "ResourceNotFoundError": "the quantum resource was not found",
    "InvalidInputError": "invalid settings",
    "UnsupportedResourceTypeError": "unsupported resource type",
    "ConfigError": "configuration error",
}


def get_pbs_home():
    """Returns PBS_HOME value"""
    pbs_conf = pbs.get_pbs_conf()
    if "PBS_HOME" in pbs_conf:
        return pbs_conf["PBS_HOME"]

    return DEFAULT_PBS_HOME  # fallback


def get_pbs_res(res_name, pbs_job, default_value=None):
    """Returns PBS resource value, or default_value if it is not set"""
    # A resource that is not requested can still be "in" Resource_List with the value None
    if res_name in pbs_job.Resource_List:
        value = pbs_job.Resource_List[res_name]
        if value is not None:
            return value

    return default_value


def parse_quantum_resources(value):
    """Returns {name: slots} from "name[:slots],...", keeping the order. The value
    has been validated by qrmi_validate; malformed counts are treated as 1."""
    result = {}
    for item in str(value).split(","):
        name, _, count_str = item.strip().partition(QRMI_COUNT_DELIMITER)
        name = name.strip()
        if not name:
            continue
        count = int(count_str) if count_str.strip().isdigit() else 1
        result[name] = max(count, 1, result.get(name, 0))
    return result


def masked(name, value):
    """Returns the value for logging, hiding values that look like secrets"""
    if any(hint in name.upper() for hint in SECRET_HINTS):
        return "********"
    return value


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
job = e.job
LOG_JOB = job.id

try:
    resources = get_pbs_res("quantum_resources", job)
    if resources is None:
        # This is not QPU job. Returns immediately
        e.accept()

    # QRMI is imported only for QPU jobs, so that a problem with the QRMI
    # installation does not block jobs that do not use quantum resources.
    if QRMI_SITE_PACKAGES not in sys.path:
        sys.path.insert(0, QRMI_SITE_PACKAGES)
    import qrmi as qrmi_module
    from qrmi import QuantumResource, ResourceType, ResourceStatusCode

    # Keep this in sync with qrmi_acquire.PY, qrmi_release.PY and qrmi_validate.PY
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

    walltime = get_pbs_res("walltime", job, DEFAULT_WALLTIME)

    # We assume that qrmi_config.json is located in the $PBS_HOME/server_priv directory,
    # which is not accessible to regular PBS users.
    cfg_path = os.path.join(get_pbs_home(), "server_priv", "qrmi_config.json")
    with open(cfg_path, "r", encoding="utf8") as f:
        cfg_json = json.load(f)
    cfg_by_name = {r["name"]: r for r in cfg_json["resources"]}

    # QRMI errors that do not go away by waiting (QRMI >= 0.26.0); see QRMI_PERMANENT_ERRORS
    permanent_errors = (
        qrmi_module.AuthenticationFailedError,
        qrmi_module.ResourceNotFoundError,
        qrmi_module.InvalidInputError,
        qrmi_module.UnsupportedResourceTypeError,
        qrmi_module.ConfigError,
    )

    res_names = []
    res_types = []
    # Resources with errors that do not go away by waiting: "<name>: <reason>"
    permanent_failures = []
    # Why the job waits, per resource: {name: reason}. Shown in the job comment
    # (qstat -f), so that the reason is visible without enabling debug logs.
    wait_reasons = {}

    # {name: slots} ("ibm_kingston:2" -> {"ibm_kingston": 2}). Duplicates are
    # merged, keeping the order.
    requested = parse_quantum_resources(resources)
    qrmi_qpu_names = list(requested)
    for name in qrmi_qpu_names:
        cfg = cfg_by_name.get(name)
        if cfg is None:
            # qrmi_config.json was changed after submission: waiting does not help
            log(pbs.LOG_ERROR, f"{name} not found in qrmi_config.json.")
            permanent_failures.append(f"{name}: not defined in the QRMI configuration")
            continue

        res_type = RES_TYPE_MAP.get(cfg.get("type"))
        if res_type is None:
            log(
                pbs.LOG_ERROR,
                f"{name}: unknown resource type '{cfg.get('type')}' in qrmi_config.json.",
            )
            permanent_failures.append(f"{name}: unknown resource type '{cfg.get('type')}'")
            continue

        # The settings (e.g. API keys) are passed to QRMI only. They are NOT written to
        # the job attributes (Variable_List), which users can see with "qstat -f".
        qrmi_config = build_qrmi_config(name, cfg, job.Variable_List)
        for key, value in qrmi_config.items():
            log(LOG_TRACE, f"{name}_{key} = {masked(key, value)}")

        try:
            qrmi = QuantumResource.from_config(name, res_type, qrmi_config)
            log(
                LOG_TRACE,
                f"resource_type = {qrmi.resource_type()}, resource_id={qrmi.resource_id()}",
            )
            status = qrmi.status()
            log(LOG_TRACE, f"status = {json.dumps(status.to_dict(), indent=2)}")
            # ResourceStatus (QRMI >= 0.26.0): status_reason, healthy, busy and
            # capacity are None for resource types that do not support them.
            # status_reason, if reported, is added to the log messages below.
            reason = status.status_reason
            reason_txt = f" (reason: {reason})" if reason is not None else ""

            if status.status != ResourceStatusCode.Online:
                # The quantum resource cannot accept jobs due to maintenance,
                # calibration, or other operational activities.
                log(LOG_TRACE, f"{name} is unaccessible{reason_txt}. The job waits in the queue.")
                wait_reasons[name] = f"not online{reason_txt}"
                continue

            # Online but reported as not healthy: wait. None (not supported) is ignored.
            if status.healthy is False:
                log(LOG_TRACE, f"{name} is not healthy{reason_txt}. The job waits in the queue.")
                wait_reasons[name] = f"not healthy{reason_txt}"
                continue

            # Busy: wait. None (not supported) is ignored.
            if status.busy is True:
                # The quantum resource is online but currently busy.
                log(LOG_TRACE, f"{name} is busy{reason_txt}. The job waits in the queue.")
                wait_reasons[name] = f"busy{reason_txt}"
                continue

            # Resources that report their capacity: the job needs at least the
            # requested number of free slots. The slots may also be used by users
            # outside PBS, so this is checked here, with the current status.
            slots = requested[name]
            capacity = status.capacity  # ResourceCapacity, or None (no slots)
            if capacity is not None:
                available = capacity.available_slots
                if available is not None and int(available) < slots:
                    log(
                        LOG_TRACE,
                        f"{name}: {slots} slots requested, {available} available{reason_txt}. The job waits in the queue.",
                    )
                    wait_reasons[name] = f"{slots} slots requested, {available} available{reason_txt}"
                    continue

            log(LOG_TRACE, f"{name} is accessible")
            res_names.append(name)
            res_types.append(cfg["type"])

        except permanent_errors as err:
            reason = QRMI_PERMANENT_ERRORS.get(type(err).__name__, type(err).__name__)
            log(pbs.LOG_ERROR, f"{name}: {type(err).__name__}: {err}")
            permanent_failures.append(f"{name}: {reason}")

        except qrmi_module.QrmiError as err:
            # Temporary QRMI error (e.g. network): the job waits in the queue. Logged
            # at every scheduling cycle while it lasts, so as a debug message. The
            # error text may contain settings; only its type goes to the comment.
            log(
                LOG_TRACE,
                f"{name}: temporary QRMI error ({type(err).__name__}: {err}); the job is retried later",
            )
            wait_reasons[name] = f"temporary QRMI error ({type(err).__name__})"

        except Exception as err:
            # Not a QRMI error: probably a bug or invalid settings. The job waits in
            # the queue, so that it runs automatically once the problem is fixed.
            log_unexpected(name, err)
            wait_reasons[name] = "unexpected error (see server_logs)"

    # A rejected runjob puts the job back in the queue and the scheduler retries it,
    # so the job waits (without using compute nodes) until the resources become
    # available. Requests that can never succeed are rejected at submission time by
    # the qrmi_validate (queuejob/modifyjob) hook. They reach this point only if
    # something changed after submission (qrmi_config.json edited, credentials
    # revoked). A runjob hook cannot end a job, so such jobs are held instead of
    # being retried forever.
    if permanent_failures:
        # Waiting does not help: hold the job instead of retrying it forever.
        job.Hold_Types = pbs.hold_types("s")
        ERR_TXT = (
            f"Held: quantum resources cannot be used ({'; '.join(permanent_failures)}). "
            f"After fixing the problem, release the job with 'qrls -h s {job.id}'."
        )
        log(pbs.LOG_ERROR, ERR_TXT)
        e.reject(ERR_TXT)

    missing = [n for n in qrmi_qpu_names if n not in res_names]
    if missing:
        # The reject message becomes the job comment (qstat -f)
        ERR_TXT = "Waiting for quantum resources: " + "; ".join(
            f"{n}: {wait_reasons.get(n, 'unavailable')}" for n in missing
        )
        log(LOG_TRACE, ERR_TXT)
        e.reject(ERR_TXT)

    # Information for the hooks on the execution hosts and for the job (no secrets)
    # {name}_QRMI_* variables are passed to QRMI by qrmi_acquire and qrmi_release
    # (QRMI_JOB_QPU_SLOTS: the number of slots acquired), and are also in the job
    # environment.
    for name in res_names:
        job.Variable_List[f"{name}_QRMI_JOB_TIMEOUT_SECONDS"] = str(int(walltime))
        job.Variable_List[f"{name}_QRMI_JOB_QPU_SLOTS"] = str(requested[name])
    job.Variable_List["QRMI_JOB_QPU_RESOURCES"] = QRMI_LIST_DELIMITER.join(res_names)
    job.Variable_List["QRMI_JOB_QPU_TYPES"] = QRMI_LIST_DELIMITER.join(res_types)
    job.Variable_List["QRMI_LIST_DELIMITER"] = QRMI_LIST_DELIMITER

    log(LOG_TRACE, job.Variable_List["QRMI_JOB_QPU_RESOURCES"])
    log(LOG_TRACE, job.Variable_List["QRMI_JOB_QPU_TYPES"])

    e.accept()

except Exception as err:
    log_unexpected(None, err)
    e.reject(str(err))
