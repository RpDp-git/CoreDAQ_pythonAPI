# plotter_tab.py

from __future__ import annotations

from typing import List, Optional

import numpy as np
from PyQt5 import QtCore, QtGui, QtWidgets
import pyqtgraph as pg

from channels import ChannelManager, ChannelConfig

# live plotting params
WINDOW_SECONDS = 5.0      # time window length (s)
UPDATE_HZ = 50.0          # snapshot polling rate (Hz)
SAMPLES_PER_WINDOW = int(WINDOW_SECONDS * UPDATE_HZ)

pg.setConfigOptions(antialias=True)


class PlotterWidget(QtWidgets.QWidget):
    """
    Live plotter tab.

    Uses a shared CoreDAQ instance passed from main.
    Does not open/close the device itself.
    """

    def __init__(self, manager: ChannelManager, daq=None, parent=None):
        super().__init__(parent)
        self.manager = manager
        self.daq = daq

        self.setObjectName("PlotterContainer")

        # ring buffer of *physical* channels: 4 x N (W)
        self.N = max(1, SAMPLES_PER_WINDOW)
        self.buf_phys = np.zeros((4, self.N), dtype=np.float32)
        self.widx = 0
        self.filled = 0
        self.tbase = np.linspace(-WINDOW_SECONDS, 0.0, self.N, dtype=np.float32)

        # logical channel cards
        self.cards: List[dict] = []

        # gain / autogain state
        self.autogain_enabled = False
        self.manual_gains = [0, 0, 0, 0]   # last manual gains per physical head
        self.gain_combos: List[Optional[QtWidgets.QComboBox]] = [None, None, None, None]

        # timer
        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(self._update_live)

        self._build_ui()
        self.on_channels_updated()

    # ------------------------------------------------------------------
    # Public API called from main.py
    # ------------------------------------------------------------------
    def set_daq(self, daq):
        """Inject / replace shared CoreDAQ instance."""
        self.daq = daq

    def set_active(self, active: bool):
        """Start/stop live polling."""
        if active:
            if not self.timer.isActive():
                interval_ms = int(1000.0 / UPDATE_HZ)
                self.timer.start(max(5, interval_ms))
        else:
            self.timer.stop()

    def on_channels_updated(self):
        """Rebuild cards when channel configuration changes."""
        # clear existing
        while self.grid.count():
            item = self.grid.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        self.cards.clear()
        self.gain_combos = [None, None, None, None]

        display_channels = self.manager.get_display_channels()
        if not display_channels:
            return

        axis_font = QtGui.QFont()
        axis_font.setPointSize(8)

        colors = [
            "#ffffff",
            "#00E5FF",
            "#FFD740",
            "#69F0AE",
            "#FF4081",
            "#7C4DFF",
            "#FF6E40",
            "#64FFDA",
        ]

        for idx, cfg in enumerate(display_channels):
            row = idx // 2
            col = idx % 2

            frame = QtWidgets.QFrame(self.inner)
            frame.setObjectName("ChannelCard")
            frame_layout = QtWidgets.QVBoxLayout(frame)
            frame_layout.setContentsMargins(10, 8, 10, 10)
            frame_layout.setSpacing(4)

            # ---- header: name + value ----
            header = QtWidgets.QHBoxLayout()
            header.setContentsMargins(0, 0, 0, 0)
            header.setSpacing(6)

            name_label = QtWidgets.QLabel(cfg.name)
            name_font = name_label.font()
            name_font.setPointSize(int(name_font.pointSize() * 1.3))
            name_font.setBold(True)
            name_label.setFont(name_font)
            name_label.setStyleSheet("color: #ffffff;")
            header.addWidget(name_label)

            header.addStretch(1)

            value_label = QtWidgets.QLabel("0.0 W")
            value_font = value_label.font()
            value_font.setPointSize(int(value_font.pointSize() * 1.1))
            value_label.setFont(value_font)
            value_label.setStyleSheet("color: #ffffff;")
            header.addWidget(value_label)

            frame_layout.addLayout(header)

            # ---- optional gain row for physical channels ----
            gain_combo = None
            if cfg.kind == "physical":
                phys_idx = cfg.index or 0
                gain_row = QtWidgets.QHBoxLayout()
                gain_row.setContentsMargins(0, 0, 0, 0)
                gain_row.setSpacing(6)

                gain_label = QtWidgets.QLabel("Gain")
                gain_label.setStyleSheet("color: #bbbbbb;")
                gain_row.addWidget(gain_label)

                combo = QtWidgets.QComboBox()
                combo.setMinimumWidth(80)
                # human-readable labels if available
                try:
                    from coredaq_py_api import CoreDAQ  # local import
                    labels = getattr(CoreDAQ, "GAIN_LABELS", None)
                except Exception:
                    labels = None
                if labels is not None and len(labels) >= 8:
                    for g in range(8):
                        combo.addItem(labels[g], g)
                else:
                    for g in range(8):
                        combo.addItem(f"G{g}", g)
                combo.setCurrentIndex(0)
                combo.currentIndexChanged[int].connect(
                    lambda value, idx=phys_idx: self._on_gain_changed(idx, value)
                )
                gain_row.addWidget(combo)
                gain_row.addStretch(1)

                frame_layout.addLayout(gain_row)

                self.gain_combos[phys_idx] = combo

            # ---- plot ----
            pw = pg.PlotWidget(background="k")
            pw.setMenuEnabled(False)
            pw.showGrid(x=True, y=True, alpha=0.15)
            pw.setLabel("bottom", "Time", units="s")
            if cfg.kind == "relative":
                pw.setLabel("left", "Relative (dB)")
            else:
                pw.setLabel("left", "Power", units="W")

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
                    "value_label": value_label,
                }
            )

        # allow extra stretch at bottom
        self.grid.setRowStretch((len(display_channels) + 1) // 2 + 1, 1)

    # ------------------------------------------------------------------
    # UI build
    # ------------------------------------------------------------------
    def _build_ui(self):
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(6)

        # ---- top bar: title + Autogain ----
        top_row = QtWidgets.QHBoxLayout()
        top_row.setContentsMargins(0, 0, 0, 0)
        top_row.setSpacing(8)

        title = QtWidgets.QLabel("Plotter")
        t_font = title.font()
        t_font.setPointSize(int(t_font.pointSize() * 1.4))
        t_font.setBold(True)
        title.setFont(t_font)
        title.setStyleSheet("color: #ffffff;")
        top_row.addWidget(title)

        top_row.addStretch(1)

        self.chk_autogain = QtWidgets.QCheckBox("Autogain")
        self.chk_autogain.setToolTip("Use snapshot_autogain_W instead of manual gains")
        self.chk_autogain.stateChanged.connect(self._on_autogain_toggled)
        top_row.addWidget(self.chk_autogain)

        outer.addLayout(top_row)

        # ---- scroll area for cards ----
        self.scroll = QtWidgets.QScrollArea(self)
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        outer.addWidget(self.scroll, 1)

        self.inner = QtWidgets.QWidget()
        self.scroll.setWidget(self.inner)

        self.grid = QtWidgets.QGridLayout(self.inner)
        self.grid.setContentsMargins(12, 12, 12, 12)
        self.grid.setSpacing(12)

    # ------------------------------------------------------------------
    # Autogain / gain handling
    # ------------------------------------------------------------------
    def _on_autogain_toggled(self, state: int):
        enabled = state == QtCore.Qt.Checked
        self.autogain_enabled = enabled

        # enable/disable combos visually
        for combo in self.gain_combos:
            if combo is not None:
                combo.setEnabled(not enabled)

        if enabled:
            # read current gains from device as "manual" snapshot
            if self.daq is not None:
                try:
                    g1, g2, g3, g4 = self.daq.get_gains()
                    self.manual_gains = [int(g1), int(g2), int(g3), int(g4)]
                except Exception:
                    # fall back to combo indices
                    self.manual_gains = [
                        c.currentIndex() if c is not None else 0
                        for c in self.gain_combos
                    ]
        else:
            # restore manual gains to device
            if self.daq is not None:
                try:
                    for head, g in enumerate(self.manual_gains, start=1):
                        self.daq.set_gain(head, int(g))
                    # sync combos
                    for i, combo in enumerate(self.gain_combos):
                        if combo is not None:
                            combo.blockSignals(True)
                            combo.setCurrentIndex(int(self.manual_gains[i]))
                            combo.blockSignals(False)
                except Exception:
                    pass

    def _on_gain_changed(self, phys_idx: int, value: int):
        if self.daq is None or self.autogain_enabled:
            return
        try:
            self.daq.set_gain(phys_idx + 1, int(value))
            self.manual_gains[phys_idx] = int(value)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Live polling
    # ------------------------------------------------------------------
    @QtCore.pyqtSlot()
    def _update_live(self):
        if self.daq is None:
            return

        # ---- 1) get latest physical powers in W ----
        try:
            if self.autogain_enabled and hasattr(self.daq, "snapshot_autogain_W"):
                power_W, mv_final, gains_final = self.daq.snapshot_autogain_W(
                    n_frames=1,
                    min_mv=50.0,
                    max_mv=4500.0,
                    max_iters=20,
                    settle_s=0.01,
                )
                # update combos to reflect autogain-chosen gains
                for i, g in enumerate(gains_final):
                    if 0 <= int(g) <= 7 and self.gain_combos[i] is not None:
                        c = self.gain_combos[i]
                        c.blockSignals(True)
                        c.setCurrentIndex(int(g))
                        c.blockSignals(False)
            else:
                power_W = self.daq.snapshot_W(
                    n_frames=1,
                    timeout_s=0.5,
                    poll_hz=200.0,
                )
        except Exception:
            return

        phys = np.zeros(4, dtype=np.float32)
        power_W = list(power_W)
        for i in range(min(4, len(power_W))):
            phys[i] = float(power_W[i])

        # ---- 2) push into ring buffer ----
        self.buf_phys[:, self.widx] = phys
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
            phys_hist = self.buf_phys[:, start:start + count]
        else:
            first = N - start
            phys_hist = np.concatenate(
                (self.buf_phys[:, start:N], self.buf_phys[:, 0:count - first]),
                axis=1,
            )

        # ---- 3) update each logical channel card ----
        for card in self.cards:
            cfg: ChannelConfig = card["cfg"]

            if cfg.kind == "physical":
                idx = cfg.index or 0
                ys = phys_hist[idx, :]
            elif cfg.kind == "math":
                # vectorised if available
                if hasattr(self.manager, "eval_math_array"):
                    ys = self.manager.eval_math_array(
                        cfg, [phys_hist[i, :] for i in range(4)]
                    )
                else:
                    vals = []
                    for k in range(count):
                        v = self.manager.eval_math_value(
                            cfg, [phys_hist[i, k] for i in range(4)]
                        )
                        vals.append(v)
                    ys = np.asarray(vals, dtype=np.float32)
            elif cfg.kind == "relative":
                if hasattr(self.manager, "eval_relative_array"):
                    ys = self.manager.eval_relative_array(
                        cfg, [phys_hist[i, :] for i in range(4)]
                    )
                else:
                    vals = []
                    for k in range(count):
                        v = self.manager.eval_relative_value(
                            cfg, [phys_hist[i, k] for i in range(4)]
                        )
                        vals.append(v)
                    ys = np.asarray(vals, dtype=np.float32)
            else:
                ys = np.zeros(count, dtype=np.float32)

            ys = np.asarray(ys, dtype=np.float32)
            if ys.shape[0] != count:
                ys = np.resize(ys, (count,))

            # update plot
            curve = card["curve"]
            plot = card["plot"]
            curve.setData(xs, ys, skipFiniteCheck=True)

            # update header value label
            latest = float(ys[-1]) if ys.size else 0.0
            txt = self._format_power_label(latest, cfg)
            card["value_label"].setText(txt)

            # y-range autoscale
            ymin = float(np.nanmin(ys))
            ymax = float(np.nanmax(ys))
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
            plot.setXRange(-WINDOW_SECONDS, 0.0, padding=0)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _format_power_label(self, value_W: float, cfg: ChannelConfig) -> str:
        """Return human-friendly power / dB label."""
        if cfg.kind == "relative":
            if not np.isfinite(value_W):
                return "— dB"
            return f"{value_W:.2f} dB"

        # power
        v = abs(value_W)
        unit = "W"
        scale = 1.0
        if v < 1e-9:
            unit = "W"
            scale = 1.0
        elif v < 1e-6:
            unit = "nW"
            scale = 1e9
        elif v < 1e-3:
            unit = "µW"
            scale = 1e6
        elif v < 1.0:
            unit = "mW"
            scale = 1e3

        return f"{value_W * scale:,.3g} {unit}"
