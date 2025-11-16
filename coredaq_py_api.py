# coredaq.py #v3.0
# High-level driver for coreDAQ
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

    NUM_HEADS = 4
    NUM_GAINS = 8

    def __init__(self, port: str, timeout: float = 0.05):
        self._ser = serial.Serial(
            port=port,
            baudrate=115200,
            timeout=timeout,
            write_timeout=0.5
        )
        self._lock = threading.Lock()
        self._drain()

        # 4×8 calibration table: [head-1][gain] -> slope (mV/W)
        self._cal_slope = [
            [0.0 for _ in range(self.NUM_GAINS)]
            for _ in range(self.NUM_HEADS)
        ]

        # load calibration from MCU
        self._load_calibration()

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
    
    # ---------- calibration loader ----------
    def _load_calibration(self):
        """
        Query all heads/gains via CAL <head> <gain> and populate
        self._cal_slope[head-1][gain] with slope (mV/W) as float.
        Expects reply: OK H<h> G<g> <HEX>
        where <HEX> is little-endian IEEE754 float.
        """
        for head in range(1, self.NUM_HEADS + 1):
            for gain in range(self.NUM_GAINS):
                status, payload = self._ask(f"CAL {head} {gain}")
                if status != "OK":
                    raise CoreDAQError(f"CAL {head} {gain} failed: {payload}")

                parts = payload.split()
                if len(parts) != 3:
                    raise CoreDAQError(f"Unexpected CAL reply: {payload!r}")

                hex_str = parts[2]
                try:
                    bits = int(hex_str, 16)
                    slope = struct.unpack(
                        "<f", bits.to_bytes(4, byteorder="little")
                    )[0]
                except Exception as e:
                    raise CoreDAQError(
                        f"Failed to parse CAL {head} {gain} payload {payload!r}: {e}"
                    )

                self._cal_slope[head - 1][gain] = slope

    # ---------- convenient accessor ----------
    def get_cal_slope(self, head: int, gain: int) -> float:
        """
        Return slope (mV/W) for given head (1..4) and gain (0..7).
        """
        if not (1 <= head <= self.NUM_HEADS):
            raise ValueError("head must be 1..4")
        if not (0 <= gain < self.NUM_GAINS):
            raise ValueError("gain must be 0..7")
        return self._cal_slope[head - 1][gain]
    
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
    def snapshot_mv(
        self,
        n_frames: int = 1,
        timeout_s: float = 1.0,
        poll_hz: float = 200.0,
    ):
        """
        Take a snapshot averaged over n_frames frames.
        Returns:
            (mv_list, gains_list)
            - mv_list:   list of 4 ints (mV) [ch1..ch4]
            - gains_list: list of 4 ints (gain index 0..7 for head1..4)
        """
        # Arm snapshot
        st, payload = self._ask(f"SNAP {n_frames}")
        if st != "OK":
            raise CoreDAQError(f"SNAP arm failed: {payload}")

        t0 = time.time()
        sleep_s = 1.0 / poll_hz

        while True:
            st, payload = self._ask("SNAP?")
            if st == "BUSY":
                if (time.time() - t0) > timeout_s:
                    raise CoreDAQError("Snapshot timeout")
                time.sleep(sleep_s)
                continue

            if st != "OK":
                raise CoreDAQError(f"SNAP? failed: {payload}")

            # Expected format:
            # "<m0> <m1> <m2> <m3> G=<g1> <g2> <g3> <g4>"
            parts = payload.split()
            if len(parts) < 4:
                raise CoreDAQError(f"SNAP? payload too short: {payload}")

            # First 4 are mV readings
            try:
                mv = [int(parts[i]) for i in range(4)]
            except ValueError as e:
                raise CoreDAQError(f"Failed to parse mV from SNAP?: {payload}") from e

            # Gains are optional but we expect "G=" marker now
            gains = [0, 0, 0, 0]
            if "G=" in parts:
                gi = parts.index("G=")
                if gi + 4 >= len(parts):
                    raise CoreDAQError(f"SNAP? gain block incomplete: {payload}")
                try:
                    gains = [int(parts[gi+1]),
                             int(parts[gi+2]),
                             int(parts[gi+3]),
                             int(parts[gi+4])]
                except ValueError as e:
                    raise CoreDAQError(f"Failed to parse gains from SNAP?: {payload}") from e
            else:
                # If firmware ever omits gains, leave zeros or raise:
                # raise CoreDAQError("SNAP? did not include gain info")
                pass

            return mv, gains

    def snapshot_mW(
        self,
        n_frames: int = 1,
        timeout_s: float = 1.0,
        poll_hz: float = 200.0,
    ):
        """
        Take a calibrated snapshot and convert each channel to optical power in watts.
        Uses per-head, per-gain slopes stored in self.cal_slopes[head][gain].

        Returns:
            (power_W, mv_list, gains_list)
            - power_W: list of 4 floats in watts [head1..4]
        """
        mv, gains = self.snapshot_mv(n_frames=n_frames,
                                     timeout_s=timeout_s,
                                     poll_hz=poll_hz)

        

        power_W = [None] * 4
        for ch in range(4):
            head = ch  # head index 0..3 for heads 1..4
            gain = gains[ch]

            try:
                slope_mV_per_W = self._cal_slope[head][gain]
            except (IndexError, KeyError, TypeError):
                raise CoreDAQError(
                    f"No calibration slope for head {head+1}, gain {gain}"
                )

            if slope_mV_per_W is None or slope_mV_per_W == 0.0:
                raise CoreDAQError(
                    f"Invalid slope for head {head+1}, gain {gain}: {slope_mV_per_W}"
                )

            # Intercept is ignored by your choice (dominated by slope)
            # P[W] = V[mV] / (slope[mV/W])
            power_W[ch] = mv[ch] / slope_mV_per_W

        return power_W, mv, gains
            

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

    def i2c_refresh(self) -> None:
        #"""Apply pending I2C changes (SHT45 read, TCA6424 writes/reads, etc.)."""
        st, payload = self._ask("I2C REFRESH")
        if st != "OK":
            raise CoreDAQError(f"I2C REFRESH failed: {payload}")

    def set_gain(self, head: int, value: int, apply: bool = True) -> None:
        """
        Queue gain change for a single head (1..4), value 0..7.
        Automatically calls I2C REFRESH unless apply=False.
        """
        if head not in (1, 2, 3, 4):
            raise ValueError("head must be 1..4")
        if not (0 <= value <= 7):
            raise ValueError("gain value must be 0..7")

        st, payload = self._ask(f"GAIN {head} {value}")
        if st != "OK":
            raise CoreDAQError(f"GAIN {head} failed: {payload}")

        if apply:
            self.i2c_refresh()

    def set_gains(self, g1: int | None = None, g2: int | None = None,
                g3: int | None = None, g4: int | None = None,
                apply: bool = True) -> None:
        """
        Queue multiple gains at once; only heads with non-None values are changed.
        Automatically calls I2C REFRESH unless apply=False.
        """
        updates = []
        if g1 is not None:
            if not (0 <= g1 <= 7): raise ValueError("g1 must be 0..7")
            updates.append(("GAIN 1", g1))
        if g2 is not None:
            if not (0 <= g2 <= 7): raise ValueError("g2 must be 0..7")
            updates.append(("GAIN 2", g2))
        if g3 is not None:
            if not (0 <= g3 <= 7): raise ValueError("g3 must be 0..7")
            updates.append(("GAIN 3", g3))
        if g4 is not None:
            if not (0 <= g4 <= 7): raise ValueError("g4 must be 0..7")
            updates.append(("GAIN 4", g4))

        for cmd, val in updates:
            st, payload = self._ask(f"{cmd} {val}")
            if st != "OK":
                raise CoreDAQError(f"{cmd} failed: {payload}")

        if updates and apply:
            self.i2c_refresh()

    def get_gains(self) -> tuple[int, int, int, int]:
        """
        Read current latched gains for all heads. Expects firmware 'GAIN?' to return
        'HEAD1=<n> HEAD2=<n> HEAD3=<n> HEAD4=<n>'.
        """
        st, payload = self._ask("GAINS?")
        if st != "OK":
            raise CoreDAQError(f"GAINS? failed: {payload}")
        # Robust parse
        parts = payload.replace("HEAD", "").replace("=", " ").split()
        try:
            # sequence should be: 1 v 2 v 3 v 4 v  -> take every 2nd number
            nums = [int(parts[i]) for i in range(1, len(parts), 2)]
            if len(nums) != 4:
                raise ValueError
            return tuple(nums)  # type: ignore[return-value]
        except Exception:
            raise CoreDAQError(f"Unexpected GAIN? payload: '{payload}'")

    # Optional convenience per-head setters:
    def set_gain1(self, value: int, apply: bool = True): self.set_gain(1, value, apply)
    def set_gain2(self, value: int, apply: bool = True): self.set_gain(2, value, apply)
    def set_gain3(self, value: int, apply: bool = True): self.set_gain(3, value, apply)
    def set_gain4(self, value: int, apply: bool = True): self.set_gain(4, value, apply)
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
