#include <mpi.h>
#include <stdio.h>

int main(int argc, char **argv)
{
    int rank, size, len;
    char host[MPI_MAX_PROCESSOR_NAME];

    MPI_Init(&argc, &argv);
    MPI_Comm_rank(MPI_COMM_WORLD, &rank);
    MPI_Comm_size(MPI_COMM_WORLD, &size);
    MPI_Get_processor_name(host, &len);

    printf("Hello from rank %d of %d on %s\n", rank, size, host);

    /* Sum the ranks on rank 0 to check communication between the nodes */
    int sum = 0;
    MPI_Reduce(&rank, &sum, 1, MPI_INT, MPI_SUM, 0, MPI_COMM_WORLD);
    if (rank == 0)
        printf("Sum of ranks = %d (expected %d)\n", sum, size * (size - 1) / 2);

    MPI_Finalize();
    return 0;
}
