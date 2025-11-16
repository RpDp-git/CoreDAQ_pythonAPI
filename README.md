Features

✔ 4-channel simultaneous sampling

Ideal for wavelength-swept measurements.

✔ Up to 4 million samples per channel per scan

Captured via SDRAM frame dump.

✔ Linear & logarithmic TIA models

Calibration handled transparently.

✔ 8 selectable gain stages

Selectable either by gain index or maximum measurable power.

✔ Calibrated optical power

Converts mV → Watts using onboard calibration.

✔ TTL-synchronized acquisition

Synchronize with any external instrument providing a TTL signal.


from coredaq import CoreDAQ

with CoreDAQ("/dev/tty.usbmodemXXXX") as daq:
    print("Device:", daq.idn())

    # Snapshot in millivolts
    mv, gains = daq.snapshot_mv(n_frames=8)
    print("mV:", mv, "gains:", gains)

    # Calibrated snapshot in Watts
    power_W, mv, gains = daq.snapshot_mW(n_frames=8)
    print("Power (W):", power_W)

    # High-speed streaming
    daq.acq_arm(1_000_000)
    daq.acq_start()
    daq.wait_done()
    raw = daq.transfer_frames_mv(1_000_000)
