


import pyvisa as visa

class TSL770:
    """
    This class is used to communicate with a TSL770 spectrometer via GPIB protocol.     
    """

    def __init__(self, gpip_address):
        """
        Initializes a new instance of the TSL770 class.

        Parameters
        ----------
        gpip_address : int
            The GPIB address of the instrument.
        """
        
        self.gpib_address = gpip_address
        self.instrument = None
        self.connected = False
        self.max_intensity = None
        self.input_check = False
        self.step = None
        self.max_pow = 0
        self.pow = 0
        self.trig =0
        

    def connect(self):
        """
        Connects to the instrument via GPIB protocol.

        Parameters
        ----------
        gpip_address : int
            The GPIB address of the instrument.

        Returns
        -------
        None
        """
        rm = visa.ResourceManager()
        self.instrument = rm.open_resource('GPIB::{}'.format(self.gpib_address))
        self.instrument.write("*RST")
        print(self.instrument.query("*IDN?"))
        self.connected = True
        self.instrument.write(":POW:ATT:AUT 1") # Disable max power mode
        

    def set_wave_unit(self, wave_mode):
        """
        Sets the wavelength unit of the instrument.

        Parameters
        ----------
        
        wave_mode : int
            The wavelength unit. 0: nm, 1: THz.

        Returns
        -------
        None
        """
        self.instrument.write(":WAV:UNIT {}".format(wave_mode)) # 0 nm, 1 THz

    def set_wavelength(self, wavelength):
        """
        Sets the wavelength of the instrument.

        Parameters
        ----------
        
        wavelength : float
            The wavelength value.

        Returns
        -------
        None
        """
        self.instrument.write(":WAV {}".format(wavelength)) # in nm

    def set_wavelength_fine (self, wave_fine):
        """
        Sets the fine wavelength of the instrument.

        Parameters
        ----------
        
        wave_fine : float
            The fine wavelength value.

        Returns
        -------
        None
        """
        self.instrument.write("WAF:FIN {}".format(wave_fine))
    

    def set_pow_unit(self, pow_mode):
        """
        Sets the power unit of the instrument.

        Parameters
        ----------
        
        pow_mode : int
            The power unit. 0: dBm, 1: mW.

        Returns
        -------
        None
        """
        self.instrument.write(":POW:UNIT {}".format(pow_mode)) # 0 dBm, 1 mW 


    def set_pow_max(self, max_pow):
        """
        Sets the maximum power of the instrument.

        Parameters
        ----------
        max_pow : float
            The maximum power of the instrument.

        Returns
        -------
        None
        """
        self.max_pow = max_pow

    def set_power(self,pow):
        """
        Sets the intensity of the instrument to the given value.

        Parameters
        ----------
        
        max_intensity : int, float
        The maximum intensity allowed by the instrument.
        intensity : int, float
           The intensity value to set.
    
        Returns
        -------
        None.
        """
        
        if pow <= self.max_pow:
            self.instrument.write(":POW {}".format(pow)) 
        else:    
            raise ValueError(f"Error. The set power is higher than the maximum allowed! Please enter a lower value.")

    def set_sweep_settings(self, start_lim, end_lim, mode, dwel_time):
        """
        Configures the sweep settings for the instrument.
        Be aware, that in sweep repetition mode. A two way sweep counts as 2 cycles.

        Parameters
        ----------
        
        start_lim : int, float
            The starting wavelength value of the sweep in nanometers.
        end_lim : int, float
            The ending wavelength value of the sweep in nanometers.
        mode : int
            The sweep mode to use: 0 for step sweep and one way, 1 for continuous sweep and one way, 2 for step sweep and two way, 3 for continuous sweep and two way.
        dwel_time : int, float
            The dwell time for the sweep in seconds.
        
        Returns
        -------
        None.
        """
        # set the starting wavelength value of the sweep in nanometers
        self.instrument.write(":WAV:SWE:STAR {}".format(start_lim)) 
        
        # set the ending wavelength value of the sweep in nanometers
        self.instrument.write(":WAV:SWE:STOP {}".format(end_lim))
        
        # set the sweep mode to use
        self.instrument.write(":WAV:SWE:MOD {}".format(mode))
        
        # set the dwell time for the sweep in seconds
        self.instrument.write(":WAV:SWE:DWEL {}".format(dwel_time))
        
        
    def set_sweep_cycles(self, cycles):
        """
        Sets the number of cycles to be performed during the sweep.

        Parameters
        ----------
        cycles : int
            The number of cycles to be performed during the sweep.

        Returns
        -------
        None.
        """
        # set the number of cycles to be performed during the sweep
        self.instrument.write(":WAV:SWE:CYCL {}".format(cycles))
        
        
    def set_sweep_speed(self, speed):
        """
        Sets the sweep speed in nanometers per second.

        Parameters
        ----------
        speed : float
            The sweep speed in nanometers per second.

        Returns
        -------
        None.
        """
        # set the sweep speed in nanometers per second
        self.instrument.write(":WAV:SWE:SPE {}".format(speed))
        
        
    def set_sweep_step(self, step):
        """
        Sets the step size of the sweep in picometers.

        Parameters
        ----------
        step : int, float
            The step size of the sweep in picometers.

        Returns
        -------
        None.
        """
        # set the step size of the sweep in picometers
        self.instrument.write(":WAV:SWE:STEP {}pm".format(step))

    
    def get_sweep_step(self):
        """
        Queries the number of sweep steps for the instrument. 
        Stores it in a variable for further use.

        Parameters
        ----------
        None
        
        Returns
        -------
        Wave Sweep Count
        """
        current_step = self.instrument.query(":WAV:SWE:COUN?") # stores step count
        print(current_step)


    def get_error_check(self,wavelength, pow, pow_mode):
        """
        Checks if the input values are correct.

        Parameters
        ----------
        
        wavelength : int, float
            The wavelength value to check.
        intensity : int, float
            The intensity value to check.
        pow_mode : int
            The power unit mode to check: 0 for dBm and 1 for mW.

        Returns
        -------
        None.
        
        comparison needs fix. device readout in wrong format:
        +1.60000000E-006
         +4.000000E+00
         1
        """
        # compares each given value to the settings of the Laser
        set_wavelength =  self.instrument.query(":WAV?")
        set_pow =  self.instrument.query("POW?")
        set_pow_mode =  self.instrument.query(":POW:UNIT?")

        if set_wavelength == wavelength and set_pow == pow and set_pow_mode == pow_mode:
            print("Input succesful")
            self.input_check = True
        else:
            print("Input not successful.")
            print(set_wavelength,set_pow, set_pow_mode)
            #self.instrument.write(":OUTP OFF")
            self.input_check = True

    # trigger related functions were not tested. Therefore their functionality can not be garanteed. Please refer to the manual in case of trouble.
    def set_trigger_in(self,trig):
        """
        Sets the trigger input on or off.

        Parameters
        ----------
        trig : int
            0 for off and 1 for on.

        Returns
        -------
        None.
        """
        self.instrument.write(":TRIG:INP:EXT{}".format(trig))
    

    def set_trigger_in_mode(self,pol_in):
        """
        Sets the trigger input mode.

        Parameters
        ----------
        pol : int
            The trigger input mode: 0 for falling edge, 1 for rising edge.

        Returns
        -------
        None.
        """
        self.instrument.write(":TRIG:INP:ACT{}".format(pol_in))
    

    def set_trig_out_mode(self, out_mode):
        """
        Sets the trigger output mode.

        Parameters
        ----------
        out_mode : int
            The trigger output mode: 0 for None, 1 for Stop, 2 for Start, 3 for Step.

        Returns
        -------
        None.
        """
        self.instrument.write(":TRIG:OUTP{}".format(out_mode)) # 0 None, 1 Stop, 2 Start, 3 Step
    
    def set_trig_out_pol(self,pol_out):
        """
        Sets the trigger output polarity.

        Parameters
        ----------
        pol_out : int
            The trigger output polarity: 0 for high active, 1 for low active.

        Returns
        -------
        None.
        """
        self.instrument.write(":TRIG:OUTP:ACT {}".format(pol_out)) # 0 high active, 1 low active

    def set_trig_out_step(self, trig_step):
        """
        Sets the trigger output step.

        Parameters
        ----------
        trig_step : int
            The trigger output step in pm. 0.1 pm to specified wavelength span

        Returns
        -------
        None.
        """
        self.instrument.write(".TRIG:OUT:STEP{}".format(trig_step)) # in nm
    
    def set_trig_out_period(self, trig_period):
        """
        Sets the trigger output settings.

        Parameters
        ----------
        trig_period : int
            The trigger output settings.

        Returns
        -------
        None.
        """
        self.instrument.write(":TRIG:OUTP:SETT{}".format(trig_period)) # 0 periodic in time, 1 periodic in wavelength
    

    def set_trig_through (self, trig_thr):
        """
        Sets the trigger through.

        Parameters
        ----------
        trig_thr : int
            The trigger through value.

        Returns
        -------
        None.
        """
        self.instrument.write(":TRIG:THR {}".format(trig_thr)) # 0 off, 1 on
    
    


    # additional function for soft trigger and standby mode? 

    def set_laser_on(self):
        """
        Turns on the laser permanently.
    
        Parameters
        ----------
        None.
    
        Returns
        -------
        None.
        """
    
        if self.input_check == True:
            self.instrument.write(":POW:STAT 1")
        else:
            print("Error. Check if inputs are correct, the set power doesn't exceed the maximum power limit and the device is connected properly.")
    
    def set_sweep_start(self):
        """
        Turns on the laser in sweep mode.
    
        Parameters
        ----------
        None.
    
        Returns
        -------
        None.
        """
    
        if self.input_check == True:
            self.instrument.write("WAV:SWE 1") 
        else:
            print("Error. Check if inputs are correct. Possible errors are: maximum power limit exceeded, no connection, wrong sweep settings")
    
    def set_sweep_rep_start(self):
        """
        Turns on the laser in sweep repetition mode.
    
        Parameters
        ----------
        None.
    
        Returns
        -------
        None.
        """
    
        if self.input_check == True:
            self.instrument.write("WAV:SWE:REP") 
        else:
            print("Error")
    
    def set_sweep_stop(self):
        """
        Stops the laser sweep.
    
        Parameters
        ----------
        None.
    
        Returns
        -------
        None.
        """
    
        self.instrument.write("WAV:SWE 0")
    
    
    def set_laser_off(self):
        """
        Turns off the laser and disconnects.
    
        Parameters
        ----------
        None.
    
        Returns
        -------
        None.
        """
    
        self.instrument.write(":POW:STAT 0")
        self.instrument.close() # disconnects from the device
