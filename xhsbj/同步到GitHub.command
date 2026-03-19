#!/bin/bash

# 出错时显示友好提示，不直接退出
set -eE
trap 'echo ""; echo "❌ 发生错误（第 $LINENO 行），请截图发给开发者。"; read -p "按回车键关闭窗口..."' ERR

echo "=========================================="
echo "  融景 — 一键发布"
echo "=========================================="
echo ""

# ── 步骤 1：本地打包 Mac 版本 ──
echo "▶ 步骤 1/3  打包 Mac 版本..."
cd /Users/xili/xhsbj
bash build_app.sh
echo ""

# ── 步骤 2：创建 GitHub Release 并上传 Mac DMG ──
# 必须在 push 之前创建 Release，这样 Windows Actions 一启动就能找到 Release
echo "▶ 步骤 2/3  创建 GitHub Release 并上传 Mac 安装包..."
cd /Users/xili/xhsbj
ARCH=$(uname -m)
DMG="dist/融景_${ARCH}.dmg"
TAG="v$(date '+%Y%m%d-%H%M')"

# 清理同名 draft（上次脚本中途失败时可能留下）
if gh release view "$TAG" --repo xiwenran/- &>/dev/null; then
  echo "  ⚠️  同名 Release 已存在，删除后重建..."
  gh release delete "$TAG" --repo xiwenran/- --yes --cleanup-tag
fi

gh release create "$TAG" "$DMG" \
  --repo xiwenran/- \
  --title "版本 $TAG" \
  --notes "Mac (${ARCH}) 安装包已附于本 Release。Windows 版本由 GitHub Actions 自动打包，约 10-15 分钟后自动附加到本 Release，刷新页面即可下载。"
echo ""

# ── 步骤 3：同步代码到 GitHub（触发 Windows 自动打包）──
# Release 已存在，Windows Actions 运行结束后会自动 attach ZIP
echo "▶ 步骤 3/3  同步代码到 GitHub（触发 Windows 自动打包）..."
cd /Users/xili
# 写入 release tag 文件，确保每次都有新 commit（避免 no-op push 导致 Actions 不触发）
echo "$TAG" > RELEASE_TAG
git add xhsbj/ .github/ README.md RELEASE_TAG
MSG="更新 $(date '+%Y-%m-%d %H:%M')"
git commit -m "$MSG"
git push
echo ""

echo "=========================================="
echo "  ✅ 全部完成！"
echo ""
echo "  Mac 下载链接："
echo "  https://github.com/xiwenran/-/releases/latest"
echo ""
echo "  Windows 包将在 10-15 分钟后自动附加到同一 Release 页面"
echo "  刷新上方链接即可看到 Windows 下载"
echo "=========================================="
echo ""
read -p "按回车键关闭窗口..."
