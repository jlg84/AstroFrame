import sys

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

from .window import MainWindow


STYLE_SHEET = """
QWidget {
    background: #11141A;
    color: #EEF1F5;
    font-family: "Helvetica Neue", Arial, sans-serif;
    font-size: 13px;
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

QLabel#appTitle {
    font-size: 24px;
    font-weight: 700;
    color: #FFFFFF;
    padding: 4px 2px 0 2px;
}

QLabel#appSubtitle {
    color: #929AA8;
    font-size: 12px;
    padding: 0 2px 8px 2px;
}

QFrame#section {
    background: #1D222B;
    border: 1px solid #2C333E;
    border-radius: 10px;
}

QLabel#sectionHeading {
    color: #8F99A8;
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 1px;
}

QLabel#fileName {
    color: #F7F8FA;
    font-size: 13px;
    font-weight: 600;
}

QLabel#estimatedStatus {
    color: #E6B85C;
    font-size: 12px;
}

QLabel#verifiedStatus {
    color: #63D69A;
    font-size: 12px;
    font-weight: 600;
}

QLabel#solvingStatus {
    color: #6DA6FF;
    font-size: 12px;
}

QLabel#failedStatus {
    color: #FF7676;
    font-size: 12px;
    font-weight: 600;
}

QLabel#unknownStatus {
    color: #8D96A5;
    font-size: 12px;
}

QLabel#fieldLabel, QLabel#valueLabel {
    color: #D9DEE6;
    font-weight: 600;
}

QLabel#valueLabel {
    color: #FFFFFF;
}

QLabel#helpText {
    color: #8D96A5;
    font-size: 11px;
}

QPushButton {
    min-height: 20px;
    background: #2A303A;
    border: 1px solid #3A424F;
    border-radius: 7px;
    padding: 8px 10px;
    color: #F3F5F8;
}

QPushButton:hover {
    background: #343B47;
}

QPushButton:pressed {
    background: #222831;
}

QPushButton#primaryButton {
    background: #3B6EDB;
    border-color: #4B7CE4;
    font-weight: 600;
}

QPushButton#primaryButton:hover {
    background: #477AE3;
}

QPushButton#solveButton {
    background: #24644D;
    border-color: #347A61;
    font-weight: 600;
}

QPushButton#solveButton:hover {
    background: #2D755B;
}

QDoubleSpinBox {
    background: #12161C;
    border: 1px solid #39414D;
    border-radius: 6px;
    padding: 7px 8px;
    selection-background-color: #3B6EDB;
}

QCheckBox#rigToggle {
    background: #151A21;
    border: 1px solid #313946;
    border-radius: 8px;
    padding: 9px 10px;
    spacing: 10px;
    color: #E9EDF3;
}

QCheckBox#rigToggle:hover {
    border-color: #4A5565;
    background: #1A2028;
}

QCheckBox#rigToggle::indicator {
    width: 13px;
    height: 13px;
    border-radius: 4px;
    border: 1px solid #596474;
    background: #0F1318;
}

QSlider::groove:horizontal {
    height: 4px;
    background: #343B46;
    border-radius: 2px;
}

QSlider::sub-page:horizontal {
    background: #5D86E8;
    border-radius: 2px;
}

QSlider::handle:horizontal {
    width: 15px;
    margin: -6px 0;
    border-radius: 7px;
    background: #F4F6FA;
    border: 1px solid #7C8797;
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
    app.setApplicationName("AstroFrame")
    app.setOrganizationName("AstroFrame")
    app.setStyle("Fusion")
    app.setStyleSheet(STYLE_SHEET)

    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor("#11141A"))
    palette.setColor(QPalette.ColorRole.WindowText, QColor("#EEF1F5"))
    app.setPalette(palette)

    window = MainWindow()
    window.show()
    return app.exec()
