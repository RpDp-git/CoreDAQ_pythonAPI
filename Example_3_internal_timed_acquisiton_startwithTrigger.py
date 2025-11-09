from coredaq_py_api import CoreDAQ
import time

daq = CoreDAQ("/dev/tty.usbmodem2062346055301") # Set your CoreDAQ port here

print("Device:", daq.idn())

# Streaming capture
daq.set_oversampling(2) # At 100 Ksps , max oversampling is 1 , At 50 Ksps : 2 , etc..
daq.set_freq(50_000)
##With External Trigger - acquistion starts only after rising edge of trigger signal is received
daq.trig_arm(100_000, rising=True)

start=time.time()
while not daq.is_data_ready():
    time.sleep(0.1)
end = time.time()
print(f"Acquired 100_000 frames")
# Bulk transfer raw data → mV arrays
ch = daq.transfer_frames_mv(100_000)

print("Ch1 first 5 samples (mV):", ch[0][:5])
print("Ch2 first 5 samples (mV):", ch[1][:5])
print("Ch3 first 5 samples (mV):", ch[2][:5])
print("Ch4 first 5 samples (mV):", ch[3][:5])





daq.close()  # Close the connection cleanly

