#!/bin/bash
set -e

echo "=========================================="
echo "  PPT 场景合成工具 — 一键发布"
echo "=========================================="
echo ""

# ── 步骤 1：本地打包 Mac 版本 ──
echo "▶ 步骤 1/3  打包 Mac 版本..."
cd /Users/xili/xhsbj
bash build_app.sh
echo ""

# ── 步骤 2：同步代码到 GitHub（触发 Windows 自动打包）──
echo "▶ 步骤 2/3  同步代码到 GitHub..."
cd /Users/xili
git add .
MSG="更新 $(date '+%Y-%m-%d %H:%M')"
git commit -m "$MSG" || echo "（没有新代码变更，跳过提交）"
git push
echo ""

# ── 步骤 3：上传 Mac DMG 到 GitHub Releases ──
echo "▶ 步骤 3/3  上传 Mac 安装包到 GitHub Releases..."
cd /Users/xili/xhsbj
ARCH=$(uname -m)
DMG="dist/PPT场景合成工具_${ARCH}.dmg"
TAG="v$(date '+%Y%m%d-%H%M')"

gh release create "$TAG" "$DMG" \
  --repo xiwenran/- \
  --title "版本 $TAG" \
  --notes "Mac (${ARCH}) 安装包。Windows 版本请在 Actions 页面下载。"

echo ""
echo "=========================================="
echo "  ✅ 全部完成！"
echo ""
echo "  Mac 下载链接："
echo "  https://github.com/xiwenran/-/releases/latest"
echo ""
echo "  Windows 打包进度（10-15分钟后完成）："
echo "  https://github.com/xiwenran/-/actions"
echo "=========================================="
echo ""
read -p "按回车键关闭窗口..."
