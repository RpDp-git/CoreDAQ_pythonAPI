#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import sys
import time
import numpy as np

from PyQt5 import QtCore, QtGui, QtWidgets
import pyqtgraph as pg

from laser.TSL770_commented import TSL770 as TSL
from coredaq_py_api import CoreDAQ, CoreDAQError


# -------------------- pyqtgraph config --------------------
pg.setConfigOptions(antialias=False)

# -------------------- Defaults --------------------
DEFAULT_START_NM     = 1480.0
DEFAULT_STOP_NM      = 1620.0
DEFAULT_POWER_MW     = 1.0
DEFAULT_SPEED_NM_S   = 50.0
DEFAULT_SAMPLE_RATE  = 50_000     # Hz
DEFAULT_DAQ_PORT     = "COM4"
DEFAULT_GPIB_ADDR    = 1


# -------------------- SWEEP BACKEND --------------------
def perform_sweep_logic(
    start_nm: float,
    stop_nm: float,
    power_mw: float,
    speed_nm_s: float,
    sample_rate: float,
    extra_params: dict,
):
    """
    Actual CoreDAQ + laser control.

    Args:
        start_nm:      sweep start wavelength (nm)
        stop_nm:       sweep stop wavelength (nm)
        power_mw:      laser power (mW)
        speed_nm_s:    sweep speed (nm/s)
        sample_rate:   DAQ sample rate (Hz)
        extra_params:  dict with "daq_port", "gpib_addr", gains, etc.

    Returns:
        wavelengths: np.ndarray of shape (N,) in nm
        channels_W:  list of 4 np.ndarray, each shape (N,), power in W
    """

    daq_port  = extra_params.get("daq_port", DEFAULT_DAQ_PORT)
    gpib_addr = int(extra_params.get("gpib_addr", DEFAULT_GPIB_ADDR))

    gain_ch1 = int(extra_params.get("gain_ch1", 0))
    gain_ch2 = int(extra_params.get("gain_ch2", 0))
    gain_ch3 = int(extra_params.get("gain_ch3", 0))
    gain_ch4 = int(extra_params.get("gain_ch4", 0))
    gains = [gain_ch1, gain_ch2, gain_ch3, gain_ch4]

    daq = None
    laser = None

    try:
        # -------- Laser setup --------
        laser = TSL(gpip_address=gpib_addr)
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
            mode=1,         # CW sweep
            dwel_time=0
        )

        laser.input_check = True

        # -------- DAQ setup --------
        daq = CoreDAQ(daq_port)
        daq.set_oversampling(1)
        daq.set_freq(sample_rate)

        # Apply gains to all 4 heads
        for head, g in enumerate(gains, start=1):
            try:
                daq.set_gain(head, g)
            except Exception:
                # If gain fails for some channel, keep going for now
                pass

        # -------- Sweep / acquisition timing --------
        sweep_span = stop_nm - start_nm
        if speed_nm_s <= 0:
            raise ValueError("Sweep speed must be > 0 nm/s")

        sweep_duration_s = abs(sweep_span) / speed_nm_s
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
        # Use signed sweep_span so wavelength direction follows start→stop
        wavelengths = start_nm + sweep_span * (t / sweep_duration_s)
        wavelengths = np.clip(
            wavelengths,
            min(start_nm, stop_nm),
            max(start_nm, stop_nm)
        )

        return wavelengths, channels_W

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
    result = QtCore.pyqtSignal(object, object)  # (wavelengths, channels_W)

    def __init__(self, params, parent=None):
        super().__init__(parent)
        self.params = params

    @QtCore.pyqtSlot()
    def run(self):
        p = self.params
        try:
            self.status.emit("Starting sweep backend…")

            start_nm    = p["start_nm"]
            stop_nm     = p["stop_nm"]
            power_mw    = p["power_mw"]
            speed_nm_s  = p["speed_nm_s"]
            sample_rate = p["sample_rate"]

            extra = {
                k: v for k, v in p.items()
                if k not in ("start_nm", "stop_nm", "power_mw",
                             "speed_nm_s", "sample_rate")
            }

            t0 = time.time()
            wavelengths, channels_W = perform_sweep_logic(
                start_nm=start_nm,
                stop_nm=stop_nm,
                power_mw=power_mw,
                speed_nm_s=speed_nm_s,
                sample_rate=sample_rate,
                extra_params=extra,
            )
            t1 = time.time()
            self.status.emit(f"Sweep backend finished in {t1 - t0:.2f} s")

            self.result.emit(wavelengths, channels_W)
        except Exception as e:
            self.error.emit(str(e))
        finally:
            self.finished.emit()


# -------------------- Sweep parameter dialog --------------------
class SweepParamsDialog(QtWidgets.QDialog):
    def __init__(self, params, parent=None):
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
        self.le_stop_nm  = add_line("Stop λ (nm)",  "stop_nm", double_validator)
        self.le_speed    = add_line("Speed (nm/s)", "speed_nm_s", double_validator)
        self.le_power    = add_line("Power (mW)",   "power_mw", double_validator)

        self.le_sample_rate = add_line("Sample rate (Hz)", "sample_rate", int_validator)

        # DAQ port + GPIB address
        self.le_daq_port = add_line("DAQ port", "daq_port")
        self.le_gpib     = add_line("GPIB address", "gpib_addr", int_validator)

        btn_box = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel,
            QtCore.Qt.Horizontal,
            self,
        )
        btn_box.accepted.connect(self.accept)
        btn_box.rejected.connect(self.reject)
        form.addRow(btn_box)

    def params(self):
        return self._params

    def accept(self):
        try:
            self._params["start_nm"]    = float(self.le_start_nm.text())
            self._params["stop_nm"]     = float(self.le_stop_nm.text())
            self._params["speed_nm_s"]  = float(self.le_speed.text())
            self._params["power_mw"]    = float(self.le_power.text())

            self._params["sample_rate"] = int(self.le_sample_rate.text())

            self._params["daq_port"]    = self.le_daq_port.text().strip()
            self._params["gpib_addr"]   = int(self.le_gpib.text())
        except ValueError:
            QtWidgets.QMessageBox.warning(
                self,
                "Invalid input",
                "Please check that all numeric fields contain valid numbers.",
            )
            return
        super().accept()


# -------------------- Main GUI window --------------------
class MainWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()

        self.setWindowTitle("CoreDAQ Timed Sweep GUI")
        self.resize(1100, 750)

        # current sweep params
        self.params = {
            "start_nm":    DEFAULT_START_NM,
            "stop_nm":     DEFAULT_STOP_NM,
            "power_mw":    DEFAULT_POWER_MW,
            "speed_nm_s":  DEFAULT_SPEED_NM_S,
            "sample_rate": DEFAULT_SAMPLE_RATE,
            "daq_port":    DEFAULT_DAQ_PORT,
            "gpib_addr":   DEFAULT_GPIB_ADDR,
        }

        self.thread = None
        self.worker = None

        # NEW: path to save CSV for current sweep
        self.save_path = None

        # ---- central widget ----
        central = QtWidgets.QWidget()
        outer_v = QtWidgets.QVBoxLayout(central)
        outer_v.setContentsMargins(8, 8, 8, 8)
        outer_v.setSpacing(6)
        self.setCentralWidget(central)

        # Summary row: label + Run button
        top_row = QtWidgets.QHBoxLayout()
        self.lbl_summary = QtWidgets.QLabel()
        self.lbl_summary.setWordWrap(True)
        top_row.addWidget(self.lbl_summary, 1)

        self.btn_run = QtWidgets.QPushButton("Run Sweep")
        self.btn_run.clicked.connect(self.run_sweep)
        top_row.addWidget(self.btn_run, 0, alignment=QtCore.Qt.AlignRight)

        outer_v.addLayout(top_row)

        # --- Grid: gain bars + 4 plots ---
        grid = QtWidgets.QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(8)
        outer_v.addLayout(grid, 1)

        self.plots = []
        self.curves = []
        self.gain_combos = [None] * 4

        colors = ['#00E5FF', '#FF4081', '#FFD740', '#69F0AE']
        axis_font = QtGui.QFont()
        axis_font.setPointSize(9)

        # Build 4 plot widgets
        for ch in range(4):
            pw = pg.PlotWidget(background=None)
            pw.setMenuEnabled(False)
            pw.showGrid(x=True, y=True, alpha=0.25)
            pw.setLabel("bottom", "Wavelength", units="nm")
            pw.setLabel("left", f"CH{ch + 1} (W)")
            pw.setYRange(0, 1.0)  # W, will autoscale

            pw.getAxis("left").setStyle(tickFont=axis_font)
            pw.getAxis("bottom").setStyle(tickFont=axis_font)

            curve = pw.plot(
                pen=pg.mkPen(colors[ch], width=2),
                clipToView=True
            )
            try:
                curve.setDownsampling(auto=True, method="peak")
            except Exception:
                pass

            self.plots.append(pw)
            self.curves.append(curve)

        # Gain bars with dropdowns
        bar_ch1 = self._make_gain_bar("CH1 gain", head=1)
        bar_ch2 = self._make_gain_bar("CH2 gain", head=2)
        bar_ch3 = self._make_gain_bar("CH3 gain", head=3)
        bar_ch4 = self._make_gain_bar("CH4 gain", head=4)

        # Layout: bars in rows 0/2, plots in rows 1/3
        grid.addWidget(bar_ch1, 0, 0)
        grid.addWidget(bar_ch2, 0, 1)
        grid.addWidget(self.plots[0], 1, 0)
        grid.addWidget(self.plots[1], 1, 1)

        grid.addWidget(bar_ch3, 2, 0)
        grid.addWidget(bar_ch4, 2, 1)
        grid.addWidget(self.plots[2], 3, 0)
        grid.addWidget(self.plots[3], 3, 1)

        grid.setRowStretch(0, 0)
        grid.setRowStretch(1, 1)
        grid.setRowStretch(2, 0)
        grid.setRowStretch(3, 1)

        # log box
        self.txt_log = QtWidgets.QPlainTextEdit()
        self.txt_log.setReadOnly(True)
        self.txt_log.setMaximumHeight(140)
        outer_v.addWidget(self.txt_log)

        # menu bar
        self._build_menus()

        self._update_summary()

    # ----- Menus -----
    def _build_menus(self):
        menubar = self.menuBar()

        file_menu = menubar.addMenu("&File")
        act_quit = QtWidgets.QAction("Quit", self)
        act_quit.triggered.connect(self.close)
        file_menu.addAction(act_quit)

        edit_menu = menubar.addMenu("&Edit")
        act_params = QtWidgets.QAction("Sweep Parameters…", self)
        act_params.triggered.connect(self.edit_params)
        edit_menu.addAction(act_params)

    def edit_params(self):
        dlg = SweepParamsDialog(self.params, self)
        if dlg.exec_() == QtWidgets.QDialog.Accepted:
            self.params = dlg.params()
            self._update_summary()

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
            f"Port: {p['daq_port']}  |  GPIB: {p['gpib_addr']}"
        )
        self.lbl_summary.setText(txt)

    # ----- Gain UI -----
    def _make_gain_bar(self, label: str, head: int) -> QtWidgets.QWidget:
        w = QtWidgets.QWidget()
        h = QtWidgets.QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(6)

        lbl = QtWidgets.QLabel(label)
        h.addWidget(lbl)

        combo = QtWidgets.QComboBox()
        for g in range(8):
            combo.addItem(str(g), g)
        combo.setCurrentIndex(0)

        combo.currentIndexChanged[int].connect(
            lambda value, h=head: self._on_gain_changed(h, value)
        )
        h.addWidget(combo)

        self.gain_combos[head - 1] = combo
        return w

    def _on_gain_changed(self, head: int, value: int):
        # GUI-only; actual gain is applied before sweep via params
        self.log(f"Gain CH{head} set to {value}")

    # ----- Logging -----
    def log(self, msg):
        t = time.strftime("%H:%M:%S")
        self.txt_log.appendPlainText(f"[{t}] {msg}")
        self.txt_log.verticalScrollBar().setValue(
            self.txt_log.verticalScrollBar().maximum()
        )
        self.statusBar().showMessage(msg, 3000)

    # ----- Sweep control -----
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

        # Inject gain settings into params before starting worker
        self.params["gain_ch1"] = self.gain_combos[0].currentIndex()
        self.params["gain_ch2"] = self.gain_combos[1].currentIndex()
        self.params["gain_ch3"] = self.gain_combos[2].currentIndex()
        self.params["gain_ch4"] = self.gain_combos[3].currentIndex()

        self.btn_run.setEnabled(False)
        # Clear plots
        for c in self.curves:
            c.setData([], [])

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

    def _on_error(self, msg):
        self.log(f"ERROR: {msg}")
        QtWidgets.QMessageBox.critical(self, "Sweep Error", msg)

    def _on_result(self, wavelengths, channels_W):
        # wavelengths: (N,), channels_W: list of 4 arrays (N,)
        wavelengths = np.asarray(wavelengths)
        if len(wavelengths) == 0:
            self.log("No data returned from sweep.")
            return

        if not isinstance(channels_W, (list, tuple)):
            self.log("Channel data not in expected list/tuple form.")
            return

        if len(channels_W) < 4:
            channels_W = list(channels_W) + [np.zeros_like(wavelengths)] * (4 - len(channels_W))
        elif len(channels_W) > 4:
            channels_W = channels_W[:4]

        for i in range(4):
            ys = np.asarray(channels_W[i])
            if ys.shape != wavelengths.shape:
                ys = np.resize(ys, wavelengths.shape)
            self.curves[i].setData(wavelengths, ys)

            # Autoscale with 30% padding, clamp at 0
            ymin = float(np.nanmin(ys))
            ymax = float(np.nanmax(ys))
            if not np.isfinite(ymin) or not np.isfinite(ymax):
                continue

            span = ymax - ymin
            if span <= 0:
                span = max(1e-9, abs(ymax) * 0.2)

            pad = 0.3 * span
            lo = max(0.0, ymin - pad)
            hi = ymax + pad
            if hi <= lo:
                hi = lo + span if span > 0 else lo + 1e-3

            self.plots[i].setYRange(lo, hi, padding=0)

        # Set common X range
        xmin = float(wavelengths.min())
        xmax = float(wavelengths.max())
        self.plots[0].setXRange(xmin, xmax, padding=0)
        for i in range(1, 4):
            self.plots[i].setXRange(xmin, xmax, padding=0)

        self.log(
            f"Sweep result: λ in [{wavelengths.min():.1f}, {wavelengths.max():.1f}] nm"
        )

        # -------- Save CSV if path was selected --------
        if self.save_path is not None:
            try:
                ch_arrays = [np.asarray(channels_W[i]) for i in range(4)]
                # Ensure shapes match
                ch_arrays = [np.resize(ch, wavelengths.shape) for ch in ch_arrays]

                data = np.column_stack([wavelengths, *ch_arrays])
                header = "wavelength_nm,ch1_W,ch2_W,ch3_W,ch4_W"

                np.savetxt(
                    self.save_path,
                    data,
                    delimiter=",",
                    header=header,
                    comments="",
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
                # Clear after one use
                self.save_path = None


# -------------------- Dark theme --------------------
def apply_dark(app):
    app.setStyle("Fusion")
    font = QtGui.QFont("Sans Serif", 10)
    app.setFont(font)

    pal = QtGui.QPalette()
    pal.setColor(QtGui.QPalette.Window, QtGui.QColor(30, 30, 30))
    pal.setColor(QtGui.QPalette.WindowText, QtGui.QColor(230, 230, 230))
    pal.setColor(QtGui.QPalette.Base, QtGui.QColor(20, 20, 20))
    pal.setColor(QtGui.QPalette.AlternateBase, QtGui.QColor(45, 45, 45))
    pal.setColor(QtGui.QPalette.Text, QtGui.QColor(230, 230, 230))
    pal.setColor(QtGui.QPalette.Button, QtGui.QColor(45, 45, 45))
    pal.setColor(QtGui.QPalette.ButtonText, QtGui.QColor(230, 230, 230))
    pal.setColor(QtGui.QPalette.Highlight, QtGui.QColor(64, 128, 255))
    pal.setColor(QtGui.QPalette.HighlightedText, QtCore.Qt.black)
    app.setPalette(pal)


def main():
    app = QtWidgets.QApplication(sys.argv)
    apply_dark(app)
    w = MainWindow()
    w.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
