# Running QRMI examples

## Clone pbs-hooks-for-qrmi github repository
```bash
cd ~
git clone https://github.com/ohtanim/pbs-hooks-for-qrmi.git
```

## Clone QRMI github repository
```bash
cd ~
git clone https://github.com/qiskit-community/qrmi.git
```

## Run QRMI examples
```bash
cd ~/pbs-hooks-for-qrmi/examples

(Edit run_sampler.sh and replace SLURM_JOB_QPU_RESOURCES value with yours defined in your qrmi_config.json)
qsub run_sampler.sh

(Edit run_estimator.sh and replace SLURM_JOB_QPU_RESOURCES value with yours defined in your qrmi_config.json)
qsub run_estimator.sh
```
