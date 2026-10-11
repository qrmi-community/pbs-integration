# Running QRMI examples

## Create python venv
```bash
cd ~
python3.12 -m venv ~/pyenv
source ~/pyenv/bin/activate
pip install --upgrade pip

# for IBM
pip install "qrmi[ibm]"

# for Pasqal
pip install "qrmi[pasqal]"

# for IQM
pip install "qrmi[iqm]"
```

## Clone pbs-integration github repository
```bash
cd ~
git clone https://github.com/qrmi-community/pbs-integration.git
```

## Clone QRMI github repository
```bash
cd ~
git clone https://github.com/qiskit-community/qrmi.git
```

## Run QRMI examples
```bash
cd ~/pbs-integration/examples

(Edit run_sampler.sh and replace `quantum_resources=` value with yours defined in your qrmi_config.json)
qsub run_sampler.sh

(Edit run_estimator.sh and replace `quantum_resources=` value with yours defined in your qrmi_config.json)
qsub run_estimator.sh
```
