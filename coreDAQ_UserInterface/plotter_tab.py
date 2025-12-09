# plotter_tab.py

import numpy as np
from typing import List, Tuple

from PyQt5 import QtCore, QtGui, QtWidgets
import pyqtgraph as pg
import serial.tools.list_ports

from channels import (
    ChannelManager,
    safe_eval_expression,
    COLOR_CYCLE,
)

from coredaq_py_api import CoreDAQ, CoreDAQError


# ------------- Live plotting parameters -------------
WINDOW_SECONDS = 5.0      # time window length (s)
UPDATE_HZ = 50.0          # snapshot polling rate (Hz)
SAMPLES_PER_WINDOW = int(WINDOW_SECONDS * UPDATE_HZ)

DEFAULT_PORT = "COM14"    # fallback port if auto-detect finds nothing


# ------------- Utility: power unit formatting -------------
def format_power_W(p_W: float) -> Tuple[str, str]:
    """
    Convert power in W to a nice value+unit string, using mW / µW / nW.
    Returns (value_str, unit_str).
    """
    if p_W is None or not np.isfinite(p_W):
        return "--", "mW"

    mag = abs(p_W)
    if mag >= 1e-3:
        return f"{p_W * 1e3:,.3g}", "mW"
    elif mag >= 1e-6:
        return f"{p_W * 1e6:,.3g}", "µW"
    else:
        return f"{p_W * 1e9:,.3g}", "nW"


# ------------- Channel card widget -------------
class ChannelCard(QtWidgets.QFrame):
    """
    One card in the Plotter grid.
    Shows:
      - Title ("Channel 1", "Math 1", "Relative 1", ...)
      - Optional gain combobox (for physical channels)
      - Live value (right-aligned, with auto unit)
      - pyqtgraph plot in black background
    """
    def __init__(
        self,
        title: str,
        color: str,
        is_physical: bool,
        gain_changed_cb=None,
        phys_index: int = None,
        parent=None,
    ):
        super().__init__(parent)
        self.setObjectName("ChannelCard")

        self.color = color
        self.is_physical = is_physical
        self.gain_changed_cb = gain_changed_cb
        self.phys_index = phys_index

        self.plot: pg.PlotWidget = None
        self.curve: pg.PlotDataItem = None
        self.lbl_value = None
        self.lbl_unit = None
        self.gain_combo = None

        self._build_ui(title)

    def _build_ui(self, title: str):
        self.setMinimumHeight(200)
        self.setSizePolicy(
            QtWidgets.QSizePolicy.Expanding,
            QtWidgets.QSizePolicy.Expanding,
        )

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(6)

        # ---- Header: title + (optional) gain + value ----
        header = QtWidgets.QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(8)

        lbl_title = QtWidgets.QLabel(title)
        title_font = lbl_title.font()
        title_font.setPointSize(int(title_font.pointSize() * 1.3))
        title_font.setBold(True)
        lbl_title.setFont(title_font)
        lbl_title.setStyleSheet("color: #ffffff;")
        header.addWidget(lbl_title)

        # Gain combobox only for physical channels
        if self.is_physical and self.gain_changed_cb is not None:
            self.gain_combo = QtWidgets.QComboBox()
            self.gain_combo.setFixedWidth(90)
            self.gain_combo.setToolTip("Channel gain")
            try:
                labels = CoreDAQ.GAIN_LABELS
            except Exception:
                labels = [f"G{g}" for g in range(8)]
            for g in range(8):
                label_item = labels[g] if g < len(labels) else f"G{g}"
                self.gain_combo.addItem(label_item, g)
            self.gain_combo.setCurrentIndex(0)
            self.gain_combo.currentIndexChanged[int].connect(
                lambda value, idx=self.phys_index: self.gain_changed_cb(idx, value)
            )
            header.addWidget(self.gain_combo)

        header.addStretch(1)

        # Value + unit on the right
        self.lbl_value = QtWidgets.QLabel("--")
        self.lbl_unit = QtWidgets.QLabel("mW")

        val_font = self.lbl_value.font()
        val_font.setPointSize(int(val_font.pointSize() * 1.1))
        val_font.setBold(True)
        self.lbl_value.setFont(val_font)
        self.lbl_value.setStyleSheet("color: #ffffff;")

        unit_font = self.lbl_unit.font()
        unit_font.setPointSize(int(unit_font.pointSize() * 1.0))
        self.lbl_unit.setFont(unit_font)
        self.lbl_unit.setStyleSheet("color: #ffffff;")

        header.addWidget(self.lbl_value)
        header.addWidget(self.lbl_unit)

        layout.addLayout(header)

        # ---- Plot ----
        self.plot = pg.PlotWidget()
        self.plot.setBackground("k")
        self.plot.setMenuEnabled(False)
        self.plot.showGrid(x=True, y=True, alpha=0.25)
        self.plot.setLabel("bottom", "Time", units="s")
        self.plot.setLabel("left", "Power")
        self.plot.setXRange(-WINDOW_SECONDS, 0, padding=0.0)

        axis_font = QtGui.QFont()
        axis_font.setPointSize(9)
        self.plot.getAxis("left").setStyle(tickFont=axis_font)
        self.plot.getAxis("bottom").setStyle(tickFont=axis_font)

        self.curve = self.plot.plot(
            pen=pg.mkPen(self.color, width=2),
            clipToView=True
        )
        try:
            self.curve.setDownsampling(auto=True, method="peak")
        except Exception:
            pass

        layout.addWidget(self.plot)

    # --- External API ---

    def set_gain_index(self, g: int):
        if self.gain_combo is not None:
            self.gain_combo.blockSignals(True)
            self.gain_combo.setCurrentIndex(int(g))
            self.gain_combo.blockSignals(False)

    def update_value_W(self, p_W: float, unit_override: str | None = None):
        if unit_override is None:
            v_str, u_str = format_power_W(p_W)
        else:
            # For dB etc; we just display raw with that unit
            if p_W is None or not np.isfinite(p_W):
                v_str = "--"
            else:
                v_str = f"{p_W:,.3g}"
            u_str = unit_override
        self.lbl_value.setText(v_str)
        self.lbl_unit.setText(u_str)

    def update_curve(self, xs: np.ndarray, ys: np.ndarray,
                     ymin_floor: float = 0.0):
        self.curve.setData(xs, ys, skipFiniteCheck=True)

        if ys.size == 0:
            return

        ymin = float(np.nanmin(ys))
        ymax = float(np.nanmax(ys))
        if not np.isfinite(ymin) or not np.isfinite(ymax):
            return

        span = ymax - ymin
        if span <= 0:
            span = max(1e-9, abs(ymax) * 0.2)

        pad = 0.3 * span
        lo = ymin - pad if ymin_floor is None else max(ymin_floor, ymin - pad)
        hi = ymax + pad
        if hi <= lo:
            hi = lo + span if span > 0 else lo + 1e-3

        self.plot.setYRange(lo, hi, padding=0)


# ------------- Plotter tab widget -------------
class PlotterWidget(QtWidgets.QWidget):
    """
    Plotter tab using real CoreDAQ hardware.

    - 5 s moving window with UPDATE_HZ snapshots.
    - Uses ChannelManager for:
        * which physical channels are enabled
        * math channels (expressions on ch1..ch4)
        * relative transmission channels (10*log10(chX/chY))
    - Cards are laid out in a scrollable 2-column grid.
    """
    def __init__(self, manager: ChannelManager, parent=None):
        super().__init__(parent)
        self.manager = manager

        # --- CoreDAQ / live state ---
        self.daq: CoreDAQ | None = None
        self.autogain_enabled = False
        self.manual_gains = [0, 0, 0, 0]

        # ring buffer for 4 physical channels (W)
        self.N = max(1, SAMPLES_PER_WINDOW)
        self.y_phys = np.zeros((4, self.N), dtype=np.float32)
        self.y_math = np.zeros((0, self.N), dtype=np.float32)   # resized per config
        self.y_rel = np.zeros((0, self.N), dtype=np.float32)
        self.widx = 0
        self.filled = 0
        self.tbase = np.linspace(-WINDOW_SECONDS, 0.0, self.N, dtype=np.float32)

        # mapping from card index -> (kind, local_index)
        #   kind in {"phys", "math", "rel"}
        self.card_infos: List[Tuple[str, int]] = []
        self.cards: List[ChannelCard] = []

        # timers
        self._live_timer = QtCore.QTimer(self)
        self._live_timer.timeout.connect(self._update_live)

        # UI
        self._build_ui()
        self._rebuild_cards()

        # populate ports + connect (auto, no UI controls here)
        self._populate_ports()
        self._connect_current_port()
        if self.daq is not None:
            self.start_live()

    # ---------------- UI ----------------
    def _build_ui(self):
        main_v = QtWidgets.QVBoxLayout(self)
        main_v.setContentsMargins(12, 12, 12, 12)
        main_v.setSpacing(10)

        # ---- Top control bar ----
        top = QtWidgets.QWidget()
        top_layout = QtWidgets.QHBoxLayout(top)
        top_layout.setContentsMargins(0, 0, 0, 0)
        top_layout.setSpacing(10)

 


        # Hidden port combo, used only for auto-detection / connection
        self.port_combo = QtWidgets.QComboBox()
        self.port_combo.setMinimumWidth(220)

        top_layout.addStretch(1)

        self.chk_autogain = QtWidgets.QCheckBox("Autogain")
        self.chk_autogain.setToolTip("Use snapshot_autogain_W instead of manual gains")
        top_layout.addWidget(self.chk_autogain)

        main_v.addWidget(top)

        # ---- Scroll area with grid of cards ----
        self.scroll = QtWidgets.QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)
        self.scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self.scroll.setObjectName("PlotterScroll")

        self.container = QtWidgets.QWidget()
        self.container.setObjectName("PlotterContainer")

        self.grid = QtWidgets.QGridLayout(self.container)
        self.grid.setContentsMargins(0, 4, 0, 4)
        self.grid.setHorizontalSpacing(10)
        self.grid.setVerticalSpacing(10)

        self.scroll.setWidget(self.container)
        main_v.addWidget(self.scroll, 1)

        # ---- Connections ----
        self.chk_autogain.stateChanged.connect(self._on_autogain_toggled)

    # ---------------- Cards / channels ----------------
    def _rebuild_cards(self):
        # clear grid
        while self.grid.count():
            item = self.grid.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        self.cards.clear()
        self.card_infos.clear()

        # rebuild math & relative buffers according to manager
        n_math = len(self.manager.math_channels)
        n_rel = len(self.manager.relative_channels)
        self.y_math = np.zeros((n_math, self.N), dtype=np.float32)
        self.y_rel = np.zeros((n_rel, self.N), dtype=np.float32)

        row = 0
        col = 0

        # --- Physical channels first, in order, respecting enabled flags ---
        for phys_idx in range(4):
            if not self.manager.is_physical_enabled(phys_idx):
                continue

            color = COLOR_CYCLE[phys_idx % len(COLOR_CYCLE)]
            title = f"Channel {phys_idx + 1}"
            card = ChannelCard(
                title=title,
                color=color,
                is_physical=True,
                gain_changed_cb=self._on_gain_changed,
                phys_index=phys_idx,
            )
            self.cards.append(card)
            self.card_infos.append(("phys", phys_idx))
            self.grid.addWidget(card, row, col)

            col += 1
            if col >= 2:
                col = 0
                row += 1

        # --- Math channels ---
        for math_idx, cfg in enumerate(self.manager.math_channels):
            color = COLOR_CYCLE[(4 + math_idx) % len(COLOR_CYCLE)]
            title = cfg.name if getattr(cfg, "name", None) else f"Math {math_idx + 1}"
            card = ChannelCard(
                title=title,
                color=color,
                is_physical=False,
            )
            self.cards.append(card)
            self.card_infos.append(("math", math_idx))
            self.grid.addWidget(card, row, col)
            col += 1
            if col >= 2:
                col = 0
                row += 1

        # --- Relative channels ---
        for rel_idx, cfg in enumerate(self.manager.relative_channels):
            color = COLOR_CYCLE[(8 + rel_idx) % len(COLOR_CYCLE)]
            title = cfg.name if getattr(cfg, "name", None) else f"Relative {rel_idx + 1}"
            card = ChannelCard(
                title=title,
                color=color,
                is_physical=False,
            )
            self.cards.append(card)
            self.card_infos.append(("rel", rel_idx))
            self.grid.addWidget(card, row, col)
            col += 1
            if col >= 2:
                col = 0
                row += 1

        self.grid.setRowStretch(row + 1, 1)

    def on_channels_updated(self):
        """
        Called by MainWindow when channels / math / relative configs change.
        """
        self._rebuild_cards()

    # ---------------- COM / CoreDAQ handling ----------------
    def _populate_ports(self):
        self.port_combo.clear()
        ports = []
        try:
            ports = CoreDAQ.find()
        except Exception:
            ports = []

        if not ports:
            ports = [p.device for p in serial.tools.list_ports.comports()]

        if not ports:
            ports = [DEFAULT_PORT]

        for p in ports:
            self.port_combo.addItem(p)

        idx = self.port_combo.findText(DEFAULT_PORT)
        if idx >= 0:
            self.port_combo.setCurrentIndex(idx)
        else:
            self.port_combo.setCurrentIndex(0)

    def _connect_current_port(self):
        port = self.port_combo.currentText().strip()
        if not port:
            return

        self.stop_live()

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
            return

        try:
            _ = self.daq.idn()
        except Exception:
            pass

        # Oversampling = 1 for fast snapshots
        try:
            self.daq.set_oversampling(1)
        except Exception:
            pass

        # Sync gain combos from device if possible
        try:
            g1, g2, g3, g4 = self.daq.get_gains()
            gains = (g1, g2, g3, g4)
            self.manual_gains = [int(g) for g in gains]
            for phys_idx in range(4):
                g = gains[phys_idx]
                for card, info in zip(self.cards, self.card_infos):
                    if info[0] == "phys" and info[1] == phys_idx:
                        card.set_gain_index(g)
        except Exception:
            self.manual_gains = [0, 0, 0, 0]

        self.start_live()

    # ---------------- Autogain / gain handling ----------------
    def _on_autogain_toggled(self, state: int):
        enabled = (state == QtCore.Qt.Checked)
        self.autogain_enabled = enabled

        if enabled:
            # Save current manual gains & disable combos
            self.manual_gains = [0, 0, 0, 0]
            for phys_idx in range(4):
                self.manual_gains[phys_idx] = 0
            for card, info in zip(self.cards, self.card_infos):
                if info[0] == "phys" and card.gain_combo is not None:
                    card.gain_combo.setEnabled(False)
        else:
            # Re-enable combos and attempt to restore manual gains to device
            for card, info in zip(self.cards, self.card_infos):
                if info[0] == "phys" and card.gain_combo is not None:
                    card.gain_combo.setEnabled(True)
            if self.daq is not None:
                try:
                    for phys_idx in range(4):
                        g = self.manual_gains[phys_idx]
                        self.daq.set_gain(phys_idx + 1, int(g))
                except Exception:
                    pass

    def _on_gain_changed(self, phys_index: int, value: int):
        if self.daq is None or self.autogain_enabled:
            return
        try:
            self.daq.set_gain(phys_index + 1, int(value))
            self.manual_gains[phys_index] = int(value)
        except Exception:
            pass

    # ---------------- Live update ----------------
    def start_live(self):
        if self.daq is None:
            return
        if self._live_timer.isActive():
            return
        interval_ms = int(1000.0 / UPDATE_HZ)
        self._live_timer.start(max(5, interval_ms))

    def stop_live(self):
        self._live_timer.stop()

    # ---------------- Tab activation ----------------
    def set_active(self, active: bool):
        if active:
            if self.daq is not None:
                self.start_live()
        else:
            self.stop_live()

    @QtCore.pyqtSlot()
    def _update_live(self):
        if self.daq is None:
            return

        # --- 1. Get physical snapshot from hardware ---
        try:
            if self.autogain_enabled:
                power_W, mv_final, gains_final = self.daq.snapshot_autogain_W(
                    n_frames=1,
                    min_mv=50.0,
                    max_mv=4500.0,
                    max_iters=20,
                    settle_s=0.01,
                )
                # reflect autogain-selected gains in combos
                for phys_idx in range(4):
                    g = int(gains_final[phys_idx])
                    for card, info in zip(self.cards, self.card_infos):
                        if info[0] == "phys" and info[1] == phys_idx:
                            card.set_gain_index(g)
            else:
                power_W = self.daq.snapshot_W(
                    n_frames=1,
                    timeout_s=0.5,
                    poll_hz=200.0,
                )
        except CoreDAQError:
            return
        except Exception:
            return

        power_W = np.asarray(power_W, dtype=np.float32)
        if power_W.size < 4:
            power_W = np.pad(power_W, (0, 4 - power_W.size))

        # --- 2. Push into ring buffer for physical channels ---
        self.y_phys[:, self.widx] = power_W[:4]

        # Derived: compute new sample from physical sample
        vars_dict = {
            "ch1": float(power_W[0]),
            "ch2": float(power_W[1]),
            "ch3": float(power_W[2]),
            "ch4": float(power_W[3]),
        }

        # Math channels (same units as W)
        for i, cfg in enumerate(self.manager.math_channels):
            if not cfg.expression:
                self.y_math[i, self.widx] = np.nan
                continue
            try:
                val = safe_eval_expression(cfg.expression, vars_dict)
                self.y_math[i, self.widx] = float(val)
            except Exception:
                self.y_math[i, self.widx] = np.nan

        # Relative channels (dB)
        for i, cfg in enumerate(self.manager.relative_channels):
            num_idx, den_idx = cfg.rel_src_indices
            if not (0 <= num_idx < 4 and 0 <= den_idx < 4):
                self.y_rel[i, self.widx] = np.nan
                continue
            p_num = vars_dict[f"ch{num_idx + 1}"]
            p_den = vars_dict[f"ch{den_idx + 1}"]
            if p_num > 0 and p_den > 0:
                self.y_rel[i, self.widx] = 10.0 * np.log10(p_num / p_den)
            else:
                self.y_rel[i, self.widx] = np.nan

        # ring buffer index bookkeeping
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

        # helper to slice ring buffer
        def slice_buf(buf: np.ndarray, row: int) -> np.ndarray:
            if start + count <= N:
                return buf[row, start:start + count]
            first = N - start
            return np.concatenate(
                (buf[row, start:N], buf[row, 0:count - first]),
                axis=0
            )

        # --- 3. Update each card ---
        for card, info in zip(self.cards, self.card_infos):
            kind, idx = info
            if kind == "phys":
                ys = slice_buf(self.y_phys, idx)
                card.update_curve(xs, ys, ymin_floor=0.0)
                card.update_value_W(ys[-1])
            elif kind == "math":
                if idx < self.y_math.shape[0]:
                    ys = slice_buf(self.y_math, idx)
                    card.update_curve(xs, ys, ymin_floor=0.0)
                    card.update_value_W(ys[-1])
            elif kind == "rel":
                if idx < self.y_rel.shape[0]:
                    ys = slice_buf(self.y_rel, idx)
                    # allow negative dB if present
                    try:
                        has_negative = np.nanmin(ys) < 0
                    except ValueError:
                        has_negative = False
                    ymin_floor = None if has_negative else 0.0
                    card.update_curve(xs, ys, ymin_floor=ymin_floor)
                    card.update_value_W(ys[-1], unit_override="dB")

    # ---------------- Cleanup ----------------
    def closeEvent(self, ev: QtGui.QCloseEvent):
        try:
            self._live_timer.stop()
        except Exception:
            pass
        if self.daq is not None:
            try:
                self.daq.close()
            except Exception:
                pass
        super().closeEvent(ev)