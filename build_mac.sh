#!/bin/bash
# Builds dist/BigSheet.app, a standalone app that does not need Python installed.
# Build on the kind of Mac you will run it on (Apple Silicon or Intel).
set -e
cd "$(dirname "$0")"
PY=${PY:-python3}
[ -d .venv ] || "$PY" -m venv .venv
./.venv/bin/python -m pip install --upgrade pip
./.venv/bin/python -m pip install -r requirements.txt "pyinstaller>=6.10"
./.venv/bin/python -m PyInstaller --noconfirm --clean --windowed --name BigSheet \
  --collect-data duckdb_extension_httpfs \
  --osx-bundle-identifier com.bigsheet.app \
  main.py
# Let Finder open .bigsheet files with the app.
/usr/libexec/PlistBuddy -c "Add :CFBundleDocumentTypes array" dist/BigSheet.app/Contents/Info.plist 2>/dev/null || true
/usr/libexec/PlistBuddy -c "Add :CFBundleDocumentTypes:0:CFBundleTypeName string 'BigSheet workbook'" dist/BigSheet.app/Contents/Info.plist 2>/dev/null || true
/usr/libexec/PlistBuddy -c "Add :CFBundleDocumentTypes:0:CFBundleTypeExtensions array" dist/BigSheet.app/Contents/Info.plist 2>/dev/null || true
/usr/libexec/PlistBuddy -c "Add :CFBundleDocumentTypes:0:CFBundleTypeExtensions:0 string bigsheet" dist/BigSheet.app/Contents/Info.plist 2>/dev/null || true
/usr/libexec/PlistBuddy -c "Add :CFBundleDocumentTypes:0:CFBundleTypeRole string Editor" dist/BigSheet.app/Contents/Info.plist 2>/dev/null || true
codesign --force --deep --sign - dist/BigSheet.app 2>/dev/null || true
echo
echo "Built dist/BigSheet.app. Drag it into Applications."
echo "First launch: right-click the app and choose Open (it is not notarized by Apple)."
