#!/bin/bash
# Build DMG for Mac

set -e

APP_NAME="MonkeyBrain"
VERSION="1.0.0"
DMG_NAME="${APP_NAME}-${VERSION}.dmg"

echo "Building ${DMG_NAME}..."

# Create DMG directory
mkdir -p dist/dmg

# Copy files
cp -r src dist/dmg/
cp main.py dist/dmg/
cp README.md dist/dmg/
cp pyproject.toml dist/dmg/
cp uv.lock dist/dmg/
cp install_agentos.py dist/dmg/
cp -r Constitutions dist/dmg/

# Create DMG (requires create-dmg or hdiutil)
if command -v hdiutil &> /dev/null; then
    hdiutil create -volname "${APP_NAME}" \
        -srcfolder dist/dmg \
        -ov \
        -format UDZO \
        dist/${DMG_NAME}
    echo "DMG created: dist/${DMG_NAME}"
else
    echo "hdiutil not found. Creating zip instead..."
    cd dist && zip -r ${DMG_NAME%.dmg}.zip dmg/
    echo "ZIP created: dist/${DMG_NAME%.dmg}.zip"
fi
