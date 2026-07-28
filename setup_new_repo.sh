#!/bin/bash
# QuantLab - 新仓库设置脚本

echo "========================================="
echo "  QuantLab - GitHub仓库设置"
echo "========================================="
echo ""

# 提示用户输入仓库URL
echo "请先在GitHub上创建新仓库:"
echo "  https://github.com/new"
echo ""
echo "创建后,请输入您的仓库SSH地址 (例如: git@github.com:JoakimStarr/quantlab.git)"
read -p "仓库地址: " REPO_URL

if [ -z "$REPO_URL" ]; then
    echo "错误: 仓库地址不能为空"
    exit 1
fi

echo ""
echo "正在添加远程仓库..."
git remote add origin "$REPO_URL"

echo "正在推送代码到GitHub..."
git push -u origin main

if [ $? -eq 0 ]; then
    echo ""
    echo "✅ 成功! 您的QuantLab项目已推送到:"
    echo "   $REPO_URL"
    echo ""
    echo "🎉 项目已成功上传!"
else
    echo ""
    echo "❌ 推送失败,请检查仓库地址是否正确"
    git remote remove origin
fi