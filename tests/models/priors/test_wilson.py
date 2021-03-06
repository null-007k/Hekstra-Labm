from careless.models.priors.wilson import *
import tensorflow_probability as tfp
from tensorflow_probability import distributions as tfd
import pytest
import numpy as np

from careless.utils.device import disable_gpu
status = disable_gpu()
assert status



def test_Centric():
    E = np.linspace(0.1, 3., 100)
    p = (2./np.pi)**0.5 *np.exp(-0.5*E**2.)
    centric = Centric(1.)
    centric.mean()
    centric.stddev()
    assert np.all(np.isclose(p, centric.prob(E)))
    assert np.all(np.isclose(np.log(p), centric.log_prob(E)))

def test_Acentric():
    acentric = Acentric(1.)
    E = np.linspace(0.1, 3., 100)
    p = 2.*E*np.exp(-E**2.)
    acentric.mean()
    acentric.stddev()
    assert np.all(np.isclose(p, acentric.prob(E)))
    assert np.all(np.isclose(np.log(p), acentric.log_prob(E)))

@pytest.mark.parametrize('mc_samples', [(), 1, 3])
def test_Wilson(mc_samples):
    centric = np.random.randint(0, 2, 100).astype(np.float32)
    epsilon = np.random.randint(1, 6, 100).astype(np.float32)
    prior = WilsonPrior(centric, epsilon)
    F = np.random.random(100).astype(np.float32)
    probs = prior.prob(F)
    log_probs = prior.log_prob(F)
    assert np.all(np.isfinite(probs))
    assert np.all(np.isfinite(log_probs))

    #This part checks indexing and gradient numerics
    q = tfd.TruncatedNormal( #<-- use this dist because wilson has positive support
        tf.Variable(prior.mean()), 
        tfp.util.TransformedVariable( 
            prior.stddev(),
            tfp.bijectors.Softplus(),
        ),
        low=1e-5,
        high=1e10,
    )
    with tf.GradientTape() as tape:
        z = q.sample(mc_samples)
        log_probs = prior.log_prob(z)
    grads = tape.gradient(log_probs, q.trainable_variables)

    assert np.all(np.isfinite(log_probs))
    for grad in grads:
        assert np.all(np.isfinite(grad))

