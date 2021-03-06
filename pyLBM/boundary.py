from __future__ import print_function
from __future__ import division
# Authors:
#     Loic Gouarin <loic.gouarin@math.u-psud.fr>
#     Benjamin Graille <benjamin.graille@math.u-psud.fr>
#
# License: BSD 3 clause

import numpy as np
from six.moves import range
import types
import collections

from .logs import setLogger
from .storage import Array
from .validate_dictionary import *

proto_bc = {
    'method':(is_dico_bcmethod, ),
    'value':(type(None), types.FunctionType, tuple),
}

class Boundary_Velocity(object):
    """
    Indices and distances for the label and the velocity ksym
    """
    def __init__(self, domain, label, ksym):
        self.label = label
        # on cherche les points de l'exterieur qui ont une vitesse qui rentre (indice ksym)
        # sur un bord labelise par label
        # on parcourt toutes les vitesses et on determine les points interieurs qui ont la vitesse
        # symmetrique (indice k) qui sort
        # puis on ecrit dans une liste reprenant l'ordre des vitesses du schema
        # - les indices des points exterieurs correspondants
        # - les distances associees
        self.v = domain.stencil.unique_velocities[ksym]
        v = self.v.get_symmetric()
        num = domain.stencil.unum2index[v.num]

        ind = np.where(domain.flag[num] == self.label)
        self.indices = np.array(ind)
        if self.indices.size != 0:
            self.indices += np.asarray(v.v)[:, np.newaxis]
        self.distance = np.array(domain.distance[(num,) + ind])

class Boundary(object):
    """
    Construct the boundary problem by defining the list of indices on the border and the methods used on each label.

    Parameters
    ----------
    domain : Domain class

    dico : a dictionary that describes the boundaries
        - key is a label
        - value are again a dictionnary with
            + "method" key that gives the boundary method class used (Bounce_back, Anti_bounce_back, ...)
            + "value_bc" key that gives the value on the boundary

    Attributes
    ----------
    bv : dictionnary
        for each label key, a list of spatial indices and distance define for each velocity the points
        on the domain that are on the boundary.

    methods : list
        list of boundary methods used in the LBM scheme
        The list contains Boundary_method instance.

    """
    def __init__(self, domain, dico):
        self.log = setLogger(__name__)
        self.domain = domain

        # build the list of indices for each unique velocity and for each label
        self.bv = {}
        for label in self.domain.list_of_labels():
            dummy_bv = []
            for k in range(self.domain.stencil.unvtot):
                dummy_bv.append(Boundary_Velocity(self.domain, label, k))
            self.bv[label] = dummy_bv

        # build the list of boundary informations for each stencil and each label
        dico_bound = dico.get('boundary_conditions',{})
        stencil = self.domain.stencil

        istore = collections.OrderedDict() # important to set the boundary conditions always in the same way !!!
        ilabel = {}
        distance = {}
        value_bc = {}

        for label in self.domain.list_of_labels():
            if label == -1: # periodic conditions
                pass
            elif label == -2: # interface conditions
                pass
            else: # non periodic conditions
                value_bc[label] = dico_bound[label].get('value', None)
                methods = dico_bound[label]['method']
                # for each method get the list of points, the labels and the distances
                # where the distribution function must be updated on the boundary
                for k, v in methods.items():
                    for inumk, numk in enumerate(stencil.num[k]):
                        if self.bv[label][stencil.unum2index[numk]].indices.size != 0:
                            indices = self.bv[label][stencil.unum2index[numk]].indices
                            distance_tmp = self.bv[label][stencil.unum2index[numk]].distance
                            velocity = (inumk + stencil.nv_ptr[k])*np.ones(indices.shape[1], dtype=np.int32)[np.newaxis, :]
                            ilabel_tmp = label*np.ones(indices.shape[1], dtype=np.int32)
                            istore_tmp = np.concatenate([velocity, indices])
                            if istore.get(v, None) is None:
                                istore[v] = istore_tmp.copy()
                                ilabel[v] = ilabel_tmp.copy()
                                distance[v] = distance_tmp.copy()
                            else:
                                istore[v] = np.concatenate([istore[v], istore_tmp], axis=1)
                                ilabel[v] = np.concatenate([ilabel[v], ilabel_tmp])
                                distance[v] = np.concatenate([distance[v], distance_tmp])

        # for each method create the instance associated
        self.methods = []
        for k in list(istore.keys()):
            self.methods.append(k(istore[k], ilabel[k], distance[k], stencil, value_bc))

class Boundary_method(object):
    """
    Set boundary method.
    
    Parameters
    ----------
    None

    Attributes
    ----------
    feq : NumPy array
       the equilibrium values of the distribution function on the border
    rhs : NumPy array
       the additional terms to fix the boundary values
    distance : NumPy array
       distance to the border (needed for Bouzidi type conditions)
    istore : NumPy array
    ilabel : NumPy array
    iload : list
    value_bc : dictionnary
       the prescribed values on the border

    Methods
    -------
    prepare_rhs :
        compute the distribution function at the equilibrium with the value on the border

    """
    def __init__(self, istore, ilabel, distance, stencil, value_bc):
        self.log = setLogger(__name__)
        self.istore = istore
        self.feq = np.zeros((stencil.nv_ptr[-1], istore.shape[1]))
        self.rhs = np.zeros(istore.shape[1])
        self.ilabel = ilabel
        self.distance = distance
        self.stencil = stencil
        self.iload = []
        self.value_bc = {}
        for k in np.unique(self.ilabel):
            self.value_bc[k] = value_bc[k]

    def prepare_rhs(self, simulation):
        nv = simulation._m.nv
        sorder = simulation._m.sorder
        nspace = [1]*(len(sorder)-1)
        v = self.stencil.get_all_velocities()

        for key, value in self.value_bc.items():
            if value is not None:
                indices = np.where(self.ilabel == key)
                # TODO: check the index in sorder to be the most contiguous
                nspace[0] = indices[0].size
                k = self.istore[0, indices]

                s = 1 - self.distance[indices]
                coords = tuple()
                for i in range(simulation.domain.dim):
                    x = simulation.domain.coords_halo[i][self.istore[i + 1, indices]]
                    x += s*v[k, i]*simulation.domain.dx
                    x = x.ravel()
                    for i in range(1, simulation.domain.dim):
                        x = x[:, np.newaxis]
                    coords += (x,)

                m = Array(nv, nspace , 0, sorder)
                m.set_conserved_moments(simulation.scheme.consm, self.stencil.nv_ptr)

                f = Array(nv, nspace , 0, sorder)
                f.set_conserved_moments(simulation.scheme.consm, self.stencil.nv_ptr)

                #TODO add error message and more tests
                if isinstance(value, types.FunctionType):
                    value(f, m, *coords)
                elif isinstance(value, tuple):
                    if len(value) != 2:
                        self.log.error("""Function set in boundary must be the function name or a tuple
                                       of size 2 with function name and extra args.""")
                    args = coords + value[1]
                    value[0](f, m, *args)
                simulation.scheme.equilibrium(m)
                simulation.scheme.m2f(m, f)

                self.feq[:, indices[0]] = f.swaparray.reshape((nv, indices[0].size))

class bounce_back(Boundary_method):
    """
    Boundary condition of type bounce-back

    Notes
    ------

    .. plot:: codes/bounce_back.py

    Methods
    -------
    set_rhs :
        compute and set the additional terms to fix the boundary values
    set_iload :
        compute the indices that are needed (symmertic velocities and space indices)
    update :
        update the values of the distribution fonctions ouside the domain
        according to the bounce back condition

    """
    def __init__(self, istore, ilabel, distance, stencil, value_bc):
        super(bounce_back, self).__init__(istore, ilabel, distance, stencil, value_bc)

    def set_iload(self):
        k = self.istore[0]
        ksym = self.stencil.get_symmetric()[k][np.newaxis, :]
        v = self.stencil.get_all_velocities()
        indices = self.istore[1:] + v[k].T
        self.iload.append(np.concatenate([ksym, indices]))

    def set_rhs(self):
        k = self.istore[0]
        ksym = self.stencil.get_symmetric()[k]
        self.rhs[:] = self.feq[k, np.arange(k.size)] - self.feq[ksym, np.arange(k.size)]

    def update(self, f):
        f[tuple(self.istore)] = f[tuple(self.iload[0])] + self.rhs

class Bouzidi_bounce_back(Boundary_method):
    """
    Boundary condition of type Bouzidi bounce-back [BFL01]

    Notes
    ------

    .. plot:: codes/Bouzidi.py

    Methods
    -------
    set_rhs :
        compute and set the additional terms to fix the boundary values
    set_iload :
        compute the indices that are needed (symmertic velocities and space indices)
    update :
        update the values of the distribution fonctions ouside the domain
        according to the Bouzidi bounce back condition

    """
    def __init__(self, istore, ilabel, distance, stencil, value_bc):
        super(Bouzidi_bounce_back, self).__init__(istore, ilabel, distance, stencil, value_bc)
        self.s = np.empty(self.istore.shape[1])

    def set_iload(self):
        k = self.istore[0]
        ksym = self.stencil.get_symmetric()[k]
        v = self.stencil.get_all_velocities()

        iload1 = np.zeros(self.istore.shape, dtype=np.int)
        iload2 = np.zeros(self.istore.shape, dtype=np.int)

        mask = self.distance < .5
        iload1[0, mask] = ksym[mask]
        iload2[0, mask] = ksym[mask]
        iload1[1:, mask] = self.istore[1:, mask] + v[k[mask]].T
        iload2[1:, mask] = self.istore[1:, mask] + 2*v[k[mask]].T
        self.s[mask] = 2.*self.distance[mask]

        mask = np.logical_not(mask)
        iload1[0, mask] = ksym[mask]
        iload2[0, mask] = k[mask]
        iload1[1:, mask] = self.istore[1:, mask] + v[k[mask]].T
        iload2[1:, mask] = self.istore[1:, mask] + v[k[mask]].T
        self.s[mask] = .5/self.distance[mask]

        self.iload.append(iload1)
        self.iload.append(iload2)

    def set_rhs(self):
        k = self.istore[0]
        ksym = self.stencil.get_symmetric()[k]
        self.rhs[:] = self.feq[k, np.arange(k.size)] - self.feq[ksym, np.arange(k.size)]

    def update(self, f):
        f[tuple(self.istore)] = self.s*f[tuple(self.iload[0])] + (1 - self.s)*f[tuple(self.iload[1])] + self.rhs

class anti_bounce_back(bounce_back):
    """
    Boundary condition of type anti bounce-back

    Notes
    ------

    .. plot:: codes/anti_bounce_back.py

    Methods
    -------
    set_rhs :
        compute and set the additional terms to fix the boundary values
    set_iload :
        compute the indices that are needed (symmertic velocities and space indices)
    update :
        update the values of the distribution fonctions ouside the domain
        according to the anti bounce back condition

    """
    def set_rhs(self):
        k = self.istore[0]
        ksym = self.stencil.get_symmetric()[k]
        self.rhs[:] = self.feq[k, np.arange(k.size)] + self.feq[ksym, np.arange(k.size)]

    def update(self, f):
        f[tuple(self.istore)] = -f[tuple(self.iload[0])] + self.rhs

class Bouzidi_anti_bounce_back(Bouzidi_bounce_back):
    """
    Boundary condition of type Bouzidi anti bounce-back

    Notes
    ------

    .. plot:: codes/Bouzidi.py

    Methods
    -------
    set_rhs :
        compute and set the additional terms to fix the boundary values
    set_iload :
        compute the indices that are needed (symmertic velocities and space indices)
    update :
        update the values of the distribution fonctions ouside the domain
        according to the Bouzidi anti bounce back condition

    """
    def set_rhs(self):
        k = self.istore[0]
        ksym = self.stencil.get_symmetric()[k]
        self.rhs[:] = self.feq[k, np.arange(k.size)] + self.feq[ksym, np.arange(k.size)]

    def update(self, f):
        f[tuple(self.istore)] = -self.s*f[tuple(self.iload[0])] + (self.s - 1)*f[tuple(self.iload[1])] + self.rhs

class Neumann(Boundary_method):
    """
    Boundary condition of type Neumann

    Methods
    -------
    set_rhs :
        compute and set the additional terms to fix the boundary values
    set_iload :
        compute the indices that are needed (symmertic velocities and space indices)
    update :
        update the values of the distribution fonctions ouside the domain
        according to the Neumann condition

    """
    def __init__(self, istore, ilabel, distance, stencil, value_bc):
        super(Neumann, self).__init__(istore, ilabel, distance, stencil, value_bc)

    def set_rhs(self):
        pass

    def set_iload(self):
        k = self.istore[0]
        v = self.stencil.get_all_velocities()
        indices = self.istore[1:] + v[k].T
        self.iload.append(np.concatenate([k[np.newaxis, :], indices]))

    def update(self, f):
        f[tuple(self.istore)] = f[tuple(self.iload[0])]

class Neumann_x(Neumann):
    """
    Boundary condition of type Neumann along the x direction

    Methods
    -------
    set_rhs :
        compute and set the additional terms to fix the boundary values
    set_iload :
        compute the indices that are needed (symmertic velocities and space indices)
    update :
        update the values of the distribution fonctions ouside the domain
        according to the Neumann condition along the x direction

    """
    def set_iload(self):
        k = self.istore[0]
        v = self.stencil.get_all_velocities()
        indices = self.istore[1:].copy()
        indices[0] += v[k].T[0]
        self.iload.append(np.concatenate([k[np.newaxis, :], indices]))

class Neumann_y(Neumann):
    """
    Boundary condition of type Neumann along the y direction

    Methods
    -------
    set_rhs :
        compute and set the additional terms to fix the boundary values
    set_iload :
        compute the indices that are needed (symmertic velocities and space indices)
    update :
        update the values of the distribution fonctions ouside the domain
        according to the Neumann condition along the y direction

    """
    def set_iload(self):
        k = self.istore[0]
        v = self.stencil.get_all_velocities()
        indices = self.istore[1:].copy()
        indices[1] += v[k].T[1]
        self.iload.append(np.concatenate([k[np.newaxis, :], indices]))

class Neumann_z(Neumann):
    """
    Boundary condition of type Neumann along the z direction

    Methods
    -------
    set_rhs :
        compute and set the additional terms to fix the boundary values
    set_iload :
        compute the indices that are needed (symmertic velocities and space indices)
    update :
        update the values of the distribution fonctions ouside the domain
        according to the Neumann condition along the z direction

    """
    def set_iload(self):
        k = self.istore[0]
        v = self.stencil.get_all_velocities()
        indices = self.istore[1:].copy()
        indices[1] += v[k].T[2]
        self.iload.append(np.concatenate([k[np.newaxis, :], indices]))

if __name__ == "__main__":
    #from pyLBM.elements import *
    #import geometry, domain
    import numpy as np

    # dim = 2
    # dx = .1
    # xmin, xmax, ymin, ymax = 0., 1., 0., 1.
    #
    # dico_geometry = {'dim':dim,
    #                  'box':{'x':[xmin, xmax], 'y':[ymin, ymax], 'label':[0,0,1,0]},
    #                  'Elements':[0],
    #                  0:{'Element':Circle([0.5*(xmin+xmax),0.5*(ymin+ymax)], 0.3),
    #                     'del':True,
    #                     'label':2}
    #                  }
    #
    # dico   = {'dim':dim,
    #           'eometry':dico_geometry,
    #           'space_step':dx,
    #           'number_of_schemes':1,
    #           0:{'velocities':range(9),}
    #           }
    #
    # geom = Geometry.Geometry(dico)
    # dom = Domain.Domain(geom,dico)
    # b = Boundary(dom, 2, 0)
    # print b.indices
    # print
    # print b.distance

    istore = np.arange(12).reshape((3, 4))
    b = Boundary_method(istore)

    def changek(k, stencil):
        ksym = stencil.get_symmetric()
        return ksym[k]

    def changei(i):
        return np.array([[1], [1]])+i

    b.add_iload(changek, changei)
    print(b.istore, b.iload)
