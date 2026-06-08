#!/bin/bash
# 生成可双击的“打包融景启动器.app”。启动器会打开终端并自动运行 bash 打包脚本。

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
LAUNCHER="$PROJECT_DIR/打包融景启动器.app"
MACOS_DIR="$LAUNCHER/Contents/MacOS"

rm -rf "$LAUNCHER"
mkdir -p "$MACOS_DIR"

cat > "$LAUNCHER/Contents/Info.plist" <<'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleExecutable</key>
  <string>launcher</string>
  <key>CFBundleIdentifier</key>
  <string>com.rongjing.package-launcher</string>
  <key>CFBundleName</key>
  <string>打包融景启动器</string>
  <key>CFBundlePackageType</key>
  <string>APPL</string>
  <key>LSMinimumSystemVersion</key>
  <string>10.13</string>
</dict>
</plist>
EOF

cat > "$MACOS_DIR/launcher" <<'EOF'
#!/bin/bash
set -e

PROJECT_DIR="$(cd "$(dirname "$0")/../../.." && pwd)"
TMP_SCRIPT="/tmp/rongjing-package.command"

cat > "$TMP_SCRIPT" <<INNER
#!/bin/bash
set -e
cd "$PROJECT_DIR"
/bin/bash ./scripts/package_rongjing.sh
echo
echo "[进程已完成]"
INNER

chmod +x "$TMP_SCRIPT"
open -a Terminal "$TMP_SCRIPT"
EOF

chmod +x "$MACOS_DIR/launcher"
xattr -cr "$LAUNCHER" 2>/dev/null || true

echo "已生成：$LAUNCHER"
