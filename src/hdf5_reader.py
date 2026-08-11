"""
HDF5 Fine-Resolution Channel Slice Reader
==========================================

Reads narrow channel slices from Parkes fine-res HDF5 files (~12 GB, 207M channels)
without loading the entire file into memory.

ROOT CAUSE OF PREVIOUS FAILURES:
  These files use the bitshuffle compression filter (HDF5 filter ID 32008).
  Standard h5py installations don't register this filter, so any attempt to
  read compressed data fails with: "OSError: Can't synchronously read data
  (can't open directory)"

FIX:
  Import ``hdf5plugin`` before ``h5py`` to register the bitshuffle filter.
  That's it. One import line fixes everything.

USAGE:
  from src.hdf5_reader import read_channel_slice, freq_to_chan, chan_to_freq

  # Read 200 channels centered on channel 91,674,873
  data = read_channel_slice("file.h5", center_chan=91674873, half_width=100)
  # data shape: (n_integrations, 2*half_width), dtype float32

  # Convert frequency to channel index
  chan = freq_to_chan(3000.093667, fch1=2743.95703125, foff=2.7939677e-06, nchans=207618048)
"""

import numpy as np

# CRITICAL: hdf5plugin MUST be imported before h5py to register the bitshuffle
# filter (ID 32008). Without this, reading any chunk from these files fails with
# "OSError: Can't synchronously read data (can't open directory)".
import hdf5plugin  # noqa: F401  -- imported for side effect: registers filters
import h5py


def read_channel_slice(h5_path, center_chan, half_width=100, t_start=None, t_stop=None):
    """
    Read a narrow channel slice from a fine-resolution HDF5 file.

    Parameters
    ----------
    h5_path : str
        Path to the HDF5 file.
    center_chan : int
        Center channel index.
    half_width : int
        Half-width of the slice in channels. Total channels read = 2 * half_width.
    t_start : int or None
        Start time integration index (inclusive). None = 0.
    t_stop : int or None
        Stop time integration index (exclusive). None = all.

    Returns
    -------
    data : np.ndarray
        Array of shape (n_times, 2*half_width), dtype float32.
        Squeezed from the (n_times, 1, n_chans) HDF5 layout.
    """
    chan_start = int(center_chan - half_width)
    chan_stop = int(center_chan + half_width)
    nchans = 207618048  # default for Parkes fine files

    with h5py.File(h5_path, 'r') as f:
        dset = f['data']
        nchans = dset.shape[2]
        n_times = dset.shape[0]

        # Clamp channel range
        if chan_start < 0:
            chan_start = 0
        if chan_stop > nchans:
            chan_stop = nchans

        # Default time range
        if t_start is None:
            t_start = 0
        if t_stop is None:
            t_stop = n_times

        # Read the slice - hdf5plugin handles decompression transparently
        data = dset[t_start:t_stop, 0, chan_start:chan_stop]

    # Squeeze the feed-id axis (shape: n_times x n_chans)
    return np.squeeze(data, axis=-2) if data.ndim == 3 else data


def freq_to_chan(freq_mhz, fch1=None, foff=None, nchans=None, h5_path=None):
    """
    Convert frequency in MHz to channel index.

    Either provide header values directly, or pass h5_path to read them.

    Parameters
    ----------
    freq_mhz : float
        Frequency in MHz.
    fch1 : float or None
        Frequency of channel 0 in MHz.
    foff : float or None
        Channel spacing in MHz (can be negative).
    nchans : int or None
        Total number of channels.
    h5_path : str or None
        If provided, read header from this file.

    Returns
    -------
    chan : int
        Channel index closest to the given frequency.
    """
    if h5_path is not None:
        with h5py.File(h5_path, 'r') as f:
            attrs = f['data'].attrs
            fch1 = float(attrs['fch1'])
            foff = float(attrs['foff'])
            nchans = int(attrs['nchans'])

    chan = int(round((freq_mhz - fch1) / foff))
    if chan < 0:
        chan = 0
    elif chan >= nchans:
        chan = nchans - 1
    return chan


def chan_to_freq(chan, fch1=None, foff=None, h5_path=None):
    """
    Convert channel index to frequency in MHz.

    Parameters
    ----------
    chan : int
        Channel index.
    fch1 : float or None
        Frequency of channel 0 in MHz.
    foff : float or None
        Channel spacing in MHz.
    h5_path : str or None
        If provided, read header from this file.

    Returns
    -------
    freq_mhz : float
    """
    if h5_path is not None:
        with h5py.File(h5_path, 'r') as f:
            attrs = f['data'].attrs
            fch1 = float(attrs['fch1'])
            foff = float(attrs['foff'])

    return fch1 + chan * foff


def get_header(h5_path):
    """
    Read the filterbank header from an HDF5 file.

    Returns
    -------
    header : dict
        Dictionary with keys: fch1, foff, nchans, tsamp, source_name,
        src_dej, src_raj, tstart, telescope_id, nbits, nifs, etc.
    """
    with h5py.File(h5_path, 'r') as f:
        attrs = f['data'].attrs
        header = {}
        for key in attrs:
            val = attrs[key]
            if isinstance(val, np.ndarray) and val.dtype == object:
                # Skip array-of-strings attrs like DIMENSION_LABELS
                continue
            try:
                header[key] = float(val) if isinstance(val, (np.floating, np.integer)) else val
            except (TypeError, ValueError):
                header[key] = str(val)
        return header
