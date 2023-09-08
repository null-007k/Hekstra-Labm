import reciprocalspaceship as rs
import numpy as np

class ReciprocalASU():
    def __init__(self, cell, spacegroup, dmin, anomalous):
        """
        Parameters
        ----------
        cell : gemmi.UnitCell
            A gemmi.UnitCell instance
        spacegroup : gemmi.SpaceGroup
            A gemmi.SpaceGroup instance
        dmin : float
            The maximum resolution in Angstroms
        anomalous : bool
            Whether the ASU includes Friedel minus acentric reflections
        """
        self.cell = cell
        self.spacegroup = spacegroup
        self.dmin = dmin
        self.anomalous = anomalous
        self.Hall = rs.utils.generate_reciprocal_asu(
                self.cell, 
                self.spacegroup, 
                self.dmin, 
                self.anomalous
            )
        h,k,l = self.Hall.T
        lookup_table = rs.DataSet({
            'H' : h,
            'K' : k,
            'L' : l,
            'id' : np.arange(len(h))
            }, 
            cell=cell,
            spacegroup=spacegroup,
        ).compute_multiplicity().label_centrics()
        self.lookup_table = lookup_table

    @property
    def centric(self):
        """ boolean array true for centric refl_ids """
        return self.lookup_table.CENTRIC.to_numpy('bool')

    @property
    def multiplicity(self):
        """ the multiplicity of each structure factor """
        return self.lookup_table.EPSILON.to_numpy('float32')

    def to_refl_id(self, H):
        """
        Parameters
        ----------
        H : np.array
            (n x 3) array of miller indices to convert to integer reflection ids

        Returns
        -------
        refl_id : np.array
            (n x 1) array of integer reflection ids
        """
        return self.lookup_table.set_index(['H','K','L']).loc[H.tolist(), 'id']

    def to_miller_index(self, refl_id):
        """
        Parameters
        ----------
        refl_id : np.array
            (n x 1) array of integer reflection ids to convert to miller indices

        Returns
        -------
        H : np.array
            (n x 3) array of miller indices 
        """
        return self.lookup_table.set_index('id').loc[refl_id].get_hkls()

class ReciprocalASUCollection():
    def __init__(self, reciprocal_asus):
        """
        Parameters
        ----------
        reciprocal_asus : list
            A list of ReciprocalAsu instances
        """
        self.reciprocal_asus = reciprocal_asus
        self.lookup_table = None
        for asu_id,asu in enumerate(self.reciprocal_asus):
            tab = asu.lookup_table.copy()
            tab['asu_id'] = asu_id
            if self.lookup_table is not None:
                tab['id'] += len(self.lookup_table)
                self.lookup_table = rs.concat((self.lookup_table, tab), check_isomorphous=False)
            else:
                self.lookup_table =  tab
        self.asu_and_miller_lookup_table = self.lookup_table.set_index(['asu_id', 'H', 'K', 'L'])
        self.refl_id_lookup_table = self.lookup_table.set_index('id')

    @property
    def centric(self):
        """ boolean array true for centric refl_ids """
        return self.refl_id_lookup_table.CENTRIC.to_numpy('bool')

    @property
    def multiplicity(self):
        """ the multiplicity of each structure factor """
        return self.refl_id_lookup_table.EPSILON.to_numpy('float32')

    @property
    def hkls(self):
        """ the multiplicity of each structure factor """
        return self.refl_id_lookup_table.get_hkls()

    @property
    def asu_ids(self):
        """ the multiplicity of each structure factor """
        return self.refl_id_lookup_table.asu_id.to_numpy('int')

    def to_asu_id_and_miller_index(self, refl_id):
        """
        Parameters
        ----------
        refl_id : np.array
            (n x 1) array of integer reflection ids to convert to miller indices

        Returns
        -------
        asu_id : np.array
            (n x 1) array of asu ids 
        H : np.array
            (n x 3) array of miller indices 
        """
        data = self.refl_id_lookup_table.loc[refl_id]
        H = data.get_hkls()
        asu_id = data['asu_id'].to_numpy('int')
        return asu_id, H

    def to_refl_id(self, asu_id, H):
        """
        Parameters
        ----------
        asu_id : np.array
            (n x 1) array of asu ids 
        H : np.array
            (n x 3) array of miller indices 

        Returns
        -------
        refl_id : np.array
            (n x 1) array of integer reflection ids to convert to miller indices
        """
        index = np.concatenate((asu_id, H), axis=1).astype('int')
        return self.asu_and_miller_lookup_table.loc[index.tolist(), 'id'].to_numpy('int')


