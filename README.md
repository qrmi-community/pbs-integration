# PBS Hook for Managing Quantum Resources via QRMI

This repository provides a PBS event hook that enables the execution of quantum jobs in PBS-based systems. The hook reads a custom PBS resource that specifies which quantum backend a job requests, and configures the execution environment so that PBS jobs can seamlessly run on quantum hardware. This PBS hook leverages the **Python bindings of the [Quantum Resource Management Interface (QRMI)](https://github.com/qiskit-community/qrmi)** to interact with quantum resources in a scheduler-agnostic manner.

This repository implements two PBS hooks — ```runjob``` and ```execjob_end``` — to manage the full lifecycle of quantum resource acquisition and release.
The ```runjob``` hook reads the value of the ```quantum_resources``` custom resource specified in the job script, checks whether the requested quantum resource is accessible, and attempts to acquire it. If the resource is not available, the hook calls reject(), leaving the job unscheduled and pending in the queue; PBS then retries resource acquisition at each subsequent scheduling cycle until the resource becomes available.
The ```execjob_end``` hook releases the quantum resource that was acquired for the job once the job completes.

## About QRMI
QRMI is a vendor-agnostic library designed to control the state, execute tasks, and monitor the behavior of quantum computational resources, including qubits, QPUs, and entire quantum systems.
QRMI acts as a **thin middleware layer** that abstracts away the complexity of interacting with quantum hardware. It provides a set of simple and consistent APIs to:

- acquire and release quantum resources,
- run quantum tasks, and
- monitor the state of quantum systems.

QRMI is implemented in Rust, with Python and C APIs exposed to enable straightforward integration into a wide range of computational environments and software stacks.

## Motivation and Proof of Concept
In addition to being vendor-agnostic, **QRMI is intentionally designed and implemented to be resource-manager agnostic**.
While QRMI’s integration with Slurm has already progressed significantly—and deployments and evaluations are ongoing at HPC data centers worldwide—this repository aims to further validate that design goal.
This Git repository serves as a **proof of concept (PoC)** to demonstrate the use of QRMI on **OpenPBS**, exploring how quantum resources can be managed and accessed via QRMI in a PBS-based environment.
Through this PoC, we confirmed that QRMI largely fulfills its resource-manager-agnostic design objectives. Although some minor improvements and adjustments were identified, the experiment shows that QRMI can be effectively integrated with OpenPBS, reinforcing its applicability beyond Slurm-based systems.

## Spank plugins vs. PBS Hooks
|| Spank plugins | PBS Hooks |
| ---- | ---- | ---- |
| Implementation language | C(shared library ```.so```) | Python(script) |
| Deployment | Compiled ```.so``` distributed to each node manually | Registered via ```qmgr```; server distributes automatically |
| Execution location | ```slurmctld``` (controller) or ```slurmd``` (compute node), depending on context | PBS Server or MOM (compute node), selected per hook type |
| Hook/callback list | ```slurm_spank_init```, ```task_init```, ```task_exit```, etc. — ~10+ callbacks. Custom options via spank_option. | ```queuejob```, ```runjob```, ```execjob_begin```, ```execjob_end```, ```execjob_epilogue```, etc. — 20+ event types |
| Job info access | SPANK API (```spank_get_item```, etc.) to read C structs | ```pbs.event().job``` object — attributes readable and writable directly |
| Job rejection / modification | [Limited] Return an error code to abort; modifying attributes is difficult | [Flexible] ```event.reject()``` and attribute rewriting are straightforward |
| Environment variable passing | ```spank_setenv``` / ```spank_getenv``` for direct manipulation | Via ```job.Variable_List```; accessible as ```$PBS_VAR``` inside the job script |
| Custom resource integration | Add SPANK options to ```sbatch``` and combine with GRES, etc. | Define custom resources via ```qmgr```; specify in select or ```-l```; read inside the hook via job.Resource_List |
| Logging / debugging | ```slurm_info```, ```slurm_error``` → slurmd log | ```pbs.logmsg()``` → PBS server log (configurable log level) |
| Code update | C source must be rebuilt and redeployed on every change | Re-register the updated Python script with qmgr |

## Design Validation: Boundary Between QRMI and Resource Managers

The PBS hooks implemented in this repository largely replicate the functionality already provided by the [Slurm SPANK plugins](https://github.com/qiskit-community/spank-plugins), reimplementing equivalent behavior on top of OpenPBS.

One notable difference lies in *when* quantum resource accessibility is checked and acquired. This implementation uses a `runjob` hook, which executes immediately before PBS allocates compute nodes and resources to a job — that is, before the job actually begins consuming CPU or memory. In contrast, the Slurm SPANK plugins perform this check during the job's execution phase, after scheduling has already taken place. Consequently, if a quantum resource is temporarily unavailable (e.g., due to maintenance or calibration), the SPANK-based approach leaves already-allocated compute resources idle while waiting for the resource to become available. With the PBS `runjob` hook, an unavailable quantum resource instead simply leaves the job in a pending state — no compute resources are allocated, and the job is automatically retried at the scheduler's next scheduling cycle.

Despite this difference in timing, the close functional correspondence between the two implementations strongly indicates that the boundary between QRMI and the resource manager is correctly designed. By keeping resource-manager-specific logic confined to hooks or plugins, and delegating quantum resource control to QRMI, the architecture cleanly separates responsibilities and avoids scheduler-specific assumptions within QRMI itself. As a result, this work further substantiates QRMI's design goal of being a resource-manager-agnostic middleware layer, capable of supporting multiple workload managers with minimal adaptation effort.


## Findings from the PoC
- In PBS, when a comma-separated string is specified as the value of an environment variable, everything after the first comma is truncated.
For example, if ```ENV=VAR1,VAR2``` is specified, only ```ENV=VAR1``` is set at job execution time.
This becomes a problem when trying to use multiple Quantum Resources with QRMI, since ```SLURM_JOB_QPU_RESOURCES``` and ```SLURM_JOB_QPU_TYPES``` contain comma-separated values.
- The ```runjob``` and ```execjob_end``` hooks in PBS are executed in separate Python interpreter processes (whereas in Slurm, SPANK plugins are invoked within the same process).
As a result, sharing data between these hooks requires serializing the data into job.Variable_List and passing it via environment variable values.

## Table of Contents

1. [Installing PBS](./docs/PBS_INSTALL.md)
2. [Installing QRMI](./docs/QRMI_INSTALL.md)
3. [Running QRMI examples](./docs/RUN_QRMI.md)

## How to Give Feedback

We encourage your feedback! You can share your thoughts with us by:
- [Opening an issue](https://github.com/ohtanim/pbs-hooks-for-qrmi/issues) in the repository

## References and Acknowledgements
1. [OpenPBS Github](https://github.com/openpbs/openpbs)
2. [Quantum spank plugins for Slurm](https://github.com/qiskit-community/spank-plugins)
3. [Quantum Resource Management Interface](https://github.com/qiskit-community/qrmi)
4. [PBS Professional 2021.1.2 Hooks Guide](https://2021.help.altair.com/2021.1.2/PBS%20Professional/PBSHooks2021.1.2.pdf)
