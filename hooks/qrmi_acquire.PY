# SPDX-License-Identifier: Apache-2.0
"""PBS hook for managing quantum resources via QRMI.
This hook services the following events:
- runjob
"""

# pylint: disable=broad-exception-caught

import os
import sys
import json
import pbs

sys.path.insert(0, "/opt/pbs/python/site-packages")

from qrmi import QuantumResource, ResourceType

RES_TYPE_MAP = {
    "quantum-system": ResourceType.IBMQuantumSystem,
    "qiskit-runtime-service": ResourceType.IBMQiskitRuntimeService,
    "pasqal-cloud": ResourceType.PasqalCloud,
    "pasqal-local": ResourceType.PasqalLocal,
    "alice-bob-felis": ResourceType.AliceBobFelis,
    "iqm-server": ResourceType.IQMServer,
}

# Default is 6 hours
DEFAULT_WALLTIME = pbs.duration(21600)

# Default PBS_HOME
DEFAULT_PBS_HOME = "/var/spool/pbs"

# Delimiter for list string
QRMI_LIST_DELIMITER = ":"


def get_pbs_home():
    """Returns PBS_HOME value"""
    pbs_conf = pbs.get_pbs_conf()
    if "PBS_HOME" in pbs_conf:
        return pbs_conf["PBS_HOME"]

    return DEFAULT_PBS_HOME  # fallback

def get_pbs_res(res_name, pbs_job, default_value=None):
    """Returns PBS resource value"""
    if res_name in pbs_job.Resource_List:
        return pbs_job.Resource_List[res_name]

    return default_value


try:
    e = pbs.event()
    job = e.job

    resources = get_pbs_res("quantum_resources", job)
    if resources is None:
        # This is not QPU job. Returns immediately
        e.accept()

    walltime = get_pbs_res("walltime", job, DEFAULT_WALLTIME)

    # We assume that qrmi_config.json is located in the $PBS_HOME/server_priv directory,
    # which is not accessible to regular PBS users.
    cfg_path = os.path.join(get_pbs_home(), "server_priv", "qrmi_config.json")
    with open(cfg_path, "r", encoding="utf8") as f:
        cfg_json = json.load(f)
        cfg_by_name = {r["name"]: r for r in cfg_json["resources"]}

        res_names = []
        res_types = []

        qrmi_qpu_names = [x.strip() for x in resources.split(",")]
        for name in qrmi_qpu_names:
            cfg = cfg_by_name.get(name)
            if cfg is None:
                pbs.logmsg(
                    pbs.LOG_ERROR, f"{name} not found in qrmi_config.json. Ignored."
                )
                continue

            for key, value in cfg["environment"].items():
                env_name = f"{name}_{key}"
                if job.Variable_List.get(env_name) is None:
                    pbs.logmsg(pbs.LOG_DEBUG, f"{env_name} = {value}")
                    job.Variable_List[env_name] = value
                else:
                    # Use the user-specified variables(e.g. API token)
                    # instead of those in the configuration file.
                    pbs.logmsg(
                        pbs.LOG_DEBUG,
                        f"{env_name} = {job.Variable_List[env_name]}",
                    )

                # Set the environment variables required by this pbs_python process
                # to make subsequent QRMI API calls
                os.environ[env_name] = job.Variable_List[env_name]

            job.Variable_List[f"{name}_QRMI_JOB_TIMEOUT_SECONDS"] = str(int(walltime))

            try:
                qrmi = QuantumResource(name, RES_TYPE_MAP.get(cfg["type"]))
                if qrmi.is_accessible() is False:
                    # The quantum resource cannot accept jobs due to maintenance,
                    # calibration, or other operational activities.
                    pbs.logmsg(
                        pbs.LOG_ERROR,
                        f"{name} is unaccessible. Ignored.",
                    )
                    continue

                pbs.logmsg(
                    pbs.EVENT_DEBUG,
                    f"{name} is accessible",
                )
                if job.Variable_List.get(f"{name}_QRMI_JOB_ACQUISITION_TOKEN") is None:
                    token = qrmi.acquire()
                    job.Variable_List[f"{name}_QRMI_JOB_ACQUISITION_TOKEN"] = token
                    pbs.logmsg(
                        pbs.LOG_DEBUG,
                        f"{name}, {cfg['type']} acquired: {token}",
                    )

                # TODO: Check to see if quantum resource has been acquired
                # If not, job should be in pending state.

                # if resource acquisition succeeded, add this resource to verified list
                res_names.append(name)
                res_types.append(cfg["type"])

            except RuntimeError as re:
                pbs.logmsg(
                    pbs.LOG_ERROR,
                    f"Failed to process request in QRMI for {name}: {re}",
                )

        if len(res_names) != len(qrmi_qpu_names):
            names_diff = list(set(qrmi_qpu_names) - set(res_names))
            ERR_TXT = f"Specified quantum resources({names_diff}) are unavailable."
            pbs.logmsg(
                pbs.EVENT_ERROR,
                ERR_TXT,
            )
            e.reject(ERR_TXT)

        job.Variable_List["QRMI_JOB_QPU_RESOURCES"] = QRMI_LIST_DELIMITER.join(
            res_names
        )
        job.Variable_List["QRMI_JOB_QPU_TYPES"] = QRMI_LIST_DELIMITER.join(
            res_types
        )
        job.Variable_List["QRMI_LIST_DELIMITER"] = QRMI_LIST_DELIMITER

        pbs.logmsg(
            pbs.LOG_DEBUG,
            job.Variable_List["QRMI_JOB_QPU_RESOURCES"],
        )
        pbs.logmsg(
            pbs.LOG_DEBUG,
            job.Variable_List["QRMI_JOB_QPU_TYPES"],
        )

    e.accept()

except Exception as err:
    e.reject(str(err))
