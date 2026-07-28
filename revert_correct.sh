#!/bin/bash
# 正确的撤回 GitHub 提交方法

echo "========================================="
echo "  撤回 GitHub 提交(保持本地不变)"
echo "========================================="
echo ""

# 方法: 创建一个反向提交推送到 GitHub
echo "步骤1: 备份当前代码状态"
git stash

echo "步骤2: 创建 revert 提交"
git revert HEAD --no-edit

echo "步骤3: 推送 revert 到 fund_project"
git push fund_project main

echo "步骤4: 恢复本地代码到最新状态"
# 回退到revert之前的状态
git reset --hard HEAD~1

# 恢复stash的更改(如果有)
git stash pop 2>/dev/null || true

echo ""
echo "✅ 完成!"
echo "   - GitHub fund_project 已回退"
echo "   - 本地代码保持最新状态"
echo ""
echo "验证本地状态:"
git log --oneline -3