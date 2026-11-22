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
  --hidden-import duckdb_extension_httpfs \
  --osx-bundle-identifier com.bigsheet.app \
  main.py

APP=dist/BigSheet.app
# The encryption component carries DuckDB's own signature, so it must be copied
# unchanged (the packager would re-sign it and break it). Real files go in
# Resources, with a link from Frameworks where the app looks for them.
EXT_SRC=$(./.venv/bin/python -c "import duckdb_extension_httpfs, pathlib; print(pathlib.Path(duckdb_extension_httpfs.__file__).parent / 'extensions')")
mkdir -p "$APP/Contents/Resources/duckdb_extension_httpfs" "$APP/Contents/Frameworks/duckdb_extension_httpfs"
cp -R "$EXT_SRC" "$APP/Contents/Resources/duckdb_extension_httpfs/"
ln -sfn ../../Resources/duckdb_extension_httpfs/extensions "$APP/Contents/Frameworks/duckdb_extension_httpfs/extensions"

# Let Finder open .bigsheet files with the app.
PLIST="$APP/Contents/Info.plist"
/usr/libexec/PlistBuddy -c "Add :CFBundleDocumentTypes array" "$PLIST" 2>/dev/null || true
/usr/libexec/PlistBuddy -c "Add :CFBundleDocumentTypes:0:CFBundleTypeName string 'BigSheet workbook'" "$PLIST" 2>/dev/null || true
/usr/libexec/PlistBuddy -c "Add :CFBundleDocumentTypes:0:CFBundleTypeExtensions array" "$PLIST" 2>/dev/null || true
/usr/libexec/PlistBuddy -c "Add :CFBundleDocumentTypes:0:CFBundleTypeExtensions:0 string bigsheet" "$PLIST" 2>/dev/null || true
/usr/libexec/PlistBuddy -c "Add :CFBundleDocumentTypes:0:CFBundleTypeRole string Editor" "$PLIST" 2>/dev/null || true

# Re-seal the app after the changes above (ad-hoc signature, not notarized).
codesign --force --sign - "$APP" || echo "Note: could not re-sign the app; it will still run after right-click, Open."
echo
echo "Built $APP. Drag it into Applications."
echo "First launch: right-click the app and choose Open (it is not notarized by Apple)."
echo "Check it with:  $APP/Contents/MacOS/BigSheet --self-test"
