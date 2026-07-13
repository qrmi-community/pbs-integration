# Installing QRMI

## Clone pbs-hooks-for-qrmi github repository
```bash
cd ~
git clone https://github.com/ohtanim/pbs-hooks-for-qrmi.git
```

## Install QRMI to PBS cluster
```bash
sudo python3.12 -m pip install --target /opt/pbs/python/site-packages qrmi
```

## Create and Install qrmi_config.json
- Create [qrmi_config.json](https://github.com/qiskit-community/spank-plugins/blob/main/plugins/spank_qrmi/qrmi_config.json.example) and define quantum resources to use.
- Copy this file to $PBS_HOME/mom_priv/qrmi_config.json and $PBS_HOME/server_priv/qrmi_config.json 

## Install PBS Hooks for QRMI
```bash
cd ~/pbs-hooks-for-qrmi/hooks
sudo bash
qmgr -c "create hook qrmi_acquire"
qmgr -c "import hook qrmi_acquire application/x-python default qrmi_acquire.PY"
qmgr -c "set hook qrmi_acquire event=runjob"
qmgr -c "set hook qrmi_acquire enabled=true"
qmgr -c "set hook qrmi_acquire debug=true"

qmgr -c "create hook qrmi_release"
qmgr -c "import hook qrmi_release application/x-python default qrmi_release.PY"
qmgr -c "set hook qrmi_release event=execjob_end"
qmgr -c "set hook qrmi_release enabled=true"
qmgr -c "set hook qrmi_release debug=true"
```

## Verify PBS Hooks installation
```bash
sudo bash
qmgr -c "list hook"
```

You must be able to see:
```bash
Hook qrmi_acquire
    type = site
    enabled = true
    event = runjob
    user = pbsadmin
    alarm = 30
    order = 1
    debug = true
    fail_action = none

Hook qrmi_release
    type = site
    enabled = true
    event = execjob_end
    user = pbsadmin
    alarm = 30
    order = 1
    debug = true
    fail_action = none
```

## Define PBS consumable resource for Quantum resource

Create a custom resource used to specify which quantum resources user's jobs should use.
This resource is not tied to node hardware allocation — it's simply carried along with the job. Its value is read by a `runjob` hook and exported into the job's execution environment for QRMI.


### Setup

```bash
sudo bash
qmgr -c "create resource quantum_resources type=string_array"
```

> [!NOTE]
> You don't need to edit ```resources``` value in ```$PBS_HOME/sched_priv/sched_config```.

Restart PBS
```bash
systemctl restart pbs
```

Verify qpu resource is available.
```bash
qmgr -c "print resource quantum_resources"
#
# Create and define resource quantum_resources
#
create resource quantum_resources
set resource quantum_resources type = string_array
```

### Usage

Specify it in your job script like this:

```bash
#PBS -l quantum_resources=ibm_kingston
```

To request multiple backends, separate them with commas. In this case, the value must be wrapped in **nested quotes**, since an unquoted comma would otherwise be interpreted as the `-l` option's own separator between different resources:

```bash
#PBS -l quantum_resources='"ibm_kingston,ibm_kobe"'
```

### Verifying

To confirm the value was correctly attached to a submitted job:

```bash
qstat -f <JOBID> | grep quantum_resources
```


## Required Setup for Each PBS User
The following steps must be executed for each user who will use PBS.
```bash
python3.12 -m venv ~/pyenv
source ~/pyenv/bin/activate
pip install --upgrade pip
pip install qrmi[ibm]
```
