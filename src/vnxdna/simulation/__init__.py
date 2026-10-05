"""``vnxdna.simulation`` (layer 5): SIMULATED DNA channels. No DNA is synthesised, stored or sequenced here.

* :mod:`~vnxdna.simulation.model`       ``vnx.channel-model/1`` documents (and reading ``/0`` and ``vnx.channel-config/0``)
* :mod:`~vnxdna.simulation.errormodels` generic error models (substitution, insertion, deletion, dropout, coverage,
                                        quality, composite) and the joint per-base kernel
* :mod:`~vnxdna.simulation.engine`      the staged simulator synthesis → storage → amplification → sequencing, with
                                        ``vnx.simulation-metadata/1`` for every run
* :mod:`~vnxdna.simulation.registry`    the named models shipped in ``vnxdna/simulation/models``
* :mod:`~vnxdna.simulation.montecarlo`  Monte Carlo trials and parameter sweeps
* :mod:`~vnxdna.simulation.channel`, :mod:`~vnxdna.simulation.loss`  the V4 channel and the V6 strand-loss model
  (unchanged; the engine reproduces them byte for byte for models that use only their mechanisms)
"""
