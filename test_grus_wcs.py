from pathlib import Path
from astroframe.plate_solve import ASTAPSolver

image = Path.home() / "Desktop/Large JPEG/Grus Two Night Combi-LargeJPEG.jpg"
output = Path.home() / "Desktop/Grus-test.wcs"

print("Starting ASTAP plate solve...", flush=True)

solution = ASTAPSolver().solve(
    image,
    image_width_px=2375,
    image_height_px=2400,
    estimated_width_deg=0.3927302134,
    ra_hours=349.7442897389 / 15,
    dec_deg=-42.30001410126,
    search_radius_deg=2.0,
    wcs_output_path=output,
)

print("Solved successfully!", flush=True)
print(f"WCS saved to: {output}", flush=True)
print(f"File size: {output.stat().st_size} bytes", flush=True)
