#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Tue Feb  8 17:22:56 2022

@author: Michi
"""

import numpy as onp
import utils
import fisherTools


class DetNet(object):
    
    def __init__(self, signals):
        
        # signals is a dictionary of the form
        # {'detector_name': GWSignal object }
        
        self.signals = signals
        
    
    def SNR(self, evParams, res=1000):
        self.snrs = {}
        for d in self.signals.keys():
            self.snrs[d] =  self.signals[d].SNRInteg(evParams, res=res)**2 
        return onp.sqrt(sum(self.snrs.values()))
        
    
    def FisherMatr(self, evParams, res=1000):
        self.Fishers = {}
        for d in self.signals.keys():
            print('\nComputing Fisher for %s...' %d)
            self.Fishers[d] =  self.signals[d].FisherMatr(evParams, res=res) 
        print('\nComputing total Fisher ...')
        totF = sum(self.Fishers.values())
        _, _, _ = fisherTools.CheckFisher(totF)
        print('Done.')
        return totF

    
    
    def CovMatr(self, evParams, 
                    FisherM=None, 
                    res=1000, 
                    invMethod='inv', 
                    condNumbMax=1e15, 
                   get_individual = True, 
                   return_inv_err=False,
                   return_dL_derivs = True, 
                   use_sin_iota=False,
                   use_reweighting=True):
        
        utils.check_evparams(evParams)
        
        if FisherM is None:
            # Compute total Fisher (which also computes individial Fishers)
            FisherM = self.FisherMatr(evParams, res=res)
            
        CovMatrices=None
        if get_individual:
            CovMatrices = {}
            for d in self.signals.keys():
                FisherM_ = self.Fishers[d]
                if FisherM is None:
                    FisherM_ = None
                print('\nComputing Covariance for %s...' %d)
                CovMatrices[d],  _ =  fisherTools.CovMatr(FisherM_, evParams, 
                                                                #FisherM=FisherM_, 
                                                                res=res,
                                                                invMethod=invMethod, 
                                                                condNumbMax=condNumbMax, 
                                                                return_dL_derivs = return_dL_derivs, 
                                                                use_sin_iota=use_sin_iota, 
                                                                use_reweighting=use_reweighting
                                                                ) 

        print('\nComputing total covariance ...')
        Cov, _ = fisherTools.CovMatr(FisherM, evParams, 
                                              #FisherM=FisherM, 
                                              res=res,
                                              invMethod=invMethod, 
                                              condNumbMax=condNumbMax, 
                                              return_dL_derivs = return_dL_derivs, 
                                              use_sin_iota=use_sin_iota, 
                                              use_reweighting=use_reweighting
                                              )
        print('Done.')
        #print(Cov.shape)
        eps=None
        if return_inv_err:
            if return_dL_derivs:
                print('Converting Fisher to dL to check inversion error...')
                FisherM_check = fisherTools.log_dL_to_dL_derivative_fish(FisherM, {'logdL':1}, evParams)
            else: FisherM_check = FisherM
            # TODO : vectorize this
            eps = [ onp.linalg.norm(Cov[:, :, i]@FisherM_check[:, :, i]-onp.identity(Cov.shape[0]), ord=onp.inf) for i in range(FisherM.shape[-1])]
            print('Inversion error: %s ' %str(eps))
        return Cov, eps, CovMatrices
        