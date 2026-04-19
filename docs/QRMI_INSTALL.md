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
- Copy this file to /var/spool/pbs/mom_priv/qrmi_config.json

## Install PBS Hooks for QRMI
```bash
cd ~/pbs-hooks-for-qrmi/hooks
sudo bash
qmgr -c "create hook qrmi_acquire"
qmgr -c "import hook qrmi_acquire application/x-python default qrmi_acquire.PY"
qmgr -c "set hook qrmi_acquire event=execjob_begin"
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
    event = execjob_begin
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

## (Optional) Define PBS consumable resource for Quantum resource
In Slurm, job scheduling supports specifying consumable resource counts using the GRES option, such as ```--gres=qpu:1```.
To achieve similar functionality in PBS, a consumable resource needs to be defined.

```bash
sudo bash
```

Define ```qpu``` resource as consumable resource.
```bash
qmgr -c "create resource qpu type=long, flag=nh"
qmgr -c "set node <your node name> resources_available.qpu=1"
```

Edit vi ```$PBS_HOME/sched_priv/sched_config``` and append ```qpu``` to ```resources``` value like below.
```bash
resources: "ncpus, mem, arch, host, vnode, aoe, eoe, qpu"
```

Restart PBS
```bash
systemctl restart pbs
```

Verify qpu resource is available.
```bash
qmgr -c "print resource qpu"

#
# Create and define resource qpu
#
create resource qpu
set resource qpu type = long
set resource qpu flag = hn
```

Verify ```resources_available.qpu``` is available.
```bash
pbsnodes -av

rocky-linux-9-pbs
     Mom = rocky-linux-9-pbs.shared
     Port = 15002
     pbs_version = 23.06.06
     ntype = PBS
     state = free
     pcpus = 2
     resources_available.arch = linux
     resources_available.host = rocky-linux-9-pbs
     resources_available.mem = 7781256kb
     resources_available.ncpus = 1
     resources_available.qpu = 1
     resources_available.vnode = rocky-linux-9-pbs
     resources_assigned.accelerator_memory = 0kb
     resources_assigned.hbmem = 0kb
     resources_assigned.mem = 0kb
     resources_assigned.naccelerators = 0
     resources_assigned.ncpus = 0
     resources_assigned.qpu = 0
     resources_assigned.vmem = 0kb
     resv_enable = True
     sharing = default_shared
     license = l
     last_state_change_time = Sun Apr 19 22:02:43 2026
     last_used_time = Sun Apr 19 22:03:11 2026
```

After ```qpu``` resource is defined, PBS users can request the number of quantum resources for each job.
```bash
#PBS -l select=1:ncpus=1:mem=2gb:qpu=1
```

## Required Setup for Each PBS User
The following steps must be executed for each user who will use PBS.
```bash
python3.12 -m venv ~/pyenv
source ~/pyenv/bin/activate
pip install --upgrade pip
pip install qrmi
```
