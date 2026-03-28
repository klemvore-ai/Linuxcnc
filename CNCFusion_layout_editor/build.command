#!/bin/bash

# Change to the directory where this script is located
cd "$(dirname "$0")"
BASE_DIR="$(pwd)"

# Read version from version.txt if it exists, otherwise use default
if [ -f "version.txt" ]; then
    VERSION=$(cat version.txt | tr -d '\r\n')
    
    # Auto-increment the last number found in the version string
    if [[ $VERSION =~ (.*[^0-9])([0-9]+)$ ]]; then
        PREFIX="${BASH_REMATCH[1]}"
        NUM="${BASH_REMATCH[2]}"
        VERSION="${PREFIX}$((NUM + 1))"
    elif [[ $VERSION =~ ^([0-9]+)$ ]]; then
        VERSION=$((VERSION + 1))
    else
        VERSION="${VERSION}.1"
    fi
else
    VERSION="v1.0.0"
fi
echo "$VERSION" > version.txt

echo "Starting CNC Fusion Pro UI Editor build process (Version: $VERSION)..."

echo "Installing dependencies..."
pip3 install pyinstaller PyQt6 paramiko

echo "Clearing previous build cache..."
rm -rf build dist CNC_Fusion_UI_Editor.spec

echo "Building Mac application bundle..."
pyinstaller --clean --windowed --name "CNC_Fusion_UI_Editor" --icon="assets/icon.icns" --splash="assets/CNCFusionSPLASHBACKGROUND.png" --add-data "assets:assets" --noconfirm main.py

echo "Compressing application into a ZIP file..."
cd dist
ZIP_NAME="CNC_Fusion_UI_Editor_Mac_${VERSION}.zip"
zip -ry "$ZIP_NAME" CNC_Fusion_UI_Editor.app

echo "Applying custom icon to ZIP file..."
ICON_PATH="${BASE_DIR}/assets/icon.icns"
ZIP_PATH="$(pwd)/$ZIP_NAME"
if [ -f "$ICON_PATH" ]; then
    osascript -l JavaScript -e 'function run(argv) { ObjC.import("AppKit"); $.NSWorkspace.sharedWorkspace.setIconForFileOptions($.NSImage.alloc.initWithContentsOfFile(argv[0]), argv[1], 0); }' "$ICON_PATH" "$ZIP_PATH"
fi

read -p "Do you want to keep the unzipped .app bundle in the dist folder? (y/n) [n]: " KEEP_APP
KEEP_APP=${KEEP_APP:-n}
if [[ "$KEEP_APP" =~ ^[Nn]$ ]]; then
    echo "Cleaning up unzipped .app bundle..."
    rm -rf CNC_Fusion_UI_Editor.app
else
    echo "Keeping unzipped .app bundle."
fi

echo "Generating SHA256 checksum..."
shasum -a 256 "$ZIP_NAME" > "${ZIP_NAME}.sha256"
cat "${ZIP_NAME}.sha256"

echo -e "\nBuild and compression complete! You can find $ZIP_NAME and its checksum inside the 'dist' folder."

# Open the dist folder in Finder
open .