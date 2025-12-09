# sweep_tab.py

import time
from typing import Dict, Any, List, Optional

import numpy as np
from PyQt5 import QtCore, QtGui, QtWidgets
import pyqtgraph as pg

from coredaq_py_api import CoreDAQ, CoreDAQError
from laser.TSL550 import TSL550
from laser.TSL570 import TSL570
from laser.TSL770 import TSL770
from channels import ChannelManager, safe_eval_expression


# -------------------- Defaults --------------------
DEFAULT_START_NM = 1480.0
DEFAULT_STOP_NM = 1620.0
DEFAULT_POWER_MW = 1.0
DEFAULT_SPEED_NM_S = 50.0
DEFAULT_SAMPLE_RATE = 50_000  # Hz
DEFAULT_DAQ_PORT = "COM4"     # fallback if auto-detect fails
DEFAULT_GPIB_ADDR = 1

# Order matters: first entry is the default
LASER_MODELS = ["TSL 770", "TSL 570", "TSL 550"]


# -------------------- SWEEP BACKEND --------------------
def perform_sweep_logic(
    start_nm: float,
    stop_nm: float,
    power_mw: float,
    speed_nm_s: float,
    sample_rate: float,
    daq_port: str,
    gpib_addr: int,
    laser_model: str,
) -> (np.ndarray, List[np.ndarray], Dict[str, Optional[float]]):
    """
    Actual CoreDAQ + laser control.

    Args:
        start_nm:    sweep start wavelength (nm)
        stop_nm:     sweep stop wavelength (nm)
        power_mw:    laser power (mW)
        speed_nm_s:  sweep speed (nm/s)
        sample_rate: DAQ sample rate (Hz)
        daq_port:    serial/USB port where CoreDAQ is connected
        gpib_addr:   GPIB address of the laser
        laser_model: label from LASER_MODELS

    Returns:
        wavelengths: np.ndarray of shape (N,) in nm
        channels_W:  list of 4 np.ndarray, each shape (N,), power in W
        env:         dict with keys:
                     "die_temp_C", "head_temp_C", "humidity_RH"
    """

    daq = None
    laser = None
    env = {
        "die_temp_C": None,
        "head_temp_C": None,
        "humidity_RH": None,
    }

    try:
        # -------- Laser setup --------
        if laser_model == "TSL 550":
            LaserClass = TSL550
        elif laser_model == "TSL 570":
            LaserClass = TSL570
        else:
            # Fallback / default
            LaserClass = TSL770

        laser = LaserClass(gpip_address=gpib_addr)
        laser.connect()

        laser.set_wave_unit(0)       # 0 = nm
        laser.set_pow_unit(1)        # 1 = mW
        laser.set_trigger_in(0)
        laser.set_sweep_cycles(1)
        laser.set_trig_out_mode(2)
        laser.set_sweep_speed(speed_nm_s)
        laser.set_pow_max(20.0)
        laser.set_power(power_mw)
        laser.set_wavelength(start_nm)
        laser.set_sweep_settings(
            start_lim=start_nm,
            end_lim=stop_nm,
            mode=1,      # CW sweep
            dwel_time=0,
        )

        laser.input_check = True

        # -------- DAQ setup --------
        daq = CoreDAQ(daq_port)
        daq.set_oversampling(1)
        daq.set_freq(sample_rate)

        # Gains are assumed to be set elsewhere (plotter/global UI)

        # -------- Sweep / acquisition timing --------
        sweep_span = stop_nm - start_nm
        if speed_nm_s <= 0:
            raise ValueError("Sweep speed must be > 0 nm/s")

        sweep_duration_s = abs(sweep_span) / speed_nm_s
        sweep_duration_s = max(sweep_duration_s, 1e-9)
        samples_total = int(max(1, round(sweep_duration_s * sample_rate)))

        print("Samples to Acquire:", samples_total)

        daq.trig_arm(samples_total)
        time.sleep(1.0)  # small setup delay

        print("Starting sweep and acquisition...")
        start_time = time.time()

        # Start wavelength sweep
        laser.set_sweep_start()

        # Wait for CoreDAQ to finish
        while not daq.is_data_ready():
            time.sleep(0.1)

        end_time = time.time()
        print(f"Acquired {samples_total} samples in {end_time - start_time:.2f} s")

        # -------- Retrieve data (in W) --------
        time.sleep(0.5)  # small delay for transfer stability
        channels_W = daq.transfer_frames_W(samples_total)  # list of 4 arrays

        # -------- Build wavelength axis --------
        t = np.arange(samples_total, dtype=float) / float(sample_rate)
        wavelengths = start_nm + sweep_span * (t / sweep_duration_s)
        wavelengths = np.clip(
            wavelengths,
            min(start_nm, stop_nm),
            max(start_nm, stop_nm),
        )

        # -------- Environment / temperatures --------
        try:
            env["die_temp_C"] = daq.get_die_temperature_C()
        except Exception:
            pass

        try:
            env["head_temp_C"] = daq.get_head_temperature_C()
        except Exception:
            pass

        try:
            env["humidity_RH"] = daq.get_head_humidity()
        except Exception:
            pass

        return wavelengths, channels_W, env

    finally:
        # Clean up hardware
        try:
            if daq is not None:
                daq.close()
        except Exception:
            pass

        try:
            if laser is not None:
                laser.close()
        except Exception:
            pass


# -------------------- Worker for sweep (runs in QThread) --------------------
class SweepWorker(QtCore.QObject):
    finished = QtCore.pyqtSignal()
    error = QtCore.pyqtSignal(str)
    status = QtCore.pyqtSignal(str)
    result = QtCore.pyqtSignal(object, object, object)  # (wavelengths, channels_W, env)

    def __init__(self, params: Dict[str, Any], parent=None):
        super().__init__(parent)
        self.params = params

    @QtCore.pyqtSlot()
    def run(self):
        p = self.params
        try:
            self.status.emit("Starting sweep backend…")

            start_nm = p["start_nm"]
            stop_nm = p["stop_nm"]
            power_mw = p["power_mw"]
            speed_nm_s = p["speed_nm_s"]
            sample_rate = p["sample_rate"]
            gpib_addr = int(p["gpib_addr"])
            laser_model = p.get("laser_model", LASER_MODELS[0])
            daq_port = p["daq_port"]

            t0 = time.time()
            wavelengths, channels_W, env = perform_sweep_logic(
                start_nm=start_nm,
                stop_nm=stop_nm,
                power_mw=power_mw,
                speed_nm_s=speed_nm_s,
                sample_rate=sample_rate,
                daq_port=daq_port,
                gpib_addr=gpib_addr,
                laser_model=laser_model,
            )
            t1 = time.time()
            self.status.emit(f"Sweep backend finished in {t1 - t0:.2f} s")

            self.result.emit(wavelengths, channels_W, env)
        except Exception as e:
            self.error.emit(str(e))
        finally:
            self.finished.emit()


# -------------------- Sweep parameter dialog --------------------
class SweepParamsDialog(QtWidgets.QDialog):
    def __init__(self, params: Dict[str, Any], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Sweep Parameters")

        self._params = params.copy()

        form = QtWidgets.QFormLayout(self)

        def add_line(label, key, validator=None):
            le = QtWidgets.QLineEdit(self)
            le.setText(str(self._params[key]))
            if validator is not None:
                le.setValidator(validator)
            form.addRow(label, le)
            return le

        double_validator = QtGui.QDoubleValidator(bottom=-1e9, top=1e9, decimals=6)
        int_validator = QtGui.QIntValidator(bottom=1, top=10**9)

        self.le_start_nm = add_line("Start λ (nm)", "start_nm", double_validator)
        self.le_stop_nm = add_line("Stop λ (nm)", "stop_nm", double_validator)
        self.le_speed = add_line("Speed (nm/s)", "speed_nm_s", double_validator)
        self.le_power = add_line("Power (mW)", "power_mw", double_validator)

        self.le_sample_rate = add_line(
            "Sample rate (Hz)", "sample_rate", int_validator
        )

        # GPIB address
        self.le_gpib = add_line("GPIB address", "gpib_addr", int_validator)

        # Laser model dropdown
        self.cmb_laser = QtWidgets.QComboBox(self)
        self.cmb_laser.addItems(LASER_MODELS)
        current_model = str(self._params.get("laser_model", LASER_MODELS[0]))
        idx = self.cmb_laser.findText(current_model)
        if idx < 0:
            idx = 0
        self.cmb_laser.setCurrentIndex(idx)
        form.addRow("Laser model", self.cmb_laser)

        btn_box = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel,
            QtCore.Qt.Horizontal,
            self,
        )
        btn_box.accepted.connect(self.accept)
        btn_box.rejected.connect(self.reject)
        form.addRow(btn_box)

    def params(self) -> Dict[str, Any]:
        return self._params

    def accept(self):
        try:
            self._params["start_nm"] = float(self.le_start_nm.text())
            self._params["stop_nm"] = float(self.le_stop_nm.text())
            self._params["speed_nm_s"] = float(self.le_speed.text())
            self._params["power_mw"] = float(self.le_power.text())

            self._params["sample_rate"] = int(self.le_sample_rate.text())
            self._params["gpib_addr"] = int(self.le_gpib.text())
            self._params["laser_model"] = self.cmb_laser.currentText()
        except ValueError:
            QtWidgets.QMessageBox.warning(
                self,
                "Invalid input",
                "Please check that all numeric fields contain valid numbers.",
            )
            return
        super().accept()


# -------------------- Sweep widget (tab) --------------------
class SweepWidget(QtWidgets.QWidget):
    """
    Tab for wavelength sweep with laser + CoreDAQ.

    - Shows one card per active channel (physical + math + relative).
    - Uses ChannelManager to know which channels exist.
    - Uses CoreDAQ + TSL laser in a QThread backend.
    """

    def __init__(self, manager: ChannelManager, parent=None):
        super().__init__(parent)
        self.manager = manager

        # current sweep params (no DAQ port here; it's auto-detected before each sweep)
        self.params: Dict[str, Any] = {
            "start_nm": DEFAULT_START_NM,
            "stop_nm": DEFAULT_STOP_NM,
            "power_mw": DEFAULT_POWER_MW,
            "speed_nm_s": DEFAULT_SPEED_NM_S,
            "sample_rate": DEFAULT_SAMPLE_RATE,
            "gpib_addr": DEFAULT_GPIB_ADDR,
            "laser_model": LASER_MODELS[0],  # default: TSL 770
            # "daq_port" will be injected in run_sweep()
        }

        self.thread: Optional[QtCore.QThread] = None
        self.worker: Optional[SweepWorker] = None
        self.save_path: Optional[str] = None

        # cards: list of dicts {entry, frame, curve, value_label, plot}
        self._cards: List[Dict[str, Any]] = []

        self._build_ui()
        self._update_summary()
        self.on_channels_updated()

    # ---------------- UI building ----------------

    def _build_ui(self):
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        # Summary row: label + Run button
        top_row = QtWidgets.QHBoxLayout()
        self.lbl_summary = QtWidgets.QLabel()
        self.lbl_summary.setWordWrap(True)
        top_row.addWidget(self.lbl_summary, 1)

        self.btn_run = QtWidgets.QPushButton("Run Sweep")
        self.btn_run.clicked.connect(self.run_sweep)
        top_row.addWidget(self.btn_run, 0, alignment=QtCore.Qt.AlignRight)

        layout.addLayout(top_row)

        # Scroll area with channel cards
        self.scroll = QtWidgets.QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QtWidgets.QFrame.NoFrame)

        self.scroll_inner = QtWidgets.QWidget()
        self.scroll.setWidget(self.scroll_inner)

        self.grid = QtWidgets.QGridLayout(self.scroll_inner)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setSpacing(10)

        layout.addWidget(self.scroll, 1)

        # Log at the bottom
        self.txt_log = QtWidgets.QPlainTextEdit()
        self.txt_log.setReadOnly(True)
        self.txt_log.setMaximumHeight(140)
        layout.addWidget(self.txt_log)

    # ---------------- Channel cards ----------------

    def on_channels_updated(self):
        """Called by MainWindow when channel config changes (View / Channels menu)."""
        self._rebuild_cards()

    def _collect_channel_entries(self) -> List[Dict[str, Any]]:
        """
        Build a list of logical channels (physical + math + relative)
        to be displayed as cards.
        Each entry is a dict describing how to compute that channel.
        """
        entries: List[Dict[str, Any]] = []

        # Physical channels (CH1..CH4), only if enabled in ChannelManager
        for idx in range(4):
            if self.manager.is_physical_enabled(idx):
                entries.append(
                    {
                        "kind": "physical",
                        "name": f"Channel {idx + 1}",
                        "unit": "W",
                        "phys_index": idx,
                    }
                )

        # Math channels from manager
        for cfg in self.manager.math_channels:
            entries.append(
                {
                    "kind": "math",
                    "name": cfg.name,
                    "unit": getattr(cfg, "unit", "W"),
                    "config": cfg,
                }
            )

        # Relative transmission channels
        for cfg in self.manager.relative_channels:
            entries.append(
                {
                    "kind": "relative",
                    "name": cfg.name,
                    "unit": getattr(cfg, "unit", "dB"),
                    "config": cfg,
                }
            )

        return entries

    def _clear_cards(self):
        while self.grid.count():
            item = self.grid.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        self._cards.clear()

    def _rebuild_cards(self):
        self._clear_cards()

        entries = self._collect_channel_entries()
        if not entries:
            return

        colors = ["#00E5FF", "#FF4081", "#FFD740", "#69F0AE", "#B388FF", "#FFAB91"]

        axis_font = QtGui.QFont()
        axis_font.setPointSize(9)

        cols = 2
        row = 0
        col = 0

        for idx, entry in enumerate(entries):
            frame = QtWidgets.QFrame()
            frame.setObjectName("SweepChannelCard")
            v = QtWidgets.QVBoxLayout(frame)
            v.setContentsMargins(10, 8, 10, 8)
            v.setSpacing(6)

            # Header row
            header = QtWidgets.QHBoxLayout()
            lbl_name = QtWidgets.QLabel(entry["name"])
            name_font = lbl_name.font()
            name_font.setPointSize(int(name_font.pointSize() * 1.3))
            name_font.setBold(True)
            lbl_name.setFont(name_font)
            header.addWidget(lbl_name, 0, QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter)

            lbl_value = QtWidgets.QLabel("—")
            val_font = lbl_value.font()
            val_font.setPointSize(int(val_font.pointSize() * 1.1))
            lbl_value.setFont(val_font)
            header.addWidget(lbl_value, 0, QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)

            v.addLayout(header)

            # Plot
            pw = pg.PlotWidget(background="k")
            pw.setMenuEnabled(False)
            pw.showGrid(x=True, y=True, alpha=0.25)
            pw.setLabel("bottom", "Wavelength", units="nm")
            pw.setLabel("left", entry["unit"])
            pw.getAxis("left").setStyle(tickFont=axis_font)
            pw.getAxis("bottom").setStyle(tickFont=axis_font)

            color = colors[idx % len(colors)]
            curve = pw.plot(pen=pg.mkPen(color, width=2), clipToView=True)
            try:
                curve.setDownsampling(auto=True, method="peak")
            except Exception:
                pass

            v.addWidget(pw, 1)

            self.grid.addWidget(frame, row, col)
            col += 1
            if col >= cols:
                col = 0
                row += 1

            self._cards.append(
                {
                    "entry": entry,
                    "frame": frame,
                    "plot": pw,
                    "curve": curve,
                    "value_label": lbl_value,
                }
            )

        # Stretch last row
        self.grid.setRowStretch(row + 1, 1)

    # ---------------- Summary ----------------

    def _update_summary(self):
        p = self.params
        sweep_span = abs(p["stop_nm"] - p["start_nm"])
        if p["speed_nm_s"] > 0:
            sweep_duration = sweep_span / p["speed_nm_s"]
            samples_est = int(max(1, round(sweep_duration * p["sample_rate"])))
        else:
            sweep_duration = float("inf")
            samples_est = 0

        txt = (
            f"Sweep: {p['start_nm']:.1f} nm → {p['stop_nm']:.1f} nm at "
            f"{p['speed_nm_s']:.1f} nm/s  |  "
            f"Power: {p['power_mw']:.1f} mW\n"
            f"Sample rate: {p['sample_rate'] / 1000:.1f} kHz, "
            f"Samples (est): {samples_est} "
            f"(~{sweep_duration:.2f} s)  |  "
            f"Laser: {p['laser_model']}  |  GPIB: {p['gpib_addr']}"
        )
        self.lbl_summary.setText(txt)

    # ---------------- Menubar hook ----------------

    def open_params_dialog(self, parent: QtWidgets.QWidget):
        dlg = SweepParamsDialog(self.params, parent)
        if dlg.exec_() == QtWidgets.QDialog.Accepted:
            self.params = dlg.params()
            self._update_summary()

    # ---------------- Logging ----------------

    def log(self, msg: str):
        t = time.strftime("%H:%M:%S")
        self.txt_log.appendPlainText(f"[{t}] {msg}")
        self.txt_log.verticalScrollBar().setValue(
            self.txt_log.verticalScrollBar().maximum()
        )

    # ---------------- Sweep control ----------------

    def _auto_detect_daq_port(self) -> str:
        """
        Try to auto-detect CoreDAQ via CoreDAQ.find().
        Fallback to DEFAULT_DAQ_PORT if detection fails.
        """
        try:
            ports = CoreDAQ.find()
            if ports:
                return ports[0]
        except Exception:
            pass
        return DEFAULT_DAQ_PORT

    def run_sweep(self):
        if self.thread is not None:
            self.log("Sweep already running.")
            return

        # Ask for CSV path *before* starting sweep
        default_name = time.strftime("coredaq_sweep_%Y%m%d_%H%M%S.csv")
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self,
            "Save sweep data as CSV",
            default_name,
            "CSV Files (*.csv);;All Files (*)",
        )
        if not path:
            self.log("Sweep canceled (no file path selected).")
            return

        self.save_path = path

        # Inject DAQ port automatically (no UI for this)
        daq_port = self._auto_detect_daq_port()
        self.params["daq_port"] = daq_port
        self.log(f"Using CoreDAQ port: {daq_port}")

        self.btn_run.setEnabled(False)

        # Clear plots
        for card in self._cards:
            card["curve"].setData([], [])

        self.log(f"Starting sweep… (saving to {self.save_path})")

        self.thread = QtCore.QThread(self)
        self.worker = SweepWorker(self.params)
        self.worker.moveToThread(self.thread)

        self.thread.started.connect(self.worker.run)
        self.worker.finished.connect(self._cleanup_thread)
        self.worker.error.connect(self._on_error)
        self.worker.status.connect(self.log)
        self.worker.result.connect(self._on_result)

        self.thread.start()

    def _cleanup_thread(self):
        if self.thread is not None:
            self.thread.quit()
            self.thread.wait()
            self.thread = None
            self.worker = None
        self.btn_run.setEnabled(True)
        self.log("Sweep thread finished.")

    def _on_error(self, msg: str):
        self.log(f"ERROR: {msg}")
        QtWidgets.QMessageBox.critical(self, "Sweep Error", msg)

    def _on_result(self, wavelengths, channels_W, env):
        """
        wavelengths: (N,),
        channels_W: list of 4 arrays (N,) in W for physical CH1..CH4
        env: dict with die/head temperature and humidity
        """
        wavelengths = np.asarray(wavelengths, dtype=float)
        if len(wavelengths) == 0:
            self.log("No data returned from sweep.")
            return

        if not isinstance(channels_W, (list, tuple)):
            self.log("Channel data not in expected list/tuple form.")
            return

        # Normalize physical data list length to 4
        if len(channels_W) < 4:
            channels_W = list(channels_W) + [
                np.zeros_like(wavelengths)
            ] * (4 - len(channels_W))
        elif len(channels_W) > 4:
            channels_W = channels_W[:4]

        phys = [np.asarray(channels_W[i], dtype=float) for i in range(4)]
        phys = [np.resize(ch, wavelengths.shape) for ch in phys]

        # For saving CSV: only enabled channels => the ones that have cards
        cols_data: List[np.ndarray] = [wavelengths]
        col_labels: List[str] = ["wavelength_nm"]

        # Compute data for each displayed channel entry
        for card in self._cards:
            entry = card["entry"]
            kind = entry["kind"]

            if kind == "physical":
                idx = entry["phys_index"]
                ys = phys[idx]

            elif kind == "math":
                cfg = entry["config"]
                expr = getattr(cfg, "expression", "")
                env_vars = {
                    "ch1": phys[0],
                    "ch2": phys[1],
                    "ch3": phys[2],
                    "ch4": phys[3],
                }
                try:
                    ys = np.asarray(safe_eval_expression(expr, env_vars), dtype=float)
                except Exception as e:
                    self.log(f"Math channel '{cfg.name}' error: {e}")
                    ys = np.zeros_like(wavelengths)

            elif kind == "relative":
                cfg = entry["config"]
                num_idx, den_idx = getattr(cfg, "rel_src_indices", (0, 1))
                num = phys[num_idx]
                den = phys[den_idx]
                ratio = np.divide(
                    num,
                    np.clip(den, 1e-15, None),
                    out=np.zeros_like(num),
                    where=den > 0,
                )
                ys = 10.0 * np.log10(np.clip(ratio, 1e-15, None))

            else:
                ys = np.zeros_like(wavelengths)

            ys = np.resize(ys, wavelengths.shape)

            curve = card["curve"]
            pw = card["plot"]
            val_label = card["value_label"]
            unit = entry["unit"]

            curve.setData(wavelengths, ys)

            # Autoscale Y with 30% padding
            ymin = float(np.nanmin(ys))
            ymax = float(np.nanmax(ys))
            if np.isfinite(ymin) and np.isfinite(ymax):
                span = ymax - ymin
                if span <= 0:
                    span = max(1e-9, abs(ymax) * 0.2)

                pad = 0.3 * span
                lo = ymin - pad
                hi = ymax + pad
                if hi <= lo:
                    hi = lo + span if span > 0 else lo + 1e-3

                pw.setYRange(lo, hi, padding=0)

            pw.setXRange(float(wavelengths.min()), float(wavelengths.max()), padding=0)

            # Update value label with last point
            last = ys[-1]
            if unit.lower() in ("w", "watt", "watts"):
                abs_val = abs(last)
                if abs_val >= 1e-3:
                    val_label.setText(f"{last * 1e3:.3f} mW")
                elif abs_val >= 1e-6:
                    val_label.setText(f"{last * 1e6:.3f} µW")
                elif abs_val >= 1e-9:
                    val_label.setText(f"{last * 1e9:.3f} nW")
                else:
                    val_label.setText(f"{last:.3e} W")
            elif unit.lower() == "db":
                val_label.setText(f"{last:.2f} dB")
            else:
                val_label.setText(f"{last:.3f} {unit}")

            # ----- add to CSV columns -----
            cols_data.append(ys)
            if unit:
                col_labels.append(f"{entry['name']} ({unit})")
            else:
                col_labels.append(entry["name"])

        self.log(
            f"Sweep result: λ in [{wavelengths.min():.1f}, {wavelengths.max():.1f}] nm"
        )

        # -------- Save CSV if path was selected --------
        if self.save_path is not None:
            try:
                data = np.column_stack(cols_data)
                column_header_line = ",".join(col_labels)

                # Metadata comments
                p = self.params
                die_temp = env.get("die_temp_C", None) if isinstance(env, dict) else None
                head_temp = env.get("head_temp_C", None) if isinstance(env, dict) else None
                humidity = env.get("humidity_RH", None) if isinstance(env, dict) else None

                meta_lines = [
                    "CoreDAQ wavelength sweep",
                    f"Timestamp: {time.strftime('%Y-%m-%d %H:%M:%S')}",
                    f"Laser model: {p['laser_model']}",
                    f"Start wavelength (nm): {p['start_nm']}",
                    f"Stop wavelength (nm): {p['stop_nm']}",
                    f"Sweep speed (nm/s): {p['speed_nm_s']}",
                    f"Laser power (mW): {p['power_mw']}",
                    f"Sample rate (Hz): {p['sample_rate']}",
                ]

                if die_temp is not None:
                    meta_lines.append(f"Device temperature (die) [°C]: {die_temp:.2f}")
                if head_temp is not None:
                    meta_lines.append(f"Frontend temperature [°C]: {head_temp:.2f}")
                if humidity is not None:
                    meta_lines.append(f"Humidity [%RH]: {humidity:.2f}")

                # Write all by hand so we get:
                #   # meta ...
                #   wavelength_nm,...
                with open(self.save_path, "w") as f:
                    for line in meta_lines:
                        f.write(f"# {line}\n")
                    f.write(column_header_line + "\n")
                    np.savetxt(f, data, delimiter=",", comments="")

                self.log(f"Saved CSV to: {self.save_path}")
            except Exception as e:
                self.log(f"ERROR saving CSV: {e}")
                QtWidgets.QMessageBox.warning(
                    self,
                    "Save Error",
                    f"Failed to save CSV:\n{e}",
                )
            finally:
                self.save_path = None