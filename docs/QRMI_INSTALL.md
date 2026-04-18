# Installing QRMI

## Clone this github repository
```bash
cd ~
git clone https://github.com/ohtanim/pbs-hooks-for-qrmi
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

