#!/bin/bash
set -e
cd "$(dirname "$0")"
python3 -m venv .venv-build
source .venv-build/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt pyinstaller
rm -rf build dist
pyinstaller --clean --noconfirm AstroFrame.spec
echo
echo "Built: $(pwd)/dist/AstroFrame.app"
echo "Double-click dist/AstroFrame.app to test it."
