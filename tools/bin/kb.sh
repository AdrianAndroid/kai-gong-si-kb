#!/bin/bash
# kb.sh —— 知识库统一入口（避免每次都记 venv 路径）
# 用法：kb.sh "问题" [--k 8] [--course 财税] [--json]
ROOT="/Volumes/wd/开公司"
KB="$ROOT/kb"
BIN="$ROOT/tools/bin"
exec "$KB/.venv/bin/python" "$BIN/ask.py" "$@"
