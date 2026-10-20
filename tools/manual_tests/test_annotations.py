"""Temporary visual test for AstroFrame Annotate."""

import sys
from pathlib import Path

from PySide6.QtGui import QGuiApplication, QImage

from astroframe.annotations import Annotation, render_annotated_image

app = QGuiApplication(sys.argv)

image_path = Path(input("Drag an astrophotograph here and press Return: ").strip().strip("'\""))

if not image_path.is_file():
    raise SystemExit(f"Image not found: {image_path}")

image = QImage(str(image_path))
if image.isNull():
    raise SystemExit("Could not open that image.")

w, h = image.width(), image.height()

annotations = [
    Annotation("Object A", w * 0.25, h * 0.35),
    Annotation("Object B", w * 0.55, h * 0.50),
    Annotation("Object C", w * 0.75, h * 0.70),
]

output = image_path.with_name(image_path.stem + "_annotated.png")

render_annotated_image(image_path, annotations, output)

print(f"\nAnnotated image saved to:\n{output}")
