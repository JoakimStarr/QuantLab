#!/bin/bash
# 最简单的撤回方法

echo "正在创建并推送 revert 提交..."

# 保存当前提交哈希
CURRENT_HASH=$(git rev-parse HEAD)

# 创建revert提交
git revert HEAD --no-edit

# 推送到fund_project
git push fund_project main

echo ""
echo "✅ GitHub 已回退!"
echo ""
echo "现在恢复本地代码..."
git reset --hard $CURRENT_HASH

echo ""
echo "✅ 完成! 本地代码已恢复"
git log --oneline -3