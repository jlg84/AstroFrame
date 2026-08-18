import sys
from pathlib import Path

from PySide6.QtGui import QColor, QIcon, QPalette
from PySide6.QtWidgets import QApplication

from .flexible_import_dialog import run_flexible_collection_import_dialog
from .rc1_onboarding import install_rc1_onboarding_fixes
from .window import MainWindow


STYLE_SHEET = """
QWidget {
    background: #11141A;
    color: #EEF1F5;
    font-family: "Helvetica Neue", Arial, sans-serif;
    font-size: 14px;
}

QWidget#sidebar {
    background: #171B22;
}

QScrollArea#sidebarScroll {
    background: #171B22;
    border-right: 1px solid #2A303A;
}

QScrollArea#sidebarScroll > QWidget > QWidget {
    background: #171B22;
}

QLabel#sectionTitle {
    color: #FFFFFF;
    font-size: 15px;
    font-weight: 600;
    padding-top: 8px;
}

QLabel#helpText {
    color: #AEB6C2;
    font-size: 12px;
}

QLabel#mutedText {
    color: #8F98A6;
}

QLabel#successText {
    color: #7FD6A4;
}

QLabel#warningText {
    color: #F3C969;
}

QLabel#errorText {
    color: #F08A8A;
}

QPushButton {
    background: #252B34;
    color: #EEF1F5;
    border: 1px solid #414A57;
    border-radius: 5px;
    padding: 6px 10px;
}

QPushButton:hover {
    background: #303744;
    border-color: #566273;
}

QPushButton:pressed {
    background: #1F242C;
}

QPushButton:disabled {
    color: #6F7783;
    background: #1B1F26;
    border-color: #2A303A;
}

QPushButton#primaryButton {
    background: #2D5F8B;
    border-color: #3D79A9;
    color: #FFFFFF;
    font-weight: 600;
}

QPushButton#primaryButton:hover {
    background: #3671A2;
}

QPushButton#dangerButton {
    background: #5A2B30;
    border-color: #784047;
}

QLineEdit,
QSpinBox,
QDoubleSpinBox,
QComboBox,
QDateEdit,
QTimeEdit,
QDateTimeEdit {
    background: #1A1F27;
    color: #EEF1F5;
    border: 1px solid #3A424E;
    border-radius: 4px;
    padding: 5px;
    selection-background-color: #3D6F99;
}

QLineEdit:focus,
QSpinBox:focus,
QDoubleSpinBox:focus,
QComboBox:focus,
QDateEdit:focus,
QTimeEdit:focus,
QDateTimeEdit:focus {
    border-color: #4C87B7;
}

QComboBox QAbstractItemView {
    background: #1A1F27;
    color: #EEF1F5;
    selection-background-color: #315A7D;
    border: 1px solid #3A424E;
}

QCheckBox,
QRadioButton {
    color: #EEF1F5;
    spacing: 7px;
}

QGroupBox {
    border: 1px solid #303744;
    border-radius: 6px;
    margin-top: 12px;
    padding-top: 10px;
    font-weight: 600;
}

QGroupBox::title {
    subcontrol-origin: margin;
    left: 10px;
    padding: 0 4px;
}

QTabWidget::pane {
    border: 1px solid #303744;
    background: #11141A;
}

QTabBar::tab {
    background: #1A1F27;
    color: #AEB6C2;
    border: 1px solid #303744;
    padding: 7px 12px;
}

QTabBar::tab:selected {
    background: #252B34;
    color: #FFFFFF;
}

QTableView,
QTreeView,
QListView,
QTextEdit,
QPlainTextEdit {
    background: #151A21;
    alternate-background-color: #191F27;
    color: #EEF1F5;
    border: 1px solid #303744;
    gridline-color: #2A303A;
    selection-background-color: #315A7D;
    selection-color: #FFFFFF;
}

QHeaderView::section {
    background: #202630;
    color: #DDE2E8;
    border: 0;
    border-right: 1px solid #303744;
    border-bottom: 1px solid #303744;
    padding: 6px;
}

QMenuBar {
    background: #171B22;
    color: #EEF1F5;
}

QMenuBar::item:selected {
    background: #2A303A;
}

QMenu {
    background: #1A1F27;
    color: #EEF1F5;
    border: 1px solid #3A424E;
}

QMenu::item:selected {
    background: #315A7D;
}

QScrollBar:vertical {
    background: #151A21;
    width: 12px;
    margin: 0;
}

QScrollBar::handle:vertical {
    background: #3A424E;
    min-height: 24px;
    border-radius: 5px;
}

QScrollBar::handle:vertical:hover {
    background: #4A5564;
}

QScrollBar::add-line:vertical,
QScrollBar::sub-line:vertical {
    height: 0;
}

QScrollBar:horizontal {
    background: #151A21;
    height: 12px;
    margin: 0;
}

QScrollBar::handle:horizontal {
    background: #3A424E;
    min-width: 24px;
    border-radius: 5px;
}

QScrollBar::handle:horizontal:hover {
    background: #4A5564;
}

QScrollBar::add-line:horizontal,
QScrollBar::sub-line:horizontal {
    width: 0;
}

QProgressBar {
    background: #1A1F27;
    color: #EEF1F5;
    border: 1px solid #3A424E;
    border-radius: 4px;
    text-align: center;
}

QProgressBar::chunk {
    background: #3D79A9;
    border-radius: 3px;
}

QSlider::groove:horizontal {
    background: #2A303A;
    height: 5px;
    border-radius: 2px;
}

QSlider::handle:horizontal {
    background: #C8D0DA;
    border: 1px solid #5A6573;
    width: 15px;
    margin: -5px 0;
    border-radius: 7px;
}

QSlider::handle:horizontal:hover {
    background: #FFFFFF;
}

QSplitter::handle {
    background: #242A33;
}

QStatusBar {
    background: #171B22;
    color: #AEB6C2;
}

QScrollArea#sidebarScroll QScrollBar:vertical {
    background: #171B22;
    width: 10px;
}

QScrollArea#sidebarScroll QScrollBar::handle:vertical {
    background: #414A57;
    min-height: 24px;
    border-radius: 4px;
}

QScrollArea#sidebarScroll QScrollBar::add-line:vertical,
QScrollArea#sidebarScroll QScrollBar::sub-line:vertical {
    height: 0;
}

QToolTip {
    background: #252B34;
    color: #F0F2F5;
    border: 1px solid #414A57;
    padding: 5px;
}
"""


def run() -> int:
    app = QApplication(sys.argv)

    base_dir = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
    icon_path = base_dir / "assets" / "AstroFrame_1024.png"
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))

    app.setApplicationName("AstroFrame")
    app.setOrganizationName("AstroFrame")
    app.setStyle("Fusion")
    app.setStyleSheet(STYLE_SHEET)

    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor("#11141A"))
    palette.setColor(QPalette.ColorRole.WindowText, QColor("#EEF1F5"))
    app.setPalette(palette)

    # Keep the mature MainWindow intact while applying the release fixes
    # validated during RC1 acceptance testing.
    MainWindow._flexible_collection_import_dialog = run_flexible_collection_import_dialog
    install_rc1_onboarding_fixes(MainWindow)

    window = MainWindow()
    window.setWindowTitle("AstroFrame 1.1.0 Beta 1")
    window.show()
    return app.exec()
