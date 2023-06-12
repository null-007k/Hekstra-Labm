from careless.models.likelihoods.base import Likelihood
from careless.models.base import PerGroupModel
from tensorflow_probability import distributions as tfd
from tensorflow_probability import bijectors as tfb
import tensorflow_probability as tfp
import tensorflow as tf
import numpy as np


class LaueBase(Likelihood):
    @staticmethod
    def get_sparse_conv_tensor(harmonic_id, nobs):
        nobs = intensities.shape[0]
        npred = harmonic_id.shape[0]
        idx = tf.concat((harmonic_id, tf.range(npred, dtype='int64')[:,None]), axis=-1)
        dense_shape = (nobs, npred)
        sparse_conv_tensor = tf.SparseTensor(idx, tf.ones(npred), dense_shape)
        return sparse_conv_tensor

    def dist(self, loc, scale):
        raise NotImplementedError(
            """ Extensions of this class must implement self.location_scale_distribution(loc, scale) """
            )

    def call(self, inputs):
        harmonic_id   = self.get_harmonic_id(inputs)
        intensities   = self.get_intensities(inputs)
        uncertainties = self.get_uncertainties(inputs)
        sparse_conv_tensor = self.get_sparse_conv_tensor(harmonic_id, nobs)
        likelihood = self._dist(intensities, uncertainties)

        class ConvolvedLikelihood():
            """
            Convolved log probability object for Laue data.
            """
            def __init__(self, distribution, sparse_conv_tensor):
                self.sparse_conv_tensor = sparse_conv_tensor
                self.distribution = distribution

            def convolve(self, value):
                tf.sparse.sparse_dense_matmul(self.sparse_conv_tensor, value, adjoint_b=True)

            def mean(self, *args, **kwargs):
                return self.distribution.mean(*args, **kwargs)

            def stddev(self, *args, **kwargs):
                return self.distribution.stddev(*args, **kwargs)

            def log_prob(self, value):
                return self.distribution.log_prob(self.convolve(value))

        return ConvolvedLikelihood(likelihood, sparse_conv_tensor)

class NormalLikelihood(LaueBase):
    def dist(self, loc, scale):
        return tfd.Normal(loc, scale)

class LaplaceLikelihood(LaueBase):
    def dist(self, loc, scale):
        return tfd.Laplace(loc, scale)

class StudentTLikelihood(LaueBase):
    def __init__(self, dof):
        """
        Parameters
        ----------
        dof : float
            Degrees of freedom of the t-distributed error model.
        """
        super().__init__()
        self.dof = dof

    def dist(self, loc, scale):
        return tfd.StudentT(self.dof, loc, scale)

