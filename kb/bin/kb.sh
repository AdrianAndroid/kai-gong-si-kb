#!/bin/bash
# kb.sh —— 知识库统一入口（避免每次都记 venv 路径）
# 用法：kb.sh "问题" [--k 8] [--course 财税] [--json]
KB="/Volumes/wd/开公司/kb"
exec "$KB/.venv/bin/python" "$KB/bin/ask.py" "$@"
