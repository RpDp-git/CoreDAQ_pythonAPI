from coredaq_py_api import CoreDAQ
import time

daq = CoreDAQ("/dev/tty.usbmodem2065344D55301") # Set your CoreDAQ port here

print("Device:", daq.idn())

# Streaming capture
daq.set_freq(50_000)
##With External Trigger - acquistion starts only after rising edge of trigger signal is received
daq.trig_arm(100_000, rising=True)

#How to wait till done
start=time.time()
while not daq.is_data_ready():
    time.sleep(0.1)
end = time.time()
print(f"Acquired 100_000 frames")

# Bulk transfer raw data → Watt arrays
ch = daq.transfer_frames_W(100_000)

print("Ch1 first 5 samples (W):", ch[0][:5])
print("Ch2 first 5 samples (W):", ch[1][:5])
print("Ch3 first 5 samples (W):", ch[2][:5])
print("Ch4 first 5 samples (W):", ch[3][:5])


daq.close()  # Close the connection cleanly






