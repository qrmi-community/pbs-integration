# PBS Hook for Managing Quantum Resources via QRMI

This repository provides a PBS event hook that enables the management and execution of quantum jobs in PBS-based systems.
The hook handles PBS resources associated with quantum computers and configures the execution environment so that PBS jobs can seamlessly run on quantum hardware.
This PBS hook leverages the **Python bindings of the [Quantum Resource Management Interface (QRMI)](https://github.com/qiskit-community/qrmi)** to interact with quantum resources in a scheduler-agnostic manner.

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

## Design Validation: Boundary Between QRMI and Resource Managers
The PBS hooks implemented in this repository **largely replicate the functionality already provided by the [Slurm SPANK plugins](https://github.com/qiskit-community/spank-plugins)**, reimplementing equivalent behavior on top of OpenPBS.
This close functional correspondence strongly indicates that the boundary between QRMI and the resource manager is correctly designed.
By keeping resource-manager-specific logic confined to hooks or plugins, and delegating quantum resource control to QRMI, the architecture cleanly separates responsibilities and avoids scheduler-specific assumptions within QRMI itself.
As a result, this work further substantiates QRMI’s design goal of being a **resource-manager-agnostic middleware layer**, capable of supporting multiple workload managers with minimal adaptation effort.

## Table of Contents

1. [Installing PBS](./docs/PBS_INSTALL.md)
2. [Installing QRMI](./docs/QRMI_INSTALL.md)
3. [Running QRMI examples](./docs/RUN_QRMI.md)

## How to Give Feedback

We encourage your feedback! You can share your thoughts with us by:
- [Opening an issue](https://github.com/ohtanim/pbs-hooks-for-qrmi/issues) in the repository

## References and Acknowledgements
1. [Quantum spank plugins for Slurm](https://github.com/qiskit-community/spank-plugins)
2. [Quantum Resource Management Interface](https://github.com/qiskit-community/qrmi)
3. [PBS Professional 2021.1.2 Hooks Guide](https://2021.help.altair.com/2021.1.2/PBS%20Professional/PBSHooks2021.1.2.pdf)
