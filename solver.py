from petsc4py import PETSc
import petsc4py
import numpy as np
import math
from mpi4py import MPI


def solve(loc_num_x, min_x, max_x, rate_matrix, n_init, num_x, ksp_solver, ksp_pc, ksp_tol):
    """Solve the matrix equation R * n = b using PETSc. R is the rate matrix, n is the density array and b is the right-hand side, which is zero apart from the last element in each spatial cell which is equal to the total impurity density

    Args:
        rate_matrix (AIJMat): rate matrix
        n_init (np.array): initial densities
        num_x (int): number of spatial cells

    Returns:
        n_solved (np.array): equilibrium densities
    """

    num_states = len(n_init[0, :])
    num_x = len(n_init[:,0])

    rank = PETSc.COMM_WORLD.Get_rank()
    
    # # Initialise the rhs and new density vectors
    n_solved = PETSc.Vec().createSeq(num_states * loc_num_x, comm=PETSc.COMM_SELF)
    
    rhs_arr = np.zeros(num_states * loc_num_x)
    
    rhs_arr[
        [num_states * (i+1) - 1 for i in range(loc_num_x)]
    ] = [np.sum(n_init[i, :]) for i in range(min_x,max_x)]

    rhs = PETSc.Vec().createSeq(num_states * loc_num_x,comm=PETSc.COMM_SELF)
    rhs.setValuesLocal(range(num_states * loc_num_x), rhs_arr)   
    rhs.assemblyBegin()
    rhs.assemblyEnd()
    

    # Set the last row of petsc matrix to ones
    rate_matrix.assemblyBegin()
    rate_matrix.assemblyEnd()
    for i in range(min_x,max_x):
        offset = (i-min_x)*num_states
        for j in range(num_states):
            row = offset + num_states-1
            col = offset + j
            rate_matrix.setValue(row, col, 1.0)
    rate_matrix.assemblyBegin()
    rate_matrix.assemblyEnd()
    

    # Initialise the KSP solver
    ksp = PETSc.KSP().create(comm=PETSc.COMM_SELF)
    ksp.setOperators(rate_matrix)
    ksp.setType(ksp_solver)
    ksp.setTolerances(ksp_tol*np.sum(n_init[min_x:max_x,:]))
    pc = ksp.getPC()
    if ksp_pc is not None:
        pc.setType(ksp_pc)
    if rank == 0:
        print('Solving with:', ksp.getType(), ', preconditioner:', pc.getType())
    
    ksp.solve(rhs, n_solved)
    if ksp.getConvergedReason() < 0:
        print(rank,'\nKSP solve failed: ' + str(ksp.getConvergedReason()))
        return None
    else:
        print(rank,'Converged in', ksp.getIterationNumber(), 'iterations.')

    n_solved = np.array([[n_solved[i + (j * num_states)]
                        for i in range(num_states)] for j in range(loc_num_x)])

    PETSc.COMM_WORLD.Barrier()
    print("Conservation check on rank " + str(rank) + ": {:.2e}".format(
        np.sum(n_solved) - np.sum(n_init[min_x:max_x,:])))

    return n_solved


def evolve(loc_num_x, min_x, max_x, rate_matrix, n_init, num_x, dt, num_t, dndt_thresh, n_norm, t_norm, ksp_solver, ksp_pc, ksp_tol):
    """Evolve the matrix equation dn/dt = R * n using PETSc using backwards Euler time-stepping. R is the rate matrix, n is the density array

    Args:
        rate_matrix (AIJMat): rate matrix
        n_init (np.array): initial densities
        num_x (int): number of spatial cells
        dt (float): time step

    Returns:
        n_solved (np.array): equilibrium densities
    """
    
    # dndt_thresh *= (n_norm / t_norm)
    
    num_states = len(n_init[0, :])

    rate_matrix.assemblyBegin()
    rate_matrix.assemblyEnd()
    
    rank = PETSc.COMM_WORLD.Get_rank()

    # Initialise the old and new density vectors
    n_old = PETSc.Vec().createSeq(num_states * loc_num_x, comm=PETSc.COMM_SELF)
    n_old.setValues(range(num_states * loc_num_x), n_init[min_x:max_x,:].flatten())
    n_new = PETSc.Vec().createSeq(num_states * loc_num_x,comm=PETSc.COMM_SELF)

    # Create an identity matrix
    I = PETSc.Mat().createAIJ([num_states * loc_num_x, num_states * loc_num_x], nnz=1,comm=PETSc.COMM_SELF)
    for row in range(num_states * loc_num_x):
        I.setValue(row, row, 1.0)
    I.assemblyBegin()
    I.assemblyEnd()

    # Create the backwards Euler operator matrix
    be_op_mat = PETSc.Mat().createAIJ(
        [num_states*loc_num_x, num_states*loc_num_x], nnz=num_states,comm=PETSc.COMM_SELF)
    be_op_mat.assemblyBegin()
    be_op_mat.assemblyEnd()
    be_op_mat = I - dt * rate_matrix
    
    # for row in range(be_op_mat.size[0]):
    #     rows, row_vals = be_op_mat.getRow(row)
    #     for row_val in row_vals:
    #         if np.isnan(row_val):
    #             print('aaah')
    #         if np.isinf(row_val):
    #             print('aaah')

    # Initialise the KSP solver
    ksp = PETSc.KSP().create(comm=PETSc.COMM_SELF)
    ksp.setOperators(be_op_mat)
    ksp.setType(ksp_solver)
    ksp.setTolerances(ksp_tol*np.sum(n_init[min_x:max_x,:]))
    pc = ksp.getPC()
    if ksp_pc is not None:
        pc.setType(ksp_pc)
    if rank == 0:
        print('Evolving with:', ksp.getType(), ', preconditioner: ', pc.getType())

    prev_residual = 1e20
    for i in range(num_t):
        
        ksp.solve(n_old, n_new)
        if ksp.getConvergedReason() < 0:
            ksp_failed = 1
        else:
            ksp_failed = 0
        num_its = ksp.getIterationNumber()

        dndt = np.max(np.abs(n_old - n_new)) / dt

        # Update densities
        n_old.setValues(range(num_states*loc_num_x),
                        n_new.getValues(range(num_states*loc_num_x)))

        # Do some communication
        all_ksp_failed = MPI.COMM_WORLD.gather(ksp_failed,root=0)
        all_dndts = MPI.COMM_WORLD.gather(dndt,root=0)
        all_its = MPI.COMM_WORLD.gather(num_its,root=0)
        max_dndt = None
        max_its = None
        solver_failed = None
        if rank == 0:
            max_dndt = max(all_dndts)
            max_its = max(all_its)
            solver_failed = sum(all_ksp_failed)
        dndt_global = MPI.COMM_WORLD.bcast(max_dndt, root=0)
        num_its_global = MPI.COMM_WORLD.bcast(max_its, root=0)
        exit_flag = MPI.COMM_WORLD.bcast(solver_failed, root=0)
        
        if exit_flag > 0:
            if rank == 0:
                print('\nKSP solve failed.')
            reason = ksp.getConvergedReason()
            if reason < 0:
                print('\nKSP failed on rank ' + str(rank) + ', reason: ' + str(reason))
            return None
        
        if dndt_global > prev_residual and i/num_t > 0.1:
            break

        prev_residual = dndt_global
        
        if rank == 0:
            if (i+1) % 10 == 0:
                print('TIMESTEP ' + str(i+1) +
                ' | max(dn/dt) {:.2e}'.format((n_norm / t_norm) * dndt_global) + ' | NUM_ITS ' + str(num_its_global) + '            ', end='\r')
        
        if dndt_global < dndt_thresh:
            break
        

    n_solved = np.array([[n_new[i + (j * num_states)]
                        for i in range(num_states)] for j in range(loc_num_x)])
    
    PETSc.COMM_WORLD.Barrier()
    
    if rank == 0:
        print('')
    print("Conservation check on rank " + str(rank) + ": {:.2e}".format(
        np.sum(n_solved) - np.sum(n_init[min_x:max_x,:])))

    return n_solved