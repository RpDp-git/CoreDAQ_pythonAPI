#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import sys
import numpy as np
from PyQt5 import QtCore, QtGui, QtWidgets
import pyqtgraph as pg

import serial.tools.list_ports

# ------------ pyqtgraph config ------------
pg.setConfigOptions(antialias=False)

# ------------ CoreDAQ driver ------------
# Use the uploaded driver:
from coredaq_py_api import CoreDAQ, CoreDAQError  # adjust name if needed

# Fallback default port if auto-detect finds nothing
DEFAULT_PORT = "COM4"  # change if you like

# Live plotting parameters
WINDOW_SECONDS = 5.0      # time window length (s)
UPDATE_HZ = 50.0          # snapshot polling rate (Hz)
SAMPLES_PER_WINDOW = int(WINDOW_SECONDS * UPDATE_HZ)


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()

        self.setWindowTitle("CoreDAQ – Live Power Monitor")
        self.resize(1200, 760)

        self.daq = None
        self.N = max(1, SAMPLES_PER_WINDOW)

        # ring buffer: 4 channels × N samples, in mW
        self.ybuf = np.zeros((4, self.N), dtype=np.float32)
        self.widx = 0
        self.filled = 0
        self.tbase = np.linspace(-WINDOW_SECONDS, 0.0, self.N, dtype=np.float32)

        # autogain state
        self.autogain_enabled = False
        self.manual_gains = [0, 0, 0, 0]  # last manual gains per head

        # ------------ central layout (controls + plots) ------------
        central = QtWidgets.QWidget()
        main_v = QtWidgets.QVBoxLayout(central)
        main_v.setContentsMargins(8, 8, 8, 8)
        main_v.setSpacing(6)
        self.setCentralWidget(central)

        # --- Top control / status bar ---
        top_widget = QtWidgets.QWidget()
        top_layout = QtWidgets.QHBoxLayout(top_widget)
        top_layout.setContentsMargins(0, 0, 0, 0)
        top_layout.setSpacing(10)

        # COM selector
        self.port_combo = QtWidgets.QComboBox()
        self.port_combo.setMinimumWidth(220)
        self.btn_refresh_ports = QtWidgets.QPushButton("Refresh")
        self.btn_connect = QtWidgets.QPushButton("Connect")

        top_layout.addWidget(QtWidgets.QLabel("Port:"))
        top_layout.addWidget(self.port_combo)
        top_layout.addWidget(self.btn_refresh_ports)
        top_layout.addWidget(self.btn_connect)

        top_layout.addSpacing(20)

        # Status labels: board temp, humidity, die temp
        self.lbl_temp = QtWidgets.QLabel("Board: --.- °C")
        self.lbl_hum = QtWidgets.QLabel("Humidity: --.- %RH")
        self.lbl_die = QtWidgets.QLabel("Die: --.- °C")

        for lbl in (self.lbl_temp, self.lbl_hum, self.lbl_die):
            lbl.setAlignment(QtCore.Qt.AlignVCenter | QtCore.Qt.AlignLeft)
            top_layout.addWidget(lbl)

        # stretch, then autogain checkbox at the very right
        top_layout.addStretch(1)

        self.chk_autogain = QtWidgets.QCheckBox("Autogain")
        self.chk_autogain.setToolTip("Use snapshot_autogain_W instead of manual gains")
        top_layout.addWidget(self.chk_autogain)

        main_v.addWidget(top_widget)

        # --- Grid for gain bars + plots ---
        grid = QtWidgets.QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(8)
        main_v.addLayout(grid, 1)

        # 4 plots
        self.plots = []
        self.curves = []
        colors = ['#00E5FF', '#FF4081', '#FFD740', '#69F0AE']

        axis_font = QtGui.QFont()
        axis_font.setPointSize(9)

        for ch in range(4):
            pw = pg.PlotWidget(background=None)
            pw.setMenuEnabled(False)
            pw.showGrid(x=True, y=True, alpha=0.25)
            pw.setLabel('left', f"CH{ch + 1} (mW)")
            pw.setLabel('bottom', 'Time', units='s')
            pw.setYRange(0, 1.0)  # mW, autoscaled later

            # axis fonts; your pyqtgraph doesn't support textFont
            pw.getAxis('left').setStyle(tickFont=axis_font)
            pw.getAxis('bottom').setStyle(tickFont=axis_font)

            curve = pw.plot(
                pen=pg.mkPen(colors[ch], width=2),
                clipToView=True
            )
            try:
                curve.setDownsampling(auto=True, method='peak')
            except Exception:
                pass

            self.plots.append(pw)
            self.curves.append(curve)

        # Gain comboboxes
        self.gain_combos = [None] * 4
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

        # Menus
        self._build_menus()

        # Timers
        self._live_timer = QtCore.QTimer(self)
        self._live_timer.timeout.connect(self._update_live)

        self._status_timer = QtCore.QTimer(self)
        self._status_timer.timeout.connect(self._update_status)

        # Connections
        self.btn_refresh_ports.clicked.connect(self._populate_ports)
        self.btn_connect.clicked.connect(self._connect_current_port)
        self.chk_autogain.stateChanged.connect(self._on_autogain_toggled)

        # Populate ports and auto-connect
        self._populate_ports()
        self._connect_current_port()

        # Start live + status polling if connection succeeded
        if self.daq is not None:
            self.start_live()
            self._status_timer.start(1000)  # 1 Hz status updates

    # ------------- UI helpers -------------

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

    def _build_menus(self):
        menubar = self.menuBar()

        fileMenu = menubar.addMenu("&File")
        actQuit = QtWidgets.QAction("Quit", self)
        actQuit.triggered.connect(self.close)
        fileMenu.addAction(actQuit)

        controlMenu = menubar.addMenu("&Control")
        actStart = QtWidgets.QAction("Start Live", self)
        actStop = QtWidgets.QAction("Stop Live", self)
        actStart.setShortcut(QtGui.QKeySequence("F6"))
        actStop.setShortcut(QtGui.QKeySequence("F7"))
        actStart.triggered.connect(self.start_live)
        actStop.triggered.connect(self.stop_live)
        controlMenu.addAction(actStart)
        controlMenu.addAction(actStop)

    # ------------- COM handling -------------

    def _populate_ports(self):
        """Fill the COM-port combo from CoreDAQ.find(), with a serial fallback."""
        self.port_combo.clear()
        ports = []
        try:
            ports = CoreDAQ.find()
        except Exception:
            ports = []

        if not ports:
            # Fallback: list all serial devices
            ports = [p.device for p in serial.tools.list_ports.comports()]

        if not ports:
            ports = [DEFAULT_PORT]

        for p in ports:
            self.port_combo.addItem(p)

        # Prefer default if present
        idx = self.port_combo.findText(DEFAULT_PORT)
        if idx >= 0:
            self.port_combo.setCurrentIndex(idx)
        else:
            self.port_combo.setCurrentIndex(0)

    def _connect_current_port(self):
        """Close any existing DAQ and open the one in the combo."""
        port = self.port_combo.currentText().strip()
        if not port:
            self.statusBar().showMessage("No port selected", 2000)
            return

        # Stop timers while reconnecting
        self.stop_live()
        self._status_timer.stop()

        # Close existing DAQ
        if self.daq is not None:
            try:
                self.daq.close()
            except Exception:
                pass
            self.daq = None

        try:
            self.daq = CoreDAQ(port)
        except Exception as e:
            QtWidgets.QMessageBox.critical(
                self,
                "CoreDAQ Error",
                f"Failed to open CoreDAQ on {port}:\n{e}"
            )
            self.statusBar().showMessage("Connection failed", 4000)
            return

        # Basic setup once connected
        try:
            idn = self.daq.idn()
        except Exception as e:
            idn = f"IDN? failed: {e}"

        self.setWindowTitle(f"CoreDAQ – Live Power Monitor  |  {idn}")

        # Oversampling = 1
        try:
            self.daq.set_oversampling(1)
        except Exception as e:
            self.statusBar().showMessage(f"set_oversampling(1) failed: {e}", 4000)

        # Sync gain combos from device, if possible
        try:
            g1, g2, g3, g4 = self.daq.get_gains()
            gains = (g1, g2, g3, g4)
            for i, g in enumerate(gains):
                if 0 <= g <= 7:
                    self.gain_combos[i].blockSignals(True)
                    self.gain_combos[i].setCurrentIndex(g)
                    self.gain_combos[i].blockSignals(False)
            # use device gains as initial manual gains
            self.manual_gains = [int(g) for g in gains]
        except Exception:
            # keep defaults if GAINS? fails
            self.manual_gains = [self.gain_combos[i].currentIndex() for i in range(4)]

        # If autogain is currently enabled, we don't override its state here
        self.start_live()
        self._status_timer.start(1000)
        self.statusBar().showMessage(f"Connected to {port}", 3000)

    # ------------- Autogain handling -------------

    def _on_autogain_toggled(self, state: int):
        enabled = (state == QtCore.Qt.Checked)
        self.autogain_enabled = enabled

        if enabled:
            # Save current manual gains and disable combos
            self.manual_gains = [c.currentIndex() for c in self.gain_combos]
            for c in self.gain_combos:
                c.setEnabled(False)
            self.statusBar().showMessage("Autogain enabled", 2000)
        else:
            # Re-enable combos and restore manual gains to device
            for c in self.gain_combos:
                c.setEnabled(True)

            if self.daq is not None and self.manual_gains:
                try:
                    for head, g in enumerate(self.manual_gains, start=1):
                        g_int = int(g)
                        self.daq.set_gain(head, g_int)
                        self.gain_combos[head - 1].blockSignals(True)
                        self.gain_combos[head - 1].setCurrentIndex(g_int)
                        self.gain_combos[head - 1].blockSignals(False)
                    self.statusBar().showMessage("Autogain disabled – manual gains restored", 3000)
                except Exception as e:
                    self.statusBar().showMessage(f"Failed to restore manual gains: {e}", 3000)

    # ------------- Gain handling -------------

    def _on_gain_changed(self, head: int, value: int):
        # Ignore manual gain changes while autogain is active
        if self.daq is None or self.autogain_enabled:
            return
        try:
            self.daq.set_gain(head, int(value))
            self.manual_gains[head - 1] = int(value)
            self.statusBar().showMessage(f"Set CH{head} gain = {value}", 1500)
        except Exception as e:
            self.statusBar().showMessage(f"Gain set failed for CH{head}: {e}", 3000)

    # ------------- Live plotting -------------

    def start_live(self):
        if self.daq is None:
            self.statusBar().showMessage("No device connected", 2000)
            return
        if self._live_timer.isActive():
            self.statusBar().showMessage("Live already running", 1500)
            return
        interval_ms = int(1000.0 / UPDATE_HZ)
        self._live_timer.start(max(5, interval_ms))
        self.statusBar().showMessage("Live snapshot mode running…")

    def stop_live(self):
        self._live_timer.stop()
        self.statusBar().showMessage("Live stopped", 1500)

    @QtCore.pyqtSlot()
    def _update_live(self):
        if self.daq is None:
            return

        try:
            if self.autogain_enabled:
                # Use autogain snapshot; keep params fairly quick so UI stays responsive
                power_W, mv_final, gains_final = self.daq.snapshot_autogain_W(
                    n_frames=1,
                    min_mv=50.0,
                    max_mv=4500.0,
                    max_iters=20,
                    settle_s=0.01,
                )
                # Update combobox displays to autogain-chosen gains
                for i, g in enumerate(gains_final):
                    if 0 <= int(g) <= 7:
                        self.gain_combos[i].blockSignals(True)
                        self.gain_combos[i].setCurrentIndex(int(g))
                        self.gain_combos[i].blockSignals(False)
            else:
                # Manual gains: use snapshot_W
                power_W = self.daq.snapshot_W(
                    n_frames=1,
                    timeout_s=0.5,
                    poll_hz=200.0
                )
        except CoreDAQError as e:
            self.statusBar().showMessage(f"snapshot error: {e}", 2000)
            return
        except Exception as e:
            self.statusBar().showMessage(f"Unexpected error: {e}", 2000)
            return

        # convert to mW for plotting
        power_mW = np.asarray(power_W, dtype=np.float32) * 1e3

        # push into ring buffer
        self.ybuf[:, self.widx] = power_mW
        self.widx += 1
        if self.widx >= self.N:
            self.widx = 0
        if self.filled < self.N:
            self.filled += 1

        count = self.filled
        if count <= 0:
            return

        N = self.N
        start = (self.widx - count) % N
        xs = self.tbase[-count:]

        if start + count <= N:
            for ch in range(4):
                ys = self.ybuf[ch, start:start + count]
                self._update_plot(ch, xs, ys)
        else:
            first = N - start
            for ch in range(4):
                ys = np.concatenate(
                    (self.ybuf[ch, start:N], self.ybuf[ch, 0:count - first]),
                    axis=0
                )
                self._update_plot(ch, xs, ys)

        # Fix x-range
        for i in range(4):
            vb = self.plots[i].getViewBox()
            xr = vb.state['viewRange'][0]
            if xr != [-WINDOW_SECONDS, 0]:
                self.plots[i].setXRange(-WINDOW_SECONDS, 0, padding=0)

    def _update_plot(self, ch: int, xs: np.ndarray, ys: np.ndarray):
        self.curves[ch].setData(xs, ys, skipFiniteCheck=True)

        ymin = float(np.nanmin(ys))
        ymax = float(np.nanmax(ys))
        if not np.isfinite(ymin) or not np.isfinite(ymax):
            return

        span = ymax - ymin
        if span <= 0:
            span = max(1e-6, abs(ymax) * 0.2)

        # 30% padding
        pad = 0.3 * span
        lo = max(0.0, ymin - pad)
        hi = ymax + pad
        if hi <= lo:
            hi = lo + span if span > 0 else lo + 1e-3

        self.plots[ch].setYRange(lo, hi, padding=0)

    # ------------- Status polling -------------

    @QtCore.pyqtSlot()
    def _update_status(self):
        if self.daq is None:
            return
        try:
            t = self.daq.get_head_temperature_C()
            self.lbl_temp.setText(f"Board: {t:.1f} °C")
        except Exception:
            self.lbl_temp.setText("Board: --.- °C")

        try:
            h = self.daq.get_head_humidity()
            self.lbl_hum.setText(f"Humidity: {h:.1f} %RH")
        except Exception:
            self.lbl_hum.setText("Humidity: --.- %RH")

        try:
            td = self.daq.get_die_temperature_C()
            self.lbl_die.setText(f"Die: {td:.1f} °C")
        except Exception:
            self.lbl_die.setText("Die: --.- °C")

    # ------------- Close handling -------------

    def closeEvent(self, ev: QtGui.QCloseEvent):
        try:
            self._live_timer.stop()
            self._status_timer.stop()
        except Exception:
            pass
        if self.daq is not None:
            try:
                self.daq.close()
            except Exception:
                pass
        super().closeEvent(ev)


def apply_dark(app: QtWidgets.QApplication):
    app.setStyle("Fusion")

    # Global font
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
