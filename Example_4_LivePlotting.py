# live_snap_60hz.py
import time
from collections import deque
import matplotlib.pyplot as plt
from coredaq_py_api import CoreDAQ

PORT = "/dev/tty.usbmodem2062346055301"
WINDOW_SECONDS = 10        # length of view
UPDATE_HZ = 60             # plotting speed
SNAP_FRAMES = 1            # average 1 frame
ROUND_DEC = 1              # displayed precision

def main():
    print("Connecting...")
    daq = CoreDAQ(PORT)

    print("Device:", daq.idn())
    daq.set_freq(1000)
    daq.set_oversampling(7)
    daq.snapshot_mv(1)     # warm up read

    # Rolling buffers
    max_len = int(WINDOW_SECONDS * UPDATE_HZ)
    t = deque(maxlen=max_len)
    ch = [deque(maxlen=max_len) for _ in range(4)]

    # Setup plot
    plt.ion()
    fig, ax = plt.subplots(figsize=(9,4))
    lines = [ax.plot([], [], label=f"CH{i+1}")[0] for i in range(4)]
    ax.set_xlabel("Time (s)")
    ax.set_ylabel("Voltage (mV)")
    ax.grid(True)
    ax.legend()

    start = time.time()

    while True:
        # ---- Acquire one reading ----
        mv = daq.snapshot_mv(SNAP_FRAMES)
        now = time.time() - start

        t.append(now)
        for i in range(4):
            ch[i].append(round(mv[i], ROUND_DEC))

        # ---- Update plot ----
        for i in range(4):
            lines[i].set_data(t, ch[i])

        ax.set_xlim(max(0, now - WINDOW_SECONDS), now)
        ymin = min(min(c) for c in ch if len(c))
        ymax = max(max(c) for c in ch if len(c))
       
        ax.set_ylim(0, 5000)

        plt.pause(1.0 / UPDATE_HZ)

    daq.close()

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nExiting.")
