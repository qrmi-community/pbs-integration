# SPDX-License-Identifier: Apache-2.0
"""PBS hook for managing quantum resources via QRMI.
This hook services the following events:
- execjob_begin
"""

# pylint: disable=broad-exception-caught

import os
import sys
import json
import pbs

sys.path.insert(0, "/opt/pbs/python/site-packages")

from qrmi import QuantumResource, ResourceType

RES_TYPE_MAP = {
    "direct-access": ResourceType.IBMQiskitRuntimeService,
    "quantum-system": ResourceType.IBMQiskitRuntimeService,
    "qiskit-runtime-service": ResourceType.IBMQiskitRuntimeService,
    "pasqal-cloud": ResourceType.PasqalCloud,
    "pasqal-local": ResourceType.PasqalLocal,
}

# Default is 6 hours
DEFAULT_WALLTIME = pbs.duration(21600)

# Default PBS_HOME
DEFAULT_PBS_HOME = "/var/spool/pbs"


def get_pbs_home():
    """Read PBS_HOME from /etc/pbs.conf"""
    try:
        # PBS_CONF_FILE is always configured by pbs_postinstall, and system administrators
        # may override the default path (/etc/pbs.conf) if needed.
        with open(os.environ.get("PBS_CONF_FILE"), "r", encoding="utf-8") as conf_file:
            for line in conf_file:
                line = line.strip()
                if line.startswith("PBS_HOME="):
                    return line.split("=", 1)[1]
    except OSError:
        pass
    return DEFAULT_PBS_HOME  # fallback


e = pbs.event()
j = e.job

try:
    resources = j.Variable_List.get("SLURM_JOB_QPU_RESOURCES")
    if resources is None:
        e.accept()

    walltime = j.Resource_List["walltime"]
    if walltime is None:
        walltime = DEFAULT_WALLTIME

    # We assume that qrmi_config.json is located in the $PBS_HOME/mom_priv directory,
    # which is not accessible by PBS users.
    cfg_path = os.path.join(get_pbs_home(), "mom_priv", "qrmi_config.json")
    with open(cfg_path, "r", encoding="utf8") as f:
        cfg_json = json.load(f)
        cfg_by_name = {r["name"]: r for r in cfg_json["resources"]}

        resource_names = []
        resource_types = []
        for name in resources.split(","):
            name = name.strip()
            cfg = cfg_by_name.get(name)
            if cfg is None:
                pbs.logmsg(
                    pbs.EVENT_ERROR, f"{name} not found in qrmi_config.json. Ignored."
                )
                continue

            for key, value in cfg["environment"].items():
                env_name = f"{name}_{key}"
                if j.Variable_List.get(env_name) is None:
                    pbs.logmsg(pbs.EVENT_DEBUG, f"{env_name} = {value}")
                    j.Variable_List[env_name] = value
                else:
                    # Use the user-specified variables instead of those in the configuration file.
                    pbs.logmsg(
                        pbs.EVENT_DEBUG,
                        f"{env_name} = {j.Variable_List[env_name]}",
                    )

                # Set the environment variables for this daemon, which are
                # required to invoke subsequent QRMI API calls.
                os.environ[env_name] = j.Variable_List[env_name]

            j.Variable_List[f"{name}_QRMI_JOB_TIMEOUT_SECONDS"] = str(int(walltime))

            try:
                qrmi = QuantumResource(name, RES_TYPE_MAP.get(cfg["type"]))
                if qrmi.is_accessible() is False:
                    pbs.logmsg(
                        pbs.EVENT_ERROR,
                        f"{name} is unaccessible. Ignored.",
                    )
                    continue

                token = qrmi.acquire()
                j.Variable_List[f"{name}_QRMI_JOB_ACQUISITION_TOKEN"] = token
                pbs.logmsg(
                    pbs.EVENT_DEBUG,
                    f"{name}, {cfg['type']} acquired: {token}",
                )

                # if resource acquisition succeeded, add this resource to verified list
                resource_names.append(name)
                resource_types.append(cfg["type"])

            except RuntimeError as re:
                pbs.logmsg(
                    pbs.EVENT_ERROR,
                    f"Failed to process request in QRMI for {name}: {re}",
                )

        if len(resource_names) == 0:
            ERR_TXT = f"No specified quantum resources({resources}) are available."
            pbs.logmsg(
                pbs.EVENT_ERROR,
                ERR_TXT,
            )
            e.reject(ERR_TXT)

        j.Variable_List["SLURM_JOB_QPU_RESOURCES"] = ",".join(resource_names)
        j.Variable_List["SLURM_JOB_QPU_TYPES"] = ",".join(resource_types)

    e.accept()

except Exception as ex:
    e.reject(f"QRMI execjob_begin failed: {ex}")
