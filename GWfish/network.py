#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import numpy as onp
import fisherUtils as utils





class DetNet(object):
    
    def __init__(self, signals, verbose=True):
        
        # signals is a dictionary of the form
        # {'detector_name': GWSignal object }
        
        self.signals = signals
        self.verbose=verbose
    

    def _clear_cache(self):
        for d in self.signals.keys():
            self.signals[d]._clear_cache()
        
    
    def _update_all_seeds(self, verbose=True):
        for d in self.signals.keys():
            self.signals[d]._update_seed()
            if verbose:
                print('\nSeed for detector %s is %s'%(d,self.signals[d].seedUse))
    
    def SNR(self, evParams, res=1000):
        self.snrs = {}
        utils.check_evparams(evParams)
        for d in self.signals.keys():
            self.snrs[d] =  self.signals[d].SNRInteg(evParams, res=res)**2 
        return onp.sqrt(sum(self.snrs.values()))
        
    
    def FisherMatr(self, evParams, **kwargs):
        nparams = self.signals[list(self.signals.keys())[0]].wf_model.nParams
        nevents = len(evParams[list(evParams.keys())[0]])
        totF = onp.zeros((nparams,nparams,nevents))
        utils.check_evparams(evParams)
        for d in self.signals.keys():
            if self.verbose:
                print('Computing Fisher for %s...' %d)
            totF +=  self.signals[d].FisherMatr(evParams, **kwargs) 
        if self.verbose:
            print('Computing total Fisher ...')
        
        print('Done.')
        return totF

    def optimal_location(self, tcoal, is_tGPS=False):
        # Function to compute the optimal theta and phi for a signal to be seen by the detector network at a given GMST. The boolean is_tGPS can be used to specify whether the provided time is a GPS time rather than a GMST, so that it will be converted.
        # For a triangle the best location is the same of an L in the same place, as can be shown by explicit geometrical computation.
        # Even if considering Earth rotation, the highest SNR will still be obtained if the source is in the optimal location close to the merger.
        from scipy.optimize import minimize
        
        if is_tGPS:
            tc = utils.GPSt_to_LMST(tcoal, lat=0., long=0.)
        else:
            tc = tcoal
        
        def pattern_fixedtpsi(pars, tc=tc):
            theta, phi = pars
            tmpsum = 0.
            for d in self.signals.keys():
                # compute the total by adding the squares of the signals and then taking the square root
                Fp, Fc = self.signals[d]._PatternFunction(theta, phi, t=tc, psi=0)
                tmpsum = tmpsum + (Fp**2 + Fc**2)
            return -onp.sqrt(tmpsum)
        # we actually minimize the total pattern function times -1, which is the same as maximizing it
        return minimize(pattern_fixedtpsi, [0.,0.]).x
