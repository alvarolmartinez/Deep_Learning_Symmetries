#####################################################################################
#
# Discovering Non-Abelian Symmetries
#
# Author: Roy Forestano
#
# Date of Completion: 13 January 2023
#
#####################################################################################
# Standard Imports Needed

import numpy as np
import matplotlib.pyplot as plt
import matplotlib as mpl
import scipy
import os
import copy
from tqdm import tqdm
from time import time

import torch
from torch import nn
from torch.utils.data import DataLoader
from torch.utils.data import Dataset
from torchvision import datasets
from torchvision.transforms import ToTensor
torch.set_default_dtype(torch.float64)
plt.rcParams["font.family"] = 'sans-serif'
np.set_printoptions(formatter={'float_kind':'{:f}'.format})

#####################################################################################


def run_model(n, n_dim, n_gen, n_com, eps, lr, epochs, oracle):
    #####################################################################################
    # Initialize general set up

    # initialiaze data
    data    = torch.tensor(np.random.randn(n,n_dim))
    # initialize generators
    initialize_matrices = torch.tensor(np.array([ np.random.randn(n_dim,n_dim) for i in range(n_gen) ]))
    # initialize structure constants
    initialize_struc_const = torch.tensor(np.random.randn(n_com,n_gen))
    # Lie Bracket or Commutator
    def bracket(A, B):
        return A @ B - B @ A


    #####################################################################################
    # Set up model paramters
    
    # Choose device
    device = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"Using {device} device")
    
    # Define model
    class find_generators(nn.Module):
        def __init__(self,n_dim,n_gen,n_com):
            super(find_generators,self).__init__()
       
            G = [ nn.Sequential( nn.Linear(in_features = n_dim*n_dim, out_features = n_dim*n_dim, bias = True),
                                 nn.ReLU(),
                                 nn.Linear(in_features = n_dim*n_dim, out_features = n_dim*n_dim, bias = True),
                                 nn.ReLU(),
                                 nn.Linear(in_features = n_dim*n_dim, out_features = n_dim*n_dim, bias = True) )  for _ in range(n_gen)]
        
        
            self.gens = nn.ModuleList(G)
            
            if n_dim<6:
                self.struct_const = nn.Sequential( nn.Linear(in_features = n_gen*n_com, out_features = n_gen*n_com),
                                                    nn.ReLU(),
                                                    nn.Linear(in_features = n_gen*n_com, out_features = n_gen*n_com),
                                                    nn.ReLU(),
                                                    nn.Linear(in_features = n_gen*n_com, out_features = n_gen*n_com) )
        
            self.n_gen = n_gen
            self.n_dim = n_dim
            self.n_com = n_com

        def forward(self, x, c):
            generators = []
            for i in range(self.n_gen):
                generators.append( ( self.gens[i](x[i].flatten()) ).reshape(self.n_dim,self.n_dim)  )
                
            if self.n_dim<6:
                structure_constants =  self.struct_const(c.flatten()).reshape(self.n_com,self.n_gen)
            else:
                structure_constants = c
        
            return structure_constants, generators
    
    # Initialize Model
    model = find_generators(n_dim,n_gen,n_com).to(device)
    
    
    # Loss function
    def loss_fn(data,generators,struc_const,eps,ainv=1,anorm=1,aorth=1,aclos=1,components = False):
    
        lossi = 0.
        lossn = 0.
        losso = 0.
        lossc = 0.
        #lossspsc = 0.
        #lossspg = 0.
        comm_index = 0
    
        for i, G in enumerate(generators): 
            transform = torch.transpose((torch.eye(G.shape[0]) + eps*G)@torch.transpose(data,dim0=1,dim1=0), dim0=1,dim1=0 )
            transform = transform.reshape(data.shape[0],data.shape[1])

            lossi  += torch.mean( ( oracle(transform) - oracle(data) )**2 ) / eps**2 
            lossn  += (torch.sum(G**2) - 2)**2
        
            for j, H in enumerate(generators):
                if i < j:
                    losso += torch.sum(G*H)**2
                    
                    if data.shape[1]<6:
                        C1 = bracket(G,H)
                        C2 = 0
                        for k,K in enumerate(generators):
                            C2 += struc_const[comm_index,k]*K
                        C = C1 - C2
                        lossc += torch.sum(C**2)**2
                        comm_index +=1

    
        # attempt at adding a sparsity condition 
        # to both the generators and structure constants
        # (Did not work well)
    
        #for i, G in enumerate(generators):
        #    lossspg += len(torch.where(torch.abs(G)>1e-02)[0])
        
        #lossspsc = len(torch.where(torch.abs(struc_const)>1e-02)[0])
               
                        
        if components:
            return [ ainv*lossi,  anorm*lossn,  aorth*losso,  aclos*lossc ]

        L = ainv*lossi + anorm*lossn + aorth*losso + aclos*lossc #+ lossspsc + lossspg
        return  L
    
    
    # Optimizer
    optimizer = torch.optim.Adam(model.parameters(), lr=lr)
    
    # Training function
    def train(initial_matrices, initial_struc_const, data, model, loss_fn, epochs, optimizer, eps):
        history = {'train_loss': [],
                   'components_loss':[]} 
    
        start = time()
    
        ainv = 1
        anorm = 1
        aorth = 1
        if data.shape[1]<6:
            aclos = 1
        else:
            aclos = 0
    
        X = initial_matrices
        Y = initial_struc_const
        size = X.shape[0]
    
        for i in range(epochs):
            train_loss = 0.
            model.train()
            struc_const, gens = model(X,Y)
        
            loss = loss_fn( data         = data,
                            generators   = gens,
                            struc_const  = struc_const,
                            eps          = eps,
                            ainv         = ainv,
                            anorm        = anorm,
                            aorth        = aorth,
                            aclos        = aclos ) #.mean()
        
            comp_loss = loss_fn( data         = data,
                                 generators   = gens,
                                 struc_const  = struc_const,
                                 eps          = eps,
                                 ainv         = ainv,
                                 anorm        = anorm,
                                 aorth        = aorth,
                                 aclos        = aclos,
                                 components   = True )

            # Backpropagation
            optimizer.zero_grad()
            loss.backward()
            optimizer.step()
            train_loss += loss.data.item()
            comp_loss_for_epoch = []
        
            for j in range(len(comp_loss)):
                if torch.is_tensor(comp_loss[j]):
                    comp_loss_for_epoch.append(comp_loss[j].data.item())
                else:
                    comp_loss_for_epoch.append(comp_loss[j])
                
            history['train_loss'].append(train_loss)
            history['components_loss'].append(comp_loss_for_epoch)
        
            if i%1==0:
                print(f"Epoch {i+1}   |  Train Loss: {train_loss}",end='\r') #{train_loss:>8f}
            if i==epochs-1:
                print(f"Epoch {i+1}   |  Train Loss: {train_loss}")
    
            if train_loss*1e25 < 1:
                print()
                print('Reached Near Machine Zero')
                break
    
        end = time()
        total_time = end-start
        print(f'Total Time: {total_time:>.8f}')
        print("Complete.")
        return {'history': history}
    
    

    training = train( initial_matrices    = initialize_matrices, 
                      initial_struc_const = initialize_struc_const,
                      data                = data,
                      model               = model, 
                      loss_fn             = loss_fn,
                      epochs              = epochs,
                      optimizer           = optimizer,
                      eps                 = eps  )
                
    if n_gen>1:
        train_loss = np.array(training['history']['train_loss'])
        comp_loss = np.array(training['history']['components_loss'])
    else:
        train_loss = np.array(training['history']['train_loss'])
        comp_loss = np.empty( ( train_loss.shape[0],len(training['history']['components_loss']) ) )
        for i,comp in enumerate(training['history']['components_loss']):
            for j,term in enumerate(comp):
                if torch.is_tensor(term) and term.requires_grad:
                    comp_loss[i,j] = term.detach().numpy()
                else:
                    comp_loss[i,j] = term

    N=train_loss.shape[0]
    plt.figure(figsize=(6,4))   #, dpi=100)
    plt.plot( train_loss[:N], linewidth=1, linestyle='-',  color = 'r', label='Total')
    plt.plot(comp_loss[:N,0], linewidth=1, linestyle=':',  color='b',   label='Invariance')
    plt.plot(comp_loss[:N,1], linewidth=1, linestyle='--', color='g',   label='Normalization')
    plt.plot(comp_loss[:N,2], linewidth=1, linestyle='-.', color='magenta', label='Orthogonality')
    plt.plot(comp_loss[:N,3], linewidth=1, linestyle='-.', color='cyan', label='Closure')
    plt.legend()

    plt.xlabel('Epoch')
    plt.ylabel('Loss')
    plt.yscale('log')
    plt.title('Components of Loss')

    plt.show()
    
    # Evaluate Model
    model.eval()

    with torch.no_grad():
        struc_pred, gens_pred = model(initialize_matrices,initialize_struc_const)
                
    return struc_pred, gens_pred




#####################################################################################
# Plot Symmetry Vector Plot            
                

def draw_sym_vectors(M, oracle):
    plt.figure(figsize=(4,3.25))   #, dpi=100)

    # Makes the background contour:
    x_grid, y_grid = np.meshgrid(np.linspace(-2,2,101), np.linspace(-2,2,101))
   
    grid_points = torch.tensor(np.stack([x_grid.flatten(), y_grid.flatten()], axis=1))
    oracle_vals = oracle(grid_points).numpy().reshape(x_grid.shape)

    plt.contourf(x_grid, y_grid, oracle_vals, 32, cmap='RdBu') #, norm = mpl.colors.CenteredNorm() )

    # now make the vector field:
    # This makes the points which are the tails of the vectors
    x_grid, y_grid = np.meshgrid(np.linspace(-2,2,20), np.linspace(-2,2,20))

    # calculates the vector at each point
    x_vec_grid, y_vec_grid = np.einsum('il,ljk', M.detach().numpy(), np.stack([x_grid, y_grid]))
       
    # loops over those points and corresponding vectors and draws the arrow
    for x, y, dx, dy in zip(x_grid.flatten(),
                            y_grid.flatten(),
                            x_vec_grid.flatten(),
                            y_vec_grid.flatten()):
       
        # this is the factor by which all vectors are scaled down:
        scale=.05
        plt.arrow(x, y, dx*scale, dy*scale, head_width=.03, lw=.5, fc='k', ec='k')

    plt.xlim(-2,2)
    plt.ylim(-2,2)
    plt.yticks(np.arange(-2,3))
    plt.xlabel('$x^{(1)}$',fontsize=12)
    plt.ylabel('$x^{(2)}$',fontsize=12)
    plt.colorbar(label='$\phi(\\vec{x})$')


#####################################################################################
# Visualize Generators

def visualize_generators(figsize, n_dim, n_gen, eps, gens_pred, rows, cols):
    # Create labels for matrix rows and columns
    ticks_gen_im =[]
    ticks_gen_im_label = []
    for i in range(n_dim):
        ticks_gen_im.append(i)
        ticks_gen_im_label.append(str(i+1))
    
    if rows==1 and cols==1:
        fig = plt.subplots(rows,cols,figsize=figsize)
        GEN = gens_pred[0]
        plt.subplot(111)
        print(f'Generator: \n {GEN} \n')
        im = plt.imshow(GEN.detach().numpy(), cmap='RdBu', vmin=-1., vmax=1.)  #norm=mpl.colors.CenteredNorm())
    #     det = np.linalg.det(np.eye(GEN.shape[0]) + eps * GEN.detach().numpy())
    #     ax.set_title(f'det = {det}')
    #     ax.axis('off')
        plt.xticks(ticks=ticks_gen_im, labels=ticks_gen_im_label)
        plt.yticks(ticks=ticks_gen_im, labels=ticks_gen_im_label)
        plt.title('Generator '+str(1),fontsize=20)
        plt.colorbar(im)
        
    elif n_dim==10:
        fig,axes = plt.subplots(rows,cols,figsize=figsize)
        for i,GEN in enumerate(gens_pred):
            plt.subplot(rows,cols,i+1)
            im = plt.imshow(GEN.detach().numpy(), cmap='RdBu')
            plt.axis('off')

    
    else:
        fig,axes = plt.subplots(rows,cols,figsize=figsize)
    #     for i, ax_GEN in enumerate(zip(axes.flat,gens_pred)):
    #         plt.subplot(rows,cols,i+1)
    #         if n_gen<5:
    #             print(f'Generator {i+1}: \n {ax_GEN[1]} \n')
    #         im = ax_GEN[0].imshow(ax_GEN[1].detach().numpy(), cmap='RdBu', vmin=-1., vmax=1.)
    #         ax_GEN[0].set_xticks(ticks=ticks_gen_im)
    #         ax_GEN[0].set_xticklabels(labels=ticks_gen_im_label)
    #         ax_GEN[0].set_yticks(ticks=ticks_gen_im)
    #         ax_GEN[0].set_yticklabels(labels=ticks_gen_im_label)
    #         ax_GEN[0].set_title('Generator '+str(i+1),fontsize=20)
        for i,GEN in enumerate(gens_pred):
            plt.subplot(rows,cols,i+1)
            if n_gen<10:
                print(f'Generator {i+1}: \n {GEN} \n')
            im = plt.imshow(GEN.detach().numpy(), cmap='RdBu', vmin=-1., vmax=1.) # use ax_GEN[0] with axes
    #         if n_gen<7:
    #             det = np.linalg.det(np.eye(GEN.shape[0]) + eps * GEN.detach().numpy()) #ax_GEN[1]
    #             plt.title(f'det = {det}')
    #         plt.axis('off')
            plt.xticks(ticks=ticks_gen_im, labels=ticks_gen_im_label)
            plt.yticks(ticks=ticks_gen_im, labels=ticks_gen_im_label)
            plt.title('Generator '+str(i+1),fontsize=20)

        plt.subplots_adjust(right=0.8)
        plt.colorbar(im, ax=axes.ravel().tolist(), ticks = [-1.0,-0.75,-0.50,-0.25,0,0.25,0.50,0.75,1.0])
    
    # Adapted from code by Alex Roman
    # Only applies to when we can draw the axes of rotation for each vector
def visualize_generator_axes(gens_pred):
    def draw_vec(ax, v, lw, color, label):
        # Draw a vector to ax, this adds lines for the projection
        # Draw vector (0,0,0) to (v0,v1,v2)
        ax.plot([0,   v[0]], [0,   v[1]], [0, v[2]], color=color, lw=lw, label=label)
        # Fix (x,y) and draw a line from z=0 to z=v[2] (z component of rot vec)
        # Draw vector (v0,v1,0) to (v0,v1,v2) == straight line up... etc.
        ax.plot([v[0],v[0]], [v[1],v[1]], [0,v[2]], color='b', alpha=.25, ls='--')
        # Fix (x,z) and draw a line from y=0 to y=v[1] (y component of rot vec)
        ax.plot([v[0],v[0]], [0   ,v[1]], [0,0   ], color='b', alpha=.25, ls='--')
        # Fix (y,z) and draw a line from x=0 to x=v[0] (x component of rot vec)
        ax.plot([0   ,v[0]], [v[1],v[1]], [0,0   ], color='b', alpha=.25, ls='--')
    
    def get_axis_np(M):
        # Finds the eigenvector with min(Imaginary(eigenvalue))
        # if the matrix is a rotation matrix or a generator of 
        # rotations, then this vector is the axis of rotation 
        eig_vals, eig_vecs = np.linalg.eig(M)
        index = np.argmin(np.sum(np.abs(eig_vecs.T.imag),axis=1)) # T is for transpose
        # find the minimum arg of the minimum imaginary component
        # pass that to the transposed eigenvector array to pull the eigenvecto
        axis = eig_vecs.T[index].real
        # Change to more positive than negative values in axis vector by multiplying by the net sign
        return np.sign(np.sum(axis))*axis

    def draw_axes(gens_pred, verbose=True):
        G1, G2, G3 = gens_pred
        # gather rotation axes
        axis1 = get_axis_np(G1.detach().numpy())
        axis2 = get_axis_np(G2.detach().numpy())
        axis3 = get_axis_np(G3.detach().numpy())
    
        # to be more verbose (include extra detail) list the 
        # rotation axes found from the axis function
        if verbose:
            print(f'Axis 1: {axis1}')
            print(f'Axis 2: {axis2}')
            print(f'Axis 3: {axis3}')
    
        # set up plot
        fig1 = plt.figure(figsize=(4,4))
        ax = fig1.add_subplot(111, projection='3d')
        ax.grid(False)
            
        # draw x,y,z axes on graph
        ax_lim = 1
        ax.plot([-ax_lim,ax_lim],[0,0],[0,0], color='black', alpha=.3)
        ax.plot([0,0],[-ax_lim,ax_lim],[0,0], color='black', alpha=.3)
        ax.plot([0,0],[0,0],[-ax_lim,ax_lim], color='black', alpha=.3)

        # set bounds on graph to be +-1
        ax.set_xlim(-ax_lim,ax_lim)
        ax.set_ylim(-ax_lim,ax_lim)
        ax.set_zlim(-ax_lim,ax_lim)
    
        # draw each rotation axis
        lw = 5
        draw_vec(ax = ax, v = axis1, lw=lw, color = 'b', label='Axis '+str(1))
        draw_vec(ax = ax, v = axis2, lw=lw, color = 'r', label='Axis '+str(2))
        draw_vec(ax = ax, v = axis3, lw=lw, color = 'g', label='Axis '+str(3))
        plt.legend()
        plt.show()
            
    draw_axes(gens_pred)
            

            
            
#####################################################################################
# Visualize Structure Constants
        
def visualize_structure_constants(figsize, n_gen, n_com, struc_pred):
    if n_gen==3:
        X = torch.tensor(struc_pred.numpy())
        struc_cyclic = X
        struc_cyclic[1] = -X[1]
        
    commutator_labels = []
    if n_com==3:
        # Make the commutations cyclic for 3 generators
        for i in range(n_gen):
             for j in range(n_gen):
                    if i<j:
                        if (j-i)==2:
                            commutator_labels.append(str(j+1)+str(i+1))
                        else:
                            commutator_labels.append(str(i+1)+str(j+1))
    else:
        for i in range(n_gen):
            for j in range(n_gen):
                if i<j:
                    commutator_labels.append(str(i+1)+str(j+1))
        
    ticks_com = []
    for i in range(n_com):
        ticks_com.append(i)

    ticks_gen = []
    generator_labels = []
    for i in range(n_gen):
        ticks_gen.append(i)
        generator_labels.append(str(i+1))
    
    fig = plt.figure(figsize=figsize)
    if n_com==3:
        plt.imshow(struc_cyclic.detach().numpy(), cmap='RdBu', vmin=-1.,vmax=1.)#norm=mpl.colors.CenteredNorm())
    else:
        plt.imshow(struc_pred.detach().numpy(), cmap='RdBu', vmin=-1.,vmax=1.)#norm=mpl.colors.CenteredNorm())
    plt.xticks(ticks=ticks_gen,labels=generator_labels)
    plt.xlabel('Generator',fontsize=15)
    plt.yticks(ticks=ticks_com,labels=commutator_labels)
    plt.ylabel('Bracket',fontsize=15)
    plt.title('Structure Constants',fontsize=15)
    plt.colorbar()
    
    # add grid lines
    # for i in range(n_gen-1):
    #     plt.axvline(x=1/2+i, linewidth=1, color ='black')
    # for i in range(n_com-1):
    #     plt.axhline(y=1/2+i-0.01, linewidth=1, color ='black')


#####################################################################################
# Verify Commutations with Structure Constants

def verify_struc_constants(n_gen, struc_pred, gens_pred):
    # Lie Bracket or Commutator
    def bracket(A, B):
        return A @ B - B @ A
    
    if n_gen==3:
        X = torch.tensor(struc_pred.numpy())
        struc_cyclic = X
        struc_cyclic[1] = -X[1]

    comm_index = 0
    Cs = []
    for i,G in enumerate(gens_pred):
        for j,H in enumerate(gens_pred):
            if i<j and n_gen!=3:
                C1 = bracket(G,H)
                C2 = 0
                for k,K in enumerate(gens_pred):
                    C2 += struc_pred[comm_index,k]*K
                C = C1 - C2
                error = torch.mean(torch.abs(C.real))
                print(str(i+1)+str(j+1)+': \n Structure Constants = '+str(struc_pred[comm_index,:].detach().numpy())+'\n \n C = \n ',C.detach().numpy(),'\n')
                if error<1e-1:
                    print(f'The structure constants were found successfully with a mean absolute error (MAE) of {error}. \n \n')
                elif error>1e-1:
                    print(f'The structure constants were NOT found successfully with a mean absolute error (MAE) of {error}. \n \n')
                Cs.append(C)
                comm_index+=1
            # Make the cyclic commutators if n_gen = 3   
            elif i<j and n_gen==3:
                if (j-i)==2:
                    C1 = bracket(H,G)
                    C2 = 0
                    for k,K in enumerate(gens_pred):
                        C2 += struc_cyclic[comm_index,k]*K
                    C = C1 - C2
                    error = torch.mean(torch.abs(C.real))
                    print(str(j+1)+str(i+1)+': \n Structure Constants = '+str(struc_cyclic[comm_index,:].detach().numpy())+'\n \n C = \n ',C.detach().numpy(),'\n')
                    if error<1e-1:
                        print(f'The structure constants were found successfully with a mean absolute error (MAE) of {error}. \n \n')
                    elif error>1e-1:
                        print(f'The structure constants were NOT found successfully with a mean absolute error (MAE) of {error}. \n \n') 
                    Cs.append(C)
                    comm_index+=1
                else:
                    C1 = bracket(G,H)
                    C2 = 0
                    for k,K in enumerate(gens_pred):
                        C2 += struc_cyclic[comm_index,k]*K
                    C = C1 - C2
                    error = torch.mean(torch.abs(C.real))
                    print(str(i+1)+str(j+1)+': \n Structure Constants = '+str(struc_cyclic[comm_index,:].detach().numpy())+'\n \n C = \n ',C.detach().numpy(),'\n')
                    if error<1e-1:
                        print(f'The structure constants were found successfully with a mean absolute error (MAE) of {error}. \n \n')
                    elif error>1e-1:
                        print(f'The structure constants were NOT found successfully with a mean absolute error (MAE) of {error}. \n \n') 
                    Cs.append(C)
                    comm_index+=1
    
    
    # Calculate the total MSE in finding the structure constants
    tot_error = 0.
    for i,C in enumerate(Cs):
        tot_error+=torch.mean(torch.abs(C.real))
    print(f'Total MAE = {tot_error}')
    # if error < 1e-1:
    #     print(f'The structure constants were found successfully with a mean absolute error (MAE) of {error}.')
    # else:
    #     print(f'The structure constants were NOT found successfully with a mean absolute error (MAE) of {error}.')




#####################################################################################
# Verify Orthogonality


def verify_orthogonality(gens_pred):
    def get_angle(v, w):
        # Angle between vectors
        return v @ w / (torch.norm(v) * torch.norm(w))

    def get_axis(M):
        # Finds the eigenvector with min(Imaginary(eigenvalue))
        # if the matrix is a rotation matrix or a generator of rotation,s then this vector is the axis of rotation  
        eig_vals, eig_vecs = torch.linalg.eig(M)
        # find the minimum arg of the minimum imaginary component
        # pass that to the transposed eigenvector array to pull the eigenvector
        axis = eig_vecs.T[torch.argmin(torch.abs(eig_vals.imag))]
        # Change to more positive than negative values in axis vector by multiplying by the net sign
        return torch.sign(torch.sum(axis).real)*axis
    
    for i,G in enumerate(gens_pred):
        for j,H in enumerate(gens_pred):
            if i<j:
                angle = get_angle(get_axis(G).real, get_axis(H).real)
                angle_deg = 180/np.pi*np.arccos(float(get_angle(get_axis(G).real, get_axis(H).real)))
                print(f'Angle between generator {i+1} and {j+1}: {angle:>.10f} rad, {angle_deg:>.10f} deg')