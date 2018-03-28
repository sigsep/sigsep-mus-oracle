import musdb
import museval
import numpy as np
import itertools
import functools
import argparse
from scipy.signal import stft, istft


def invert(M, eps):
    """"inverting matrices M (matrices are the two last dimensions).
    This is assuming that these are 2x2 matrices, using the explicit
    inversion formula available in that case."""
    invDet = 1.0/(eps + M[..., 0, 0]*M[..., 1, 1] - M[..., 0, 1]*M[..., 1, 0])
    invM = np.zeros(M.shape, dtype='complex')
    invM[..., 0, 0] = invDet*M[..., 1, 1]
    invM[..., 1, 0] = -invDet*M[..., 1, 0]
    invM[..., 0, 1] = -invDet*M[..., 0, 1]
    invM[..., 1, 1] = invDet*M[..., 0, 0]
    return invM


def MWF(track, eval_dir=None):
    """Multichannel Wiener Filter:
    processing all channels jointly with the ideal multichannel filter
    based on the local gaussian model, assuming time invariant spatial
    covariance matrix."""

    # to avoid dividing by zero
    eps = np.finfo(np.float).eps

    # parameters for STFT
    nfft = 2048

    # compute STFT of Mixture
    N = track.audio.shape[0]  # remember number of samples for future use
    X = stft(track.audio.T, nperseg=nfft)[-1]
    (I, F, T) = X.shape

    # Allocate variables P: PSD, R: Spatial Covarianc Matrices
    P = {}
    R = {}
    for name, source in track.sources.items():

        # compute STFT of target source
        Yj = stft(source.audio.T, nperseg=nfft)[-1]

        # Learn Power Spectral Density and spatial covariance matrix
        # -----------------------------------------------------------

        # 1/ compute observed covariance for source
        Rjj = np.zeros((F, T, I, I), dtype='complex')
        for (i1, i2) in itertools.product(range(I), range(I)):
            Rjj[..., i1, i2] = Yj[i1, ...] * np.conj(Yj[i2, ...])

        # 2/ compute first naive estimate of the source spectrogram as the
        #    average of spectrogram over channels
        P[name] = np.mean(np.abs(Yj)**2, axis=0)

        # 3/ take the spatial covariance matrix as the average of
        #    the observed Rjj weighted Rjj by 1/Pj. This is because the
        #    covariance is modeled as Pj Rj
        R[name] = np.mean(Rjj / (eps+P[name][..., None, None]), axis=1)

        # add some regularization to this estimate: normalize and add small
        # identify matrix, so we are sure it behaves well numerically.
        R[name] = R[name] * I / np.trace(R[name]) + eps * np.tile(
            np.eye(I, dtype='complex64')[None, ...], (F, 1, 1)
        )

        # 4/ Now refine the power spectral density estimate. This is to better
        #    estimate the PSD in case the source has some correlations between
        #    channels.

        #    invert Rj
        Rj_inv = invert(R[name], eps)

        #    now compute the PSD
        P[name] = 0
        for (i1, i2) in itertools.product(range(I), range(I)):
            P[name] += 1./I*np.real(
                Rj_inv[:, i1, i2][:, None]*Rjj[..., i2, i1]
            )

    # All parameters are estimated. compute the mix covariance matrix as
    # the sum of the sources covariances.
    Cxx = 0
    for name, source in track.sources.items():
        Cxx += P[name][..., None, None]*R[name][:, None, ...]

    # we need its inverse for computing the Wiener filter
    invCxx = invert(Cxx, eps)

    # now separate sources
    estimates = {}
    accompaniment_source = 0
    for name, source in track.sources.items():
        # computes multichannel Wiener gain as Pj Rj invCxx
        G = np.zeros(invCxx.shape, dtype='complex64')
        SR = P[name][..., None, None]*R[name][:, None, ...]
        for (i1, i2, i3) in itertools.product(range(I), range(I), range(I)):
            G[..., i1, i2] += SR[..., i1, i3]*invCxx[..., i3, i2]
        SR = 0  # free memory

        # separates by (matrix-)multiplying this gain with the mix.
        Yj = 0
        for i in range(I):
            Yj += G[..., i]*X[i, ..., None]
        Yj = np.rollaxis(Yj, -1)  # gets channels back in first position

        # inverte to time domain
        target_estimate = istft(Yj)[1].T[:N, :]

        # set this as the source estimate
        estimates[name] = target_estimate

        # accumulate to the accompaniment if this is not vocals
        if name != 'vocals':
            accompaniment_source += target_estimate

    estimates['accompaniment'] = accompaniment_source

    if eval_dir is not None:
        museval.eval_mus_track(
            track,
            estimates,
            output_dir=eval_dir,
        )

    return estimates


if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description='Evaluate Multichannel Wiener Filter'
    )
    parser.add_argument(
        '--audio_dir',
        nargs='?',
        help='Folder where audio results are saved'
    )

    parser.add_argument(
        '--eval_dir',
        nargs='?',
        help='Folder where evaluation results are saved'
    )

    args = parser.parse_args()

    # initiate musdb
    mus = musdb.DB()

    mus.run(
        functools.partial(
            MWF, eval_dir=args.eval_dir
        ),
        estimates_dir=args.audio_dir,
        subsets='test',
        parallel=True,
        cpus=2
    )
