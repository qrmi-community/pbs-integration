from mpi4py import MPI
import socket
import sys

comm = MPI.COMM_WORLD
rank = comm.Get_rank()
size = comm.Get_size()

print(f"[mpi4py] rank {rank} of {size} on {socket.gethostname()} (Python {sys.version.split()[0]})", flush=True)

total = comm.reduce(rank, op=MPI.SUM, root=0)
if rank == 0:
    print(f"[mpi4py] sum of ranks = {total} (expected {size * (size - 1) // 2})")
