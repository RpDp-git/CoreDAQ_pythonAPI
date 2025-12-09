# channels.py
from dataclasses import dataclass
from typing import Optional, Tuple, List
import ast
import operator as op

from PyQt5 import QtWidgets, QtCore

# ------------------------------------------------------------
# Channel definition + manager
# ------------------------------------------------------------

@dataclass
class ChannelConfig:
    """
    Unified channel description.

    kind:
        "physical"  - direct CoreDAQ channel
        "math"      - expression based on ch1..ch4
        "relative"  - 10*log10(Pa/Pb) between two physical channels
    """
    name: str
    kind: str  # "physical" | "math" | "relative"
    unit: str = ""
    physical_index: Optional[int] = None               # 0..3 for physical
    expression: Optional[str] = None                   # for math channels
    rel_src_indices: Optional[Tuple[int, int]] = None  # (num_idx, den_idx)


class ChannelManager:
    """
    Global channel registry shared between all tabs.
    """
    def __init__(self):
        self.enabled_physical: List[bool] = [True, True, True, True]
        self.math_channels: List[ChannelConfig] = []
        self.relative_channels: List[ChannelConfig] = []

    # ---- physical channels ----
    def set_physical_enabled(self, index: int, enabled: bool):
        if 0 <= index < 4:
            self.enabled_physical[index] = bool(enabled)

    def is_physical_enabled(self, index: int) -> bool:
        if 0 <= index < 4:
            return self.enabled_physical[index]
        return False

    # ---- math channels ----
    def add_math_channel(self, cfg: ChannelConfig):
        if cfg.kind != "math":
            raise ValueError("ChannelConfig.kind must be 'math' for math channels")
        self.math_channels.append(cfg)

    # ---- relative channels ----
    def add_relative_channel(self, cfg: ChannelConfig):
        if cfg.kind != "relative":
            raise ValueError("ChannelConfig.kind must be 'relative' for relative channels")
        self.relative_channels.append(cfg)

    # ---- active channel list ----
    def get_active_channel_configs(self) -> List[ChannelConfig]:
        """
        Return all *enabled* physical channels + all math + all relative.
        """
        configs: List[ChannelConfig] = []

        # Physical
        for i in range(4):
            if self.enabled_physical[i]:
                configs.append(
                    ChannelConfig(
                        name=f"Channel {i + 1}",
                        kind="physical",
                        unit="W",
                        physical_index=i,
                    )
                )

        # Math and relative: as stored
        configs.extend(self.math_channels)
        configs.extend(self.relative_channels)
        return configs


# ------------------------------------------------------------
# Expression + formatting helpers shared by tabs
# ------------------------------------------------------------

_ALLOWED_OPS = {
    ast.Add: op.add,
    ast.Sub: op.sub,
    ast.Mult: op.mul,
    ast.Div: op.truediv,
    ast.Pow: op.pow,
    ast.Mod: op.mod,
    ast.USub: op.neg,
    ast.UAdd: op.pos,
}


def safe_eval_expression(expr: str, variables: dict):
    """
    Safely evaluate an arithmetic expression using ch1..ch4 etc.

    Allowed:
        - Numbers
        - Variables in `variables`, e.g. 'ch1', 'ch2'
        - +, -, *, /, **, %, unary +/- and parentheses

    Works with scalars OR numpy arrays.

    Raises ValueError on invalid expressions.
    """
    expr = expr.strip()
    if not expr:
        raise ValueError("Empty expression")
    if len(expr) > 80:
        raise ValueError("Expression too long")

    node = ast.parse(expr, mode="eval")

    def _eval(n):
        if isinstance(n, ast.Expression):
            return _eval(n.body)
        if isinstance(n, ast.Constant):
            if isinstance(n.value, (int, float)):
                return n.value
            raise ValueError("Only int/float constants allowed")
        if isinstance(n, ast.Num):  # py<3.8
            return n.n
        if isinstance(n, ast.BinOp) and type(n.op) in _ALLOWED_OPS:
            return _ALLOWED_OPS[type(n.op)](_eval(n.left), _eval(n.right))
        if isinstance(n, ast.UnaryOp) and type(n.op) in _ALLOWED_OPS:
            return _ALLOWED_OPS[type(n.op)](_eval(n.operand))
        if isinstance(n, ast.Name):
            if n.id in variables:
                return variables[n.id]
            raise ValueError(f"Unknown variable '{n.id}'")
        raise ValueError("Unsupported expression")

    return _eval(node)


def format_power_auto(value_w: float):
    """
    Format a power value (in watts) into a sensible engineering unit
    (W, mW, µW, nW, pW) and return (string, unit).
    """
    if value_w is None:
        return "—", "W"

    v = float(value_w)
    av = abs(v)

    if av >= 1.0 or av == 0.0:
        return f"{v:.3g}", "W"
    elif av >= 1e-3:
        return f"{v * 1e3:.3g}", "mW"
    elif av >= 1e-6:
        return f"{v * 1e6:.3g}", "µW"
    elif av >= 1e-9:
        return f"{v * 1e9:.3g}", "nW"
    else:
        return f"{v * 1e12:.3g}", "pW"


# High-contrast color cycle for curves
COLOR_CYCLE = [
    "#f5f5f5",  # near-white
    "#ff5252",  # red
    "#40c4ff",  # cyan
    "#ffc400",  # amber
    "#b39ddb",  # soft violet
    "#69f0ae",  # mint
    "#ff9e80",  # orange
    "#8e24aa",  # purple
]


# ------------------------------------------------------------
# Dialogs for creating math and relative channels
# ------------------------------------------------------------

class MathChannelDialog(QtWidgets.QDialog):
    """
    Dialog to define a math channel:
      - Name
      - Expression in terms of ch1..ch4
      - Unit (optional)
    Produces a ChannelConfig(kind="math", expression=..., unit=...).
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Add Math Channel")
        self.setModal(True)
        self.config: Optional[ChannelConfig] = None

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        form = QtWidgets.QFormLayout()
        form.setLabelAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)

        self.le_name = QtWidgets.QLineEdit(self)
        self.le_name.setPlaceholderText("e.g. CH1 - CH2")
        form.addRow("Name:", self.le_name)

        self.le_expr = QtWidgets.QLineEdit(self)
        self.le_expr.setPlaceholderText("Expression in ch1..ch4, e.g. (ch1 - ch2) / ch2")
        form.addRow("Expression:", self.le_expr)

        self.le_unit = QtWidgets.QLineEdit(self)
        self.le_unit.setPlaceholderText("Optional, e.g. W, mW, dB")
        form.addRow("Unit:", self.le_unit)

        layout.addLayout(form)

        hint = QtWidgets.QLabel(
            "Allowed: numbers, variables ch1..ch4, and + - * / ** % with parentheses."
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)

        btn_box = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel,
            QtCore.Qt.Horizontal,
            self,
        )
        btn_box.accepted.connect(self._on_accept)
        btn_box.rejected.connect(self.reject)
        layout.addWidget(btn_box)

    def _on_accept(self):
        name = self.le_name.text().strip()
        expr = self.le_expr.text().strip()
        unit = self.le_unit.text().strip()

        if not expr:
            QtWidgets.QMessageBox.warning(
                self,
                "Invalid expression",
                "Please enter a valid expression in terms of ch1..ch4.",
            )
            return

        # Try a dry-run parse to provide early feedback
        try:
            _ = safe_eval_expression(
                expr,
                {"ch1": 1.0, "ch2": 1.0, "ch3": 1.0, "ch4": 1.0},
            )
        except Exception as e:
            QtWidgets.QMessageBox.warning(
                self,
                "Invalid expression",
                f"Expression could not be parsed:\n{e}",
            )
            return

        if not name:
            name = f"Math ({expr})"

        cfg = ChannelConfig(
            name=name,
            kind="math",
            unit=unit,
            expression=expr,
        )
        self.config = cfg
        self.accept()

    def get_config(self) -> Optional[ChannelConfig]:
        return self.config


class RelativeTransmissionDialog(QtWidgets.QDialog):
    """
    Dialog to define a relative transmission channel:
      - Name
      - Numerator physical channel (1..4)
      - Denominator physical channel (1..4)
    Produces a ChannelConfig(kind="relative", unit="dB",
    rel_src_indices=(num_idx, den_idx)).
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Add Relative Transmission Channel")
        self.setModal(True)
        self.config: Optional[ChannelConfig] = None

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        form = QtWidgets.QFormLayout()
        form.setLabelAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)

        self.le_name = QtWidgets.QLineEdit(self)
        self.le_name.setPlaceholderText("e.g. CH1 / CH2 (dB)")
        form.addRow("Name:", self.le_name)

        self.cb_num = QtWidgets.QComboBox(self)
        self.cb_den = QtWidgets.QComboBox(self)
        for i in range(4):
            label = f"Channel {i + 1}"
            self.cb_num.addItem(label, i)
            self.cb_den.addItem(label, i)
        form.addRow("Numerator:", self.cb_num)
        form.addRow("Denominator:", self.cb_den)

        layout.addLayout(form)

        hint = QtWidgets.QLabel(
            "Computes 10·log10(P_num / P_den) based on physical channels."
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)

        btn_box = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel,
            QtCore.Qt.Horizontal,
            self,
        )
        btn_box.accepted.connect(self._on_accept)
        btn_box.rejected.connect(self.reject)
        layout.addWidget(btn_box)

    def _on_accept(self):
        name = self.le_name.text().strip()
        num_idx = int(self.cb_num.currentData())
        den_idx = int(self.cb_den.currentData())

        if num_idx == den_idx:
            QtWidgets.QMessageBox.warning(
                self,
                "Invalid selection",
                "Numerator and denominator must be different channels.",
            )
            return

        if not name:
            name = f"Rel CH{num_idx + 1}/CH{den_idx + 1} (dB)"

        cfg = ChannelConfig(
            name=name,
            kind="relative",
            unit="dB",
            rel_src_indices=(num_idx, den_idx),
        )
        self.config = cfg
        self.accept()

    def get_config(self) -> Optional[ChannelConfig]:
        return self.config