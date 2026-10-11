# OpenPBS Cluster Containers

A local, container-based OpenPBS cluster (1 server + 2 compute nodes) for developing and testing QRMI and PBS hooks without access to a production HPC cluster.

- Rocky Linux 9 + OpenPBS (`master`, embedded Python 3.12)
- Open MPI 4.1.8 with PBS TM support + mpi4py
- Works with Docker or Podman on Apple silicon, Intel/AMD and Windows (WSL2)

| Container | Role |
|-----------|------|
| `pbs-server` | PBS server, scheduler, comm and datastore; job submission host |
| `pbs-node1` | Compute node #1 (`pbs_mom`) |
| `pbs-node2` | Compute node #2 (`pbs_mom`) |

See **[docs/INSTALL.md](docs/INSTALL.md)** for setup, verification, image details, customization and troubleshooting.

## Files

| Path | Description |
|------|-------------|
| `Dockerfile` | Image shared by all nodes (OpenPBS, Open MPI, mpi4py, tools) |
| `compose.yaml` | Cluster definition: 3 containers, network, shared `/home` volume and `/shared` bind mount |
| `entrypoint.sh` | Configures and starts PBS according to `PBS_ROLE` (`server` / `mom`) without systemd |
| `examples/` | MPI test programs and job scripts (C and mpi4py) |
| `docs/INSTALL.md` | Installation and reference guide |
| `images/` | Cluster diagram (PNG and SVG) |
