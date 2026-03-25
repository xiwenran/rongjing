#!/bin/bash
# 打包为 macOS .app + .dmg（便于分发给其他 Mac 用户）
# 运行方式：bash build_app.sh（在仓库根目录执行）
#
# 注意：在哪种 Mac 上打包，就只能在同架构 Mac 上运行：
#   Apple Silicon (M1/M2/M3) → 生成的包只能在 M 系列 Mac 运行
#   Intel Mac               → 生成的包只能在 Intel Mac 运行
#   如需跨架构，需在目标架构的 Mac 上分别打包。

set -e
cd "$(dirname "$0")"

APP_NAME="融景"
ARCH=$(uname -m)   # arm64 = Apple Silicon, x86_64 = Intel

echo "=========================================="
echo "  PPT 场景合成工具 — 打包脚本"
echo "  当前架构: $ARCH"
echo "=========================================="
echo ""
echo "▶ 步骤 1/3  PyInstaller 打包 .app ..."

# 注入构建标识（git 短 SHA）
BUILD=$(git rev-parse --short HEAD 2>/dev/null || echo "unknown")
echo "BUILD = \"${BUILD}\"" > _build_info.py
echo "  构建标识: $BUILD"

pyinstaller \
  --windowed \
  --name "$APP_NAME" \
  --add-data "templates:templates" \
  --hidden-import "PIL._tkinter_finder" \
  --hidden-import "av" \
  --collect-all "av" \
  --noconfirm \
  --add-data "_build_info.py:." \
  main.py

echo ""
echo "▶ 步骤 2/3  移除旧的隔离属性（本机测试用）..."
xattr -cr "dist/$APP_NAME.app" 2>/dev/null || true

echo ""
echo "▶ 步骤 3/3  打包为 .dmg ..."

DMG_NAME="${APP_NAME}_${ARCH}.dmg"
DMG_TMP="dist/dmg_tmp"
DMG_OUT="dist/$DMG_NAME"

# 清理旧文件
rm -rf "$DMG_TMP" "$DMG_OUT"
mkdir -p "$DMG_TMP"

# 复制 .app 到临时目录
cp -r "dist/$APP_NAME.app" "$DMG_TMP/"

# 创建 .dmg（使用 macOS 内置 hdiutil，无需额外工具）
hdiutil create \
  -volname "$APP_NAME" \
  -srcfolder "$DMG_TMP" \
  -ov \
  -format UDZO \
  "$DMG_OUT"

# 清理临时目录
rm -rf "$DMG_TMP"

echo ""
echo "=========================================="
echo "  ✅ 打包完成！"
echo ""
echo "  .app 路径：dist/$APP_NAME.app"
echo "  .dmg 路径：dist/$DMG_NAME"
echo ""
echo "  发给其他 Mac 用户时："
echo "  1. 发送 $DMG_NAME 文件"
echo "  2. 对方双击挂载 DMG，将 .app 拖入「应用程序」"
echo "  3. 首次打开：右键点击 .app → 选择「打开」→ 点击「打开」"
echo "     （系统提示「无法验证开发者」时必须用右键，不能直接双击）"
echo ""
echo "  当前架构 $ARCH — 此包只能在同架构 Mac 上运行"
echo "  若需在 Intel Mac 运行，请在 Intel Mac 上重新打包"
echo "=========================================="
