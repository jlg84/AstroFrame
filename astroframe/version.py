"""AstroFrame application version."""

import sys
from pathlib import Path


def get_version() -> str:
    """Read the version in development or a packaged application."""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
    version_file = base / "VERSION"
    return version_file.read_text(encoding="utf-8").strip()


def window_title() -> str:
    return f"AstroFrame {get_version()}"
