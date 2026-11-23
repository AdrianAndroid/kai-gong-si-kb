#!/bin/bash
# 双击即可用：终端里循环提问，输入 q 退出
cd "$(dirname "$0")/kb" || exit 1
export PATH="/usr/bin:/bin:/usr/local/bin:$PATH"
clear
printf '\033[1m%s\033[0m\n' "  开公司知识库 · 随问随答"
printf '%s\n\n' "  资料：8 门课程 + 6 大模块合集（文档 + 121 集视频转写）"
printf '%s\n\n' "  直接提问，回车出结果；输入 q 退出"
while true; do
  printf '\033[36m❯ \033[0m'
  IFS= read -r q
  [ -z "$q" ] && continue
  case "$q" in q|Q|quit|exit) echo "再见"; break;; esac
  echo
  .venv/bin/python bin/ask.py "$q" --k 6
  echo
  printf '\033[90m%s\033[0m\n\n' "────────────────────────────────────────────"
done
