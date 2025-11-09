from coredaq_py_api import CoreDAQ
import time

daq = CoreDAQ("/dev/tty.usbmodem2062346055301") # Set your CoreDAQ port here

print("Device:", daq.idn())

# Snapshot (quick voltage read) with 5 frames averaging
daq.set_freq(1000)
daq.set_oversampling(7)
print("Snapshot 5 frames:", daq.snapshot_mv(1))

daq.close() # Close the connection cleanly


