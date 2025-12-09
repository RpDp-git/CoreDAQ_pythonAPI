# main.py

import sys
from PyQt5 import QtCore, QtGui, QtWidgets
import pyqtgraph as pg

from plotter_tab import PlotterWidget
from sweep_tab import SweepWidget
from channels import (
    ChannelManager,
    MathChannelDialog,
    RelativeTransmissionDialog,
    ChannelConfig,
    safe_eval_expression,
)


# ------------------------------------------------------------
# Main window with sidebar + tabs
# ------------------------------------------------------------

class MainWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("CoreDAQ Control")
        self.resize(1280, 800)

        # Shared channel manager
        self.manager = ChannelManager()

        self._build_central_ui()
        self._build_menubar()
        self._apply_theme()

        # Periodic sidebar status updates (temperature, humidity, etc.)
        self._status_timer = QtCore.QTimer(self)
        self._status_timer.timeout.connect(self._update_status)
        # 10 seconds
        self._status_timer.start(10_000)

        # Start Plotter acquisition by default
        self.plotter.set_active(True)

    # --------------------------------------------------------
    # Central UI: sidebar + stacked pages
    # --------------------------------------------------------

    def _build_central_ui(self):
        central = QtWidgets.QWidget()
        central.setObjectName("CentralWidget")
        self.setCentralWidget(central)

        layout = QtWidgets.QHBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # ----- Sidebar list -----
        self.sidebar = QtWidgets.QListWidget()
        self.sidebar.setSpacing(2)
        self.sidebar.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.sidebar.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self.sidebar.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self.sidebar.setObjectName("Sidebar")

        sfont = self.sidebar.font()
        sfont.setPointSize(int(sfont.pointSize() * 1.4))
        self.sidebar.setFont(sfont)

        self.sidebar.addItem("Live Monitoring")
        self.sidebar.addItem("Sweep with Laser")

        # ----- Sidebar footer -----
        sidebar_footer = QtWidgets.QFrame()
        sidebar_footer.setObjectName("SidebarFooter")
        footer_layout = QtWidgets.QVBoxLayout(sidebar_footer)
        footer_layout.setContentsMargins(10, 8, 10, 10)
        footer_layout.setSpacing(4)

        # Product / website
        self.footer_title = QtWidgets.QLabel("coreDAQ")
        f_title_font = self.footer_title.font()
        f_title_font.setPointSize(int(f_title_font.pointSize() * 2.0))
        f_title_font.setBold(True)
        self.footer_title.setFont(f_title_font)

        self.footer_subtitle = QtWidgets.QLabel("core-instrumentation.com")
        f_sub_font = self.footer_subtitle.font()
        f_sub_font.setPointSize(int(f_sub_font.pointSize() * 0.9))
        self.footer_subtitle.setFont(f_sub_font)

        # Thin separator line
        sep = QtWidgets.QFrame()
        sep.setFrameShape(QtWidgets.QFrame.HLine)
        sep.setFrameShadow(QtWidgets.QFrame.Sunken)

        # Environment / status labels
        self.lbl_device_temp = QtWidgets.QLabel("Device temperature:  — °C")
        self.lbl_frontend_temp = QtWidgets.QLabel("Frontend temperature:  — °C")
        self.lbl_frontend_rh = QtWidgets.QLabel("Humidity:  — % RH")

        footer_layout.addWidget(self.footer_title)
        footer_layout.addWidget(self.footer_subtitle)
        footer_layout.addWidget(sep)
        footer_layout.addWidget(self.lbl_device_temp)
        footer_layout.addWidget(self.lbl_frontend_temp)
        footer_layout.addWidget(self.lbl_frontend_rh)
        footer_layout.addStretch(0)

        # ----- Sidebar container (list + footer) -----
        sidebar_container = QtWidgets.QWidget()
        sidebar_container.setObjectName("SidebarContainer")
        sidebar_container.setFixedWidth(230)

        side_v = QtWidgets.QVBoxLayout(sidebar_container)
        side_v.setContentsMargins(0, 0, 0, 0)
        side_v.setSpacing(0)
        side_v.addWidget(self.sidebar)
        side_v.addStretch(1)
        side_v.addWidget(sidebar_footer, 0)

        # ----- Main pages -----
        self.pages = QtWidgets.QStackedWidget()
        self.plotter = PlotterWidget(self.manager)
        self.sweep = SweepWidget(self.manager)
        self.pages.addWidget(self.plotter)
        self.pages.addWidget(self.sweep)

        layout.addWidget(sidebar_container)
        layout.addWidget(self.pages)

        self.sidebar.currentRowChanged.connect(self._on_tab_changed)
        self.sidebar.setCurrentRow(0)

    def _on_tab_changed(self, index: int):
        self.pages.setCurrentIndex(index)
        # Only Plotter needs live acquisition toggling
        self.plotter.set_active(index == 0)

    # --------------------------------------------------------
    # Menubar
    # --------------------------------------------------------

    def _build_menubar(self):
        menubar = self.menuBar()
        menubar.setNativeMenuBar(False)  # consistent styling

        # View menu: enable/disable physical channels
        view_menu = menubar.addMenu("&View")

        self.channel_actions = []
        for i in range(4):
            ch_num = i + 1
            act = QtWidgets.QAction(f"Enable Channel {ch_num}", self)
            act.setCheckable(True)
            act.setChecked(self.manager.is_physical_enabled(i))
            act.toggled.connect(
                lambda checked, idx=i: self._on_toggle_physical(idx, checked)
            )
            view_menu.addAction(act)
            self.channel_actions.append(act)

        # Channels menu
        channels_menu = menubar.addMenu("&Channels")

        add_math_act = QtWidgets.QAction("Add math channel…", self)
        add_math_act.triggered.connect(self._on_add_math_channel)
        channels_menu.addAction(add_math_act)

        add_rel_act = QtWidgets.QAction("Add relative transmission channel…", self)
        add_rel_act.triggered.connect(self._on_add_relative_channel)
        channels_menu.addAction(add_rel_act)

        # Sweep menu
        sweep_menu = menubar.addMenu("&Sweep")
        sweep_params_act = QtWidgets.QAction("Sweep Parameters…", self)
        sweep_params_act.triggered.connect(self._on_edit_sweep_params)
        sweep_menu.addAction(sweep_params_act)

    # --------------------------------------------------------
    # Theme / global styling
    # --------------------------------------------------------

    def _apply_theme(self):
        QtWidgets.QApplication.setStyle("Fusion")

        # High-contrast dark palette
        pal = self.palette()
        pal.setColor(QtGui.QPalette.Window, QtGui.QColor("#121212"))
        pal.setColor(QtGui.QPalette.WindowText, QtGui.QColor("#ffffff"))
        pal.setColor(QtGui.QPalette.Base, QtGui.QColor("#1e1e1e"))
        pal.setColor(QtGui.QPalette.AlternateBase, QtGui.QColor("#252525"))
        pal.setColor(QtGui.QPalette.Text, QtGui.QColor("#f5f5f5"))
        pal.setColor(QtGui.QPalette.Button, QtGui.QColor("#252525"))
        pal.setColor(QtGui.QPalette.ButtonText, QtGui.QColor("#f5f5f5"))
        pal.setColor(QtGui.QPalette.Highlight, QtGui.QColor("#42a5f5"))
        pal.setColor(QtGui.QPalette.HighlightedText, QtGui.QColor("#000000"))
        pal.setColor(QtGui.QPalette.Disabled, QtGui.QPalette.Text, QtGui.QColor("#666666"))
        pal.setColor(QtGui.QPalette.Disabled, QtGui.QPalette.ButtonText, QtGui.QColor("#666666"))
        self.setPalette(pal)

        # Global stylesheet for modern flat look
        self.setStyleSheet("""
            QMainWindow {
                background-color: #121212;
            }

            #Sidebar {
                background-color: #181818;
                border-right: 1px solid #2c2c2c;
            }

            #Sidebar::item {
                padding: 10px 16px;
                color: #e0e0e0;
            }

            #Sidebar::item:selected {
                background-color: #2b5fb8;
                color: #ffffff;
            }

            #Sidebar::item:hover {
                background-color: #2a2a2a;
            }

            QScrollArea {
                background-color: #121212;
                border: none;
            }

            #PlotterContainer {
                background-color: #121212;
            }

            #CentralWidget {
                background-color: #121212;
            }

            QMenuBar {
                background-color: #181818;
                color: #e0e0e0;
            }

            QMenuBar::item {
                spacing: 3px;
                padding: 4px 10px;
                background: transparent;
            }

            QMenuBar::item:selected {
                background: #2a2a2a;
            }

            QMenu {
                background-color: #1e1e1e;
                color: #f5f5f5;
                border: 1px solid #333333;
            }

            QMenu::item:selected {
                background-color: #2b5fb8;
            }

            QToolTip {
                background-color: #2a2a2a;
                color: #ffffff;
                border: 1px solid #3c3c3c;
            }

            QPushButton {
                color: #f5f5f5;
                background-color: #2a2a2a;
                border-radius: 4px;
                padding: 6px 14px;
                border: 1px solid #3a3a3a;
            }

            QPushButton:hover {
                background-color: #333333;
            }

            QPushButton:pressed {
                background-color: #1f1f1f;
            }

            QPushButton:disabled {
                color: #777777;
                background-color: #222222;
                border: 1px solid #333333;
            }

            QLineEdit, QPlainTextEdit, QComboBox, QSpinBox, QDoubleSpinBox {
                background-color: #1f1f1f;
                color: #f5f5f5;
                border: 1px solid #3a3a3a;
                border-radius: 3px;
                padding: 3px 6px;
            }

            QComboBox QAbstractItemView {
                background-color: #1f1f1f;
                color: #f5f5f5;
                selection-background-color: #2b5fb8;
            }

            QScrollBar:vertical {
                background: #202020;
                width: 10px;
                margin: 0px;
            }

            QScrollBar::handle:vertical {
                background: #444444;
                border-radius: 4px;
                min-height: 20px;
            }

            QScrollBar::handle:vertical:hover {
                background: #5a5a5a;
            }

            QScrollBar::add-line:vertical,
            QScrollBar::sub-line:vertical {
                height: 0px;
            }

            QFrame#ChannelCard,
            QFrame#SweepChannelCard {
                background-color: #1e1e1e;
                border-radius: 8px;
                border: 1px solid #333333;
            }
        """)

        pg.setConfigOptions(antialias=True)

    # --------------------------------------------------------
    # Sidebar status: temperature / humidity
    # --------------------------------------------------------

    def _update_status(self):
        """
        Poll CoreDAQ environmental/status values and display them in the sidebar footer.

        Uses the CoreDAQ instance owned by the PlotterWidget, if available.
        """
        daq = getattr(self.plotter, "daq", None)
        if daq is None:
            self.lbl_device_temp.setText("Device temperature:  — °C")
            self.lbl_frontend_temp.setText("Frontend temperature:  — °C")
            self.lbl_frontend_rh.setText("Humidity:  — % RH")
            return

        # Device / die temperature
        try:
            t_die = daq.get_die_temperature_C()
            if t_die is not None:
                self.lbl_device_temp.setText(f"Device temperature:  {t_die:.1f} °C")
            else:
                self.lbl_device_temp.setText("Device temperature:  — °C")
        except Exception:
            self.lbl_device_temp.setText("Device temperature:  — °C")

        # Frontend temperature
        try:
            t_head = daq.get_head_temperature_C()
            if t_head is not None:
                self.lbl_frontend_temp.setText(f"Frontend temperature:  {t_head:.1f} °C")
            else:
                self.lbl_frontend_temp.setText("Frontend temperature:  — °C")
        except Exception:
            self.lbl_frontend_temp.setText("Frontend temperature:  — °C")

        # Humidity
        try:
            rh = daq.get_head_humidity()
            if rh is not None:
                self.lbl_frontend_rh.setText(f"Humidity:  {rh:.1f} % RH")
            else:
                self.lbl_frontend_rh.setText("Humidity:  — % RH")
        except Exception:
            self.lbl_frontend_rh.setText("Humidity:  — % RH")

    # --------------------------------------------------------
    # View menu handlers
    # --------------------------------------------------------

    def _on_toggle_physical(self, index: int, enabled: bool):
        self.manager.set_physical_enabled(index, enabled)
        self.plotter.on_channels_updated()
        self.sweep.on_channels_updated()

    # --------------------------------------------------------
    # Channels menu handlers
    # --------------------------------------------------------

    def _on_add_math_channel(self):
        dlg = MathChannelDialog(self)
        if dlg.exec_() != QtWidgets.QDialog.Accepted:
            return

        name = dlg.channel_name
        expr = dlg.expression
        unit = dlg.unit or ""

        if not expr:
            QtWidgets.QMessageBox.warning(
                self,
                "Invalid expression",
                "Expression cannot be empty.",
            )
            return

        # Quick validation
        try:
            _ = safe_eval_expression(
                expr, {"ch1": 1.0, "ch2": 2.0, "ch3": 3.0, "ch4": 4.0}
            )
        except Exception as e:
            QtWidgets.QMessageBox.warning(
                self,
                "Invalid expression",
                f"Could not parse expression:\n{e}",
            )
            return

        if not name:
            name = f"Math {len(self.manager.math_channels) + 1}"

        cfg = ChannelConfig(
            name=name,
            kind="math",
            unit=unit,
            expression=expr,
        )
        self.manager.add_math_channel(cfg)
        self.plotter.on_channels_updated()
        self.sweep.on_channels_updated()

    def _on_add_relative_channel(self):
        dlg = RelativeTransmissionDialog(self)
        if dlg.exec_() != QtWidgets.QDialog.Accepted:
            return

        name = dlg.channel_name
        num_idx = dlg.numerator_index
        den_idx = dlg.denominator_index

        if not name:
            name = f"Rel Trans Ch{num_idx+1}/Ch{den_idx+1}"

        cfg = ChannelConfig(
            name=name,
            kind="relative",
            unit="dB",
            rel_src_indices=(num_idx, den_idx),
        )
        self.manager.add_relative_channel(cfg)
        self.plotter.on_channels_updated()
        self.sweep.on_channels_updated()

    # --------------------------------------------------------
    # Sweep menu handler
    # --------------------------------------------------------

    def _on_edit_sweep_params(self):
        self.sweep.open_params_dialog(self)


# ------------------------------------------------------------
# Entry point
# ------------------------------------------------------------

def main():
    app = QtWidgets.QApplication(sys.argv)
    w = MainWindow()
    w.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()