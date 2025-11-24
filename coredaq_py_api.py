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

import serial, time, struct, threading, math
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
    def snapshot_mV(
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
            # Example: "11 10 11 12 G=5 0 0 5"
            parts = payload.split()
            if len(parts) < 4:
                raise CoreDAQError(f"SNAP? payload too short: {payload}")

            # First 4 are mV readings
            try:
                mv = [int(parts[i]) for i in range(4)]
            except ValueError as e:
                raise CoreDAQError(f"Failed to parse mV from SNAP?: {payload}") from e

            # Gains: look for element containing "G="
            gains = [0, 0, 0, 0]
            for i, part in enumerate(parts):
                if "G=" in part:
                    # Found the marker, extract first gain from "G=<value>"
                    try:
                        gains[0] = int(part.split("=")[1])
                        # Next 3 gains are in following elements
                        gains[1] = int(parts[i + 1])
                        gains[2] = int(parts[i + 2])
                        gains[3] = int(parts[i + 3])
                    except (ValueError, IndexError) as e:
                        raise CoreDAQError(f"Failed to parse gains from SNAP?: {payload}") from e
                    break

            return mv, gains

    def snapshot_W(
        self,
        n_frames: int = 1,
        timeout_s: float = 1.0,
        poll_hz: float = 200.0,
    ):
        """
        Take a calibrated snapshot and convert each channel to optical power in watts.
        Uses per-head, per-gain slopes stored in self._cal_slope[head][gain] (mV/W).
        Rounding is calculated from ADC resolution (16-bit, 5V reference):
        - ADC code resolution: 5V / 32768 = 0.1526 mV per LSB
        - Power precision: LSB / slope [mV/W]

        Returns:
            list of 4 floats in watts [head1..4], with precision scaled to ADC and gain
        """
        import math
        
        mv, gains = self.snapshot_mV(n_frames=n_frames,
                                     timeout_s=timeout_s,
                                     poll_hz=poll_hz)

        # ADC resolution: 16-bit, 5V reference
        # mV per LSB = 5000 mV / 32768 codes
        adc_mv_per_lsb = (self.FS_VOLTS * 1000) / self.CODES_PER_FS
        
        power_W = []
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

            # Calculate power precision from ADC LSB
            # Power LSB = ADC_mV_LSB / slope_mV_per_W
            power_lsb = adc_mv_per_lsb / slope_mV_per_W
            
            # Determine decimal places needed to represent one LSB
            # decimals = -log10(power_lsb), clamped to [0, 12]
            decimals = max(0, min(12, round(-math.log10(power_lsb))))
            
            power = mv[ch] / slope_mV_per_W
            power_W.append(round(power, decimals))

        return power_W

    def snapshot_autogain_W(
            self,
            n_frames: int = 5,
            min_mv: float = 50.0,
            max_mv: float = 4500.0,
            max_iters: int = 100,
            settle_s: float = 0.05,
        ):
            """
            Snapshot with auto-gain and convert to Watts.

            - Iteratively adjusts gains so each channel's |mV| is in [min_mv, max_mv]
            as far as possible (0 <= gain <= 7).
            - Uses snapshot_mV(n_frames) which returns (mv[4], gains[4]).
            - After gains settle, does one final snapshot and converts to Watts
            using self._cal_slope[head_idx][gain] (mV/W) with power-LSB-based rounding.

            Returns:
                (power_W, mv_final, gains_final)
            """

            if not hasattr(self, "_cal_slope"):
                raise CoreDAQError("Calibration slopes (self._cal_slope) not loaded.")

            # --- Iterative gain adjustment ---
            for _ in range(max_iters):
                mv, gains = self.snapshot_mV(n_frames)  # mv: list[4], gains: list[4]
                changed = False

                for ch in range(4):
                    v = abs(float(mv[ch]))
                    g = int(gains[ch])
                    head = ch + 1  # heads are 1..4 at the protocol level

                    if v < min_mv and g < 7:
                        # too small -> increase gain
                        self.set_gain(head, g + 1)
                        changed = True
                    elif v > max_mv and g > 0:
                        # too large -> decrease gain
                        self.set_gain(head, g - 1)
                        changed = True

                if not changed:
                    # all channels in range (or at gain limits)
                    break

                # Let the MCU's main loop run I2C_Refresh once
                time.sleep(settle_s)

            # --- Final snapshot with settled gains ---
            mv, gains = self.snapshot_mV(n_frames)

            # --- Convert to Watts with LSB-based rounding ---
            adc_mv_per_lsb = (self.FS_VOLTS * 1000.0) / self.CODES_PER_FS
            power_W = []

            for ch in range(4):
                head_idx = ch              # 0..3 in Python
                gain = int(gains[ch])
                mv_ch = float(mv[ch])

                try:
                    slope_mV_per_W = self._cal_slope[head_idx][gain]
                except Exception as e:
                    raise CoreDAQError(
                        f"No calibration slope for head {head_idx+1}, gain {gain}"
                    ) from e

                if slope_mV_per_W is None or slope_mV_per_W == 0.0:
                    raise CoreDAQError(
                        f"Invalid slope for head {head_idx+1}, gain {gain}: {slope_mV_per_W}"
                    )

                # Power LSB = ADC_mV_LSB / slope_mV_per_W
                power_lsb = adc_mv_per_lsb / slope_mV_per_W

                # decimals = -log10(power_lsb), clamped to [0, 12]
                if power_lsb <= 0.0:
                    decimals = 0
                else:
                    decimals = max(0, min(12, round(-math.log10(power_lsb))))

                power = mv_ch / slope_mV_per_W
                power_W.append(round(power, decimals))

            return power_W
            

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
    def transfer_frames_mV(self, frames: int) -> List[List[float]]:
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


    def transfer_frames_W(self, frames: int) -> List[List[float]]:
        """
        Transfers <frames> frames, converts them to optical power (W)
        using per-head, per-gain calibration and sensible rounding.

        Returns:
            power_ch: [ch1_list, ch2_list, ch3_list, ch4_list] in watts
        """

        if not hasattr(self, "_cal_slope"):
            raise CoreDAQError("Calibration slopes (self._cal_slope) not loaded.")

        # 1) Get voltages in mV (4 x N)
        mv_ch = self.transfer_frames_mV(frames)

        # 2) Get current gains for each head (assumed fixed during acquisition)
        gains = self.get_gains()  # e.g. [g1, g2, g3, g4]

        # 3) Convert mV -> W with LSB-based rounding
        adc_mv_per_lsb = (self.FS_VOLTS * 1000.0) / self.CODES_PER_FS
        power_ch = [[], [], [], []]

        for ch in range(4):
            head_idx = ch
            gain = int(gains[ch])

            try:
                slope_mV_per_W = self._cal_slope[head_idx][gain]
            except Exception as e:
                raise CoreDAQError(
                    f"No calibration slope for head {head_idx+1}, gain {gain}"
                ) from e

            if slope_mV_per_W is None or slope_mV_per_W == 0.0:
                raise CoreDAQError(
                    f"Invalid slope for head {head_idx+1}, gain {gain}: {slope_mV_per_W}"
                )

            # Power LSB = ADC_mV_LSB / slope_mV_per_W
            power_lsb = adc_mv_per_lsb / slope_mV_per_W

            # decimals = -log10(power_lsb), clamped to [0, 12]
            if power_lsb <= 0.0:
                decimals = 0
            else:
                decimals = max(0, min(12, round(-math.log10(power_lsb))))

            out_list = power_ch[ch]
            for v_mv in mv_ch[ch]:
                power = v_mv / slope_mV_per_W
                out_list.append(round(power, decimals))

        return power_ch
    

    def i2c_refresh(self) -> None:
        #"""Apply pending I2C changes (SHT45 read, TCA6424 writes/reads, etc.)."""
        st, payload = self._ask("I2C REFRESH")
        if st != "OK":
            raise CoreDAQError(f"I2C REFRESH failed: {payload}")

    def set_gain(self, head: int, value: int) -> None:
        """
        Set gain for a single head (1..4), value 0..7.
        The gain is applied immediately by the firmware; no I2C refresh required.
        """
        if head not in (1, 2, 3, 4):
            raise ValueError("head must be 1..4")
        if not (0 <= value <= 7):
            raise ValueError("gain value must be 0..7")

        st, payload = self._ask(f"GAIN {head} {value}")
        if st != "OK":
            raise CoreDAQError(f"GAIN {head} failed: {payload}")

       


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
    def set_gain1(self, value: int): self.set_gain(1, value)
    def set_gain2(self, value: int): self.set_gain(2, value)
    def set_gain3(self, value: int): self.set_gain(3, value)
    def set_gain4(self, value: int): self.set_gain(4, value)
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

        # -------------------------------------------------------
    # Temperature (SHT45 on board)
    # -------------------------------------------------------
    def get_head_temperature_C(self) -> float:
        """
        Returns board temperature in °C (float).
        MCU returns:  OK <xx.x>
        """
        with self._lock:
            self._writeln("TEMP?")
            line = self._readline()

        if not line.startswith("OK"):
            raise CoreDAQError(f"TEMP? error: {line}")

        val = line[3:].strip()
        try:
            return float(val)
        except ValueError:
            raise CoreDAQError(f"Bad TEMP format: '{val}'")

    # -------------------------------------------------------
    # Relative Humidity (%)
    # -------------------------------------------------------
    def get_head_humidity(self) -> float:
        """
        Returns relative humidity in % (float).
        MCU returns:  OK <xx.x>
        """
        with self._lock:
            self._writeln("HUM?")
            line = self._readline()

        if not line.startswith("OK"):
            raise CoreDAQError(f"HUM? error: {line}")

        val = line[3:].strip()
        try:
            return float(val)
        except ValueError:
            raise CoreDAQError(f"Bad HUM format: '{val}'")

    # -------------------------------------------------------
    # MCU Die Temperature (internal ADC)
    # -------------------------------------------------------
    def get_die_temperature_C(self) -> float:
        """
        Returns STM32 internal die temperature (°C).
        MCU returns: OK <xx.x>
        """
        with self._lock:
            self._writeln("DIE_TEMP?")
            line = self._readline()

        if not line.startswith("OK"):
            raise CoreDAQError(f"DIE_TEMP? error: {line}")

        val = line[3:].strip()
        try:
            return float(val)
        except ValueError:
            raise CoreDAQError(f"Bad DIE_TEMP format: '{val}'")
        
        
    # ---------- Helper: auto port discovery ----------
    @staticmethod
    def find():
        ports = []
        for p in serial.tools.list_ports.comports():
            if "modem" in p.device.lower() or "STM" in p.description:
                ports.append(p.device)
        return ports
