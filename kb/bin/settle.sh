#!/bin/bash
# settle.sh —— ASR 跑完后自动：生成视频块 → 笔记 → 重建索引 → 接着跑 OCR
# 由 agent 在后台启动；也可手动执行以把流程推到下一阶段
KB="/Volumes/wd/开公司/kb"
cd "$KB" || exit 1
PY="$KB/.venv/bin/python"

echo "[settle] 等待 ASR 进程结束 ..."
while pgrep -f "transcribe[.]py asr" >/dev/null; do sleep 30; done
echo "[settle] ASR 进程已结束，开始构建"

$PY bin/build_chunks.py
$PY bin/transcribe.py notes
$PY bin/build_index.py

echo "[settle] 索引已更新，启动 OCR 长尾任务"
$PY bin/transcribe.py ocr --workers 3
