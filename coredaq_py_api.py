# coredaq.py #v1.0
# High-level driver for LumetriX CoreDAQ STM32 + AD7606 system
#
# REQUIREMENTS:
#   pip install pyserial
#
# USAGE EXAMPLE:
#   from coredaq import CoreDAQ
#   with CoreDAQ("/dev/tty.usbmodemxxxx") as daq:
#       print(daq.idn())
#       v = daq.snapshot_mv(8)
#       print(v)

import serial, time, struct, threading
import serial.tools.list_ports
from typing import Optional, Tuple, List

class CoreDAQError(Exception): pass


class CoreDAQ:
    # Conversion constants (fits AD7606 ±5V range)
    FS_VOLTS = 5.0          # Full-scale magnitude
    CODES_PER_FS = 32768.0  # 16-bit signed full-scale

    def __init__(self, port: str, timeout: float = 0.05):
        self._ser = serial.Serial(
            port=port,
            baudrate=115200,
            timeout=timeout,
            write_timeout=0.5
        )
        self._lock = threading.Lock()
        self._drain()

    # ---------- Lifecycle ----------
    def close(self):
        """Close USB CDC cleanly."""
        try:
            if self._ser.is_open:
                self._ser.flush()
                self._ser.reset_input_buffer()
                self._ser.reset_output_buffer()
                self._ser.close()
        except Exception:
            pass

    def __enter__(self):
        return self
    def __exit__(self, et, ev, tb):
        self.close()

    # ---------- Helpers ----------
    def _drain(self):
        try:
            self._ser.reset_input_buffer()
        except Exception:
            pass

    def _writeln(self, s: str):
        if not s.endswith("\n"):
            s += "\n"
        self._ser.write(s.encode("ascii"))

    def _readline(self) -> str:
        raw = self._ser.readline()
        if not raw:
            raise CoreDAQError("Device timeout")
        return raw.decode("ascii", "ignore").strip()

    def _ask(self, cmd: str) -> Tuple[str, str]:
        with self._lock:
            self._writeln(cmd)
            line = self._readline()
        if line.startswith("OK"):   return "OK", line[2:].strip()
        if line.startswith("ERR"):  return "ERR", line[3:].strip()
        if line.startswith("BUSY"): return "BUSY", ""
        return "ERR", line

    @staticmethod
    def _parse_int(s: str) -> int:
        return int(s, 0)

    # ---------- Identity / State ----------
    def idn(self) -> str:
        st, p = self._ask("IDN?")
        if st != "OK": raise CoreDAQError(p)
        return p
    
        # ---------- Triggered Acquisition ----------
    def trig_arm(self, frames: int, rising: bool = True):
        """
        Arm for an external trigger on TIM3 CH3.
        Acquisition will start automatically on edge.

        frames : total frames to acquire into SDRAM
        rising : True = rising edge, False = falling edge
        """
        if frames <= 0:
            raise ValueError("frames must be > 0")

        pol = "R" if rising else "F"
        st, payload = self._ask(f"TRIGARM {frames} {pol}")
        if st != "OK":
            raise CoreDAQError(f"TRIGARM failed: {payload}")

    def state_enum(self) -> int:
        st, p = self._ask("STATE?")
        if st != "OK": raise CoreDAQError(p)
        return self._parse_int(p)

    # ---------- Snapshot ----------
    def snapshot_mv(self, n_frames: int, timeout_s: float = 0.3) -> Tuple[float, float, float, float]:
        """
        Take a snapshot average of N samples at the current sampling rate.
        Returns 4 voltages in millivolts (rounded to 0.1 mV).
        """
        st, p = self._ask(f"SNAP {n_frames}")
        if st != "OK":
            raise CoreDAQError(f"SNAP failed: {p}")

        t0 = time.time()
        while True:
            st, p = self._ask("SNAP?")
            if st == "OK":
                vals = tuple(int(x) for x in p.split())
                # Convert to millivolts, rounded
                return tuple(round(v, 1) for v in vals)

            if time.time() - t0 > timeout_s:
                self._ask("SNAP CANCEL")
                raise CoreDAQError("Snapshot timeout")

            time.sleep(0.005)

    # ---------- Streaming ----------
    def acq_arm(self, frames: int):
        st, p = self._ask(f"ACQ ARM {frames}")
        if st != "OK": raise CoreDAQError(p)

    def acq_start(self):
        st, p = self._ask("ACQ START")
        if st != "OK": raise CoreDAQError(p)

    def frames_left(self) -> int:
        st, p = self._ask("LEFT?")
        if st != "OK": raise CoreDAQError(p)
        return self._parse_int(p)

    def stream_status(self) -> str:
        st, p = self._ask("STREAM?")
        if st != "OK": raise CoreDAQError(p)
        return p  # "STREAMING" or "IDLE"


    def is_data_ready(self) -> bool:
        state = self.state_enum()
        return state == 4  


    def wait_done(self, poll_s: float = 0.25):
        while self.state_enum() != 4:
            time.sleep(poll_s)

    def sdram_addr(self) -> int:
        st, p = self._ask("ADDR?")
        if st != "OK": raise CoreDAQError(p)
        return self._parse_int(p)
    
     
    # ---------- Bulk Data Transfer ----------
    def transfer_frames_mv(self, frames: int) -> List[List[float]]:
        """
        Transfers <frames> frames (each 4 channels) from SDRAM.
        Returns: [ch1_list, ch2_list, ch3_list, ch4_list] in millivolts.
        """
        bytes_needed = frames * 4 * 2  # 4 channels * int16
        ser = self._ser

        ser.reset_input_buffer()
        self._writeln(f"XFER {bytes_needed}")
        ser.flush()

        line = self._readline()
        if not line.startswith("OK"):
            raise CoreDAQError(f"XFER refused: {line}")

        buf = bytearray(bytes_needed)
        mv = memoryview(buf)
        got = 0
        t0 = time.time()

        while got < bytes_needed:
            r = ser.readinto(mv[got:])
            if r:
                got += r
            else:
                time.sleep(0.0005)

        dt = time.time() - t0
        print(f"[CoreDAQ] Received {bytes_needed} bytes in {dt:.3f}s → {(bytes_needed/1e6/dt):.2f} MB/s")

        # Parse into 4 channels
        ch = [[], [], [], []]
        idx = 0
        for _ in range(frames):
            for c in range(4):
                raw = struct.unpack_from("<h", buf, idx)[0]
                val_mv = (raw*self.FS_VOLTS*1000)/self.CODES_PER_FS
                ch[c].append(round(val_mv, 1))  # 0.1 mV resolution
                idx += 2

        return ch

    # ---------- Frequency ----------
    def get_freq_hz(self) -> int:
        st, p = self._ask("FREQ?")
        if st != "OK": raise CoreDAQError(p)
        return self._parse_int(p)

    def set_freq(self, hz: int):
        st, p = self._ask(f"FREQ {hz}")
        if st != "OK": raise CoreDAQError(p)

    # ---------- Oversampling ----------
    def set_oversampling(self, os_idx: int):
        st, p = self._ask(f"OS {os_idx}")
        if st != "OK": raise CoreDAQError(p)

    def get_oversampling(self) -> int:
        st, p = self._ask("OS?")
        if st != "OK": raise CoreDAQError(p)
        return self._parse_int(p)

    # ---------- Helper: auto port discovery ----------
    @staticmethod
    def find():
        ports = []
        for p in serial.tools.list_ports.comports():
            if "modem" in p.device.lower() or "STM" in p.description:
                ports.append(p.device)
        return ports