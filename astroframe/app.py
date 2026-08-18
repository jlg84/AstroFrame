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

QLabel#appTitle {
    font-size: 30px;
    font-weight: 700;
    color: #FFFFFF;
    padding: 5px 3px 0 3px;
}

QLabel#appSubtitle {
    color: #98A2B1;
    font-size: 13px;
    padding: 0 3px 14px 3px;
}

QFrame#section {
    background: #1C222B;
    border: 2px solid #394352;
    border-radius: 13px;
}

QLabel#sectionHeading {
    color: #F4F7FB;
    font-size: 14px;
    font-weight: 800;
    letter-spacing: 0.5px;
    padding-bottom: 5px;
}

QLabel#workflowHeading {
    color: #7FB0FF;
    font-size: 13px;
    font-weight: 800;
    letter-spacing: 1px;
    padding: 10px 3px 2px 3px;
}
QLabel#resultsHeading {
    color: #78D6A6;
    font-size: 13px;
    font-weight: 800;
    letter-spacing: 1px;
    padding: 18px 3px 2px 3px;
}
QLabel#setupHeading {
    color: #AEB8C6;
    font-size: 12px;
    font-weight: 800;
    letter-spacing: 1px;
    padding: 18px 3px 2px 3px;
}

QScrollArea#collectionsScroll {
    background: transparent;
    border: none;
}

QScrollArea#collectionsScroll > QWidget > QWidget {
    background: transparent;
}

QLabel#collectionsSummary {
    background: transparent;
    color: #D9DEE6;
    padding: 0 3px 2px 0;
}

QScrollArea#collectionsScroll QScrollBar:vertical {
    background: #171B22;
    width: 8px;
    margin: 0;
}

QScrollArea#collectionsScroll QScrollBar::handle:vertical {
    background: #4A5565;
    min-height: 28px;
    border-radius: 4px;
}

QScrollArea#collectionsScroll QScrollBar::add-line:vertical,
QScrollArea#collectionsScroll QScrollBar::sub-line:vertical {
    height: 0;
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

QLabel#externalStatus {
    color: #E6B85C;
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
    color: #B7C0CC;
    font-size: 12px;
}

QPushButton {
    min-height: 26px;
    background: #2A303A;
    border: 1px solid #3A424F;
    border-radius: 7px;
    padding: 10px 12px;
    color: #F3F5F8;
}

QPushButton:hover {
    background: #343C48;
    border-color: #505B6B;
}

QPushButton:pressed {
    background: #222831;
}

QPushButton:focus {
    border-color: #668FEA;
}

QPushButton:disabled {
    background: #20252D;
    border-color: #2B323D;
    color: #687281;
}


QFrame#rigCard {
    background: #171C23;
    border: 1px solid #3A424F;
    border-radius: 10px;
}

QFrame#selectedRigCard {
    background: #192A42;
    border: 2px solid #4B86F1;
    border-radius: 10px;
}

QFrame#recommendedRigCard {
    background: #1A1F27;
    border: 1px solid #586272;
    border-radius: 10px;
}

QPushButton#selectedRigButton {
    background: #315FAF;
    border-color: #5A91F2;
    font-weight: 700;
}

QPushButton#selectedRigButton:hover {
    background: #3A6DC2;
}

QPushButton#recommendedRigButton {
    background: #262D37;
    border-color: #586272;
    font-weight: 600;
}

QPushButton#recommendedRigButton:hover {
    background: #303844;
}

QPushButton#alternativeTargetButton {
    background: #242A33;
    border-color: #3A424F;
    padding: 6px 8px;
}

QPushButton#alternativeTargetButton:hover {
    background: #2E3540;
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

QComboBox, QLineEdit, QSpinBox, QDateEdit {
    background: #12161C;
    border: 1px solid #39414D;
    border-radius: 6px;
    padding: 6px 8px;
    selection-background-color: #3B6EDB;
}

QComboBox:hover, QLineEdit:hover, QSpinBox:hover, QDateEdit:hover, QDoubleSpinBox:hover {
    border-color: #505B6B;
}

QComboBox:focus, QLineEdit:focus, QSpinBox:focus, QDateEdit:focus, QDoubleSpinBox:focus {
    border-color: #668FEA;
}

QDoubleSpinBox {
    background: #12161C;
    border: 1px solid #39414D;
    border-radius: 6px;
    padding: 7px 8px;
    selection-background-color: #3B6EDB;
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

QScrollArea#sidebarScroll QScrollBar:vertical {
    background: #171B22;
    width: 9px;
    margin: 2px 1px;
}

QScrollArea#sidebarScroll QScrollBar::handle:vertical {
    background: #3B4553;
    min-height: 34px;
    border-radius: 4px;
}

QScrollArea#sidebarScroll QScrollBar::handle:vertical:hover {
    background: #4C5868;
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
