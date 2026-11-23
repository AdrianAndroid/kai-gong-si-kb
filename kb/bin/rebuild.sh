#!/bin/bash
# rebuild.sh —— 一键重建知识库索引（新资料入库后跑这个）
# 用法：bash kb/bin/rebuild.sh
set -e
KB="/Volumes/wd/开公司/kb"
cd "$KB"
PY="$KB/.venv/bin/python"

echo "① 解析文档 ...";  $PY bin/prepare_docs.py  | tail -3 || true
echo "② 视频切块 ...";  $PY bin/build_chunks.py  | tail -2
echo "③ 生成笔记 ...";  $PY bin/transcribe.py notes | tail -2
echo "④ 重建索引 ...";  $PY bin/build_index.py  | grep -v jieba | tail -4
echo "完成。直接提问： bash $KB/bin/kb.sh \"你的问题\""
