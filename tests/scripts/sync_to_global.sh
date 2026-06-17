#!/bin/bash
# 同步 trae-multi-agent 到全局（增量合并，跳过测试/缓存/大文件）
set -euo pipefail

SOURCE="/Users/wangwei/claw/.trae/skills/trae-multi-agent"
TARGET="/Users/wangwei/.trae/skills/trae-multi-agent"

# 排除规则：测试、缓存、日志、运行时数据、可视化大文件
EXCLUDES=(
  --exclude='__pycache__'
  --exclude='.git'
  --exclude='tests/'
  --exclude='.coverage'
  --exclude='coverage.json'
  --exclude='logs/'
  --exclude='*.pyc'
  --exclude='*.bak'
  --exclude='*.tmp'
  --exclude='.*.swp'
  --exclude='.trae/'
  --exclude='context/'
  --exclude='scripts/.trae/'
  --exclude='scripts/logs/'
  --exclude='workflows/'
  --exclude='registry/'
  --exclude='progress/'
  --exclude='docs/code-map-visualizer.html'
  --exclude='docs/task-visualizer.html'
  --exclude='docs/wechat_article_code_map.drawio'
  --exclude='docs/long_agent_architecture.*'
  --exclude='docs/*.svg'
  --exclude='docs/RELEASE_SUMMARY.md'
  --exclude='docs/wechat_article*.md'
  --exclude='docs/superpowers/'
  --exclude='docs/dev/pattern_*'
  --exclude='docs/spec/role-prompts/'
  --exclude='src/'
  --exclude='trae-agent'
)

echo "==> 同步源: $SOURCE"
echo "==> 同步到: $TARGET"
echo ""

# 确保目标目录存在
mkdir -p "$TARGET"

# 执行 rsync 增量合并
rsync -a --update "${EXCLUDES[@]}" "$SOURCE/" "$TARGET/"

echo ""
echo "==> 同步完成"
