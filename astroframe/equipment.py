from dataclasses import dataclass
from math import atan, degrees


@dataclass(frozen=True)
class Rig:
    key: str
    name: str
    sensor_width_mm: float
    sensor_height_mm: float
    focal_length_mm: float
    colour: str

    @property
    def fov_width_deg(self) -> float:
        return degrees(2 * atan(self.sensor_width_mm / (2 * self.focal_length_mm)))

    @property
    def fov_height_deg(self) -> float:
        return degrees(2 * atan(self.sensor_height_mm / (2 * self.focal_length_mm)))


RIGS = (
    Rig(
        key="asi1600_442",
        name="ASI1600MM Pro + 442 mm",
        sensor_width_mm=17.69,
        sensor_height_mm=13.38,
        focal_length_mm=442.0,
        colour="#FFD83D",
    ),
    Rig(
        key="asi533_1448",
        name="ASI533MC Pro + 1448 mm",
        sensor_width_mm=11.31,
        sensor_height_mm=11.31,
        focal_length_mm=1448.0,
        colour="#42C8FF",
    ),
)


USER_RIG_COLOURS = (
    # Twelve deliberately separated colours for dark-sky imagery.  A setup's
    # assigned colour is stored with the setup record, so adding/removing other
    # rigs does not make familiar frame colours unexpectedly change.
    "#FFD83D", "#42C8FF", "#C084FC", "#34D399",
    "#FB7185", "#F59E0B", "#2DD4BF", "#F472B6",
    "#A3E635", "#818CF8", "#FB923C", "#E879F9",
)
