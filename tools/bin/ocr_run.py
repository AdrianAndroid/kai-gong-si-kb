#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ocr_run.py v2 —— 画面 OCR 全量调度（云端 video-ocr，>10min 自动切片）

v1 用 6 个线程各自 fork mediakit-cli，在 macOS 上撞上多线程 fork 死锁（采样栈卡在
subprocess_fork_exec → __fork），任务早已 completed 却收不回来。v2 改为单线程协调器：
所有 CLI 调用串行，靠"同时在途任务数"并行，不跟 fork 打架。

流程：切片 → 提交(限速, 保持 inflight ≤ N) → 轮询回收 → 按偏移合并 → data/ocr/<key>.json
状态：logs/ocr_state.json（每个切片独立记 task_id/状态，可断点续跑）

用法：
  python bin/ocr_run.py --inflight 6 --budget 240
  python bin/ocr_run.py --limit 5
"""
import json, hashlib, math, subprocess, time, argparse
from pathlib import Path

ROOT = Path('/Volumes/wd/开公司')
KB = ROOT / 'kb'
DATA = KB / 'data'
OCR_DIR = DATA / 'ocr'
SLICE_DIR = DATA / 'ocr_slices'
STATE_F = KB / 'logs' / 'ocr_state.json'
LOG_F = KB / 'logs' / 'ocr.log'
OCR_MAX = 600
SLICE_SEC = 540
SUBMIT_GAP = 600
POLL_ROUND_GAP = 20


def log(msg):
    line = f'[{time.strftime("%H:%M:%S")}] {msg}'
    print(line, flush=True)
    with open(LOG_F, 'a', encoding='utf8') as f:
        f.write(line + '\n')


def key_of(p: Path) -> str:
    return hashlib.md5(str(p.relative_to(ROOT)).encode('utf8')).hexdigest()[:12]


def load_state():
    return json.loads(STATE_F.read_text(encoding='utf8')) if STATE_F.exists() else {}


def save_state(st):
    STATE_F.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_F.with_suffix('.tmp')
    tmp.write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding='utf8')
    tmp.replace(STATE_F)


def dur_of(p: Path) -> float:
    r = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
                        '-of', 'csv=p=0', str(p)], capture_output=True, text=True)
    try:
        return float(r.stdout.strip())
    except Exception:
        return 0.0


def plan_parts(p: Path, dur: float):
    if dur <= OCR_MAX:
        return [(0, p)]
    key = key_of(p)
    SLICE_DIR.mkdir(parents=True, exist_ok=True)
    out = []
    for i in range(math.ceil(dur / SLICE_SEC)):
        off = i * SLICE_SEC
        f = SLICE_DIR / f'{key}_p{i}.mp4'
        if not (f.exists() and f.stat().st_size > 1000):
            subprocess.run(['ffmpeg', '-y', '-v', 'error', '-ss', str(off), '-t', str(SLICE_SEC),
                            '-i', str(p), '-c', 'copy', '-avoid_negative_ts', 'make_zero',
                            str(f)], capture_output=True, timeout=1800)
        out.append((off, f))
    return out


def submit_one(path: str, tries=2):
    for i in range(tries):
        r = subprocess.run(['mediakit-cli', 'video', 'video-ocr',
                            '--video-url', path, '--mode', 'Detailed'],
                           capture_output=True, text=True, timeout=900)
        try:
            j = json.loads(r.stdout)
        except Exception:
            j = {}
        if j.get('task_id'):
            return j['task_id'], None
        msg = ((r.stderr or '') + (r.stdout or ''))[:100].replace('\n', ' ')
        log(f'    submit 失败({i+1}/{tries}) {msg}')
        time.sleep(120)
    return None, 'submit failed'


def poll_one(tid: str):
    r = subprocess.run(['mediakit-cli', 'shared', 'query-task', '--task-id', tid],
                       capture_output=True, text=True, timeout=240)
    try:
        return json.loads(r.stdout), None
    except Exception:
        return None, 'parse'


def merge_one(st, p, key):
    info = st.get(key, {})
    parts = info.get('parts', {})
    if not parts or info.get('ocr_done'):
        return False
    if not all(v.get('status') == 'done' for v in parts.values()):
        return False
    dur = info.get('duration') or dur_of(p)
    subs = []
    for idx, v in sorted(parts.items(), key=lambda x: int(x[0])):
        f = OCR_DIR / f'{key}_{idx}.json'
        if not f.exists():
            return False
        part = json.loads(f.read_text(encoding='utf8'))
        off = v.get('offset', 0)
        for s in part.get('subtitles', []):
            subs.append(dict(start_time=round(s.get('start_time', 0) + off, 2),
                             end_time=round(s.get('end_time', 0) + off, 2),
                             subtitle_text=(s.get('subtitle_text') or '').strip(),
                             text_label=s.get('text_label'),
                             text_location=s.get('text_location')))
    subs.sort(key=lambda x: x['start_time'])
    OCR_DIR.mkdir(parents=True, exist_ok=True)
    (OCR_DIR / f'{key}.json').write_text(
        json.dumps(dict(duration=round(dur, 2), subtitles=subs), ensure_ascii=False), encoding='utf8')
    info['ocr_done'] = True
    info['ocr_count'] = len(subs)
    save_state(st)
    log(f'[merge] {p.name[:38]} → {len(subs)} 条')
    return True


def main():
    import fcntl
    _lock = open(KB / 'logs' / 'ocr.lock', 'w')
    try:
        fcntl.flock(_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        log('已有实例在运行，本进程退出')
        return
    ap = argparse.ArgumentParser()
    ap.add_argument('--inflight', type=int, default=6)
    ap.add_argument('--budget', type=int, default=300)
    ap.add_argument('--limit', type=int, default=10 ** 9)
    ap.add_argument('--first', default='')
    a = ap.parse_args()

    vids = sorted([p for p in ROOT.rglob('*.mp4') if 'kb' not in p.parts])
    if a.first:
        ks = [x for x in a.first.split(',') if x]
        head = [p for p in vids if any(k in str(p) for k in ks)]
        vids = head + [p for p in vids if p not in head]
    st = load_state()

    for p in vids:
        info = st.setdefault(key_of(p), {})
        info.setdefault('parts', {})
        info['duration'] = info.get('duration') or dur_of(p)
        info.setdefault('source', str(p.relative_to(ROOT)))
    save_state(st)

    vids_pending = [p for p in vids if not st[key_of(p)].get('ocr_done')][:a.limit]
    tasks = []
    for p in vids_pending:
        info = st[key_of(p)]
        for idx, (off, path) in enumerate(plan_parts(p, info['duration'])):
            pinfo = info['parts'].setdefault(str(idx), {'offset': off, 'status': 'new'})
            pinfo['offset'] = off
            pinfo['path'] = str(path)
            tasks.append((key_of(p), p, str(idx)))
    save_state(st)

    todo = [t for t in tasks if st[t[0]]['parts'][t[2]].get('status') != 'done']
    log(f'切片任务 {len(tasks)}，待跑 {len(todo)}，在途上限 {a.inflight}，预算 {a.budget} 分钟')

    end = time.time() + a.budget * 60
    idx = 0
    inflight = []
    last_submit = 0
    n_done = n_err = 0

    def harvest():
        nonlocal n_done, n_err
        keep = []
        for key, p, pi, tid, t0 in inflight:
            pinfo = st[key]['parts'][pi]
            j, err = poll_one(tid)
            stt = (j or {}).get('status')
            if stt in ('completed', 'success'):
                OCR_DIR.mkdir(parents=True, exist_ok=True)
                (OCR_DIR / f'{key}_{pi}.json').write_text(json.dumps(j, ensure_ascii=False), encoding='utf8')
                pinfo['status'] = 'done'
                pinfo['seconds'] = round(time.time() - t0, 1)
                save_state(st)
                merge_one(st, p, key)
                n_done += 1
                log(f'  完成 {p.name[:34]} p{pi} 用时 {(time.time()-t0)/60:.1f} 分')
            elif stt in ('failed', 'error'):
                pinfo['status'] = 'task_failed'
                pinfo['error'] = json.dumps((j or {}).get('error') or {}, ensure_ascii=False)[:150]
                save_state(st)
                n_err += 1
                log(f'  任务失败 {p.name[:34]} p{pi}')
            else:
                keep.append((key, p, pi, tid, t0))
        inflight[:] = keep

    while time.time() < end:
        while len(inflight) < a.inflight and idx < len(todo):
            key, p, pi = todo[idx]
            idx += 1
            pinfo = st[key]['parts'][pi]
            if pinfo.get('status') == 'done':
                continue
            tid = pinfo.get('task_id')
            if not tid:
                gate = last_submit + SUBMIT_GAP - time.time()
                if gate > 0:
                    time.sleep(gate)
                tid, err = submit_one(pinfo['path'])
                last_submit = time.time()
                if not tid:
                    pinfo['status'] = 'submit_err'
                    save_state(st)
                    n_err += 1
                    log(f'  放弃提交 {p.name[:30]} p{pi}（限流冷却 10 分钟）')
                    time.sleep(120)
                    continue
                pinfo['task_id'] = tid
                pinfo['status'] = 'pending'
                save_state(st)
                log(f'  提交 {p.name[:30]} p{pi} → {tid}')
            inflight.append((key, p, pi, tid, time.time()))

        if not inflight:
            break
        harvest()
        time.sleep(POLL_ROUND_GAP)

    harvest()
    done_parts = sum(1 for _, v in st.items() for pp in v.get('parts', {}).values() if pp.get('status') == 'done')
    tot_parts = sum(len(v.get('parts', {})) for v in st.values())
    merged = sum(1 for v in st.values() if v.get('ocr_done'))
    log(f'本轮结束：切片完成 {done_parts}/{tot_parts}，整集合并 {merged}，成功 {n_done}，失败 {n_err}')


if __name__ == '__main__':
    main()
