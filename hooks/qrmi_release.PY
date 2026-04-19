# SPDX-License-Identifier: Apache-2.0
"""PBS hook for managing quantum resources via QRMI.
This hook services the following events:
- execjob_end
"""

# pylint: disable=broad-exception-caught

import os
import sys
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

# Delimiter for list string
QRMI_LIST_DELIMITER = ":"

e = pbs.event()
j = e.job

resnames = j.Variable_List.get("SLURM_JOB_QPU_RESOURCES")
restypes = j.Variable_List.get("SLURM_JOB_QPU_TYPES")
if resnames is None or restypes is None:
    e.accept()

names = resnames.split(QRMI_LIST_DELIMITER)
types = restypes.split(QRMI_LIST_DELIMITER)

try:
    if j.Variable_List is None:
        j.Variable_List = {}

    for idx, name in enumerate(names):

        # Retrieve acquisition token from PBS variable list
        env_name = f"{name}_QRMI_JOB_ACQUISITION_TOKEN"
        token = j.Variable_List.get(env_name)
        if token is None:
            continue

        # Set the environment variables for this daemon, which are
        # required to invoke subsequent QRMI API calls.
        for key, value in j.Variable_List.items():
            if key.startswith(f"{name}_QRMI_"):
                os.environ[key] = value

        # Release quantum resources, which were acquired in execjob_begin
        try:
            qrmi = QuantumResource(name, RES_TYPE_MAP.get(types[idx]))
            if qrmi is None:
                continue
            qrmi.release(token)
            pbs.logmsg(pbs.EVENT_DEBUG, f"{name}, {types[idx]} released = {token}")

        except RuntimeError as re:
            pbs.logmsg(pbs.EVENT_DEBUG, f"{name}, {types[idx]} release failed {re}")

    e.accept()

except Exception as ex:
    e.reject(f"execjob_end failed: {ex}")
