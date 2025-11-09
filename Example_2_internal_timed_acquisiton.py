from coredaq_py_api import CoreDAQ
import time

daq = CoreDAQ("/dev/tty.usbmodem2062346055301")

print("Device:", daq.idn())

# Streaming capture
daq.set_oversampling(2) # At 100 Ksps , max oversampling is 1 , At 50 Ksps : 2 , etc..
daq.set_freq(50_000)
daq.acq_arm(100_000)
daq.acq_start()
start=time.time()
while not daq.is_data_ready():
    time.sleep(0.1)
end = time.time()
print(f"Acquired 3,000,000 frames in {end-start:.2f} seconds")
# Bulk transfer raw data → mV arrays
ch = daq.transfer_frames_mv(100_000)

print("Ch1 first 10 samples (mV):", ch[0][:10])
print("Ch2 first 10 samples (mV):", ch[1][:10])
print("Ch3 first 10 samples (mV):", ch[2][:10])
print("Ch4 first 10 samples (mV):", ch[3][:10])



daq.close()  # Close the connection cleanly

