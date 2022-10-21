import os
import re
from astropy import units as u
from astropy.cosmology import FlatLambdaCDM
import h5py
import pandas as pd

import numpy as np
import numpy.ma as ma
from numpy.random import default_rng
from dust_extinction.parameter_averages import F19
import galsim

__all__ = ['ObservedSedFactory', 'Extinguisher', 'AB_mag']
class ObservedSedFactory:
    _clight = 3e8  # m/s
    _to_W_per_Hz = 4.4659e13  # conversion of cosmoDC2 tophat Lnu values to W/Hz
    def __init__(self, config, delta_wl=0.001):
        # Get wavelength and frequency bin boundaries.
        bins = config['SED_models']['tophat']['bins']
        wl0 = [_[0] for _ in bins]

        # Find index of original bin which includes 500 nm == 5000 ang
        ix = -1
        for w in wl0:
            if w > 5000:
                break
            ix += 1

        self._ix_500nm = ix

        wl0.append(bins[-1][0] + bins[-1][1])

        #wl0 = [0.0] + wl0
        # Prepend a 1nm bin
        ## no, don't
        ##wl0 = [wl0[0] - 10.0] + wl0
        wl0 = 0.1*np.array(wl0)
        self.wl = np.array(wl0)
        self.nu = self._clight/(self.wl*1e-9)  # frequency in Hz

        # Also save version of wl where vertical rise is replaced by
        # steep slope
        wl_deltas = []
        for i in range(len(bins)):
            wl_deltas.extend((self.wl[i], self.wl[i+1] - delta_wl))

        # Prepend more bins which will have 0 value
        n_bins = int(wl0[0]) - 1
        pre_wl = [float(i) for i in range(n_bins)]

        wl_deltas = np.insert(wl_deltas, 0, pre_wl)

        # Also make a matching array of 0 values
        self.pre_val = [0.0 for i in range(n_bins)]

        self._wl_deltas = wl_deltas
        self._wl_deltas_u_nm = wl_deltas*u.nm

        # Create a FlatLambdaCDM cosmology from a dictionary of input
        # parameters.  This code is based on/borrowed from
        # https://github.com/LSSTDESC/gcr-catalogs/blob/master/GCRCatalogs/cosmodc2.py#L128
        cosmo_astropy_allowed = FlatLambdaCDM.__init__.__code__.co_varnames[1:]
        cosmo_astropy = {k: v for k, v in config['Cosmology'].items()
                         if k in cosmo_astropy_allowed}
        self.cosmology = FlatLambdaCDM(**cosmo_astropy)

        self.sims_sed_library_dir = os.getenv('SIMS_SED_LIBRARY_DIR')

    # Useful for getting magnorm from f_nu values
    @property
    def ix_500nm(self):
        return self._ix_500nm

    @property
    def wl_deltas(self):
        return self._wl_deltas

    @property
    def wl_deltas_u_nm(self):
        return self._wl_deltas_u_nm

    def dl(self, z):
        """
        Return the luminosity distance in units of meters.
        """
        # Conversion factor from Mpc to meters (obtained from pyccl).
        MPC_TO_METER = 3.085677581491367e+22
        return self.cosmology.luminosity_distance(z).value*MPC_TO_METER

    def create(self, Lnu, redshift_hubble, redshift):
        '''
        Given tophat values from cosmoDC2 produce redshifted sed.
        Does not apply extinction.
        '''
        # Insert 0 value to line up with wl, nu
        ## no, don't
        ##Lnu = np.insert(Lnu, 0, 0.0)
        # Compute Llambda in units of W/nm
        Llambda = (Lnu*self._to_W_per_Hz*(self.nu[:-1] - self.nu[1:])
                   /(self.wl[1:] - self.wl[:-1]))

        # Fill the arrays for the galsim.LookupTable.   Prepend
        # zero-valued bins down to mix extinction wl to handle redshifts z > 2.
        my_Llambda = []
        my_Llambda += self.pre_val
        for i in range(len(Llambda)):
            # Dealt with wl already in __init__
            #my_wl.extend((self.wl[i], self.wl[i+1] - delta_wl))
            my_Llambda.extend((Llambda[i], Llambda[i]))

        # Convert to (unredshifted) flux given redshift_hubble.
        flambda = np.array(my_Llambda)/(4.0*np.pi*self.dl(redshift_hubble)**2)

        # Convert to cgs units
        flambda *= (1e7/1e4)  # (erg/joule)*(m**2/cm**2)

        # Create the lookup table.
        #lut = galsim.LookupTable(my_wl, flambda, interpolant='nearest')
        lut = galsim.LookupTable(self.wl_deltas, flambda, interpolant='nearest')

        # Create the SED object and apply redshift.
        sed = galsim.SED(lut, wave_type='nm', flux_type='flambda')\
                    .atRedshift(redshift)

        return sed

    def create_pointsource(self, rel_path, redshift=0):
        '''
        Return a galsim SED from information in a file
        '''
        fpath = os.path.join(self.sims_sed_libary_dir, rel_path)

        sed = galsim.SED(fpath, wave_type='nm', flux_type='flambda')
        if redshift > 0:
            sed = sed.atRedshift(redshift)
        return sed

class Extinguisher:
    '''
    Applies extinction to a SED
    '''
    def __init__(self, sed_factory, ext_bin_width=0.1, mwRv=3.1):
        '''
        sed_factory         instance of ObservedSedFactory

        '''

        # Save bins to be used for extinction. F19x_range is in units of
        # 1/micron so convert.
        wl_ext_min = 1e3/F19.x_range[1]
        wl_ext_max = 1e3/F19.x_range[0]
        wl_extinct = np.linspace(wl_ext_min, wl_ext_max,
                                 int((wl_ext_max - wl_ext_min)/ext_bin_width))
        self.wl_extinct = wl_extinct
        self.wl_extinct_nm = wl_extinct*u.nm
        self.extinction = F19(Rv=mwRv)

        my_wl = sed_factory.wl_deltas
        my_wl = my_wl[np.where((wl_ext_min < my_wl) & (my_wl < wl_ext_max))]
        self.wl_deltas = my_wl
        self.wl_deltas_u = my_wl*u.nm

    def extinguish(self, sed, mwAv):
        ext = self.extinction.extinguish(self.wl_deltas_u, Av=mwAv)
        spec = galsim.LookupTable(self.wl_deltas, ext)
        mw_ext = galsim.SED(spec, wave_type='nm', flux_type='1')
        sed = sed*mw_ext

        return sed

class AB_mag:
    """
    Convert flux to AB magnitude for a set of bandpasses.
    """
    def __init__(self, bps):
        ab_sed = galsim.SED(lambda nu : 3631e-23, wave_type='nm',
                            flux_type='fnu')
        self.ab_fluxes = {band: ab_sed.calculateFlux(bp) for
                          band, bp in bps.items()}
    def __call__(self, flux, band):
        return -2.5*np.log10(flux/self.ab_fluxes[band])
