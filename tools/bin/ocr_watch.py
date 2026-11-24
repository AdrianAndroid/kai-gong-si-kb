#!/usr/bin/env python3
"""OCR 巡检看门狗：每 10 分钟检查协调器，卡死自动重启。

判定规则：
  1. 没有 ocr_run.py 进程            -> 启动
  2. ocr.log 最后修改超过 12 分钟    -> 判为卡死，杀掉重启
  3. 否则                             -> 只记录状态
"""
import fcntl
import json
import os
import subprocess
import time
from datetime import datetime
from pathlib import Path

KB = Path('/Volumes/wd/开公司/kb')
STATE_F = KB / 'logs' / 'ocr_state.json'
LOG_F = KB / 'logs' / 'ocr.log'
WATCH_LOG = KB / 'logs' / 'ocr_watch.log'
LOCK_F = KB / 'logs' / 'ocr_watch.lock'
INTERVAL = 600          # 10 分钟
STALL_SEC = 25 * 60     # 超过 25 分钟没写日志算卡死（容纳限流冷却）
CMD = ['.venv/bin/python', '-u', 'bin/ocr_run.py', '--inflight', '4', '--budget', '600']


def note(msg: str):
    line = f'[{datetime.now():%H:%M:%S}] {msg}'
    print(line, flush=True)
    with open(WATCH_LOG, 'a', encoding='utf8') as f:
        f.write(line + '\n')


def running() -> bool:
    r = subprocess.run(['ps', '-Ao', 'command='], capture_output=True, text=True)
    return 'bin/ocr_run.py' in r.stdout


def progress() -> str:
    try:
        st = json.loads(STATE_F.read_text(encoding='utf8'))
    except Exception as e:
        return f'状态文件读取失败({e.__class__.__name__})'
    done = sum(1 for v in st.values() for p in v.get('parts', {}).values() if p.get('status') == 'done')
    pend = sum(1 for v in st.values() for p in v.get('parts', {}).values() if p.get('status') == 'pending')
    err = sum(1 for v in st.values() for p in v.get('parts', {}).values()
              if p.get('status') in ('submit_err', 'task_failed'))
    merged = sum(1 for v in st.values() if v.get('ocr_done'))
    total = sum(len(v.get('parts', {})) for v in st.values())
    return f'切片 {done}/{total or 195} 在途 {pend} 异常 {err} 合并 {merged}/121'


def stop():
    subprocess.run(['pkill', '-f', 'bin/ocr_run.py'], capture_output=True)
    time.sleep(4)


def start():
    stop()
    with open(KB / 'logs' / 'ocr.out', 'a', encoding='utf8') as out:
        subprocess.Popen(CMD, cwd=str(KB), stdout=out, stderr=subprocess.STDOUT,
                         stdin=subprocess.DEVNULL, start_new_session=True)
    time.sleep(20)
    note('已启动协调器（单实例锁保证唯一）')


def main():
    lock = open(LOCK_F, 'w')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print('看门狗已在运行，退出', flush=True)
        return

    note(f'看门狗启动：每 {INTERVAL // 60} 分钟巡检，{STALL_SEC // 60} 分钟无日志判卡死')
    while True:
        try:
            if not running():
                note(f'进程缺失 | {progress()} -> 重启')
                start()
            else:
                idle = time.time() - os.path.getmtime(LOG_F)
                if idle > STALL_SEC:
                    note(f'卡死 {int(idle // 60)} 分钟无日志 | {progress()} -> 重启')
                    start()
                else:
                    note(f'正常 | {progress()} | 最近活动 {int(idle // 60)} 分钟前')
        except Exception as e:
            note(f'巡检自身异常：{e.__class__.__name__}: {e}')
        time.sleep(INTERVAL)


if __name__ == '__main__':
    main()
