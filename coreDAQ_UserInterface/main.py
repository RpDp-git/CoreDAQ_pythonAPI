# main.py

import sys
from typing import List

from PyQt5 import QtCore, QtGui, QtWidgets
import pyqtgraph as pg

from coredaq_py_api import CoreDAQ, CoreDAQError

from plotter_tab import PlotterWidget
from sweep_tab import SweepWidget
from channels import (
    ChannelManager,
    MathChannelDialog,
    RelativeTransmissionDialog,
    ChannelConfig,
    safe_eval_expression,
)

# ----------------------------------------------------------------------
# Main window
# ----------------------------------------------------------------------


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()

        self.setWindowTitle("CoreDAQ Control")
        self.resize(1280, 800)

        # Channel / device state
        self.manager = ChannelManager()
        self.daq: CoreDAQ | None = None

                # Timer for environment status (do NOT start yet)
        self.env_timer = QtCore.QTimer(self)
        self.env_timer.setInterval(10_000)  # 10 s
        self.env_timer.timeout.connect(self._update_env_status)

        # Connect to CoreDAQ once
        self._connect_coredaq()

        # Build UI
        self._build_central_ui()
        self._build_menubar()
        self._apply_theme()

        # Hand CoreDAQ instance into tabs
        self.plotter.set_daq(self.daq)
        self.sweep.set_daq(self.daq)

        # Initial one-shot update for labels
        self._update_env_status()


        # Start Plotter acquisition by default
        self.plotter.set_active(True)

    # ------------------------------------------------------------------
    # CoreDAQ connection
    # ------------------------------------------------------------------
    def _connect_coredaq(self):
        """Connect once to CoreDAQ and keep the instance."""
        port = None
        try:
            ports = CoreDAQ.find()
            if ports:
                port = ports[0]
        except Exception:
            ports = []

        if port is None:
            # Fallback default; adjust to your typical port on Windows/macOS
            port = "COM7"

        try:
            self.daq = CoreDAQ(port)
            # Basic setup
            try:
                self.daq.set_oversampling(1)
            except Exception:
                pass

            try:
                idn = self.daq.idn()
                self.setWindowTitle(f"CoreDAQ Control – {idn}")
            except Exception:
                pass

        except Exception as e:
            self.daq = None
            # We can still run UI without hardware; just log to console
            print(f"Failed to connect to CoreDAQ on {port}: {e}")

    # ------------------------------------------------------------------
    # Central UI: sidebar + stacked pages
    # ------------------------------------------------------------------
    def _build_central_ui(self):
        central = QtWidgets.QWidget()
        central.setObjectName("CentralWidget")
        self.setCentralWidget(central)

        layout = QtWidgets.QHBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # ----- Sidebar with pages list -----
        self.sidebar = QtWidgets.QListWidget()
        self.sidebar.setObjectName("Sidebar")
        self.sidebar.setSpacing(2)
        self.sidebar.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.sidebar.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self.sidebar.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)

        sfont = self.sidebar.font()
        sfont.setPointSize(int(sfont.pointSize() * 1.4))
        self.sidebar.setFont(sfont)

        self.sidebar.addItem("Plotter")
        self.sidebar.addItem("Sweep with Laser")

        # ----- Sidebar footer (branding + temperatures) -----
        sidebar_footer = QtWidgets.QFrame()
        sidebar_footer.setObjectName("SidebarFooter")
        footer_layout = QtWidgets.QVBoxLayout(sidebar_footer)
        footer_layout.setContentsMargins(10, 10, 10, 10)
        footer_layout.setSpacing(6)

        # Branding
        self.footer_title = QtWidgets.QLabel("coreDAQ")
        self.footer_title.setObjectName("SidebarFooterTitle")
        title_font = self.footer_title.font()
        title_font.setPointSize(int(title_font.pointSize() * 1.8))
        title_font.setBold(True)
        self.footer_title.setFont(title_font)

        self.footer_subtitle = QtWidgets.QLabel("core-instrumentation.com")
        self.footer_subtitle.setObjectName("SidebarFooterSubtitle")

        footer_layout.addWidget(self.footer_title)
        footer_layout.addWidget(self.footer_subtitle)

        # Separator line
        sep = QtWidgets.QFrame()
        sep.setFrameShape(QtWidgets.QFrame.HLine)
        sep.setFrameShadow(QtWidgets.QFrame.Sunken)
        sep.setStyleSheet("color: #444444;")
        footer_layout.addWidget(sep)

        # Environmental labels
        self.lbl_device_temp = QtWidgets.QLabel("Device temperature: — °C")
        self.lbl_frontend_temp = QtWidgets.QLabel("Frontend temperature: — °C")
        self.lbl_frontend_rh = QtWidgets.QLabel("Humidity: — % RH")

        footer_layout.addWidget(self.lbl_device_temp)
        footer_layout.addWidget(self.lbl_frontend_temp)
        footer_layout.addWidget(self.lbl_frontend_rh)

        footer_layout.addStretch(1)

        # Sidebar container (list + footer)
        sidebar_container = QtWidgets.QWidget()
        sidebar_container.setObjectName("SidebarContainer")
        sidebar_container.setFixedWidth(230)

        side_layout = QtWidgets.QVBoxLayout(sidebar_container)
        side_layout.setContentsMargins(0, 0, 0, 0)
        side_layout.setSpacing(0)
        side_layout.addWidget(self.sidebar)
        side_layout.addWidget(sidebar_footer)

        # ----- Pages -----
        self.pages = QtWidgets.QStackedWidget()
        self.plotter = PlotterWidget(self.manager, daq=None)
        self.sweep = SweepWidget(self.manager, daq=None)

        self.pages.addWidget(self.plotter)
        self.pages.addWidget(self.sweep)

        layout.addWidget(sidebar_container)
        layout.addWidget(self.pages)

        self.sidebar.currentRowChanged.connect(self._on_tab_changed)
        self.sidebar.setCurrentRow(0)

    def _on_tab_changed(self, index: int):
        self.pages.setCurrentIndex(index)
        self.plotter.set_active(index == 0)
        # If you want to pause anything in Sweep when leaving it, you can
        # add a set_active() method there later.

    # ------------------------------------------------------------------
    # Menu bar
    # ------------------------------------------------------------------
    def _build_menubar(self):
        menubar = self.menuBar()
        menubar.setNativeMenuBar(False)

        # ----- View menu: enable/disable physical channels -----
        view_menu = menubar.addMenu("&View")

        self.channel_actions: List[QtWidgets.QAction] = []
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

        # ----- Channels menu: math / relative channels -----
        channels_menu = menubar.addMenu("&Channels")

        add_math_act = QtWidgets.QAction("Add math channel…", self)
        add_math_act.triggered.connect(self._on_add_math_channel)
        channels_menu.addAction(add_math_act)

        add_rel_act = QtWidgets.QAction("Add relative transmission channel…", self)
        add_rel_act.triggered.connect(self._on_add_relative_channel)
        channels_menu.addAction(add_rel_act)

        # ----- Sweep menu -----
        sweep_menu = menubar.addMenu("&Sweep")
        sweep_params_act = QtWidgets.QAction("Sweep Parameters…", self)
        sweep_params_act.triggered.connect(self._on_edit_sweep_params)
        sweep_menu.addAction(sweep_params_act)

        # File menu (quit)
        file_menu = menubar.addMenu("&File")
        act_quit = QtWidgets.QAction("Quit", self)
        act_quit.triggered.connect(self.close)
        file_menu.addAction(act_quit)

    # ------------------------------------------------------------------
    # Theming / style
    # ------------------------------------------------------------------
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
        pal.setColor(QtGui.QPalette.Highlight, QtGui.QColor("#2b5fb8"))
        pal.setColor(QtGui.QPalette.HighlightedText, QtGui.QColor("#000000"))
        pal.setColor(
            QtGui.QPalette.Disabled,
            QtGui.QPalette.Text,
            QtGui.QColor("#666666"),
        )
        pal.setColor(
            QtGui.QPalette.Disabled,
            QtGui.QPalette.ButtonText,
            QtGui.QColor("#666666"),
        )
        self.setPalette(pal)

        # Global stylesheet for modern flat look
        self.setStyleSheet(
            """
            QMainWindow {
                background-color: #121212;
            }

            QListWidget#Sidebar {
                background-color: #181818;
                border-right: 1px solid #2c2c2c;
                color: #e0e0e0;
            }

            QListWidget#Sidebar::item {
                padding: 10px 16px;
                color: #e0e0e0;
            }

            QListWidget#Sidebar::item:selected {
                background-color: #2b5fb8;
                color: #ffffff;
            }

            QListWidget#Sidebar::item:hover {
                background-color: #2a2a2a;
            }

            QFrame#SidebarFooter {
                background-color: #181818;
                border-top: 1px solid #2c2c2c;
            }

            QFrame#SidebarFooter QLabel {
                color: #e0e0e0;
            }

            QLabel#SidebarFooterTitle {
                color: #f5f5f5;
                font-weight: bold;
            }

            QLabel#SidebarFooterSubtitle {
                color: #bbbbbb;
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
                background-color: #121212;
                border-radius: 8px;
                border: 1px solid #3a3a3a;
            }
            """
        )

        pg.setConfigOptions(antialias=True)

    # ------------------------------------------------------------------
    # View menu handlers
    # ------------------------------------------------------------------
    def _on_toggle_physical(self, index: int, enabled: bool):
        self.manager.set_physical_enabled(index, enabled)
        self.plotter.on_channels_updated()
        self.sweep.on_channels_updated()

    # ------------------------------------------------------------------
    # Channels menu handlers
    # ------------------------------------------------------------------
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

        # Validate expression
        try:
            safe_eval_expression(
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
            name = f"Rel Trans Ch{num_idx + 1}/Ch{den_idx + 1}"

        cfg = ChannelConfig(
            name=name,
            kind="relative",
            unit="dB",
            rel_src_indices=(num_idx, den_idx),
        )
        self.manager.add_relative_channel(cfg)
        self.plotter.on_channels_updated()
        self.sweep.on_channels_updated()

    # ------------------------------------------------------------------
    # Sweep menu handler
    # ------------------------------------------------------------------
    def _on_edit_sweep_params(self):
        self.sweep.open_params_dialog(self)

    # ------------------------------------------------------------------
    # Environmental status polling
    # ------------------------------------------------------------------
    def _update_env_status(self):
        if self.daq is None:
            self.lbl_device_temp.setText("Device temperature: — °C")
            self.lbl_frontend_temp.setText("Frontend temperature: — °C")
            self.lbl_frontend_rh.setText("Humidity: — % RH")
            return

        try:
            t_board = self.daq.get_head_temperature_C()
            self.lbl_device_temp.setText(f"Device temperature: {t_board:.1f} °C")
        except Exception:
            self.lbl_device_temp.setText("Device temperature: — °C")

        try:
            t_front = self.daq.get_head_temperature_C()  # or separate API if exists
            self.lbl_frontend_temp.setText(
                f"Frontend temperature: {t_front:.1f} °C"
            )
        except Exception:
            self.lbl_frontend_temp.setText("Frontend temperature: — °C")

        try:
            h = self.daq.get_head_humidity()
            self.lbl_frontend_rh.setText(f"Humidity: {h:.1f} % RH")
        except Exception:
            self.lbl_frontend_rh.setText("Humidity: — % RH")

    # ------------------------------------------------------------------
    # Close handling
    # ------------------------------------------------------------------
    def closeEvent(self, ev: QtGui.QCloseEvent):
        self.env_timer.stop()
        try:
            self.plotter.set_active(False)
        except Exception:
            pass
        if self.daq is not None:
            try:
                self.daq.close()
            except Exception:
                pass
        super().closeEvent(ev)


# ----------------------------------------------------------------------
# Entry point
# ----------------------------------------------------------------------


def main():
    app = QtWidgets.QApplication(sys.argv)
    w = MainWindow()
    w.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
