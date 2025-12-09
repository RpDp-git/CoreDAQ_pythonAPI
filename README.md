<body>

  <h1>CoreDAQ Python API v4.0</h1>

  <p>
    High-level Python driver for the <strong>CoreDAQ</strong> 4-channel photonic data-acquisition system.
  </p>

  <p>
    This API provides a clean and robust interface for:
  </p>

  <ul>
    <li>Optical power measurement</li>
    <li>Swept-source photonic component characterization</li>
    <li>Multi-channel power monitoring</li>
    <li>High-speed streaming (millions of samples)</li>
    <li>Triggered acquisition</li>
    <li>Gain control (index-based or power-based)</li>
    <li>Calibration-corrected optical power</li>
    <li>USB-synchronized capture via TTL</li>
    <li>Full SDRAM frame dumps</li>
  </ul>

  <p>
    CoreDAQ communicates over USB (CDC / Virtual COM Port).
    The Python API uses only <strong>pyserial</strong> and standard Python libraries.
  </p>

  <hr>

  <h2>Installation</h2>

  <pre><code class="language-bash">pip install pyserial
</code></pre>

  <p>Place <code>coredaq.py</code> in your project or install it as a module/package.</p>

  <hr>

  <h2>Quickstart</h2>

  <pre><code class="language-python">from coredaq import CoreDAQ

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
</code></pre>

  <hr>

  <h1>Features</h1>

  <h3>✔ 4-channel simultaneous sampling</h3>
  <p>All four channels are sampled with aligned timing, ideal for wavelength-swept measurements and multi-port photonic component characterization.</p>

  <h3>✔ Up to 4 million samples per channel per scan</h3>
  <p>Data is captured into SDRAM and can be transferred in bulk to the host PC.</p>

  <h3>✔ Linear &amp; logarithmic TIA models</h3>
  <p>Supports both linear and logarithmic front-end configurations. Calibration is handled transparently in the Python API.</p>

  <h3>✔ 8 selectable gain stages</h3>
  <p>Gain can be selected per channel either by hardware index (0–7) or by specifying the desired maximum optical power range.</p>

  <h3>✔ Calibrated optical power</h3>
  <p>Converts millivolt readings to optical power in Watts using per-channel, per-gain calibration slopes loaded from the device.</p>

  <h3>✔ TTL-synchronized acquisition</h3>
  <p>Supports synchronization with external instruments via TTL sync, suitable for swept lasers, shutters, modulators, and other timing hardware.</p>

  <hr>

  <h1>Gain → Power Mapping</h1>

  <p>CoreDAQ exposes 8 discrete gain stages corresponding to maximum power ranges:</p>

  <table>
    <thead>
      <tr>
        <th>Gain</th>
        <th>Max Power</th>
        <th>Notes</th>
      </tr>
    </thead>
    <tbody>
      <tr>
        <td>0</td>
        <td>3.5 mW</td>
        <td>High power</td>
      </tr>
      <tr>
        <td>1</td>
        <td>1.5 mW</td>
        <td></td>
      </tr>
      <tr>
        <td>2</td>
        <td>750 µW</td>
        <td></td>
      </tr>
      <tr>
        <td>3</td>
        <td>350 µW</td>
        <td></td>
      </tr>
      <tr>
        <td>4</td>
        <td>75 µW</td>
        <td></td>
      </tr>
      <tr>
        <td>5</td>
        <td>35 µW</td>
        <td></td>
      </tr>
      <tr>
        <td>6</td>
        <td>3.5 µW</td>
        <td></td>
      </tr>
      <tr>
        <td>7</td>
        <td>350 nW</td>
        <td>Highest sensitivity</td>
      </tr>
    </tbody>
  </table>

  <p>Example (selecting by power range):</p>

  <pre><code class="language-python">daq.set_gain(1, power_range=3.5e-3)  # sets gain 0 (3.5 mW)
</code></pre>

  <hr>

  <h1>API Overview</h1>

  <p>The following sections summarize the most important parts of the CoreDAQ API. For the full implementation, see <code>coredaq.py</code>.</p>

  <h2>Class: CoreDAQ(port, timeout=0.05)</h2>

  <p>Opens a connection to a CoreDAQ device, configures the serial port, and loads calibration data from the device.</p>

  <h3>Lifecycle</h3>

  <table>
    <thead>
      <tr>
        <th>Method</th>
        <th>Description</th>
      </tr>
    </thead>
    <tbody>
      <tr>
        <td><code>close()</code></td>
        <td>Close the serial connection cleanly.</td>
      </tr>
      <tr>
        <td><code>__enter__ / __exit__</code></td>
        <td>Context manager support for <code>with CoreDAQ(...) as daq:</code>.</td>
      </tr>
    </tbody>
  </table>

  <hr>

  <h2>Identity &amp; Status</h2>

  <table>
    <thead>
      <tr>
        <th>Method</th>
        <th>Description</th>
      </tr>
    </thead>
    <tbody>
      <tr>
        <td><code>idn()</code></td>
        <td>Returns firmware identity string (e.g. <code>"CoreDAQ vX.Y"</code>).</td>
      </tr>
      <tr>
        <td><code>state_enum()</code></td>
        <td>Returns the current internal state machine code.</td>
      </tr>
      <tr>
        <td><code>get_freq_hz()</code></td>
        <td>Returns the current sampling/acquisition frequency in Hz.</td>
      </tr>
      <tr>
        <td><code>get_oversampling()</code></td>
        <td>Returns the current oversampling index used by the ADC front-end.</td>
      </tr>
    </tbody>
  </table>

  <hr>

  <h2>Calibration</h2>

  <p>On initialization, the driver loads a 4×8 table of calibration slopes (in mV/W) from the device, indexed by head and gain.</p>

  <table>
    <thead>
      <tr>
        <th>Method</th>
        <th>Description</th>
      </tr>
    </thead>
    <tbody>
      <tr>
        <td><code>get_cal_slope(head, gain)</code></td>
        <td>Returns the calibration slope (mV/W) for the given head (1–4) and gain (0–7).</td>
      </tr>
    </tbody>
  </table>

  <hr>

  <h1>Snapshot Measurements</h1>

  <h2><code>snapshot_mv(n_frames=1, ...)</code></h2>

  <p>
    Takes a snapshot averaged over <code>n_frames</code> frames and returns millivolt readings and active gains per channel.
  </p>

  <pre><code class="language-python">mv, gains = daq.snapshot_mv(n_frames=8)
print("mV:", mv)
print("gains:", gains)
</code></pre>

  <ul>
    <li><code>mv</code>: list of 4 integers (mV) for channels 1–4.</li>
    <li><code>gains</code>: list of 4 integers (0–7) for heads 1–4.</li>
  </ul>

  <h2><code>snapshot_mW(n_frames=1, ...)</code></h2>

  <p>
    Takes a calibrated snapshot and converts each channel to optical power in Watts, using the per-head, per-gain calibration slopes.
  </p>

  <pre><code class="language-python">power_W, mv, gains = daq.snapshot_mW(n_frames=8)
print("Power (W):", power_W)
</code></pre>

  <ul>
    <li><code>power_W</code>: list of 4 floats (W) for heads 1–4.</li>
    <li><code>mv</code>: underlying mV readings.</li>
    <li><code>gains</code>: corresponding gain indices.</li>
  </ul>

  <hr>

  <h1>Triggered Acquisition</h1>

  <table>
    <thead>
      <tr>
        <th>Method</th>
        <th>Description</th>
      </tr>
    </thead>
    <tbody>
      <tr>
        <td><code>trig_arm(frames, rising=True)</code></td>
        <td>Arm the device for an external trigger (e.g. TTL on TIM3 CH3) to capture a specified number of frames.</td>
      </tr>
      <tr>
        <td><code>acq_arm(frames)</code></td>
        <td>Arm internal acquisition logic for a specified number of frames.</td>
      </tr>
      <tr>
        <td><code>acq_start()</code></td>
        <td>Start the acquisition after arming.</td>
      </tr>
      <tr>
        <td><code>wait_done(poll_s=0.25)</code></td>
        <td>Block until acquisition is complete (state_enum() indicates data ready).</td>
      </tr>
      <tr>
        <td><code>frames_left()</code></td>
        <td>Returns the number of frames remaining to be acquired.</td>
      </tr>
      <tr>
        <td><code>stream_status()</code></td>
        <td>Returns <code>"STREAMING"</code> or <code>"IDLE"</code>, as reported by firmware.</td>
      </tr>
      <tr>
        <td><code>is_data_ready()</code></td>
        <td>Convenience: returns True when the internal state indicates data is ready to be read.</td>
      </tr>
    </tbody>
  </table>

  <hr>

  <h1>Streaming &amp; Bulk Transfer</h1>

  <h2><code>transfer_frames_mv(frames)</code></h2>

  <p>
    Transfers the specified number of frames from SDRAM and converts each ADC code to millivolts.
  </p>

  <pre><code class="language-python">data_mv = daq.transfer_frames_mv(1_000_000)
</code></pre>

  <p>Return format:</p>

  <pre><code>[
    [ch1_sample0, ch1_sample1, ...],
    [ch2_sample0, ch2_sample1, ...],
    [ch3_sample0, ch3_sample1, ...],
    [ch4_sample0, ch4_sample1, ...]
]
</code></pre>

  <p>
    Each value is scaled using the AD7606 full-scale range (±5 V) and reported in millivolts (typically with ~0.1 mV resolution).
  </p>

  <h2><code>transfer_frames_W(frames, gains=None, sig_digits=4)</code></h2>

  <p>
    Transfers frames from SDRAM, converts each sample to Watts using calibration slopes, and rounds to a physically meaningful precision (configurable using <code>sig_digits</code>).
  </p>

  <pre><code class="language-python">data_W = daq.transfer_frames_W(1_000_000)
</code></pre>

  <p>
    The return format mirrors <code>transfer_frames_mv</code>:
  </p>

  <pre><code>[
    [ch1_power0, ch1_power1, ...],
    [ch2_power0, ch2_power1, ...],
    [ch3_power0, ch3_power1, ...],
    [ch4_power0, ch4_power1, ...]
]
</code></pre>

  <ul>
    <li>If <code>gains</code> is <code>None</code>, the API queries <code>get_gains()</code> to determine the active gains per head.</li>
    <li><code>sig_digits</code> controls the number of significant digits for each power value.</li>
  </ul>

  <hr>

  <h1>Gain Control</h1>

  <h2><code>get_gains(with_power=False)</code></h2>

  <p>
    Reads the current latched gains for all four heads.
  </p>

  <pre><code class="language-python"># Raw gain indices
g1, g2, g3, g4 = daq.get_gains()

# Gain indices with corresponding max power
(g1, p1), (g2, p2), (g3, p3), (g4, p4) = daq.get_gains(with_power=True)
print("Head 1: gain", g1, "max power", p1, "W")
</code></pre>

  <ul>
    <li><code>with_power=False</code> (default): returns a 4-tuple of gain indices (0–7).</li>
    <li><code>with_power=True</code>: returns 4 tuples of (gain_index, max_power_W) using the predefined mapping.</li>
  </ul>

  <h2><code>set_gain(head, value=None, power_range=None, apply=True)</code></h2>

  <p>
    Sets the gain for a single head (1–4). Exactly one of <code>value</code> or <code>power_range</code> must be specified.
  </p>

  <pre><code class="language-python"># Set head 1 by direct gain index
daq.set_gain(1, value=3)

# Set head 2 by desired max power (e.g. 750 µW)
daq.set_gain(2, power_range=750e-6)
</code></pre>

  <ul>
    <li><code>value</code>: integer gain index (0–7).</li>
    <li><code>power_range</code>: maximum power in Watts (e.g. <code>3.5e-3</code>, <code>350e-9</code>), mapped internally to a gain index.</li>
    <li><code>apply=True</code>: triggers an <code>I2C REFRESH</code> to apply changes immediately.</li>
  </ul>

  <p>Convenience wrappers are provided:</p>

  <pre><code class="language-python">daq.set_gain1(value=2)
daq.set_gain2(value=5)
daq.set_gain3(value=7)
daq.set_gain4(value=0)
</code></pre>

  <hr>

  <h1>Frequency &amp; Oversampling</h1>

  <table>
    <thead>
      <tr>
        <th>Method</th>
        <th>Description</th>
      </tr>
    </thead>
    <tbody>
      <tr>
        <td><code>set_freq(hz)</code></td>
        <td>Set the acquisition or sample frequency, interpreted by firmware as Hz.</td>
      </tr>
      <tr>
        <td><code>get_freq_hz()</code></td>
        <td>Return the current configured sampling frequency (Hz).</td>
      </tr>
      <tr>
        <td><code>set_oversampling(os_idx)</code></td>
        <td>Set the oversampling index used by the ADC front-end.</td>
      </tr>
      <tr>
        <td><code>get_oversampling()</code></td>
        <td>Query the current oversampling index.</td>
      </tr>
    </tbody>
  </table>

  <hr>

  <h1>Auto-Discovery</h1>

  <p>
    The static method <code>CoreDAQ.find()</code> attempts to discover likely CoreDAQ serial ports automatically (based on device name and description).
  </p>

  <pre><code class="language-python">from coredaq import CoreDAQ

ports = CoreDAQ.find()
print("Possible CoreDAQ ports:", ports)

if ports:
    daq = CoreDAQ(ports[0])
</code></pre>

  <hr>

  <h1>Error Handling</h1>

  <p>
    All device or protocol errors raise the custom exception:
  </p>

  <pre><code class="language-python">class CoreDAQError(Exception):
    pass
</code></pre>

  <p>
    Catch <code>CoreDAQError</code> to handle timeouts, communication failures, and unexpected device responses.
  </p>

  <hr>

  <h1>Supported Python Versions</h1>

  <ul>
    <li>Python 3.9+</li>
    <li>Dependency: <code>pyserial</code></li>
  </ul>

  <hr>

  <h1>License</h1>

  <p>
    Insert your license text here (e.g. MIT, Apache 2.0, or a custom research license).
  </p>

</body>
</html>
