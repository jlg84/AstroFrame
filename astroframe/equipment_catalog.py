from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CameraModel:
    key: str
    name: str
    sensor_width_mm: float
    sensor_height_mm: float


@dataclass(frozen=True)
class TelescopeModel:
    key: str
    name: str
    focal_length_mm: float


CAMERAS = (
    CameraModel("zwo_asi1600mm_pro", "ZWO ASI1600MM Pro", 17.69, 13.38),
    CameraModel("zwo_asi533mc_pro", "ZWO ASI533MC Pro", 11.31, 11.31),
    CameraModel("zwo_asi533mm_pro", "ZWO ASI533MM Pro", 11.31, 11.31),
    CameraModel("zwo_asi2600mc_pro", "ZWO ASI2600MC Pro", 23.50, 15.70),
    CameraModel("zwo_asi2600mm_pro", "ZWO ASI2600MM Pro", 23.50, 15.70),
    CameraModel("zwo_asi294mc_pro", "ZWO ASI294MC Pro", 19.10, 13.00),
    CameraModel("zwo_asi294mm_pro", "ZWO ASI294MM Pro", 19.10, 13.00),
    CameraModel("zwo_asi183mc_pro", "ZWO ASI183MC Pro", 13.20, 8.80),
    CameraModel("zwo_asi183mm_pro", "ZWO ASI183MM Pro", 13.20, 8.80),
    CameraModel("canon_r5", "Canon EOS R5", 36.00, 24.00),
    CameraModel("canon_r5ii", "Canon EOS R5 Mark II", 36.00, 24.00),
    CameraModel("canon_t3i", "Canon EOS 600D / Rebel T3i", 22.30, 14.90),
    CameraModel("sony_a7iv", "Sony α7 IV", 35.90, 23.90),
    CameraModel("nikon_z6ii", "Nikon Z6 II", 35.90, 23.90),
    CameraModel("seestar_s50_camera", "ZWO Seestar S50 (integrated camera)", 3.13, 5.57),
)

TELESCOPES = (
    TelescopeModel("seestar_s50", "ZWO Seestar S50 (integrated telescope)", 250.0),
    TelescopeModel("celestron_edgehd8", "Celestron EdgeHD 8", 2032.0),
    TelescopeModel("celestron_edgehd925", "Celestron EdgeHD 9.25", 2350.0),
    TelescopeModel("celestron_edgehd11", "Celestron EdgeHD 11", 2800.0),
    TelescopeModel("celestron_edgehd14", "Celestron EdgeHD 14", 3910.0),
    TelescopeModel("skywatcher_esprit80", "Sky-Watcher Esprit 80ED", 400.0),
    TelescopeModel("skywatcher_esprit100", "Sky-Watcher Esprit 100ED", 550.0),
    TelescopeModel("skywatcher_esprit120", "Sky-Watcher Esprit 120ED", 840.0),
    TelescopeModel("william_redcat51", "William Optics RedCat 51", 250.0),
    TelescopeModel("william_gt81", "William Optics Gran Turismo 81", 478.0),
    TelescopeModel("askar_fra400", "Askar FRA400", 400.0),
    TelescopeModel("askar_fra500", "Askar FRA500", 500.0),
    TelescopeModel("tak_fsq106", "Takahashi FSQ-106ED", 530.0),
)

CAMERA_BY_KEY = {item.key: item for item in CAMERAS}
TELESCOPE_BY_KEY = {item.key: item for item in TELESCOPES}

OPTICAL_MODIFIERS = (
    ("None / native", 1.0),
    ("0.63× reducer", 0.63),
    ("0.70× reducer", 0.70),
    ("0.72× reducer", 0.72),
    ("0.75× reducer", 0.75),
    ("0.80× reducer", 0.80),
    ("0.85× reducer", 0.85),
    ("1.40× extender", 1.40),
    ("2.00× Barlow / extender", 2.00),
)
