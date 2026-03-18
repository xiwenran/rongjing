#!/bin/bash
cd /Users/xili

echo "正在同步代码到 GitHub..."
git add .

# 用当前时间作为提交说明
MSG="更新 $(date '+%Y-%m-%d %H:%M')"
git commit -m "$MSG"

git push

echo ""
echo "✅ 同步完成！GitHub 正在自动打包，10-15 分钟后可下载安装包。"
echo ""
echo "下载地址：https://github.com/xiwenran/-/actions"
echo ""
read -p "按回车键关闭窗口..."
