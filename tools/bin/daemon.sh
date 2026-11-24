#!/bin/bash
# daemon.sh —— 常驻补跑守护：分批转写剩余视频，直到全部完成，然后重建索引
# 由 nohup / launchd 拉起；也可手动运行。单实例保护用 pid 文件。
ROOT="/Volumes/wd/开公司"
KB="$ROOT/kb"
cd "$KB" || exit 1
PY="$KB/.venv/bin/python"
LOCK="$KB/logs/daemon.pid"

if [ -f "$LOCK" ] && kill -0 "$(cat "$LOCK" 2>/dev/null)" 2>/dev/null; then
  echo "[daemon] 已有实例在跑 ($(cat "$LOCK"))，退出"
  exit 0
fi
echo $$ > "$LOCK"
trap 'rm -f "$LOCK"' EXIT

total=$(find "$ROOT" -name '*.mp4' -not -path "$KB/*" | wc -l | tr -d ' ')
echo "[daemon] $(date +%H:%M) 启动，视频总数 $total"

for i in $(seq 1 40); do
  done_n=$(ls "$KB/data/asr" 2>/dev/null | wc -l | tr -d ' ')
  echo "[daemon] 轮次 $i：已完成 $done_n / $total"
  if [ "$done_n" -ge "$total" ]; then
    echo "[daemon] 全部完成，重建索引"
    "$PY" "$BIN/build_chunks.py"
    "$PY" "$BIN/transcribe.py" notes | tail -1
    "$PY" "$BIN/build_index.py" | grep -v jieba | tail -3
    echo "[daemon] 结束"
    exit 0
  fi
  "$PY" -u "$BIN/transcribe.py" asr --minutes 40 \
      --first "老板必听财税课,创业融资必修课,ICP备案,人事合规" >> "$KB/logs/asr_run_daemon.log" 2>&1
  sleep 60
done
echo "[daemon] 达到轮次上限，退出"
