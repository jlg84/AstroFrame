from astroframe.app import run
from astroframe.equipment_integration import install_equipment_library_integration
from astroframe.window import MainWindow

install_equipment_library_integration(MainWindow)

if __name__ == "__main__":
    raise SystemExit(run())
