#!/bin/bash
# auto_rebuild.sh —— 等转写进程结束后自动重建索引（让新入库的视频立刻可检索）
# 可随时手动执行；不触发 OCR，避免和 ASR 抢上传配额
ROOT="/Volumes/wd/开公司"
KB="$ROOT/kb"
BIN="$ROOT/tools/bin"
cd "$KB" || exit 1
PY="$KB/.venv/bin/python"

echo "[auto] 等待转写进程结束 ..."
while pgrep -f "python -u bin/transcribe[.]py asr" >/dev/null; do sleep 20; done
echo "[auto] 转写结束，重建视频块与索引"
"$PY" "$BIN/build_chunks.py"
"$PY" "$BIN/transcribe.py" notes | tail -1
"$PY" "$BIN/build_index.py" | grep -v jieba | tail -3
echo "[auto] 完成"
