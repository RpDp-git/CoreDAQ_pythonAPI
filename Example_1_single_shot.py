from coredaq_py_api import CoreDAQ
import time

daq = CoreDAQ("/dev/tty.usbmodem2065344D55301") # Set your CoreDAQ port here

print("Device:", daq.idn())

# Snapshot (quick voltage read) with 5 frames averaging
print("Snapshot 5 frames (mV):", daq.snapshot_mv(5)) # Output in mv

print("Snapshot 5 frames (Watts):", daq.snapshot_W(1)[0]) # Output in Watts

print("Gains : ", daq.snapshot_mv(1)[1]) # Returns current gains

daq.close() # Close the connection cleanly


