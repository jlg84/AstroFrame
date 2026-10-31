"""Convert TIFF images into RGB arrays suitable for Qt display."""

from pathlib import Path

import numpy as np
import tifffile


def load_tiff_rgb(path: str | Path) -> np.ndarray:
    """Return an 8-bit RGB array without modifying the source TIFF."""
    image = tifffile.imread(str(path))

    if image.ndim != 3 or image.shape[-1] not in (3, 4):
        raise ValueError(
            f"Expected an RGB or RGBA TIFF; got shape {image.shape}"
        )

    image = image[..., :3]

    if image.dtype == np.uint8:
        return np.ascontiguousarray(image)

    if image.dtype == np.uint16:
        # Preserve the intensity relationships of an already-stretched
        # astrophotograph, rather than independently stretching channels.
        return np.ascontiguousarray((image / 257).astype(np.uint8))

    if np.issubdtype(image.dtype, np.floating):
        image = np.nan_to_num(image, nan=0.0, posinf=1.0, neginf=0.0)
        return np.ascontiguousarray(
            np.round(np.clip(image, 0.0, 1.0) * 255).astype(np.uint8)
        )

    raise ValueError(f"Unsupported TIFF pixel type: {image.dtype}")
