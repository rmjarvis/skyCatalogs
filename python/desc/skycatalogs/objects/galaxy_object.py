import numpy as np
import galsim

from .base_object import BaseObject

__all__ = ['GalaxyObject']

class GalaxyObject(BaseObject):
    _type_name = 'galaxy'
    def _get_sed(self, component=None, resolution=None):
        '''
        Return sed and mag_norm for a galaxy component or for a star
        Parameters
        ----------
        component    one of 'bulge', 'disk', 'knots' for now. Other components
                     may be supported.  Ignored for stars
        resolution   desired resolution of lambda in nanometers. Ignored
                     for stars.

        Returns
        -------
        A pair (sed, mag_norm)
        '''
        if component not in ['disk', 'bulge', 'knots']:
            raise ValueError(f'Cannot fetch SED for component type {component}')

        th_val = self.get_native_attribute(f'sed_val_{component}')
        if  th_val is None:   #  values for this component are not in the file
            raise ValueError(f'{component} not part of this catalog')

        # if values are all zeros or nearly no point in trying to convert
        if max(th_val) < np.finfo('float').resolution:
            return None, 0.0

        z_h = self.get_native_attribute('redshift_hubble')
        z = self.get_native_attribute('redshift')

        sky_cat = self._belongs_to._sky_catalog
        sed = sky_cat.observed_sed_factory.create(th_val, z_h, z,
                                                  resolution=resolution)
        magnorm = sky_cat.observed_sed_factory.magnorm(th_val, z_h)

        return sed, magnorm

    def get_wl_params(self):
        """Return the weak lensing parameters, g1, g2, mu."""
        gamma1 = self.get_native_attribute('shear_1')
        gamma2 = self.get_native_attribute('shear_2')
        kappa =  self.get_native_attribute('convergence')
        # Compute reduced shears and magnification.
        g1 = gamma1/(1. - kappa)    # real part of reduced shear
        g2 = gamma2/(1. - kappa)    # imaginary part of reduced shear
        mu = 1./((1. - kappa)**2 - (gamma1**2 + gamma2**2)) # magnification
        return g1, g2, mu

    def get_gsobject_components(self, gsparams=None, rng=None):

        if gsparams is not None:
            gsparams = galsim.GSParams(**gsparams)

        obj_dict = {}
        for component in self.subcomponents:
            # knots use the same major/minor axes as the disk component.
            my_component = 'disk' if component != 'bulge' else 'bulge'
            a = self.get_native_attribute(
                f'size_{my_component}_true')
            b = self.get_native_attribute(
                f'size_minor_{my_component}_true')
            assert a >= b
            hlr = (a*b)**0.5   # approximation for half-light radius

            e1 = self.get_native_attribute(
                f'ellipticity_1_{my_component}_true')
            e2 = self.get_native_attribute(
                f'ellipticity_2_{my_component}_true')

            if component == 'knots':
                npoints = self.get_native_attribute('n_knots')
                assert npoints > 0
                obj = galsim.RandomKnots(npoints=npoints,
                                         half_light_radius=hlr, rng=rng,
                                         gsparams=gsparams)
            else:
                n = self.get_native_attribute(f'sersic_{component}')
                # Quantize the n values at 0.05 so that galsim can
                # possibly amortize sersic calculations from the previous
                # galaxy.
                n = round(n*20.)/20.
                obj = galsim.Sersic(n=n, half_light_radius=hlr,
                                    gsparams=gsparams)

            # NOTE: Whether or not the minus signs in the next executable
            # line are needed in general or just for generating DC2-like
            # results is still TBD. They are included here in order to
            # reproduce the effect of adding 90 degrees to position angle
            # in the old code.
            shear = galsim.Shear(g1=-e1, g2=-e2)
            obj = obj._shear(shear)
            g1, g2, mu = self.get_wl_params()
            obj_dict[component] = obj._lens(g1, g2, mu)
        return obj_dict

    def _apply_component_extinction(self, sed):
        '''
        Apply extinction to sed for galaxy component
        Return resulting sed
        '''
        iAv, iRv, mwAv, mwRv = self._get_dust()
        if iAv > 0:
            # Apply internal extinction model, which is assumed
            # to be the same for all subcomponents.
            pass  #TODO add implementation for internal extinction.

        # Apply Milky Way extinction.
        sky_cat = self._belongs_to._sky_catalog
        sed = sky_cat.extinguisher.extinguish(sed, mwAv)
        return sed


    def get_observer_sed_component(self, component, mjd=None):
        sed, _ = self._get_sed(component=component)
        if sed is not None:
            sed = self._apply_component_extinction(sed)

        return sed
