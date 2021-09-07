import gemmi
import numpy as np
import tensorflow as tf
import pandas as pd
import reciprocalspaceship as rs


def get_first_key_of_type(ds, typestring):
    idx = ds.dtypes=='J'
    if idx.sum() < 1:
        raise KeyError(f"No key matching typestring {typestring}")
    else:
        return ds.dtypes[idx].keys()[0]
        



class BaseMerger():
    """
    This Merger object is for combining all experiments into a single set of Structure Factors.

    Special keys in MergerBase.data
        file_id   : numeric index for mtz file populated at load time
        miller_id : unique numeric index of miller indices
        observation_id : unique index for each reflection observation

    Attributes
    ----------
    results : rs.DataSet
    data : rs.DataSet
    merger : careless.models.merging.VariationalMergingModel
    """
    results = None
    merger = None
    data = None
    spacegroup = None
    prior = None
    likelihood = None
    scaling_model = None
    intensity_key = None
    sigma_intensity_key = None

    def __init__(self, dataset, anomalous=False):
        self.data = dataset.copy() #chaos ensues otherwise

        if self.data.index.names != [None]: #If you have a non-numeric index
            self.data.reset_index(inplace=True)
        self.data[['Hobs', 'Kobs', 'Lobs']] = self.data.loc[:,['H', 'K', 'L']]

        # 2020-07-30 The current pypi DataSet version cannot handle hkl_to_asu unless the index is ['H', 'K', 'L']
        # The next line can be removed after the next release.
        self.data.set_index(['H', 'K', 'L'], inplace=True) 

        self.data.hkl_to_asu(inplace=True)

        # 2020-07-30 The current pypi DataSet version cannot handle hkl_to_asu unless the index is ['H', 'K', 'L']
        # The next line can be removed after the next release.
        self.data.reset_index(inplace=True) #Return to numerical indices

        if anomalous:
            friedel_sign = 2 * (self.data['M/ISYM'] %2 - 0.5).to_numpy()
            self.data.loc[:,['H', 'K', 'L']] = friedel_sign[:,None] * self.data.loc[:,['H', 'K', 'L']]

        # Try to guess sensible default keys. 
        # The user can change after the constructor is finished
        self.intensity_key = get_first_key_of_type(self.data, "J")
        if f'Sig{self.intensity_key}' in self.data:
            self.sigma_intensity_key = f'Sig{self.intensity_key}'
        elif f'SIG{self.intensity_key}' in self.data:
            self.sigma_intensity_key = f'SIG{self.intensity_key}'
        else:
            self.sigma_intensity_key = get_first_key_of_type(self.data, "Q")

        self.metadata_keys = list(self.data.dtypes[self.data.dtypes == 'R'].keys() )
        self.metadata_keys += list(self.data.dtypes[self.data.dtypes == 'B'].keys() )
        if self.data.file_id.max() > 0:
            self.metadata_keys += ['file_id']
        self.metadata_keys += ['dHKL']

    @classmethod
    def from_isomorphous_mtzs(cls, *filenames, anomalous=False):
        from careless.utils.io import load_isomorphous_mtzs
        return cls(load_isomorphous_mtzs(*filenames), anomalous)

    def append_anomalous_data(self, mtz_filename):
        raise NotImplementedError("This module does not support priors with anomalous differences yet")

    def append_reference_data(self, data):
        """Append reference data from an rs.DataSet or an Mtz filename."""
        #For now we don't support having different amplitudes for friedel mates
        #But we must make sure to copy amplitudes to their friedel mate before we append the reference
        #in case the data being merged is anomalous
        if isinstance(data, str):
            ds = rs.read_mtz(data)
        elif isinstance(data, rs.DataSet):
            ds = data
            if data.index.names != ['H', 'K', 'L']:
                ds.reset_index().set_index(['H', 'K', 'L'])
        else:
            raise TypeError(f"append_reference_data expected string or rs.DataSet, but received {type(ds)}")

        cell,sg = self.data.cell,self.data.spacegroup
        ds = pd.concat((ds, ds.apply_symop(gemmi.Op("-x, -y, -z"))))
        self.data = self.data.join(ds.loc[:,ds.dtypes=='F'].iloc[:,0].rename("REF"), on=['H', 'K', 'L'])
        self.data = self.data.join(ds.loc[:,ds.dtypes=='Q'].iloc[:,0].rename("SIGREF"), on=['H', 'K', 'L'])
        self.data.cell,self.data.spacegroup = cell,sg
        return self

    def set_merging_spacegroup(sg):
        """
        Parameters
        ----------
        sg : gemmi.SpaceGroup or str or int
        """
        if isinstance(sg, str):
            self.spacegroup = gemmi.SpaceGroup(sg)
        elif isinstance(sg, int):
            self.spacegroup = gemmi.SpaceGroup(sg)
        elif isinstance(sg, gemmi.SpaceGroup):
            self.spacegroup = sg
        else:
            raise ValueError(f"Set_merging_spacegroup received unexpected argument type {type(sg)}")

    def get_results(self):
        df = self.data.reset_index()
        results = rs.DataSet(cell = self.data.cell, spacegroup = self.data.spacegroup)
        results['F'] = self.merger.surrogate_posterior.mean()
        results['SigF'] = self.merger.surrogate_posterior.stddev()
        results['dHKL'] = df.groupby('miller_id').first()['dHKL']
        results['H'] = df.groupby('miller_id')['H'].first()  
        results['K'] = df.groupby('miller_id')['K'].first()  
        results['L'] = df.groupby('miller_id')['L'].first()  
        results['experiment_id'] = df.groupby('miller_id')['experiment_id'].first()  
        keys = ['H', 'K', 'L', 'F', 'SigF', 'experiment_id']
        results.infer_mtz_dtypes(inplace=True)
        results.set_index(['H', 'K', 'L'], inplace=True)
        return results

    def prep_indices(self, image_id_key='BATCH', experiment_id_key='file_id'):
        #This is for merging equivalent millers accross mtzs
        df = self.data.copy().dropna() #There will be nans if reference data were added
        df['miller_id'] = df.groupby(['H', 'K', 'L']).ngroup() 
        df['image_id'] = df.groupby([image_id_key, experiment_id_key]).ngroup()
        df['observation_id'] = df.groupby(['miller_id', 'image_id']).ngroup()
        self.data = df
        return self

    def train_model(self, iterations, mc_samples=1, learning_rate=0.01):
        from careless.models.merging.variational import VariationalMergingModel

        self.merger = VariationalMergingModel(
            self.data['miller_id'].to_numpy().astype(np.int32),
            [self.scaling_model],
            self.prior,
            self.likelihood,
        )

        optimizer = tf.keras.optimizers.Adam(learning_rate)#, clipvalue=.1)
        losses = self.merger.fit(optimizer, iterations, s=mc_samples)
        self.results = self.get_results()
        return losses

    def _add_reference_prior(self, priorfun, reference_f_key="REF", reference_sigf_key="SIGREF"):
        f = self.data.groupby('miller_id').first()[reference_f_key].to_numpy().astype(np.float32)
        sigf = self.data.groupby('miller_id').first()[reference_sigf_key].to_numpy().astype(np.float32)
        self.prior = priorfun(f, sigf)

    def add_laplace_prior(self, reference_f_key='REF', reference_sigf_key='SIGREF'):
        from careless.models.priors.empirical import LaplaceReferencePrior
        self._add_reference_prior(LaplaceReferencePrior)

    def add_normal_prior(self, reference_f_key='REF', reference_sigf_key='SIGREF'):
        from careless.models.priors.empirical import NormalReferencePrior
        self._add_reference_prior(NormalReferencePrior)

    def add_studentt_prior(self, dof, reference_f_key='REF', reference_sigf_key='SIGREF'):
        from careless.models.priors.empirical import StudentTReferencePrior
        self._add_reference_prior(lambda x,y : StudentTReferencePrior(x, y, dof))

    def add_wilson_prior(self):
        from careless.models.priors.wilson import WilsonPrior
        centric = self.data.groupby('miller_id').first().CENTRIC.to_numpy().astype(np.float32)
        epsilon = self.data.groupby('miller_id').first().EPSILON.to_numpy().astype(np.float32)
        self.prior = WilsonPrior(centric, epsilon)

    def add_scaling_model(self, layers=20, metadata_keys=None):
        """
        Parameters
        ----------
        layers : int
            Sequential dense leaky relu layers. The default is 20.
        metadata_keys : list
            List of keys to use for generating the metadata. If None, self.metadata_keys will be used.
        """
        if metadata_keys is None:
            metadata_keys = self.metadata_keys
        if metadata_keys is None:
            raise TypeError("metadata_keys has type None but list expected.")
        metadata = self.data[metadata_keys].to_numpy().astype(np.float32)
        metadata = (metadata - metadata.mean(0))/metadata.std(0)
        from careless.models.scaling.nn import SequentialScaler
        self.scaling_model = SequentialScaler(metadata, layers)

class HarmonicDeconvolutionMixin:
    def expand_harmonics(self, dmin=None, wavelength_key='Wavelength', wavelength_range=None):
        from careless.utils.laue import expand_harmonics
        expanded = self.data.compute_dHKL() #add 'dHKL' if missing. do not trust if present
        if wavelength_range is None:
            lambda_min = expanded[wavelength_key].min()
            lambda_max = expanded[wavelength_key].max()
        else:
            lambda_min, lambda_max = wavelength_range

        expanded = expand_harmonics(expanded, dmin=None, wavelength_key='Wavelength')
        self.data = expanded[(expanded[wavelength_key] >= lambda_min) & (expanded[wavelength_key] <= lambda_max)]
        return self

class PolyMerger(BaseMerger, HarmonicDeconvolutionMixin):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.expand_harmonics()

    def prep_indices(self, separate_files=False, image_id_key='BATCH', experiment_id_key='file_id'):
        """
        Parameters
        ----------
        separate_files : bool
            Default is False. If True, miller indices originating from different input files will be kept separate.
        image_id_key : str
            Key used to identify which image an observation originated from. Default is 'BATCH'. 
        file_id_key : str
            Key used to identify which image an observation originated from. 
            Default is 'file_id' which is populated by the MergerBase.from_isomorphous_mtzs constructor. 
        """
        #This is for merging equivalent millers accross mtzs
        df = self.data.copy() #There will be nans if reference data were added
        df['null'] = df.isnull().any(axis=1)

        # Any observation that contains a constituent harmonic missing reference data must be removed en bloc
        # This is a quick way of doing that without invoking groupby.filter with a lambda (very slow)
        obs_group_keys = ['H_0', 'K_0', 'L_0', image_id_key, experiment_id_key]
        idx = df[obs_group_keys + ['null']].groupby(obs_group_keys).transform('any').to_numpy()
        df = df[~idx]
        del(df['null'])

        if separate_files:
            df['miller_id'] = df.groupby(['H', 'K', 'L', experiment_id_key]).ngroup() 
            df['experiment_id'] = df[experiment_id_key]
        else:
            df['miller_id'] = df.groupby(['H', 'K', 'L']).ngroup() 
            df['experiment_id'] = 0

        df['image_id'] = df.groupby([image_id_key, 'experiment_id']).ngroup()
        df['ray_id'] = df.groupby(['H_0','K_0', 'L_0']).ngroup()
        df['observation_id'] = df.groupby(['ray_id', 'image_id']).ngroup()
        df.label_centrics(inplace=True)
        df['EPSILON'] = rs.utils.compute_structurefactor_multiplicity(df.get_hkls(), df.spacegroup)
        df.compute_dHKL(inplace=True)
        df['dHKL'] = df.dHKL**-2.
        self.data = df
        return self

    def _add_likelihood(self, likelihood_func):
        iobs    = self.data.groupby('observation_id').first()[self.intensity_key].to_numpy().astype(np.float32)
        sigiobs = self.data.groupby('observation_id').first()[self.sigma_intensity_key].to_numpy().astype(np.float32)
        harmonic_id = self.data.observation_id.to_numpy().astype(np.int32)
        self.likelihood = likelihood_func(iobs, sigiobs, harmonic_id)

    def add_normal_likelihood(self):
        from careless.models.likelihoods.laue import NormalLikelihood
        self._add_likelihood(NormalLikelihood)

    def add_laplace_likelihood(self):
        from careless.models.likelihoods.laue import LaplaceLikelihood
        self._add_likelihood(LaplaceLikelihood)

    def add_studentt_likelihood(self, dof):
        from careless.models.likelihoods.laue import StudentTLikelihood
        self._add_likelihood(lambda x,y,z : StudentTLikelihood(x, y, z, dof))

class MonoMerger(BaseMerger):
    def prep_indices(self, separate_files=False, image_id_key='BATCH', experiment_id_key='file_id'):
        """
        Parameters
        ----------
        separate_files : bool
            Default is False. If True, miller indices originating from different input files will be kept separate.
        image_id_key : str
            Key used to identify which image an observation originated from. Default is 'BATCH'. 
        file_id_key : str
            Key used to identify which image an observation originated from. 
            Default is 'file_id' which is populated by the MergerBase.from_isomorphous_mtzs constructor. 
        """
        #This is for merging equivalent millers accross mtzs
        df = self.data.copy().dropna() #There will be nans if reference data were added
        if separate_files:
            df['miller_id'] = df.groupby(['H', 'K', 'L', experiment_id_key]).ngroup() 
            df['experiment_id'] = df[experiment_id_key]
        else:
            df['miller_id'] = df.groupby(['H', 'K', 'L']).ngroup() 
            df['experiment_id'] = 0
        df['image_id'] = df.groupby([image_id_key, experiment_id_key]).ngroup()
        df['observation_id'] = df.groupby(['miller_id', 'image_id']).ngroup()
        df.label_centrics(inplace=True)
        df['EPSILON'] = rs.utils.compute_structurefactor_multiplicity(df.get_hkls(), df.spacegroup)
        df.compute_dHKL(inplace=True)
        df['dHKL'] = df.dHKL**-2.
        self.data = df
        return self

    def _add_likelihood(self, likelihood_func):
        iobs = self.data[self.intensity_key].to_numpy().astype(np.float32)
        sigiobs = self.data[self.sigma_intensity_key].to_numpy().astype(np.float32)
        self.likelihood = likelihood_func(iobs, sigiobs)

    def add_normal_likelihood(self):
        from careless.models.likelihoods.mono import NormalLikelihood
        self._add_likelihood(NormalLikelihood)

    def add_laplace_likelihood(self):
        from careless.models.likelihoods.mono import LaplaceLikelihood
        self._add_likelihood(LaplaceLikelihood)

    def add_studentt_likelihood(self, dof):
        from careless.models.likelihoods.mono import StudentTLikelihood
        self._add_likelihood(lambda x,y : StudentTLikelihood(x, y, dof))
