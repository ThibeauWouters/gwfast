#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Tue Feb  8 18:23:17 2022

@author: Michi
"""


#Enable 64bit on JAX, fundamental
from jax.config import config
config.update("jax_enable_x64", True)

# We use both the original numpy, denoted as onp, and the JAX implementation of numpy, denoted as np
import numpy as onp
#import numpy as np
import jax.numpy as np
from jax import vmap, jacrev #jacfwd
import time
import os
import h5py
import copy

import utils
import Globals as glob
import fisherTools



class GWSignal(object):
    '''
    Class to compute the GW signal emitted by a coalescing binary system as seen by a detector on Earth.
    
    The functions defined within this class allow to get the amplitude of the signal, its phase, and SNR.
    
    Inputs are an object containing the waveform model, the coordinates of the detector (latitude and longitude in deg),
    its shape (L or T), the angle w.r.t. East of the bisector of the arms (deg) 
    and its ASD (given in a .txt file containing two columns: one with the frequencies and one with the ASD values, 
    remember ASD=sqrt(PSD))
    
    '''
    def __init__(self, wf_model, 
                psd_path=None,
                detector_shape = 'T',
                det_lat=40.44,
                det_long=9.45,
                det_xax=0., 
                verbose=False,
                useEarthMotion = False,
                fmin=5,
                IntTablePath=None,
                WindowWidth = 5.):
        
        if (detector_shape!='L') and (detector_shape!='T'):
            raise ValueError('Enter valid detector configuration')
        
        if psd_path is None:
            raise ValueError('Enter a valid PSD path')
        
        if verbose:
            print('Using PSD from file %s ' %psd_path)
        
        if (useEarthMotion) and (wf_model.objType == 'BBH'):
            print('WARNING: the motion of Earth gives a negligible contribution for BBH signals, consider switching it off to make the code run faster')
        if (not useEarthMotion) and (wf_model.objType == 'BNS'):
            print('WARNING: the motion of Earth gives a relevant contribution for BNS signals, consider switching it on')
        
        self.wf_model = wf_model
        
        self.psd_base_path = ('/').join(psd_path.split('/')[:-1])
        self.psd_file_name = psd_path.split('/')[-1]
 
        self.verbose = verbose
        self.detector_shape = detector_shape
        
        self.det_lat_rad  = det_lat*np.pi/180.
        self.det_long_rad = det_long*np.pi/180.
        
        self.det_xax_rad  = det_xax*np.pi/180.
        
        self.IntTablePath = IntTablePath
        
        noise = onp.loadtxt(psd_path, usecols=(0,1))
        f = noise[:,0]
        S = (noise[:,1])**2
        
        self.strainFreq = f
        self.noiseCurve = S
        
        import scipy.integrate as igt
        mask = self.strainFreq >= fmin
        self.strainInteg = igt.cumtrapz(self.strainFreq[mask]**(-7./3.)/S[mask], self.strainFreq[mask], initial = 0)
        
        self.fcutNum = wf_model.fcutNum
        self.useEarthMotion = useEarthMotion
        self.fmin = fmin #Hz
        self.WindowWidth = WindowWidth
        if detector_shape == 'L':
            self.angbtwArms = 0.5*np.pi
        elif detector_shape == 'T':
            self.angbtwArms = np.pi/3.
        
        self.IntegInterpArr = None
        
    def _tabulateIntegrals(self, res=200, store=True, Mcmin=.9, Mcmax=9., etamin=.1):
        import scipy.integrate as igt
        
        def IntegrandC(f, Mc, tcoal, n):
            t = tcoal - 2.18567 * ((1.21/Mc)**(5./3.)) * ((100/f[:,onp.newaxis])**(8./3.))/(3600.*24)
            return (f[:,onp.newaxis]**(-7./3.))*np.cos(n*2.*np.pi*t)
        def IntegrandS(f, Mc, tcoal, n):
            t = tcoal - 2.18567 * ((1.21/Mc)**(5./3.)) * ((100/f[:,onp.newaxis])**(8./3.))/(3600.*24)
            return (f[:,onp.newaxis]**(-7./3.))*np.sin(n*2.*np.pi*t)
            
        Mcgrid = onp.linspace(Mcmin, Mcmax, res)
        etagrid = onp.linspace(etamin, 0.25, res)
        tcgrid = onp.linspace(0.,2.*np.pi,res)
        
        Igrid = onp.zeros((res,res,res,9))
        
        if self.verbose:
            print('Computing table of integrals...\n')
        
        in_time=time.time()
        
        for i,Mc in enumerate(Mcgrid):
            for j,eta in enumerate(etagrid):
                fcut = self.fcutNum/(Mc/(eta**(3./5.)))
                mask = (self.strainFreq >= self.fmin) & (self.strainFreq <= fcut)
                #for k,tc in enumerate(tcgrid):
                fgrids = np.ones((res, len(self.strainFreq[mask])))*self.strainFreq[mask]
                noisegrids = np.ones((res, len(self.noiseCurve[mask])))*self.noiseCurve[mask]
                for m in range(4):
                    tmpIntegrandC = IntegrandC(self.strainFreq[mask], Mc, tcgrid, m+1.)
                    tmpIntegrandS = IntegrandS(self.strainFreq[mask], Mc, tcgrid, m+1.)
                    Igrid[i,j,:,m] = onp.trapz(tmpIntegrandC/noisegrids.T, fgrids.T, axis=0)
                    Igrid[i,j,:,m+4] = onp.trapz(tmpIntegrandS/noisegrids.T, fgrids.T, axis=0)
                    
                tmpIntegrand = IntegrandC(self.strainFreq[mask], Mc, tcgrid, 0.)
                Igrid[i,j,:,8] = onp.trapz(tmpIntegrand/noisegrids.T, fgrids.T, axis=0)
                
        if self.verbose:
            print('Done in %.2fs \n' %(time.time() - in_time))
        
        if store:
            print('Saving result...')
            if not os.path.isdir(os.path.join(self.psd_base_path, 'Integral_Tables')):
                os.mkdir(os.path.join(self.psd_base_path, 'Integral_Tables'))
                
            with h5py.File(os.path.join(self.psd_base_path, 'Integral_Tables', type(self.wf_model).__name__+str(res)+'.h5'), 'w') as out:
                out.create_dataset('Mc', data=Mcgrid, compression='gzip', shuffle=True)
                out.create_dataset('eta', data=etagrid, compression='gzip', shuffle=True)
                out.create_dataset('tc', data=tcgrid, compression='gzip', shuffle=True)
                out.create_dataset('Integs', data=Igrid, compression='gzip', shuffle=True)
                out.attrs['npoints'] = res
                out.attrs['etamin'] = etamin
                out.attrs['Mcmin'] = Mcmin
                out.attrs['Mcmax'] = Mcmax
        
        return Igrid, Mcgrid, etagrid, tcgrid
    
    def _make_SNRig_interpolator(self, ):
        
        from scipy.interpolate import RegularGridInterpolator
        if self.IntTablePath is not None:
            if os.path.exists(self.IntTablePath):
                if self.verbose:
                    print('Pre-computed optimal integrals grid is present for this waveform. Loading...')
                with h5py.File(self.IntTablePath, 'r') as inp:
                    Mcs = np.array(inp['Mc'])
                    etas = np.array(inp['eta'])
                    tcs = np.array(inp['tc'])
                    Igrid = np.array(inp['Integs'])
                    #res = inp.attrs['npoints']
                    #etamin =  inp.attrs['etamin']
                    #Mcmin = inp.attrs['Mcmin']
                    #Mcmax = inp.attrs['Mcmax']
                    if self.verbose:
                        print('Attributes of pre-computed integrals: ')
                        print([(k, inp.attrs[k]) for k in inp.attrs.keys()])
                        #print(inp.attrs)
            else:
                print('Tabulating integrals...')
                Igrid, Mcs, etas, tcs = self._tabulateIntegrals()
                
        else:
            print('Tabulating integrals...')
            Igrid, Mcs, etas, tcs = self._tabulateIntegrals()
        
        self.IntegInterpArr = onp.array([])
        for i in range(9):
            # The interpolator contains in the elements from i=0 to 3 the integrals of cos((i+1) Om t)f^{-7/3}
            # in the elements from 4 to 7 the integrals of the sine (from lower to higher i), and in element 8 the
            # integral of f^-7/3 alone
            
            self.IntegInterpArr =  onp.append(self.IntegInterpArr,RegularGridInterpolator((Mcs, etas, tcs), Igrid[:,:,:,i]))
        
    
    def _ra_dec_from_th_phi(self, theta, phi):
        return utils.ra_dec_from_th_phi_rad(theta, phi)
        
    
    def _PatternFunction(self, theta, phi, t, psi, rot=0.):
        # See P. Jaranowski, A. Krolak, B. F. Schutz, PRD 58, 063001, eq. (10)--(13)
        # rot (deg) is an additional parameter, needed for the triangle configuration, allowing to specify a further rotation
        # of the interferometer w.r.t. xax. In this case, the three arms will have orientations 1 -> xax, 2 -> xax+60°, 3 -> xax+120° 
        
    
        def afun(ra, dec, t, rot):
            phir = self.det_long_rad
            a1 = 0.0625*np.sin(2*(self.det_xax_rad+rot))*(3.-np.cos(2.*self.det_lat_rad))*(3.-np.cos(2.*dec))*np.cos(2.*(ra - phir - 2.*np.pi*t))
            a2 = 0.25*np.cos(2*(self.det_xax_rad+rot))*np.sin(self.det_lat_rad)*(3.-np.cos(2.*dec))*np.sin(2.*(ra - phir - 2.*np.pi*t))
            a3 = 0.25*np.sin(2*(self.det_xax_rad+rot))*np.sin(2.*self.det_lat_rad)*np.sin(2.*dec)*np.cos(ra - phir - 2.*np.pi*t)
            a4 = 0.5*np.cos(2*(self.det_xax_rad+rot))*np.cos(self.det_lat_rad)*np.sin(2.*dec)*np.sin(ra - phir - 2.*np.pi*t)
            a5 = 3.*0.25*np.sin(2*(self.det_xax_rad+rot))*(np.cos(self.det_lat_rad)*np.cos(dec))**2.
            return a1 - a2 + a3 - a4 + a5
        
        def bfun(ra, dec, t, rot):
            phir = self.det_long_rad
            b1 = np.cos(2*(self.det_xax_rad+rot))*np.sin(self.det_lat_rad)*np.sin(dec)*np.cos(2.*(ra - phir - 2.*np.pi*t))
            b2 = 0.25*np.sin(2*(self.det_xax_rad+rot))*(3.-np.cos(2.*self.det_lat_rad))*np.sin(dec)*np.sin(2.*(ra - phir - 2.*np.pi*t))
            b3 = np.cos(2*(self.det_xax_rad+rot))*np.cos(self.det_lat_rad)*np.cos(dec)*np.cos(ra - phir - 2.*np.pi*t)
            b4 = 0.5*np.sin(2*(self.det_xax_rad+rot))*np.sin(2.*self.det_lat_rad)*np.cos(dec)*np.sin(ra - phir - 2.*np.pi*t)
            
            return b1 + b2 + b3 + b4
        
        rot_rad = rot*np.pi/180
        
        ras, decs = self._ra_dec_from_th_phi(theta, phi)
        afac = afun(ras, decs, t, rot_rad)
        bfac = bfun(ras, decs, t, rot_rad)
        
        Fp = np.sin(self.angbtwArms)*(afac*np.cos(2.*psi) + bfac*np.sin(2*psi))
        Fc = np.sin(self.angbtwArms)*(bfac*np.cos(2.*psi) - afac*np.sin(2*psi))
        
        return Fp, Fc
    
    def _phiDoppler(self, theta, phi, t, f):
        
        
        phiD = 2.*np.pi*f*(glob.REarth/glob.clight)*np.sin(theta)*np.cos(2.*np.pi*t - phi)
        
        ddot_phiD = ((2.*np.pi)**3)*f*(glob.REarth/glob.clight)*np.sin(theta)*np.cos(2.*np.pi*t - phi)/((3600.*24)**2.)
        
        return phiD, ddot_phiD
    
    def _phiPhase(self, theta, phi, t, iota, psi, Fp=None,Fc=None):
        #The polarization phase contribution (the change in F+ and Fx with time influences also the phase)
        
        if (Fp is None) or (Fc is None):
            Fp, Fc = self._PatternFunction(theta, phi, t, psi)
        
        phiP = -np.arctan2(np.cos(iota)*Fc,0.5*(1.+((np.cos(iota))**2))*Fp)
        
        #The contriution to the amplitude is negligible, so we do not compute it
        
        return phiP
    
    def _phiLoc(self, theta, phi, t, f):
        
        
        ras, decs = self._ra_dec_from_th_phi(theta, phi)
        
        comp1 = np.cos(decs)*np.cos(ras)*np.cos(self.det_lat_rad)*np.cos(self.det_long_rad + 2.*np.pi*t)
        comp2 = np.cos(decs)*np.sin(ras)*np.cos(self.det_lat_rad)*np.sin(self.det_long_rad + 2.*np.pi*t)
        comp3 = np.sin(decs)*np.sin(self.det_lat_rad)
        
        phiL = -(2.*np.pi*f/glob.clight)*glob.REarth*(comp1+comp2+comp3)
        
        return phiL
    
    
        
    
    
    def GWAmplitudes(self, evParams, f, ddot_phiDoppler=None, rot=0.):
        # evParams are all the parameters characterizing the event(s) under exam. It has to be a dictionary containing the entries: 
        # Mc -> chirp mass (Msun), logdL -> log of luminosity distance (Gpc), theta & phi -> sky position (rad), iota -> inclination angle of orbital angular momentum to l.o.s toward the detector,
        # psi -> polarisation angle, tcoal -> time of coalescence (days), eta -> symmetric mass ratio, Phicoal -> GW frequency at coalescence.
        # f is the frequency (Hz)
        
        '''
        As can be seen in M. Maggiore Vol. 1, problem 4.1, the amplitude of the signal common to both the + and x 
        polarisations (so omitting the factor depending on cosiota) is given by 
        
        Agw(f) = 0.5*A(t*)*sqrt(2pi/ddot(Phi(t*)))
        
        In the Newtonian and restricted PN approximation, the first term is given by
        
        0.5*A(f(t*)) = (2c/dL) (G Mc/ c^3)^(5/3) (pi f)^(2/3) 
        
        '''
        
        #self._check_evparams(evParams)
        
        Mc, dL, theta, phi, iota, psi, tcoal, eta = evParams['Mc'], evParams['dL'], evParams['theta'], evParams['phi'], evParams['iota'], evParams['psi'], evParams['tcoal'], evParams['eta'] 
        def A0(dL, Mc, f):
            # First common term of the amplitude
            #return 2.*(clightGpc/dL)*((GMsun_over_c3*Mc)**(5./3.))*((np.pi*f)**(2./3.))
            return 2.*glob.clightGpc/dL*((glob.GMsun_over_c3*Mc)**(5./3.))*((np.pi*f)**(2./3.))
        
        if self.useEarthMotion:
            # Earth motion gives a relevant contribution only for BNSs, for which Newtonian or restricted PN approximation works
            t = tcoal - self.wf_model.tau_star(f, **evParams)/(3600.*24)
            overallAmpl = A0(dL, Mc, f)
            ddot_Phi = self.wf_model.ddot_Phi(f, **evParams)
            if ddot_phiDoppler is None:
                _, ddot_phiDoppler = self._phiDoppler(theta, phi, t, f)
            Fp, Fc = self._PatternFunction(theta, phi, t, psi, rot=rot)
            Ap = overallAmpl*np.sqrt(2.*np.pi/abs(ddot_Phi + ddot_phiDoppler))*Fp*0.5*(1.+(np.cos(iota))**2)
            Ac = overallAmpl*np.sqrt(2.*np.pi/abs(ddot_Phi + ddot_phiDoppler))*Fc*np.cos(iota)
            
        else:
            t = tcoal - self.wf_model.tau_star(self.fmin, **evParams)/(3600.*24)
            overallAmpl = A0(dL, Mc, f)
            mod_Ampl = self.wf_model.ampl_mod_fac(f, **evParams)
            ddot_Phi = self.wf_model.ddot_Phi(f, **evParams)
            Fp, Fc = self._PatternFunction(theta, phi, t, psi, rot=rot)
            Ap = overallAmpl*mod_Ampl*np.sqrt(2.*np.pi/ddot_Phi)*Fp*0.5*(1.+(np.cos(iota))**2)
            Ac = overallAmpl*mod_Ampl*np.sqrt(2.*np.pi/ddot_Phi)*Fc*np.cos(iota)
        
        return Ap, Ac
    
    def GWPhase(self, evParams, f):
        # Phase of the GW signal. This is actually Psi+, but Psix = Psi+ + pi/2
        Mc, eta, tcoal, Phicoal = evParams['Mc'], evParams['eta'], evParams['tcoal'], evParams['Phicoal']
        PhiGw = self.wf_model.Phi(f, **evParams)
        return 2.*np.pi*f*tcoal - Phicoal - 0.25*np.pi - PhiGw
        
    def GWstrain(self, f, Mc, dL, theta, phi, iota, psi, tcoal, eta, Phicoal, rot=0., useWindow=False):
        # Full GW strain expression (complex)
        # Here we have the decompressed parameters and we put them back in a dictionary just to have an easier
        # implementation of the JAX module for derivatives
        evParams = {'Mc':Mc, 'dL':dL, 'theta':theta, 'phi':phi, 'iota':iota, 'psi':psi, 'tcoal':tcoal, 'eta':eta, 'Phicoal':Phicoal}
        if self.useEarthMotion:
            # Compute Doppler contribution
            t = tcoal - self.wf_model.tau_star(f, **evParams)/(3600.*24)
            phiD, ddot_phiD = self._phiDoppler(theta, phi, t, f)
            phiP = self._phiPhase(theta, phi, t, iota, psi)
        else:
            phiD, ddot_phiD, phiP = Mc*0., Mc*0., Mc*0.
            t = tcoal
        phiL = self._phiLoc(theta, phi, t, f)
        Ap, Ac = self.GWAmplitudes(evParams, f, ddot_phiDoppler=ddot_phiD, rot=rot)
        Psi = self.GWPhase(evParams, f) + phiD + phiL #+ phiP
        
        if (useWindow) and (not np.isscalar(f)):
            # Use a Welch window on the signal for better performance in the derivatives
            Ampl = np.where(f<=self.fmin+self.WindowWidth, np.sqrt(Ap**2 + Ac**2)*(1. - ((f-self.WindowWidth-self.fmin)/self.WindowWidth)**2), np.sqrt(Ap**2 + Ac**2))

            #Ampl = np.sqrt(Ap**2 + Ac**2)
            #fmaxmask = np.tile(fmaxarr, (f.shape[0],1))
            #print(fmaxmask.shape)
            #Ampl = np.where(f>=fmaxmask-self.WindowWidth, Ampl*(1.-((f-fmaxmask+self.WindowWidth)/self.WindowWidth)**2), Ampl)
            #Ampl = np.where(f>=max(f)-self.WindowWidth, Ampl*(1.-((f-max(f)+self.WindowWidth)/self.WindowWidth)**2), Ampl)
        else:
            Ampl = np.sqrt(Ap**2 + Ac**2)
            
        return (Ap + Ac*1j)*np.exp(Psi*1j)
        #return Ampl*np.exp(Psi*1j)
    
    def SNRInteg(self, evParams, res=1000):
        # SNR calculation performing the frequency integral for each signal
        # This is computationally expensive, but might be needed for complex waveform models
        if not np.isscalar(evParams['Mc']):
            SNR = np.zeros(len(np.asarray(evParams['Mc'])))
        else:
            SNR = 0
        
        fcut = self.fcutNum/(evParams['Mc']/(evParams['eta']**(3./5.)))
        fcut = np.full(evParams['eta'].shape, 100.)
        fminarr = np.full(fcut.shape, self.fmin)
        fgrids = np.geomspace(fminarr,fcut,num=int(res))
        strainGrids = np.interp(fgrids, self.strainFreq, self.noiseCurve)
        #strainGrids = np.ones(fgrids.shape)
        if self.detector_shape=='L':    
            Aps, Acs = self.GWAmplitudes(evParams, fgrids, ddot_phiDoppler=None)
            Atot = Aps*Aps + Acs*Acs
            SNR = np.sqrt(np.trapz(Atot/strainGrids, fgrids, axis=0))
        
        elif self.detector_shape=='T':
            for i in range(3):
                Aps, Acs = self.GWAmplitudes(evParams, fgrids, ddot_phiDoppler=None, rot=i*60.)
                Atot = Aps*Aps + Acs*Acs
                tmpSNRsq = np.trapz(Atot/strainGrids, fgrids, axis=0)
                SNR = SNR + tmpSNRsq
            SNR = np.sqrt(SNR)
        
        
        return 2.*SNR # The factor of two arises by cutting the integral from 0 to infinity
    
    
    def FisherMatr(self, evParams, res=1000, fst=100, useWindow=False):
        
        utils.check_evparams(evParams)
  
        Mc, dL, theta, phi = evParams['Mc'].astype('complex128'), evParams['dL'].astype('complex128'), evParams['theta'].astype('complex128'), evParams['phi'].astype('complex128')
        iota, psi, tcoal, eta, Phicoal = evParams['iota'].astype('complex128'), evParams['psi'].astype('complex128'), evParams['tcoal'].astype('complex128'), evParams['eta'].astype('complex128'), evParams['Phicoal'].astype('complex128')
        
        fcut = self.fcutNum/(Mc/(eta**(3./5.))) 
        #print(fcut)
        fminarr = np.full(fcut.shape, self.fmin)
        fgrids = np.geomspace(fminarr,fcut,num=int(res))
        strainGrids = np.interp(fgrids, self.strainFreq, self.noiseCurve)
        if 'NewtInspiral' in type(self.wf_model).__name__:
            print('WARNING: In the Newtonian inspiral case the mass ratio does not enter the waveform, and the corresponding Fisher matrix element vanish, we then discard it.\n')
            #derivargs = (1,2,3,4,5,6,7,9)
            derivargs = (1,3,4,5,6,7,9)
            nParams = 8
        else:
            derivargs = (1,3,4,5,6,7,8,9)
            nParams = 9
        
        if self.detector_shape=='L': 
            #Build gradient
            if useWindow:
                GWstrainUse = lambda f, Mc, dL, theta, phi, iota, psi, tcoal, eta, Phicoal: self.GWstrain(f, Mc, dL, theta, phi, iota, psi, tcoal, eta, Phicoal, rot=0., useWindow=useWindow)
                dh = vmap(jacrev(GWstrainUse, argnums=derivargs, holomorphic=True))
            else:
                dh = vmap(jacrev(self.GWstrain, argnums=derivargs, holomorphic=True))
            
            FisherDerivs = np.asarray(dh(fgrids.T, Mc, dL, theta, phi, iota, psi, tcoal, eta, Phicoal))
            
            tmpsplit1, tmpsplit2, _ = np.vsplit(FisherDerivs, np.array([1, nParams-1]))
            logdLderiv = -onp.asarray(self.GWstrain(fgrids, Mc, dL, theta, phi, iota, psi, tcoal, eta, Phicoal)).T
            FisherDerivs = np.vstack((tmpsplit1, logdLderiv[onp.newaxis,:], tmpsplit2))
            
            FisherIntegrands = (onp.conjugate(FisherDerivs[:,:,onp.newaxis,:])*FisherDerivs.transpose(1,0,2))
    
            Fisher = onp.zeros((nParams,nParams,len(Mc)))
            # This for is unavoidable i think
            for alpha in range(nParams):
                for beta in range(alpha,nParams):
                    tmpElem = FisherIntegrands[alpha,:,beta,:].T
                    Fisher[alpha,beta, :] = onp.trapz(tmpElem.real/strainGrids.real, fgrids.real, axis=0)*4.

                    Fisher[beta,alpha, :] = Fisher[alpha,beta, :]
        else:
            Fisher = onp.zeros((nParams,nParams,len(Mc)))
            for i in range(3):
                # Change rot
                GWstrainRot = lambda f, Mc, dL, theta, phi, iota, psi, tcoal, eta, Phicoal: self.GWstrain(f, Mc, dL, theta, phi, iota, psi, tcoal, eta, Phicoal, rot=i*60., useWindow=useWindow)
                # Build gradient
                dh = vmap(jacrev(GWstrainRot, argnums=derivargs, holomorphic=True))
            
                FisherDerivs = onp.asarray(dh(fgrids.T, Mc, dL, theta, phi, iota, psi, tcoal, eta, Phicoal))
                
                tmpsplit1, tmpsplit2, _ = onp.vsplit(FisherDerivs, np.array([1, nParams-1]))
                logdLderiv = -onp.asarray(GWstrainRot(fgrids, Mc, dL, theta, phi, iota, psi, tcoal, eta, Phicoal)).T
                FisherDerivs = onp.vstack((tmpsplit1, logdLderiv[onp.newaxis,:], tmpsplit2))
                
                FisherIntegrands = (onp.conjugate(FisherDerivs[:,:,onp.newaxis,:])*FisherDerivs.transpose(1,0,2))
                
                tmpFisher = onp.zeros((nParams,nParams,len(Mc)))
                # This for is unavoidable i think
                for alpha in range(nParams):
                    for beta in range(alpha,nParams):
                        tmpElem = FisherIntegrands[alpha,:,beta,:].T
                        tmpFisher[alpha,beta, :] = onp.trapz(tmpElem.real/strainGrids.real, fgrids.real, axis=0)*4.
                        
                        tmpFisher[beta,alpha, :] = tmpFisher[alpha,beta, :]
                Fisher += tmpFisher
            #dhst = vmap(jacrev(self.GWstrain, argnums=(1,2,3,4,5,6,7,9), holomorphic=True))
            #FisherDerivst = onp.asarray(dhst(np.array([fst]), Mc, logdL, theta, phi, iota, psi, tcoal, eta, Phicoal))
            #FisherIntegrandst = (onp.conjugate(FisherDerivst[:,:])*FisherDerivst.T)
            #print(FisherIntegrandst*1e50)
            #print(FisherDerivst)
        return Fisher
    
    
    

        

    def SNRFastInsp(self, evParams, checkInterp=False):
        # This module allows to compute the inspiral SNR taking into account Earth rotation, without the need 
        # of performing an integral for each event
        
        Mc, dL, theta, phi, iota, psi, tcoal, eta = evParams['Mc'], evParams['dL'], evParams['theta'], evParams['phi'], evParams['iota'], evParams['psi'], evParams['tcoal'], evParams['eta'] 
        
        ras, decs = self._ra_dec_from_th_phi(theta, phi)
        
        
        if not np.isscalar(Mc):
            SNR = np.zeros(Mc.shape)
        else:
            SNR = 0
            
        # Factor in front of the integral in the inspiral only case
        fac = np.sqrt(5./6.)/np.pi**(2./3.)*(glob.GMsun_over_c3*Mc)**(5./6.)*glob.clightGpc/dL#*np.exp(-logdL)
        
        fcut = self.fcutNum/(Mc/(eta**(3./5.)))
        mask = self.strainFreq >= self.fmin
        
        def CoeffsRot(ra, dec, psi, rot=0.):
            rot = rot*np.pi/180.
            rasDet = ra - self.det_long_rad
            # Referring to overleaf, I now call VC2 the last vector appearing in the C2 expression, VS2 the one in the S2 expression and so on
            # e1 is the first element and e2 the second
        
            VC2e1 = 0.0675*np.cos(2*rasDet)*np.sin(2*(self.det_xax_rad+rot))*(3.-np.cos(2*dec))*(3.-np.cos(2.*self.det_lat_rad)) - 0.25*np.sin(2*rasDet)*np.cos(2*(self.det_xax_rad+rot))*(3.-np.cos(2*dec))*np.sin(self.det_lat_rad)
            VC2e2 = 0.25*np.sin(2*rasDet)*np.sin(2*(self.det_xax_rad+rot))*np.sin(dec)*(3.-np.cos(2.*self.det_lat_rad)) + np.cos(2*rasDet)*np.cos(2*(self.det_xax_rad+rot))*np.sin(dec)*np.sin(self.det_lat_rad)
            C2p = (np.cos(2.*psi)*VC2e1 + np.sin(2*psi)*VC2e2)*np.sin(self.angbtwArms)
            C2c = (-np.sin(2*psi)*VC2e1 + np.cos(2.*psi)*VC2e2)*np.sin(self.angbtwArms)
            
            VS2e1 = 0.0675*np.sin(2*rasDet)*np.sin(2*(self.det_xax_rad+rot))*(3.-np.cos(2*dec))*(3.-np.cos(2.*self.det_lat_rad)) + 0.25*np.cos(2*rasDet)*np.cos(2*(self.det_xax_rad+rot))*(3.-np.cos(2*dec))*np.sin(self.det_lat_rad)
            VS2e2 = -0.25*np.cos(2*rasDet)*np.sin(2*(self.det_xax_rad+rot))*np.sin(dec)*(3.-np.cos(2.*self.det_lat_rad)) + np.sin(2*rasDet)*np.cos(2*(self.det_xax_rad+rot))*np.sin(dec)*np.sin(self.det_lat_rad)
            S2p = (np.cos(2.*psi)*VS2e1 + np.sin(2*psi)*VS2e2)*np.sin(self.angbtwArms)
            S2c = (-np.sin(2*psi)*VS2e1 + np.cos(2.*psi)*VS2e2)*np.sin(self.angbtwArms)
           
            VC1e1 = 0.25*np.cos(rasDet)*np.sin(2.*(self.det_xax_rad+rot))*np.sin(2*dec)*np.sin(2.*self.det_lat_rad) - 0.5*np.sin(rasDet)*np.cos(2.*(self.det_xax_rad+rot))*np.sin(2*dec)*np.cos(self.det_lat_rad)
            VC1e2 = np.cos(rasDet)*np.cos(2*(self.det_xax_rad+rot))*np.cos(dec)*np.cos(self.det_lat_rad) + 0.5*np.sin(rasDet)*np.sin(2.*(self.det_xax_rad+rot))*np.cos(dec)*np.sin(2.*self.det_lat_rad)
            C1p = (np.cos(2.*psi)*VC1e1 + np.sin(2*psi)*VC1e2)*np.sin(self.angbtwArms)
            C1c = (-np.sin(2*psi)*VC1e1 + np.cos(2.*psi)*VC1e2)*np.sin(self.angbtwArms)
            
            VS1e1 = 0.25*np.sin(rasDet)*np.sin(2.*(self.det_xax_rad+rot))*np.sin(2*dec)*np.sin(2.*self.det_lat_rad) + 0.5*np.cos(rasDet)*np.cos(2.*(self.det_xax_rad+rot))*np.sin(2*dec)*np.cos(self.det_lat_rad)
            VS1e2 = np.sin(rasDet)*np.cos(2*(self.det_xax_rad+rot))*np.cos(dec)*np.cos(self.det_lat_rad) - 0.5*np.cos(rasDet)*np.sin(2.*(self.det_xax_rad+rot))*np.cos(dec)*np.sin(2.*self.det_lat_rad)
            S1p = (np.cos(2.*psi)*VS1e1 + np.sin(2*psi)*VS1e2)*np.sin(self.angbtwArms)
            S1c = (-np.sin(2*psi)*VS1e1 + np.cos(2.*psi)*VS1e2)*np.sin(self.angbtwArms)
            
            C0p = 0.75*np.cos(2.*psi)*np.sin(2.*(self.det_xax_rad+rot))*((np.cos(dec)*np.cos(self.det_lat_rad))**2)*np.sin(self.angbtwArms)
            C0c = -0.75*np.sin(2.*psi)*np.sin(2.*(self.det_xax_rad+rot))*((np.cos(dec)*np.cos(self.det_lat_rad))**2)*np.sin(self.angbtwArms)
            
            return np.array([C2p, C2c]), np.array([S2p, S2c]), np.array([C1p, C1c]), np.array([S1p, S1c]), np.array([C0p, C0c])
        
        def FpFcsqInt(C2s, S2s, C1s, S1s, C0s, Igs, iota):
            Fp4 = 0.5*(C2s[0]**2 - S2s[0]**2)*Igs[3] +  C2s[0]*S2s[0]*Igs[7]   
            #print('Cp4Om = '+str(0.5*(C2s[0]**2 - S2s[0]**2))+' Sp4Om = '+str(C2s[0]*S2s[0]))
            Fp3 = (C2s[0]*C1s[0] - S2s[0]*S1s[0])*Igs[2] + (C2s[0]*S1s[0] + S2s[0]*C1s[0])*Igs[6]
            #print('Cp3Om = '+str(C2s[0]*C1s[0] - S2s[0]*S1s[0])+' Sp3Om = '+str(C2s[0]*S1s[0] + S2s[0]*C1s[0]))
            Fp2 = (0.5*(C1s[0]**2 - S1s[0]**2) + 2.*C2s[0]*C0s[0])*Igs[1] + (2.*C0s[0]*S2s[0] + C1s[0]*S1s[0])*Igs[5]
            #print('Cp2Om = '+str(0.5*(C1s[0]**2 - S1s[0]**2) + 2.*C2s[0]*C0s[0])+' Sp2Om = '+str(2.*C0s[0]*S2s[0] + C1s[0]*S1s[0]))
            Fp1 = (2.*C0s[0]*C1s[0] + C1s[0]*C2s[0] + S2s[0]*S1s[0])*Igs[0] + (2.*C0s[0]*S1s[0] + C1s[0]*S2s[0] - S1s[0]*C2s[0])*Igs[4]
            #print('Cp1Om = '+str(2.*C0s[0]*C1s[0] + C1s[0]*C2s[0] + S2s[0]*S1s[0])+' Sp1Om = '+str(2.*C0s[0]*S1s[0] + C1s[0]*S2s[0] - S1s[0]*C2s[0]))
            Fp0 = (C0s[0]**2 + 0.5*(C1s[0]**2 + C2s[0]**2 + S1s[0]**2 + S2s[0]**2))*Igs[8]
            #print('Cp0 = '+str(C0s[0]**2 + 0.5*(C1s[0]**2 + C2s[0]**2 + S1s[0]**2 + S2s[0]**2)))
            FpsqInt = Fp4 + Fp3 + Fp2 + Fp1 + Fp0
            
            Fc4 = 0.5*(C2s[1]**2 - S2s[1]**2)*Igs[3] +  C2s[1]*S2s[1]*Igs[7]   
            #print('Cc4Om = '+str(0.5*(C2s[1]**2 - S2s[1]**2))+' Sc4Om = '+str(C2s[1]*S2s[1]))
            Fc3 = (C2s[1]*C1s[1] - S2s[1]*S1s[1])*Igs[2] + (C2s[1]*S1s[1] + S2s[1]*C1s[1])*Igs[6]
            #print('Cc3Om = '+str(C2s[1]*C1s[1] - S2s[1]*S1s[1])+' Sc3Om = '+str(C2s[1]*S1s[1] + S2s[1]*C1s[1]))
            Fc2 = (0.5*(C1s[1]**2 - S1s[1]**2) + 2.*C2s[1]*C0s[1])*Igs[1] + (2.*C0s[1]*S2s[1] + C1s[1]*S1s[1])*Igs[5]
            #print('Cc2Om = '+str(0.5*(C1s[1]**2 - S1s[1]**2) + 2.*C2s[1]*C0s[1])+' Sc2Om = '+str(2.*C0s[1]*S2s[1] + C1s[1]*S1s[1]))
            Fc1 = (2.*C0s[1]*C1s[1] + C1s[1]*C2s[1] + S2s[1]*S1s[1])*Igs[0] + (2.*C0s[1]*S1s[1] + C1s[1]*S2s[1] - S1s[1]*C2s[1])*Igs[4]
            #print('Cc1Om = '+str(2.*C0s[1]*C1s[1] + C1s[1]*C2s[1] + S2s[1]*S1s[1])+' Sc1Om = '+str(2.*C0s[1]*S1s[1] + C1s[1]*S2s[1] - S1s[1]*C2s[1]))
            Fc0 = (C0s[1]**2 + 0.5*(C1s[1]**2 + C2s[1]**2 + S1s[1]**2 + S2s[1]**2))*Igs[8]
            #print('Cc0 = '+str(C0s[1]**2 + 0.5*(C1s[1]**2 + C2s[1]**2 + S1s[1]**2 + S2s[1]**2)))
            FcsqInt = Fc4 + Fc3 + Fc2 + Fc1 + Fc0
            
            return FpsqInt*(0.5*(1.+(np.cos(iota))**2))**2, FcsqInt*(np.cos(iota))**2
        
        if not self.useEarthMotion:
            t = tcoal - self.wf_model.tau_star(self.fmin, **evParams)/(3600.*24)
            if self.detector_shape=='L':
                Fp, Fc = self._PatternFunction(theta, phi, t, psi, rot=0.)
                Qsq = (Fp*0.5*(1.+(np.cos(iota))**2))**2 + (Fc*np.cos(iota))**2
                SNR = fac * np.sqrt(Qsq*onp.interp(fcut, self.strainFreq[mask], self.strainInteg))
            elif self.detector_shape=='T':
                for i in range(3):
                    Fp, Fc = self._PatternFunction(theta, phi, t, psi, rot=60.*i)
                    Qsq = (Fp*0.5*(1.+(np.cos(iota))**2))**2 + (Fc*np.cos(iota))**2
                    tmpSNR = fac * np.sqrt(Qsq*onp.interp(fcut, self.strainFreq[mask], self.strainInteg))
                    SNR = SNR + tmpSNR*tmpSNR
                SNR = np.sqrt(SNR)
        else:
            if self.IntegInterpArr is None:
                self._make_SNRig_interpolator()
            Igs = onp.zeros((9,len(Mc)))
            if not checkInterp:
                for i in range(9):
                    Igs[i,:] = self.IntegInterpArr[i](onp.array([Mc, eta, tcoal]).T)
            else:
                def IntegrandC(f, Mc, tcoal, n):
                    t = tcoal - 2.18567 * ((1.21/Mc)**(5./3.)) * ((100/f)**(8./3.))/(3600.*24)
                    return (f**(-7./3.))*np.cos(n*2.*np.pi*t)
                def IntegrandS(f, Mc, tcoal, n):
                    t = tcoal - 2.18567 * ((1.21/Mc)**(5./3.)) * ((100/f)**(8./3.))/(3600.*24)
                    return (f**(-7./3.))*np.sin(n*2.*np.pi*t)
                
                fminarr = np.full(fcut.shape, self.fmin)
                fgrids = np.geomspace(fminarr,fcut,num=int(5000))
                strainGrids = np.interp(fgrids, self.strainFreq, self.noiseCurve)
                
                for m in range(4):
                    tmpIntegrandC = IntegrandC(fgrids, Mc, tcoal, m+1.)
                    tmpIntegrandS = IntegrandS(fgrids, Mc, tcoal, m+1.)
                    Igs[m,:] = onp.trapz(tmpIntegrandC/strainGrids, fgrids, axis=0)
                    Igs[m+4,:] = onp.trapz(tmpIntegrandS/strainGrids, fgrids, axis=0)
                tmpIntegrand = IntegrandC(fgrids, Mc, tcoal, 0.)
                Igs[8,:] = onp.trapz(tmpIntegrand/strainGrids, fgrids, axis=0)
            
            
            if self.detector_shape=='L':
                C2s, S2s, C1s, S1s, C0s = CoeffsRot(ras, decs, psi, rot=0.)
                FpsqInt, FcsqInt = FpFcsqInt(C2s, S2s, C1s, S1s, C0s, Igs, iota)
                QsqInt = FpsqInt + FcsqInt
                SNR = fac * np.sqrt(QsqInt)
            elif self.detector_shape=='T':
                for i in range(3):
                    C2s, S2s, C1s, S1s, C0s = CoeffsRot(ras, decs, psi, rot=i*60.)                        
                    FpsqInt, FcsqInt = FpFcsqInt(C2s, S2s, C1s, S1s, C0s, Igs, iota)
                    QsqInt = FpsqInt + FcsqInt
                    tmpSNR = fac * np.sqrt(QsqInt)
                    SNR = SNR + tmpSNR*tmpSNR
                SNR = np.sqrt(SNR)
        
        return SNR
            
            