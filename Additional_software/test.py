# -*- coding: utf-8 -*-
"""
Created on Tue Nov 25 03:03:15 2025

@author: 1550nm
"""

import os
import time
import warnings
from dataclasses import dataclass

import matplotlib.pyplot as plt
import numpy as np

from sys import exit


from laser.TSL770_commented import TSL770 as TSL
os.add_dll_directory(os.path.join(os.path.dirname(__file__), "runtime"))

from API.CeboMsrApiPython import Device, DeviceType, LibraryInterface, Trigger
 

global SAMPLING_RATE
SAMPLING_RATE = 50000 #50000 to be reduced when using multiple channels

 	
class parameters():
    def __init__(self):
        self.laser_gpib = 1
        self.DAQ_channels = [2,3] #must be a list also if you measure a single channel
        self.sweep_wavelength_start_nm = 1480
        self.sweep_wavelength_end_nm = 1600
        self.sweep_speed_nm_per_s =50 #50 #50 #speed of the laser, do not change this value if you don't know what you are doing
        self.laser_power_mW = 1
        self.standard_wavelength = 1542  #1550 #wavelength at which the laser will be reset after the sweep
        self.reponse_factor_V_per_mW = 0.55  # to be calibrated with power meter
    
        self.gain = [30000,30000]
        #length = 10.0
        #gap = 17
        path = r"C:\Users\1550nm\Desktop\Linus Kuerpick\06_10_2025"   
        self.fname = path + r'\Bragg_1_von_links.csv'#                           
        # self.fname = path + f'10_20_laser_{self.laser_power_mW}mw_reference_gain={self.gain}.txt' 
        #self.fname = path + f'R4-4_{self.laser_power_mW}mW_gain{self.gain}_range{self.sweep_wavelength_start_nm}_{self.sweep_wavelength_end_nm}.txt'#
        #self.fname = path + f'R9-5_{self.laser_power_mW}mW_gain{self.gain}_range{self.sweep_wavelength_start_nm}_{self.sweep_wavelength_end_nm}.txt'#
        
# 6.7,6.8,6.9,7.0d   
# 25.1
@dataclass
class SweepConfig:
    wavelength_start_nm: float
    wavelength_end_nm: float
    
    
    sweep_slope_nm_per_s: float
    sweep_count: int
    trigger_step_pm: int

def acquire_sweep_single_trigger(
    laser: None,
    device: Device,
    sampling_rate_hz: int,
    sweep_config: SweepConfig,
    channels: list[int] = None,
    wait_for_trigger: bool = True,
    
   
):
    """This function acquires data at a fixed rate, hardware timed by the Cebo DAQ.

    Arguments:
        device -- The Cebo device.
        sampling_rate_hz -- Sampling rate in Hertz
        sweep_config -- Sweep configuration, used for determining the number of samples.

    Keyword Arguments:
        channels -- List of channels to sample. (default: [0])
        wait_for_trigger -- When set to False, data acquistion starts immediately. (default: {True})

    TODO: Add functionality to run multiple sweeps and average.
    """

    if not channels:
        channels = [0]

    sweep_duration_s = (
        sweep_config.wavelength_end_nm - sweep_config.wavelength_start_nm
    ) / sweep_config.sweep_slope_nm_per_s
    samples_total = round(sweep_duration_s * sampling_rate_hz)

    sample_buffer = []
    device.startContinuousDataAcquisition(sampling_rate_hz, wait_for_trigger)
    print("-- Waiting for trigger...")

    time.sleep(0.5)
    laser.set_sweep_start()

    
    while len(sample_buffer) < samples_total:
        new_samples = device.readNonBlocking()
        if len(new_samples) and not len(sample_buffer):
            print("Starting measurement.")
            sweep_start_ns = time.time_ns()
        sample_buffer.extend(new_samples)
    sweep_duration_measured_s = (time.time_ns() - sweep_start_ns) / 1e9
    device.stopDataAcquisition()
    # Remove any additional samples at the end.
    sample_buffer = sample_buffer[0:samples_total]

    data = [
        np.array([sample.getSingleEnded(channel) for sample in sample_buffer])
        for channel in channels
    ]
    # timescale = np.arange(len(data[0])) / sampling_rate_hz
    wavelengths = np.linspace(
        sweep_config.wavelength_start_nm, sweep_config.wavelength_end_nm, len(data[0])
    )

    print(f"{len(sample_buffer)} samples read.")
    print(f"Calculated sweep duration: {sweep_duration_s} s.")
    print(f"Measured sweep duration: {sweep_duration_measured_s} s.")

    return wavelengths,data


def define_input_frame(device: Device, channels: list[int] = None):
    """Creates an input frame for the Cebo DAQ based on a list of channels.

    Arguments:
        device -- The DAQ Device.

    Keyword Arguments:
        channels -- The list with single ended channels. (default: [0])

    Returns:
        Input frame that can be used to set up the Cebo DAQ.
    """
    if not channels:
        channels = [0]
    inputs = [device.getSingleEndedInputs()[channel] for channel in channels]
    return inputs
        

def main():    
    ''' Import Parameters
    
    
    if os.path.exists(par.fname):
        print('The file exists! program will be aborted')
        
        exit()'''
        
    par=parameters()
    global SAMPLING_RATE
    SAMPLING_RATE = SAMPLING_RATE/len(par.DAQ_channels)
    print('SAMPLING_RATE = ', SAMPLING_RATE)
    print('RESOLUTION = %f pm\n'%(1e3*par.sweep_speed_nm_per_s/SAMPLING_RATE))
    

    
    '''Save Parameters to file'''
    f = open(par.fname, 'w')
    f.write('------------------------------------ PARAMETERS: ------------------------------------------\n')
    f.write('\n'.join("%s: %s" % i for i in vars(par).items()))
    f.write('\nUSED SAMPLING RATE = %f'%SAMPLING_RATE)
    f.write('\nRESOLUTION = %f pm'%(1e3*par.sweep_speed_nm_per_s/SAMPLING_RATE))
    f.write('\n--------------------------------------------------------------------------------------------\n\n')
    f.close()
    
    device = None
    
    try:
        '''Initialize the laser'''
        '''The sweep mode is set to continuous mode by default (cannot be changed in the parameters setting). 
        Same for the dwell time which is set to zero seconds.
        Number of cycles is fixed to 1
        The laser trigger mode is set to out and will give a signal when the sweep starts
        The laser power is set to mW and the wavelength unit to mW'''
        
        laser=TSL(gpip_address=par.laser_gpib)
        laser.connect()
        laser.set_wave_unit(0)
        laser.set_pow_unit(1)
        laser.set_trigger_in(0)
        laser.set_sweep_cycles(1)
        laser.set_trig_out_mode(2)
        laser.set_sweep_speed(par.sweep_speed_nm_per_s)        
        #laser.set_pow_max(5.)
        laser.set_pow_max(30.)
        laser.set_power(par.laser_power_mW)
        laser.set_wavelength(par.sweep_wavelength_start_nm)
        laser.set_sweep_settings(start_lim=par.sweep_wavelength_start_nm, end_lim=par.sweep_wavelength_end_nm, mode=1, dwel_time=0)
        '''The get error check function seems not working, I bypassed it but shall be corrected'''
        laser.input_check=True
        #print(par.sweep_wavelength_start_nm, par.laser_power_mW, 1)
        #laser.get_error_check(par.sweep_wavelength_start_nm, par.laser_power_mW, 1)
        time.sleep(1)
        
        '''Initialize the CEBO-LC'''        
        
        devices = LibraryInterface.enumerate(DeviceType.All)#CeboLC)
        if len(devices) == 0:
            raise RuntimeError("No CeboLC found.")
        elif len(devices) > 1:
            warnings.warn("Multiple CeboLC found, first device is used.")

        device = devices[0]
        device.open()

        if device.getDeviceType() != DeviceType.CeboStick:#DeviceType.CeboLC:
            raise RuntimeError("Detected device is not correct") #of type LC.")
        
        device.resetDevice()
        
        ''' 1) Define the input frame '''
        ADC_CHANNELS=par.DAQ_channels   
        input_frame = define_input_frame(device, ADC_CHANNELS)
        device.setupInputFrame(input_frame)

        ''' 2) Set up the trigger '''
        device.getTriggers()[0].setConfig(Trigger.TriggerConfig.InputRisingEdge)
        device.getTriggers()[0].setEnabled(True)

        ''' 3) Start the data acquisition.
        the sweep count is set to 1, i.e. single sweep
        the trigger step function is used only for repeated sweep
        '''
        sweep_config = SweepConfig(par.sweep_wavelength_start_nm, par.sweep_wavelength_end_nm, par.sweep_speed_nm_per_s, 1, 50)
        # a)
        wavelengths,data_single_trigger = acquire_sweep_single_trigger(laser,
            device, SAMPLING_RATE, sweep_config, ADC_CHANNELS
        )
       
        output_array=[wavelengths]
        header_string=['Wavelength\t']
        header_units_string=['nm\t']
        fig, axs = plt.subplots(1,len(par.DAQ_channels),sharex=True,figsize=(12,5))
        
        for idx,y in enumerate(data_single_trigger):
            y=y/(par.reponse_factor_V_per_mW*par.gain[idx])
            
            if idx==0:
                axs.plot(wavelengths, y,label=f"C{idx}")
            else:
                #axs[1].plot(wavelengths, y,"o",label=f"C{idx}",)
                axs[idx].plot(wavelengths, y,label=f"C{idx}",)
            output_array.append(y)
            if idx<len(data_single_trigger)-1:
                header_string.append('CH%d\t'%ADC_CHANNELS[idx])
                header_units_string.append('mW\t')
                
            else:
                header_string.append('CH%d\n'%ADC_CHANNELS[idx])
                header_units_string.append('mW\n')
        
        axs[0].set_xlabel("Wavelength [nm]")
        axs[0].set_ylabel("Power [mW]")
        axs[0].set_xlim(par.sweep_wavelength_start_nm,par.sweep_wavelength_end_nm)
        #ax.legend()
        plt.tight_layout()
        plt.show()
        
        header = '%s%s'%(''.join(header_string),''.join(header_units_string))
        
        with open(par.fname, "ab") as f:
            np.savetxt(f, np.transpose(output_array), delimiter='\t', header=header)

                
        '''Reset the wavelength at the end of the sweep'''
        laser.set_wavelength(par.standard_wavelength)
    except Exception as e:
        print(e)
        #deletes file in case of buffer overflow
        os.remove(parameters().fname)

    finally:
        if device:
            device.close()

    
    


if __name__ == "__main__":
    main()
