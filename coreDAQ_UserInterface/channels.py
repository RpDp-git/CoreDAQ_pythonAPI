# channels.py
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Tuple, Dict, Any, Iterable

import numpy as np
from PyQt5 import QtCore, QtWidgets


# -------------------------------------------------------------------
# Channel config dataclass
# -------------------------------------------------------------------

@dataclass
class ChannelConfig:
    """
    Generic channel configuration.

    kind:
      - "physical"   -> direct CoreDAQ channel (index 0..3)
      - "math"       -> math expression based on ch1..ch4
      - "relative"   -> 10*log10(ch_num/ch_den) in dB
    """
    name: str
    kind: str  # "physical", "math", "relative"
    unit: str = "W"
    index: Optional[int] = None                  # for physical channels: 0..3
    expression: Optional[str] = None             # for math channels
    rel_src_indices: Optional[Tuple[int, int]] = None  # (num, den) for relative
    enabled: bool = True                         # for physical channels


# -------------------------------------------------------------------
# Safe expression evaluation for math channels
# -------------------------------------------------------------------

_SAFE_FUNCS: Dict[str, Any] = {
    "abs": np.abs,
    "sin": np.sin,
    "cos": np.cos,
    "tan": np.tan,
    "exp": np.exp,
    "log": np.log,
    "log10": np.log10,
    "sqrt": np.sqrt,
    "maximum": np.maximum,
    "minimum": np.minimum,
}


def safe_eval_expression(expr: str, context: Dict[str, Any]) -> Any:
    """
    Very small wrapper around eval with a restricted namespace.

    'context' typically contains:
        ch1, ch2, ch3, ch4 : scalars or numpy arrays (in W)
    """
    allowed = dict(_SAFE_FUNCS)
    allowed.update(context)
    return eval(expr, {"__builtins__": {}}, allowed)


# -------------------------------------------------------------------
# Channel manager
# -------------------------------------------------------------------

class ChannelManager:
    """
    Holds physical, math and relative channels and their enabled state.
    """

    def __init__(self) -> None:
        # 4 physical channels by default
        self.physical_channels: List[ChannelConfig] = [
            ChannelConfig(
                name=f"Channel {i+1}",
                kind="physical",
                unit="W",
                index=i,
                enabled=True,
            )
            for i in range(4)
        ]

        self.math_channels: List[ChannelConfig] = []
        self.relative_channels: List[ChannelConfig] = []

    # --- Physical enable/disable ---

    def is_physical_enabled(self, idx: int) -> bool:
        if 0 <= idx < len(self.physical_channels):
            return self.physical_channels[idx].enabled
        return False

    def set_physical_enabled(self, idx: int, enabled: bool) -> None:
        if 0 <= idx < len(self.physical_channels):
            self.physical_channels[idx].enabled = enabled

    # --- Add math / relative ---

    def add_math_channel(self, cfg: ChannelConfig) -> None:
        cfg.kind = "math"
        self.math_channels.append(cfg)

    def add_relative_channel(self, cfg: ChannelConfig) -> None:
        cfg.kind = "relative"
        self.relative_channels.append(cfg)

    # --- For UI / plotting ---

    def get_display_channels(self) -> List[ChannelConfig]:
        """
        Order: enabled physical channels, then math, then relative.
        """
        chs: List[ChannelConfig] = [
            c for c in self.physical_channels if c.enabled
        ]
        chs.extend(self.math_channels)
        chs.extend(self.relative_channels)
        return chs

    # --- Evaluation helpers (scalar) ---

    def eval_math_scalar(self, cfg: ChannelConfig, phys_values_W: Iterable[float]) -> float:
        """
        Evaluate math expression on current scalar physical values in W.
        phys_values_W: length-4 iterable [ch1_W, ch2_W, ch3_W, ch4_W]
        """
        ch1, ch2, ch3, ch4 = phys_values_W
        context = {
            "ch1": ch1,
            "ch2": ch2,
            "ch3": ch3,
            "ch4": ch4,
        }
        return float(safe_eval_expression(cfg.expression or "0", context))

    def eval_relative_scalar(self, cfg: ChannelConfig, phys_values_W: Iterable[float]) -> float:
        """
        10*log10(ch_num/ch_den) with some safety.
        """
        ch1, ch2, ch3, ch4 = phys_values_W
        arr = [ch1, ch2, ch3, ch4]
        num_idx, den_idx = cfg.rel_src_indices or (0, 1)
        num = float(arr[num_idx])
        den = float(arr[den_idx])
        if den <= 0 or num <= 0:
            # effectively -inf, but clamp for display
            return float("-inf")
        return 10.0 * np.log10(num / den)

    # --- Evaluation helpers (array) ---

    def eval_math_array(self, cfg: ChannelConfig, phys_arrays_W: List[np.ndarray]) -> np.ndarray:
        """
        Evaluate math expression on arrays for sweep plots.
        phys_arrays_W: [ch1_array, ch2_array, ch3_array, ch4_array] in W
        """
        ch1, ch2, ch3, ch4 = phys_arrays_W
        context = {
            "ch1": ch1,
            "ch2": ch2,
            "ch3": ch3,
            "ch4": ch4,
        }
        return np.asarray(safe_eval_expression(cfg.expression or "0", context))

    def eval_relative_array(self, cfg: ChannelConfig, phys_arrays_W: List[np.ndarray]) -> np.ndarray:
        """
        Relative transmission in dB on arrays for sweep plots.
        """
        ch1, ch2, ch3, ch4 = phys_arrays_W
        num_idx, den_idx = cfg.rel_src_indices or (0, 1)
        num = np.asarray([ch1, ch2, ch3, ch4][num_idx])
        den = np.asarray([ch1, ch2, ch3, ch4][den_idx])
        num = np.maximum(num, 1e-20)
        den = np.maximum(den, 1e-20)
        return 10.0 * np.log10(num / den)


# -------------------------------------------------------------------
# Math channel dialog
# -------------------------------------------------------------------

class MathChannelDialog(QtWidgets.QDialog):
    """
    Simple dialog for creating/editing a math channel.
    User enters:
      - Name (optional)
      - Expression (required), e.g. "ch1 - ch2" or "10*log10(ch1/ch2)"
      - Unit (optional)
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Add Math Channel")

        self.channel_name: str = ""
        self.expression: str = ""
        self.unit: str = ""

        layout = QtWidgets.QFormLayout(self)

        self.name_edit = QtWidgets.QLineEdit(self)
        self.expr_edit = QtWidgets.QLineEdit(self)
        self.unit_edit = QtWidgets.QLineEdit(self)

        self.expr_edit.setPlaceholderText("e.g. ch1 - ch2 or 10*log10(ch1/ch2)")

        layout.addRow("Name:", self.name_edit)
        layout.addRow("Expression:", self.expr_edit)
        layout.addRow("Unit:", self.unit_edit)

        help_label = QtWidgets.QLabel(
            "Variables: ch1, ch2, ch3, ch4 (in W)\n"
            "Functions: sin, cos, exp, log, log10, sqrt, maximum, minimum, abs"
        )
        help_label.setWordWrap(True)
        layout.addRow(help_label)

        btn_box = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel,
            QtCore.Qt.Horizontal,
            self,
        )
        btn_box.accepted.connect(self._on_accept)
        btn_box.rejected.connect(self.reject)
        layout.addRow(btn_box)

    def _on_accept(self):
        self.channel_name = self.name_edit.text().strip()
        self.expression = self.expr_edit.text().strip()
        self.unit = self.unit_edit.text().strip()
        if not self.expression:
            QtWidgets.QMessageBox.warning(
                self,
                "Invalid expression",
                "Expression cannot be empty.",
            )
            return
        self.accept()


# -------------------------------------------------------------------
# Relative transmission dialog
# -------------------------------------------------------------------

class RelativeTransmissionDialog(QtWidgets.QDialog):
    """
    Dialog to define a relative transmission channel of the form:
        10 * log10(Ch_num / Ch_den)
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Add Relative Transmission Channel")

        self.channel_name: str = ""
        self.numerator_index: int = 0
        self.denominator_index: int = 1

        layout = QtWidgets.QFormLayout(self)

        self.name_edit = QtWidgets.QLineEdit(self)

        self.num_combo = QtWidgets.QComboBox(self)
        self.den_combo = QtWidgets.QComboBox(self)

        # We assume 4 physical channels (1..4)
        for i in range(4):
            self.num_combo.addItem(f"Channel {i+1}", i)
            self.den_combo.addItem(f"Channel {i+1}", i)

        self.num_combo.setCurrentIndex(0)
        self.den_combo.setCurrentIndex(1)

        layout.addRow("Name:", self.name_edit)
        layout.addRow("Numerator:", self.num_combo)
        layout.addRow("Denominator:", self.den_combo)

        info = QtWidgets.QLabel("Result = 10·log10( Numerator / Denominator ) in dB")
        layout.addRow(info)

        btn_box = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel,
            QtCore.Qt.Horizontal,
            self,
        )
        btn_box.accepted.connect(self._on_accept)
        btn_box.rejected.connect(self.reject)
        layout.addRow(btn_box)

    def _on_accept(self):
        name = self.name_edit.text().strip()
        self.channel_name = name

        self.numerator_index = int(self.num_combo.currentData())
        self.denominator_index = int(self.den_combo.currentData())

        if self.numerator_index == self.denominator_index:
            QtWidgets.QMessageBox.warning(
                self,
                "Invalid selection",
                "Numerator and denominator must be different channels.",
            )
            return

        self.accept()
