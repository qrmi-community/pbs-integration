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


def get_field(obj, key):
    """Returns obj.key or obj[key], or None"""
    if obj is None:
        return None
    if isinstance(obj, dict):
        return obj.get(key)
    return getattr(obj, key, None)


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


def qrmi_error_classes(qrmi_module):
    """Returns (permanent, qrmi_errors):
    permanent   -- QRMI exception classes for errors that do not go away by waiting
                   (qrmi.AuthenticationFailedError, ...)
    qrmi_errors -- all QRMI errors (qrmi.QrmiError, the base class of the above)
    Classes that the installed QRMI version does not provide are skipped and logged."""

    def is_exception(cls):
        return isinstance(cls, type) and issubclass(cls, BaseException)

    base = getattr(qrmi_module, "QrmiError", None)
    permanent = []
    missing = []
    for class_name in QRMI_PERMANENT_ERRORS:
        cls = getattr(qrmi_module, class_name, None)
        if is_exception(cls):
            permanent.append(cls)
        else:
            missing.append(class_name)
    if missing:
        pbs.logmsg(
            pbs.LOG_WARNING,
            f"QRMI does not provide {', '.join(missing)}; these errors are treated as temporary",
        )
    qrmi_errors = (base,) if is_exception(base) else tuple(permanent)
    return tuple(permanent), qrmi_errors


def log_unexpected(context, err):
    """Logs an unexpected (non-QRMI) error with its traceback, on one log line"""
    tb = traceback.format_exc().strip().replace("\n", " | ")
    pbs.logmsg(
        pbs.LOG_ERROR,
        f"{context}: unexpected error (possibly a bug in the hook or invalid settings): "
        f"{type(err).__name__}: {err} | {tb}",
    )


e = pbs.event()
job = e.job

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

    permanent_errors, qrmi_errors = qrmi_error_classes(qrmi_module)

    res_names = []
    res_types = []
    # Resources with errors that do not go away by waiting: "<name>: <reason>"
    permanent_failures = []

    # {name: slots} ("ibm_kingston:2" -> {"ibm_kingston": 2}). Duplicates are
    # merged, keeping the order.
    requested = parse_quantum_resources(resources)
    qrmi_qpu_names = list(requested)
    for name in qrmi_qpu_names:
        cfg = cfg_by_name.get(name)
        if cfg is None:
            pbs.logmsg(pbs.LOG_ERROR, f"{name} not found in qrmi_config.json. Ignored.")
            continue

        res_type = RES_TYPE_MAP.get(cfg.get("type"))
        if res_type is None:
            pbs.logmsg(
                pbs.LOG_ERROR,
                f"{name}: unknown resource type '{cfg.get('type')}' in qrmi_config.json. Ignored.",
            )
            continue

        # The settings (e.g. API keys) are passed to QRMI only. They are NOT written to
        # the job attributes (Variable_List), which users can see with "qstat -f".
        qrmi_config = build_qrmi_config(name, cfg, job.Variable_List)
        for key, value in qrmi_config.items():
            pbs.logmsg(pbs.LOG_DEBUG, f"{name}_{key} = {masked(key, value)}")

        try:
            qrmi = QuantumResource.from_config(name, res_type, qrmi_config)
            pbs.logmsg(
                pbs.LOG_DEBUG,
                f"resource_type = {qrmi.resource_type()}, resource_id={qrmi.resource_id()}",
            )
            status = qrmi.status()
            pbs.logmsg(pbs.LOG_DEBUG, f"status = {json.dumps(status.to_dict(), indent=2)}")
            # Optional fields: resource types that do not support them return None.
            # status_reason, if reported, is added to the log messages below.
            reason = getattr(status, "status_reason", None)
            reason_txt = f" (reason: {reason})" if reason is not None else ""

            if status.status != ResourceStatusCode.Online:
                # The quantum resource cannot accept jobs due to maintenance,
                # calibration, or other operational activities.
                pbs.logmsg(pbs.LOG_ERROR, f"{name} is unaccessible{reason_txt}. Ignored.")
                continue

            # Online but reported as not healthy: wait. None (not supported) is ignored.
            if getattr(status, "healthy", None) is False:
                pbs.logmsg(pbs.LOG_ERROR, f"{name} is not healthy{reason_txt}. Ignored.")
                continue

            # Some resource types do not report "busy" (None, or no attribute at all).
            # In that case only the checks above are used.
            if getattr(status, "busy", None) is True:
                # The quantum resource is online but currently busy.
                pbs.logmsg(pbs.LOG_ERROR, f"{name} is busy{reason_txt}. Ignored.")
                continue

            # Resources that report their capacity: the job needs at least the
            # requested number of free slots. The slots may also be used by users
            # outside PBS, so this is checked here, with the current status.
            slots = requested[name]
            capacity = getattr(status, "capacity", None)
            if capacity is not None:
                available = get_field(capacity, "available_slots")
                if available is not None and int(available) < slots:
                    pbs.logmsg(
                        pbs.LOG_ERROR,
                        f"{name}: {slots} slots requested, {available} available{reason_txt}. Ignored.",
                    )
                    continue

            pbs.logmsg(pbs.LOG_DEBUG, f"{name} is accessible")
            res_names.append(name)
            res_types.append(cfg["type"])

        except permanent_errors as err:
            reason = QRMI_PERMANENT_ERRORS.get(type(err).__name__, type(err).__name__)
            pbs.logmsg(pbs.LOG_ERROR, f"{name}: {type(err).__name__}: {err}")
            permanent_failures.append(f"{name}: {reason}")

        except qrmi_errors as err:
            # Temporary QRMI error (e.g. network): the job waits in the queue
            pbs.logmsg(
                pbs.LOG_WARNING,
                f"{name}: temporary QRMI error ({type(err).__name__}: {err}); the job is retried later",
            )

        except Exception as err:
            # Not a QRMI error: probably a bug or invalid settings. The job waits in
            # the queue, so that it runs automatically once the problem is fixed.
            log_unexpected(f"{name}: qrmi_check", err)

    # A rejected runjob puts the job back in the queue and the scheduler retries it,
    # so the job waits (without using compute nodes) until the resources become
    # available. Requests that can never succeed (unknown names or types) are
    # rejected at submission time by the qrmi_validate (queuejob/modifyjob) hook;
    # they reach this point only if qrmi_config.json was changed after submission.
    if permanent_failures:
        # Waiting does not help: hold the job instead of retrying it forever.
        job.Hold_Types = pbs.hold_types("s")
        ERR_TXT = (
            f"Held: quantum resources cannot be used ({'; '.join(permanent_failures)}). "
            f"After fixing the problem, release the job with 'qrls -h s {job.id}'."
        )
        pbs.logmsg(pbs.LOG_ERROR, ERR_TXT)
        e.reject(ERR_TXT)

    missing = [n for n in qrmi_qpu_names if n not in res_names]
    if missing:
        ERR_TXT = f"Specified quantum resources({missing}) are unavailable."
        pbs.logmsg(pbs.LOG_ERROR, ERR_TXT)
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

    pbs.logmsg(pbs.LOG_DEBUG, job.Variable_List["QRMI_JOB_QPU_RESOURCES"])
    pbs.logmsg(pbs.LOG_DEBUG, job.Variable_List["QRMI_JOB_QPU_TYPES"])

    e.accept()

except Exception as err:
    log_unexpected("qrmi_check", err)
    e.reject(str(err))
