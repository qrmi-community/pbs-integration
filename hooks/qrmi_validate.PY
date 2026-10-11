# SPDX-License-Identifier: Apache-2.0
"""PBS hook for validating quantum resource requests for QRMI.
This hook services the following events:
- queuejob  (qsub)
- modifyjob (qalter)

Requests that can never succeed are rejected at submission time, so the job
fails immediately with an error message instead of waiting in the queue:
- unknown resource names, unsupported resource types, unreadable QRMI configuration
- errors from QRMI that do not go away by waiting (for example invalid or revoked
  credentials), detected by checking the status of each requested resource

Temporary problems (the resource is offline or busy, network errors) are not
rejected here; they are handled by the qrmi_check (runjob) hook, which retries.

Each quantum resource has a PBS server-level consumable resource "qrmi_<name>"
(created by install_qrmi_hooks.sh). Its server resources_available value is the
number of slots of the quantum resource (max_slots, or 1 if the resource has no
slots), set by the administrator with qmgr. This hook sets the job's Resource_List.qrmi_<name> from
quantum_resources ("<name>[:<N>]", N slots, 1 by default), so that the PBS
scheduler starts a job only when enough slots are free, and starts
the waiting jobs automatically when running jobs end. Users do not specify
qrmi_<name> themselves.
"""

# pylint: disable=broad-exception-caught,import-outside-toplevel

import os
import re
import sys
import json
import traceback
import pbs

# Directory where QRMI is installed for PBS hooks
# (pip install --target /opt/pbs/python/site-packages "qrmi[ibm]")
QRMI_SITE_PACKAGES = "/opt/pbs/python/site-packages"

# Default PBS_HOME
DEFAULT_PBS_HOME = "/var/spool/pbs"

# Resource types supported by the QRMI hooks.
# Keep this in sync with RES_TYPE_MAP in qrmi_check.PY, qrmi_acquire.PY and qrmi_release.PY.
QRMI_RESOURCE_TYPES = (
    "ibm-quantum-system",
    "ibm-quantum-compute-service",
    "qiskit-runtime-service",
    "pasqal-cloud",
    "pasqal-local",
    "alice-bob-felis",
    "iqm-server",
    "oqtopus",
)

# QRMI errors that do not go away by waiting (qrmi.<name>, subclasses of qrmi.QrmiError),
# with the explanation shown to the user.
# Keep this in sync with qrmi_check.PY.
QRMI_PERMANENT_ERRORS = {
    "AuthenticationFailedError": "authentication failed (invalid, expired or revoked credentials)",
    "ResourceNotFoundError": "the quantum resource was not found",
    "InvalidInputError": "invalid settings",
    "UnsupportedResourceTypeError": "unsupported resource type",
    "ConfigError": "configuration error",
}


# Delimiter between a quantum resource name and its count ("ibm_kingston:2")
QRMI_COUNT_DELIMITER = ":"


class SpecError(Exception):
    """Raised when quantum_resources cannot be parsed"""


def counter_resource(name):
    """Returns the name of the PBS consumable resource counting the concurrent
    accesses to the quantum resource. Keep this in sync with install_qrmi_hooks.sh."""
    return "qrmi_" + re.sub(r"[^A-Za-z0-9_]", "_", name)


def parse_quantum_resources(value):
    """Parses "name[:count],name[:count],..." into {name: count}, keeping the order.
    A name listed more than once gets the largest count."""
    result = {}
    for item in str(value).split(","):
        item = item.strip()
        if not item:
            continue
        name, sep, count_str = item.partition(QRMI_COUNT_DELIMITER)
        name = name.strip()
        if not name:
            raise SpecError(f"'{item}' has no resource name")
        count = 1
        if sep:
            count_str = count_str.strip()
            if not count_str.isdigit() or int(count_str) < 1:
                raise SpecError(
                    f"'{item}': the number of slots must be a positive integer (<name>{QRMI_COUNT_DELIMITER}<N>)"
                )
            count = int(count_str)
        result[name] = max(count, result.get(name, 0))
    return result


def get_res_value(resource_list, res_name):
    """Returns the value of a resource, or None if it is not set or not defined in PBS"""
    try:
        return resource_list[res_name]
    except Exception:
        return None


def warn_capacity_mismatch(name, status, server):
    """Logs a warning if the concurrency limit set by the administrator
    (server resources_available.qrmi_<name>) does not match the capacity reported
    by QRMI: it should be max_slots, or 1 if the resource has no capacity."""
    try:
        res_name = counter_resource(name)
        limit = get_res_value(server.resources_available, res_name)
        if limit is None:
            return
        capacity = getattr(status, "capacity", None)
        if capacity is None:
            max_slots = 1
        elif isinstance(capacity, dict):
            max_slots = capacity.get("max_slots")
        else:
            max_slots = getattr(capacity, "max_slots", None)
        if max_slots is not None and int(limit) not in (0, int(max_slots)):
            pbs.logmsg(
                pbs.LOG_WARNING,
                f"{name}: server resources_available.{res_name} = {int(limit)}, but QRMI reports "
                f"max_slots = {int(max_slots)}{' (no capacity)' if capacity is None else ''}. "
                f"Set it with: qmgr -c \"set server resources_available.{res_name} = {int(max_slots)}\"",
            )
    except Exception as err:
        pbs.logmsg(pbs.LOG_WARNING, f"{name}: cannot compare the capacity: {err}")


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


def build_qrmi_config(name, cfg, variable_list):
    """Returns the configuration passed to QRMI (keys without the "{name}_" prefix):
    the settings in the QRMI configuration, overridden by the job's "{name}_QRMI_*"
    variables (e.g. the user's own API token specified with qsub -v)."""
    config = {k: str(v) for k, v in (cfg or {}).get("environment", {}).items()}
    prefix = f"{name}_"
    for key, value in (variable_list or {}).items():
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

    # qrmi_config.json is located in $PBS_HOME/server_priv (readable by root only)
    cfg_path = os.path.join(get_pbs_home(), "server_priv", "qrmi_config.json")
    try:
        with open(cfg_path, "r", encoding="utf8") as f:
            cfg_json = json.load(f)
        cfg_by_name = {r["name"]: r for r in cfg_json["resources"]}
    except Exception as err:
        if resources is None:
            # Not a QPU request: nothing to check
            e.accept()
        pbs.logmsg(pbs.LOG_ERROR, f"Cannot read {cfg_path}: {err}")
        e.reject(
            "Quantum resources are not available: the QRMI configuration cannot be read. "
            "Contact the administrator."
        )

    try:
        requested = parse_quantum_resources(resources) if resources is not None else {}
    except SpecError as err:
        e.reject(f"Invalid quantum_resources: {err}")

    # The qrmi_<name> counters are set by this hook only. Requesting them directly
    # (e.g. "-l qrmi_ibm_kingston=0") would bypass the limit or use it for nothing.
    for cfg_name in cfg_by_name:
        res_name = counter_resource(cfg_name)
        if cfg_name not in requested and get_res_value(job.Resource_List, res_name) is not None:
            e.reject(
                f"{res_name} is set automatically from quantum_resources. "
                f"Use '-l quantum_resources={cfg_name}{QRMI_COUNT_DELIMITER}<N>' instead."
            )

    if resources is None:
        # This is not QPU job (or quantum_resources is not modified by qalter)
        e.accept()

    if not requested:
        e.reject("quantum_resources is empty. Specify one or more quantum resource names.")
    names = list(requested)

    # qalter: the current request, to reset the counters of removed resources.
    # quantum_resources of a running job cannot be changed: the resources are
    # already acquired.
    old_requested = {}
    if e.type == pbs.MODIFYJOB:
        old_job = pbs.server().job(job.id)
        try:
            old_value = get_res_value(old_job.Resource_List, "quantum_resources")
            old_requested = parse_quantum_resources(old_value) if old_value else {}
        except SpecError:
            old_requested = {}
        if requested != old_requested and old_job.job_state in (
            pbs.JOB_STATE_RUNNING,
            pbs.JOB_STATE_EXITING,
        ):
            e.reject("quantum_resources of a running job cannot be changed.")

    errors = []
    for name in names:
        cfg = cfg_by_name.get(name)
        if cfg is None:
            errors.append(f"'{name}' is not defined")
        elif cfg.get("type") not in QRMI_RESOURCE_TYPES:
            errors.append(f"'{name}' has an unsupported type '{cfg.get('type')}'")

    if errors:
        available = ", ".join(
            sorted(n for n, c in cfg_by_name.items() if c.get("type") in QRMI_RESOURCE_TYPES)
        ) or "(none)"
        msg = (
            f"Invalid quantum_resources: {'; '.join(errors)}. "
            f"Available quantum resources: {available}"
        )
        pbs.logmsg(pbs.LOG_WARNING, f"{job.id if job.id else 'new job'}: {msg}")
        e.reject(msg)

    # Check the requested slots against the limit set by the
    # administrator (server resources_available.qrmi_<name>). A limit of 0 means
    # that the resource is suspended: the job is accepted and waits.
    server = pbs.server()
    for name, count in requested.items():
        res_name = counter_resource(name)
        limit = get_res_value(server.resources_available, res_name)
        if limit is None:
            pbs.logmsg(
                pbs.LOG_ERROR,
                f"{name}: server resources_available.{res_name} is not set. "
                "Run install_qrmi_hooks.sh again.",
            )
            errors.append(f"'{name}' is not set up in PBS")
        elif 0 < int(limit) < count:
            errors.append(
                f"'{name}': {count} slots requested, "
                f"but at most {int(limit)} are allowed"
            )
    if errors:
        msg = f"Invalid quantum_resources: {'; '.join(errors)}. Contact the administrator if needed."
        pbs.logmsg(pbs.LOG_WARNING, f"{job.id if job.id else 'new job'}: {msg}")
        e.reject(msg)

    # Check each resource with QRMI, to reject errors that do not go away by
    # waiting (e.g. invalid credentials) now instead of keeping the job in the queue.
    if QRMI_SITE_PACKAGES not in sys.path:
        sys.path.insert(0, QRMI_SITE_PACKAGES)
    try:
        import qrmi
        from qrmi import QuantumResource, ResourceType
    except Exception as err:
        pbs.logmsg(pbs.LOG_ERROR, f"Cannot import QRMI: {err}")
        e.reject(
            "Quantum resources are not available: QRMI cannot be loaded on the PBS server. "
            "Contact the administrator."
        )

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
    permanent_errors, qrmi_errors = qrmi_error_classes(qrmi)

    for name in names:
        cfg = cfg_by_name[name]
        try:
            qrmi_config = build_qrmi_config(name, cfg, job.Variable_List)
            resource = QuantumResource.from_config(name, RES_TYPE_MAP[cfg["type"]], qrmi_config)
            status = resource.status()
            warn_capacity_mismatch(name, status, server)
        except permanent_errors as err:
            # The details go to the server log only; they may contain settings
            reason = QRMI_PERMANENT_ERRORS.get(type(err).__name__, type(err).__name__)
            pbs.logmsg(pbs.LOG_ERROR, f"{name}: {type(err).__name__}: {err}")
            errors.append(f"'{name}': {reason}")
        except qrmi_errors as err:
            # Temporary QRMI errors (e.g. network errors) do not reject the job;
            # qrmi_check checks the resource again before the job runs.
            pbs.logmsg(
                pbs.LOG_WARNING,
                f"{name}: temporary QRMI error at submission ({type(err).__name__}: {err}); "
                "it is checked again before the job runs",
            )
        except Exception as err:
            # Not a QRMI error: probably a bug or invalid settings. The job is not
            # rejected (it is checked again before it runs), but the error is logged.
            log_unexpected(f"{name}: qrmi_validate", err)

    if errors:
        msg = (
            f"Quantum resources cannot be used: {'; '.join(errors)}. "
            "Check your settings (qsub -v) or contact the administrator."
        )
        pbs.logmsg(pbs.LOG_WARNING, f"{job.id if job.id else 'new job'}: {msg}")
        e.reject(msg)

    # Request the slots from the PBS scheduler. Counters of
    # resources removed by qalter are set to 0.
    for name in old_requested:
        if name not in requested:
            job.Resource_List[counter_resource(name)] = 0
    for name, count in requested.items():
        job.Resource_List[counter_resource(name)] = count

    e.accept()

except Exception as err:
    log_unexpected("qrmi_validate", err)
    e.reject(f"Failed to validate quantum_resources: {err}")
