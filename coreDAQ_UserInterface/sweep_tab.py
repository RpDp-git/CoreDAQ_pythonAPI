# sweep_tab.py

from __future__ import annotations

from typing import Optional, List, Dict, Any

import time
import numpy as np
from PyQt5 import QtCore, QtGui, QtWidgets
import pyqtgraph as pg

from channels import ChannelManager, ChannelConfig
from coredaq_py_api import CoreDAQError, CoreDAQ

# -------------------- pyqtgraph config --------------------
pg.setConfigOptions(antialias=False)

# -------------------- Laser defaults --------------------
DEFAULT_START_NM = 1480.0
DEFAULT_STOP_NM = 1620.0
DEFAULT_POWER_MW = 1.0
DEFAULT_SPEED_NM_S = 50.0
DEFAULT_SAMPLE_RATE = 50_000  # Hz
DEFAULT_GPIB_ADDR = 1
DEFAULT_LASER_MODEL = "TSL770"

# -------------------- Laser model imports --------------------
try:
    from laser.TSL550 import TSL550
except Exception:  # pragma: no cover
    TSL550 = None  # type: ignore

try:
    from laser.TSL570 import TSL570
except Exception:  # pragma: no cover
    TSL570 = None  # type: ignore

try:
    from laser.TSL770 import TSL770  # adjust to your actual filename
except Exception:  # pragma: no cover
    TSL770 = None  # type: ignore

LASER_MODELS = {
    "TSL550": TSL550,
    "TSL570": TSL570,
    "TSL770": TSL770,
}


# -------------------- Worker for sweep (runs in QThread) --------------------
class SweepWorker(QtCore.QObject):
    finished = QtCore.pyqtSignal()
    error = QtCore.pyqtSignal(str)
    status = QtCore.pyqtSignal(str)
    result = QtCore.pyqtSignal(object, object)  # (wavelengths, channels_W)

    def __init__(self, params: Dict[str, Any], daq, parent=None):
        super().__init__(parent)
        self.params = params
        self.daq = daq  # shared CoreDAQ instance (do NOT close here)

    @QtCore.pyqtSlot()
    def run(self):
        p = self.params
        daq = self.daq

        if daq is None:
            self.error.emit("No CoreDAQ device connected.")
            self.finished.emit()
            return

        try:
            laser_model = p.get("laser_model", DEFAULT_LASER_MODEL)
            LaserClass = LASER_MODELS.get(laser_model, None)
            if LaserClass is None:
                raise RuntimeError(
                    f"Laser model '{laser_model}' is not available. "
                    f"Please ensure its driver is installed."
                )

            start_nm = float(p["start_nm"])
            stop_nm = float(p["stop_nm"])
            power_mw = float(p["power_mw"])
            speed_nm_s = float(p["speed_nm_s"])
            sample_rate = int(p["sample_rate"])
            gpib_addr = int(p["gpib_addr"])

            gain_ch1 = int(p.get("gain_ch1", 0))
            gain_ch2 = int(p.get("gain_ch2", 0))
            gain_ch3 = int(p.get("gain_ch3", 0))
            gain_ch4 = int(p.get("gain_ch4", 0))
            gains = [gain_ch1, gain_ch2, gain_ch3, gain_ch4]

            self.status.emit("Configuring laser…")

            laser = None
            try:
                # ----- Laser setup -----
                laser = LaserClass(gpip_address=gpib_addr)
                laser.connect()

                laser.set_wave_unit(0)  # nm
                laser.set_pow_unit(1)   # mW
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

                # ----- CoreDAQ setup -----
                self.status.emit("Configuring CoreDAQ…")
                try:
                    daq.set_oversampling(1)
                except Exception:
                    pass
                try:
                    daq.set_freq(sample_rate)
                except Exception:
                    pass

                # Apply user-selected gains
                for head, g in enumerate(gains, start=1):
                    try:
                        daq.set_gain(head, g)
                    except Exception:
                        pass

                # ----- Sweep timing -----
                sweep_span = stop_nm - start_nm
                if speed_nm_s <= 0:
                    raise ValueError("Sweep speed must be > 0 nm/s")

                sweep_duration_s = abs(sweep_span) / speed_nm_s
                samples_total = int(max(1, round(sweep_duration_s * sample_rate)))

                self.status.emit(f"Samples to acquire: {samples_total}")
                daq.trig_arm(samples_total)
                time.sleep(1.0)

                self.status.emit("Starting sweep and acquisition…")
                start_time = time.time()

                laser.set_sweep_start()

                while not daq.is_data_ready():
                    time.sleep(0.1)

                end_time = time.time()
                self.status.emit(
                    f"Acquired {samples_total} samples in {end_time - start_time:.2f} s"
                )

                # ----- Retrieve data (in W) -----
                time.sleep(0.5)
                try:
                    channels_W = daq.transfer_frames_W(samples_total)
                except CoreDAQError as e:
                    raise RuntimeError(f"CoreDAQ transfer error: {e}")

                # ----- Build wavelength axis -----
                t = np.arange(samples_total, dtype=float) / float(sample_rate)
                wavelengths = start_nm + sweep_span * (t / sweep_duration_s)
                wavelengths = np.clip(
                    wavelengths,
                    min(start_nm, stop_nm),
                    max(start_nm, stop_nm),
                )

                self.result.emit(wavelengths, channels_W)

            finally:
                try:
                    if laser is not None:
                        laser.close()
                except Exception:
                    pass

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

        self.le_gpib = add_line("GPIB address", "gpib_addr", int_validator)

        # Laser model combo
        self.laser_combo = QtWidgets.QComboBox(self)
        for name in ["TSL550", "TSL570", "TSL770"]:
            self.laser_combo.addItem(name, name)
        current_model = self._params.get("laser_model", DEFAULT_LASER_MODEL)
        idx = self.laser_combo.findData(current_model)
        if idx < 0:
            idx = self.laser_combo.findData(DEFAULT_LASER_MODEL)
        if idx >= 0:
            self.laser_combo.setCurrentIndex(idx)
        form.addRow("Laser model", self.laser_combo)

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

            model = self.laser_combo.currentData()
            self._params["laser_model"] = model
        except ValueError:
            QtWidgets.QMessageBox.warning(
                self,
                "Invalid input",
                "Please check that all numeric fields contain valid numbers.",
            )
            return
        super().accept()


# -------------------- Sweep tab widget --------------------
class SweepWidget(QtWidgets.QWidget):
    """
    Sweep-with-laser tab.

    Uses:
      - ChannelManager for which channels to plot/save
      - Shared CoreDAQ instance passed from main
      - Laser model selection + sweep params
    """

    sweep_started = QtCore.pyqtSignal()
    sweep_finished = QtCore.pyqtSignal()

    def __init__(self, manager: ChannelManager, daq, parent=None):
        super().__init__(parent)
        self.manager = manager
        self.daq = daq

        self.setObjectName("SweepContainer")

        self.params: Dict[str, Any] = {
            "start_nm": DEFAULT_START_NM,
            "stop_nm": DEFAULT_STOP_NM,
            "power_mw": DEFAULT_POWER_MW,
            "speed_nm_s": DEFAULT_SPEED_NM_S,
            "sample_rate": DEFAULT_SAMPLE_RATE,
            "gpib_addr": DEFAULT_GPIB_ADDR,
            "laser_model": DEFAULT_LASER_MODEL,
            "gain_ch1": 0,
            "gain_ch2": 0,
            "gain_ch3": 0,
            "gain_ch4": 0,
        }

        self.thread: Optional[QtCore.QThread] = None
        self.worker: Optional[SweepWorker] = None
        self.save_path: Optional[str] = None

        self.cards: List[Dict[str, Any]] = []
        self.gain_combos: List[QtWidgets.QComboBox] = []

        # Pre-fetch gain labels if available
        try:
            self.gain_labels = list(getattr(CoreDAQ, "GAIN_LABELS", []))
        except Exception:
            self.gain_labels = []

        self._build_ui()
        self.on_channels_updated()
        self._update_summary()

    # ------------------------------------------------------------------
    # Public API called from main
    # ------------------------------------------------------------------
    def set_daq(self, daq):
        self.daq = daq
        # Sync gain combos from device once, without autogain
        if self.daq is None or not self.gain_combos:
            return
        try:
            g1, g2, g3, g4 = self.daq.get_gains()
            gains = [int(g1), int(g2), int(g3), int(g4)]
            for i, g in enumerate(gains):
                if 0 <= g < self.gain_combos[i].count():
                    self.gain_combos[i].blockSignals(True)
                    self.gain_combos[i].setCurrentIndex(g)
                    self.gain_combos[i].blockSignals(False)
                    self.params[f"gain_ch{i+1}"] = g
        except Exception:
            pass

    def open_params_dialog(self, parent):
        dlg = SweepParamsDialog(self.params, parent)
        if dlg.exec_() == QtWidgets.QDialog.Accepted:
            self.params = dlg.params()
            self._update_summary()

    def on_channels_updated(self):
        while self.grid.count():
            item = self.grid.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        self.cards.clear()

        display_channels = self.manager.get_display_channels()
        if not display_channels:
            return

        axis_font = QtGui.QFont()
        axis_font.setPointSize(8)

        colors = [
            "#00E5FF",
            "#FF4081",
            "#FFD740",
            "#69F0AE",
            "#7C4DFF",
            "#FF6E40",
            "#64FFDA",
            "#FFEB3B",
        ]

        for idx, cfg in enumerate(display_channels):
            row = idx // 2
            col = idx % 2

            frame = QtWidgets.QFrame(self.inner)
            frame.setObjectName("SweepChannelCard")
            frame_layout = QtWidgets.QVBoxLayout(frame)
            frame_layout.setContentsMargins(10, 8, 10, 10)
            frame_layout.setSpacing(4)

            header = QtWidgets.QHBoxLayout()
            header.setContentsMargins(0, 0, 0, 0)
            header.setSpacing(6)

            name_label = QtWidgets.QLabel(cfg.name)
            name_font = name_label.font()
            name_font.setPointSize(int(name_font.pointSize() * 1.2))
            name_font.setBold(True)
            name_label.setFont(name_font)
            name_label.setStyleSheet("color: #ffffff;")
            header.addWidget(name_label)
            header.addStretch(1)

            frame_layout.addLayout(header)

            pw = pg.PlotWidget(background="k")
            pw.setMenuEnabled(False)
            pw.showGrid(x=True, y=True, alpha=0.2)
            pw.setLabel("bottom", "Wavelength", units="nm")
            if cfg.kind == "relative":
                pw.setLabel("left", cfg.name, units="dB")
            else:
                pw.setLabel("left", cfg.name, units="W")

            left_axis = pw.getAxis("left")
            bottom_axis = pw.getAxis("bottom")
            left_axis.setStyle(tickFont=axis_font)
            bottom_axis.setStyle(tickFont=axis_font)
            left_axis.setPen(pg.mkPen("#bbbbbb"))
            bottom_axis.setPen(pg.mkPen("#bbbbbb"))

            color = colors[idx % len(colors)]
            curve = pw.plot(
                pen=pg.mkPen(color, width=2),
                clipToView=True,
            )

            frame_layout.addWidget(pw, 1)
            self.grid.addWidget(frame, row, col)

            self.cards.append(
                {
                    "cfg": cfg,
                    "frame": frame,
                    "plot": pw,
                    "curve": curve,
                }
            )

        self.grid.setRowStretch((len(display_channels) + 1) // 2 + 1, 1)

    # ------------------------------------------------------------------
    # UI build
    # ------------------------------------------------------------------
    def _build_ui(self):
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(6)

        # --- summary + run ---
        top_row = QtWidgets.QHBoxLayout()
        top_row.setContentsMargins(0, 0, 0, 0)
        top_row.setSpacing(8)

        self.lbl_summary = QtWidgets.QLabel()
        self.lbl_summary.setWordWrap(True)
        top_row.addWidget(self.lbl_summary, 1)

        self.btn_run = QtWidgets.QPushButton("Run Sweep")
        self.btn_run.clicked.connect(self.run_sweep)
        top_row.addWidget(self.btn_run, 0, alignment=QtCore.Qt.AlignRight)

        outer.addLayout(top_row)

        # --- Gain row (manual gains only) ---
        gain_row = QtWidgets.QHBoxLayout()
        gain_row.setContentsMargins(0, 0, 0, 0)
        gain_row.setSpacing(10)

        self.gain_combos = []

        for head in range(1, 5):
            group = QtWidgets.QHBoxLayout()
            group.setSpacing(4)

            lbl = QtWidgets.QLabel(f"Gain CH{head}")
            lbl.setStyleSheet("color: #ffffff;")
            group.addWidget(lbl)

            combo = QtWidgets.QComboBox()
            combo.setMinimumWidth(70)

            for g in range(8):
                if self.gain_labels and g < len(self.gain_labels):
                    text = self.gain_labels[g]
                else:
                    text = f"G{g}"
                combo.addItem(text, g)
            combo.setCurrentIndex(0)

            combo.currentIndexChanged[int].connect(
                lambda value, h=head: self._on_gain_changed(h, value)
            )

            group.addWidget(combo)
            gain_row.addLayout(group)

            self.gain_combos.append(combo)

        gain_row.addStretch(1)
        outer.addLayout(gain_row)

        # --- plots area ---
        self.scroll = QtWidgets.QScrollArea(self)
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        outer.addWidget(self.scroll, 1)

        self.inner = QtWidgets.QWidget()
        self.scroll.setWidget(self.inner)

        self.grid = QtWidgets.QGridLayout(self.inner)
        self.grid.setContentsMargins(12, 12, 12, 12)
        self.grid.setSpacing(12)

        # --- log ---
        self.txt_log = QtWidgets.QPlainTextEdit()
        self.txt_log.setReadOnly(True)
        self.txt_log.setMaximumHeight(140)
        outer.addWidget(self.txt_log)

    # ------------------------------------------------------------------
    # Gain handling
    # ------------------------------------------------------------------
    def _on_gain_changed(self, head: int, value: int):
        """User changed gain for a head; apply immediately, no autogain."""
        self.params[f"gain_ch{head}"] = int(value)
        if self.daq is None:
            return
        try:
            self.daq.set_gain(head, int(value))
            self.log(f"Set gain CH{head} = {value}")
        except Exception as e:
            self.log(f"Failed to set gain CH{head}: {e}")

    # ------------------------------------------------------------------
    # Summary text
    # ------------------------------------------------------------------
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
            f"DAQ: {p['sample_rate'] / 1000:.1f} kHz, "
            f"Samples (est): {samples_est} "
            f"(~{sweep_duration:.2f} s)  |  "
            f"Laser: {p['laser_model']}  |  GPIB: {p['gpib_addr']}"
        )
        self.lbl_summary.setText(txt)

    # ------------------------------------------------------------------
    # Logging
    # ------------------------------------------------------------------
    def log(self, msg: str):
        t = time.strftime("%H:%M:%S")
        self.txt_log.appendPlainText(f"[{t}] {msg}")
        self.txt_log.verticalScrollBar().setValue(
            self.txt_log.verticalScrollBar().maximum()
        )

    # ------------------------------------------------------------------
    # Sweep control
    # ------------------------------------------------------------------
    def run_sweep(self):
        if self.thread is not None:
            self.log("Sweep already running.")
            return
        if self.daq is None:
            QtWidgets.QMessageBox.warning(
                self,
                "CoreDAQ not connected",
                "No CoreDAQ device is connected. Connect in the main window first.",
            )
            return

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

        # Take gains from comboboxes (manual only)
        for head in range(1, 5):
            if 0 <= head - 1 < len(self.gain_combos):
                combo = self.gain_combos[head - 1]
                g = combo.currentData()
                if g is None:
                    g = combo.currentIndex()
            else:
                g = 0
            self.params[f"gain_ch{head}"] = int(g)

        self._update_summary()
        self.btn_run.setEnabled(False)

        for card in self.cards:
            card["curve"].setData([], [])

        self.log(f"Starting sweep… (saving to {self.save_path})")

        self.sweep_started.emit()

        self.thread = QtCore.QThread(self)
        self.worker = SweepWorker(self.params, self.daq)
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
        self.sweep_finished.emit()

    def _on_error(self, msg: str):
        self.log(f"ERROR: {msg}")
        QtWidgets.QMessageBox.critical(self, "Sweep Error", msg)

    def _on_result(self, wavelengths, channels_W):
        wavelengths = np.asarray(wavelengths)
        if wavelengths.size == 0:
            self.log("No data returned from sweep.")
            return

        if not isinstance(channels_W, (list, tuple)):
            self.log("Channel data not in expected list/tuple form.")
            return

        phys_arrays: List[np.ndarray] = []
        for i in range(4):
            if i < len(channels_W):
                ys = np.asarray(channels_W[i])
                if ys.shape != wavelengths.shape:
                    ys = np.resize(ys, wavelengths.shape)
            else:
                ys = np.zeros_like(wavelengths)
            phys_arrays.append(ys)

        display_channels = self.manager.get_display_channels()
        channel_arrays: List[np.ndarray] = []

        for cfg in display_channels:
            if cfg.kind == "physical":
                idx = cfg.index or 0
                if 0 <= idx < 4:
                    arr = phys_arrays[idx]
                else:
                    arr = np.zeros_like(wavelengths)
            elif cfg.kind == "math":
                try:
                    arr = self.manager.eval_math_array(cfg, phys_arrays)
                except Exception:
                    arr = np.zeros_like(wavelengths)
            elif cfg.kind == "relative":
                try:
                    arr = self.manager.eval_relative_array(cfg, phys_arrays)
                except Exception:
                    arr = np.full_like(wavelengths, np.nan)
            else:
                arr = np.zeros_like(wavelengths)

            arr = np.asarray(arr)
            if arr.shape != wavelengths.shape:
                arr = np.resize(arr, wavelengths.shape)
            channel_arrays.append(arr)

        for card, arr in zip(self.cards, channel_arrays):
            cfg: ChannelConfig = card["cfg"]
            curve = card["curve"]
            plot = card["plot"]

            curve.setData(wavelengths, arr)

            ymin = float(np.nanmin(arr))
            ymax = float(np.nanmax(arr))
            if not np.isfinite(ymin) or not np.isfinite(ymax):
                continue

            span = ymax - ymin
            if span <= 0:
                span = max(1e-9, abs(ymax) * 0.2)
            pad = 0.3 * span
            lo = ymin - pad
            hi = ymax + pad

            if cfg.kind != "relative":
                lo = max(0.0, lo)

            if hi <= lo:
                hi = lo + span if span > 0 else lo + 1e-3

            plot.setYRange(lo, hi, padding=0)
            plot.setXRange(float(wavelengths.min()), float(wavelengths.max()), padding=0)

        self.log(
            f"Sweep result: λ in [{wavelengths.min():.1f}, {wavelengths.max():.1f}] nm"
        )

        if self.save_path is not None:
            try:
                self._save_csv_with_metadata(
                    self.save_path, wavelengths, display_channels, channel_arrays
                )
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

    # ------------------------------------------------------------------
    # CSV saving with metadata
    # ------------------------------------------------------------------
    def _save_csv_with_metadata(
        self,
        path: str,
        wavelengths: np.ndarray,
        display_channels: List[ChannelConfig],
        channel_arrays: List[np.ndarray],
    ):
        p = self.params

        board_T = None
        board_H = None
        die_T = None

        if self.daq is not None:
            try:
                board_T = float(self.daq.get_head_temperature_C())
            except Exception:
                board_T = None
            try:
                board_H = float(self.daq.get_head_humidity())
            except Exception:
                board_H = None
            try:
                die_T = float(self.daq.get_die_temperature_C())
            except Exception:
                die_T = None

        meta_lines = [
            f"laser_model={p.get('laser_model', DEFAULT_LASER_MODEL)}",
            f"start_nm={p.get('start_nm', DEFAULT_START_NM)}",
            f"stop_nm={p.get('stop_nm', DEFAULT_STOP_NM)}",
            f"power_mW={p.get('power_mw', DEFAULT_POWER_MW)}",
            f"speed_nm_per_s={p.get('speed_nm_s', DEFAULT_SPEED_NM_S)}",
            f"sample_rate_Hz={p.get('sample_rate', DEFAULT_SAMPLE_RATE)}",
            f"gpib_addr={p.get('gpib_addr', DEFAULT_GPIB_ADDR)}",
        ]

        if board_T is not None:
            meta_lines.append(f"board_temperature_C={board_T:.2f}")
        if board_H is not None:
            meta_lines.append(f"humidity_percent={board_H:.2f}")
        if die_T is not None:
            meta_lines.append(f"die_temperature_C={die_T:.2f}")

        col_names = ["wavelength_nm"]
        for cfg in display_channels:
            unit = cfg.unit or ("dB" if cfg.kind == "relative" else "W")
            safe_name = cfg.name.replace(",", "_").replace(" ", "_")
            col_names.append(f"{safe_name}_{unit}")

        header_data = ",".join(col_names)

        cols = [np.asarray(wavelengths)]
        for arr in channel_arrays:
            cols.append(np.asarray(arr))
        data = np.column_stack(cols)

        full_header = "\n".join(meta_lines + [header_data])

        np.savetxt(
            path,
            data,
            delimiter=",",
            header=full_header,
            comments="# ",
        )
