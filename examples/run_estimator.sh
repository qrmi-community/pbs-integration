#!/bin/bash
#PBS -N run_estimator
#PBS -l select=1:ncpus=1:mem=2gb
#PBS -l walltime=00:10:00
#PBS -l quantum_resources=ibm_fez
#PBS -j oe
#PBS -m bae

# To allocate multiple quantum resources, specify the resource identifiers separated by colons.
# #PBS -l quantum_resources='"ibm_marrakesh,ibm_kobe"'

# Change to the directory where the job was submitted
cd $PBS_O_WORKDIR              

# Your actual commands
source ~/pyenv/bin/activate
python3.12 ~/qrmi/examples/qiskit_primitives/ibm/estimator.py
