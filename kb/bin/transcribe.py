#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""transcribe.py —— 批量把课程视频转成带时间戳的文本（mediakit ASR + 画面 OCR）

用法：
  python bin/transcribe.py asr  [--limit N] [--workers W]   # 只做语音转写
  python bin/transcribe.py ocr  [--limit N] [--workers W]   # 只做画面OCR（>10min 自动切片）
  python bin/transcribe.py notes                            # 由已存 json 生成 Markdown 笔记
  python bin/transcribe.py status                           # 查看进度

产物：
  kb/data/asr/<key>.json      ASR 原始结果
  kb/data/ocr/<key>.json      OCR 原始结果
  kb/notes/<课程>/<集名>.md   人可读笔记
状态：kb/logs/transcribe_state.json（断点续跑）
"""
import os, sys, json, time, subprocess, hashlib, threading, queue
from pathlib import Path

ROOT = Path('/Volumes/wd/开公司')
KB = ROOT / 'kb'
DATA = KB / 'data'
ASR_DIR = DATA / 'asr'
OCR_DIR = DATA / 'ocr'
AUDIO_DIR = DATA / 'audio'
SEG_DIR = DATA / 'segs'
NOTES = KB / 'notes'
STATE_F = KB / 'logs' / 'transcribe_state.json'
SEG_SEC = 540          # 9 分钟切片（OCR 上限 10 分钟，留余量）
OCR_MAX_SEC = 600
CLI = 'mediakit-cli'

_lock = threading.Lock()
_submit_lock = threading.Lock()
_last_submit = [0.0]
SUBMIT_GAP = 30.0       # 上传地址接口配额很紧，按 2 次/分钟 保守推进


def key_of(p: Path) -> str:
    return hashlib.md5(str(p.relative_to(ROOT)).encode('utf8')).hexdigest()[:12]


def load_state():
    if STATE_F.exists():
        return json.loads(STATE_F.read_text(encoding='utf8'))
    return {}


def save_state(st):
    STATE_F.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_F.with_suffix('.tmp')
    tmp.write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding='utf8')
    tmp.replace(STATE_F)


def run_cli(args, timeout=180):
    try:
        r = subprocess.run([CLI] + args, capture_output=True, text=True, timeout=timeout)
        out = r.stdout.strip()
        # 取最后一个 JSON 对象
        start = out.find('{')
        if start < 0:
            return None, (r.stderr or out)[:300]
        return json.loads(out[start:]), None
    except subprocess.TimeoutExpired:
        return None, 'cli-timeout'
    except json.JSONDecodeError as e:
        return None, f'json-error {e}'
    except Exception as e:
        return None, f'{type(e).__name__}: {e}'


def video_duration(p: Path) -> float:
    try:
        r = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
                            '-of', 'csv=p=0', str(p)], capture_output=True, text=True, timeout=30)
        return float(r.stdout.strip())
    except Exception:
        return 0.0


def submit(tool_args, attempts=6) -> str:
    """提交异步任务。上传通道会因突发并发失败（"申请上传地址响应缺少 result"
    / "缺少 task_id"），这类属暂时性错误：串行化 + 限速 + 退避重试。
    服务端对相同参数有 24h 幂等，重复提交不会产生重复任务。"""
    last = ''
    for attempt in range(attempts):
        with _submit_lock:
            gap = SUBMIT_GAP - (time.time() - _last_submit[0])
            if gap > 0:
                time.sleep(gap)
            j, err = run_cli(tool_args, timeout=300)
            _last_submit[0] = time.time()
        if j and j.get('success'):
            return j['task_id']
        last = json.dumps(j or err, ensure_ascii=False)[:300]
        transient = any(k in last for k in ('Timeout', 'timeout', '缺少 result', '缺少 task_id', 'upload', 'transport'))
        if err == 'cli-timeout':
            transient = True
        if not transient:
            break
        # 限流类错误要等更久，普通超时短退避
        time.sleep((45 if '缺少' in last else 10) * (attempt + 1))
    raise RuntimeError(f'submit failed: {last}')


def poll(task_id: str, max_wait=900, interval=10):
    """轮询到终态；暂时性错误（Gateway Timeout 等）继续等。每次轮询结果写入
    logs/poll.log，便于诊断卡点。"""
    t0 = time.time()
    while time.time() - t0 < max_wait:
        j, err = run_cli(['shared', 'query-task', '--task-id', task_id], timeout=90)
        st = (j or {}).get('status') if j else ('err:' + str(err))[:40]
        try:
            with open(KB / 'logs' / 'poll.log', 'a') as f:
                f.write(f'{time.strftime("%H:%M:%S")} {task_id[-12:]} status={st} '
                        f'keys={list((j or {}).keys())[:6] if j else None}\n')
        except Exception:
            pass
        if j and j.get('status') in ('completed', 'success'):
            return j, None
        if j and j.get('status') in ('failed', 'error'):
            return None, json.dumps(j.get('error') or j, ensure_ascii=False)[:400]
        if j and j.get('success') is False and 'status' not in j:
            msg = str((j.get('error') or {}).get('message', ''))
            if 'Timeout' in msg or 'timeout' in msg:
                time.sleep(interval)
                continue
            return None, msg[:400]
        if err and err != 'cli-timeout':
            return None, err
        time.sleep(interval)
    return None, 'poll-timeout'


def split_video(p: Path, key: str, dur: float):
    """无损切片，返回 [(part_path, offset_sec)]"""
    outdir = SEG_DIR / key
    outdir.mkdir(parents=True, exist_ok=True)
    parts = []
    n = int(dur // SEG_SEC) + 1
    for i in range(n):
        f = outdir / f'p{i:02d}.mp4'
        if not (f.exists() and f.stat().st_size > 1000):
            subprocess.run(['ffmpeg', '-y', '-v', 'error', '-ss', str(i * SEG_SEC),
                            '-i', str(p), '-t', str(SEG_SEC), '-c', 'copy', str(f)],
                           check=False, timeout=300)
        if f.exists() and f.stat().st_size > 1000:
            parts.append((f, i * SEG_SEC))
    return parts


def audio_of(p: Path, key: str):
    """抽 16k 单声道音频（约 0.5MB/分钟），比原视频小 5–10 倍，
    规避上传地址接口的限流，也更快。"""
    AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    f = AUDIO_DIR / f'{key}.m4a'
    if f.exists() and f.stat().st_size > 1000:
        return f
    subprocess.run(['ffmpeg', '-y', '-v', 'error', '-i', str(p), '-vn',
                    '-ac', '1', '-ar', '16000', '-c:a', 'aac', '-b:a', '48k', str(f)],
                   capture_output=True, timeout=600)
    return f if (f.exists() and f.stat().st_size > 1000) else None


def do_asr(p: Path, st: dict):
    key = key_of(p)
    dst = ASR_DIR / f'{key}.json'
    if dst.exists() and dst.stat().st_size > 200:
        return 'cached'
    info = st.setdefault(key, {})
    tid = info.get('asr_task')
    if not tid:
        a = audio_of(p, key)
        args = ['video', 'asr-subtitles',
                ('--audio-url' if a else '--video-url'), str(a or p),
                '--language', 'cmn-Hans-CN']
        tid = submit(args)
        with _lock:
            info['asr_task'] = tid
            info['source'] = str(p.relative_to(ROOT))
            info['asr_input'] = 'audio' if a else 'video'
            save_state(st)
    res, err = poll(tid)
    if not res:
        with _lock:
            info['asr_error'] = err
            info.pop('asr_task', None)
            save_state(st)
        return f'FAIL {err}'
    ASR_DIR.mkdir(parents=True, exist_ok=True)
    dst.write_text(json.dumps(res, ensure_ascii=False), encoding='utf8')
    with _lock:
        info['asr_status'] = 'done'
        info.pop('asr_task', None)
        save_state(st)
    return f'ok {len(res.get("subtitles", []))} 句'


def do_ocr(p: Path, st: dict, dur: float):
    key = key_of(p)
    dst = OCR_DIR / f'{key}.json'
    if dst.exists() and dst.stat().st_size > 100:
        return 'cached'
    info = st.setdefault(key, {})
    merged = info.get('ocr_parts', {})
    if dur <= OCR_MAX_SEC:
        tasks = [('', str(p))]
    else:
        tasks = [(str(off), str(f)) for f, off in split_video(p, key, dur)]
    subs_all = []
    for off, path in tasks:
        tkey = off or 'single'
        if merged.get(tkey) == 'done' and (OCR_DIR / f'{key}_{tkey}.json').exists():
            part = json.loads((OCR_DIR / f'{key}_{tkey}.json').read_text(encoding='utf8'))
        else:
            tid = info.get('ocr_tasks', {}).get(tkey)
            if not tid:
                try:
                    tid = submit(['video', 'video-ocr', '--video-url', path, '--mode', 'Detailed'])
                except Exception as e:
                    return f'FAIL submit {e}'
                with _lock:
                    info.setdefault('ocr_tasks', {})[tkey] = tid
                    save_state(st)
            res, err = poll(tid, max_wait=1200)
            if not res:
                with _lock:
                    info.setdefault('ocr_tasks', {}).pop(tkey, None)
                    save_state(st)
                return f'FAIL {err}'
            OCR_DIR.mkdir(parents=True, exist_ok=True)
            (OCR_DIR / f'{key}_{tkey}.json').write_text(json.dumps(res, ensure_ascii=False), encoding='utf8')
            with _lock:
                merged[tkey] = 'done'
                info['ocr_parts'] = merged
                info.get('ocr_tasks', {}).pop(tkey, None)
                save_state(st)
            part = res
        offs = float(off) if off else 0.0
        for s in part.get('subtitles', []):
            subs_all.append(dict(start_time=s.get('start_time', 0) + offs,
                                 end_time=s.get('end_time', 0) + offs,
                                 subtitle_text=s.get('subtitle_text', ''),
                                 text_label=s.get('text_label'),
                                 text_location=s.get('text_location')))
    subs_all.sort(key=lambda x: x['start_time'])
    dst.write_text(json.dumps(dict(duration=dur, subtitles=subs_all), ensure_ascii=False), encoding='utf8')
    with _lock:
        info['ocr_status'] = 'done'
        save_state(st)
    return f'ok {len(subs_all)} 条'


def sweep(pending, st, limit=4):
    """对已提交任务做一次快速回收（不阻塞），返回本次完成的条数"""
    n = 0
    for key in list(pending)[:limit]:
        tid = pending[key]
        j, err = run_cli(['shared', 'query-task', '--task-id', tid], timeout=60)
        stt = (j or {}).get('status')
        if stt in ('completed', 'success'):
            ASR_DIR.mkdir(parents=True, exist_ok=True)
            (ASR_DIR / f'{key}.json').write_text(json.dumps(j, ensure_ascii=False), encoding='utf8')
            with _lock:
                info = st.setdefault(key, {})
                info['asr_status'] = 'done'
                info.pop('asr_task', None)
                info.pop('asr_error', None)
                save_state(st)
            pending.pop(key)
            n += 1
            print(f'[asr] 完成 {st.get(key, {}).get("source", key)[-38:]}', flush=True)
        elif stt in ('failed', 'error'):
            with _lock:
                info = st.setdefault(key, {})
                info['asr_error'] = json.dumps(j.get('error') or j, ensure_ascii=False)[:200]
                info.pop('asr_task', None)
                save_state(st)
            pending.pop(key)
        elif err:
            pass
    return n


def collect_asr(max_minutes=90, first=None):
    """两阶段：串行限速提交 → 轮转回收。避免提交限流与轮询互相阻塞。
    已提交未回收的 task_id 会继续被回收（不会重复上传）。
    first: 路径关键字列表，命中的视频排到队首（优先补某门课）。"""
    st = load_state()
    vids = sorted([p for p in ROOT.rglob('*.mp4') if 'kb' not in p.parts])

    def done(p):
        return (ASR_DIR / f'{key_of(p)}.json').exists()

    pending = {}  # key -> task_id
    for k, v in st.items():
        if v.get('asr_task'):
            pending[k] = v['asr_task']
    todo = [p for p in vids if not done(p) and key_of(p) not in pending]
    if first:
        head = [p for p in todo if any(f in str(p) for f in first)]
        todo = head + [p for p in todo if p not in head]
        print(f'[asr] 优先队列 {len(head)} 集', flush=True)
    print(f'[asr] 待提交 {len(todo)}，待回收 {len(pending)}，已完成 {sum(1 for p in vids if done(p))}/{len(vids)}', flush=True)

    t_end = time.time() + max_minutes * 60

    # ---- 阶段 1：串行提交（限速 + 限流退避）----
    for p in todo:
        if time.time() > t_end:
            print('[asr] 提交阶段到达时间上限，转回收', flush=True)
            break
        key = key_of(p)
        info = st.setdefault(key, {})
        if info.get('asr_task'):
            pending[key] = info['asr_task']
            continue
        a = audio_of(p, key)
        args = ['video', 'asr-subtitles', ('--audio-url' if a else '--video-url'),
                str(a or p), '--language', 'cmn-Hans-CN']
        try:
            tid = submit(args, attempts=1)
        except Exception as e:
            msg = str(e)
            print(f'[asr] 提交失败（跳过，下轮再试） {p.name[:32]} {msg[:60]}', flush=True)
            info['asr_error'] = msg[:200]
            with _lock:
                save_state(st)
            if '缺少' in msg:
                print('[asr] 命中限流，冷却 120s', flush=True)
                time.sleep(120)
            continue
        with _lock:
            info['asr_task'] = tid
            info['source'] = str(p.relative_to(ROOT))
            info['asr_input'] = 'audio' if a else 'video'
            info.pop('asr_error', None)
            save_state(st)
        pending[key] = tid
        print(f'[asr] 已提交 {p.name[:32]}', flush=True)
        # 边提交边回收：每提交一集顺手扫几条已完成的任务，避免它们白等
        sweep(pending, st, limit=3)

    # ---- 阶段 2：轮转回收（不阻塞在单个任务上）----
    idle_rounds = 0
    while pending and time.time() < t_end:
        progressed = False
        for key in list(pending):
            tid = pending[key]
            j, err = run_cli(['shared', 'query-task', '--task-id', tid], timeout=90)
            stt = (j or {}).get('status')
            if stt in ('completed', 'success'):
                ASR_DIR.mkdir(parents=True, exist_ok=True)
                (ASR_DIR / f'{key}.json').write_text(json.dumps(j, ensure_ascii=False), encoding='utf8')
                with _lock:
                    info = st.setdefault(key, {})
                    info['asr_status'] = 'done'
                    info.pop('asr_task', None)
                    info.pop('asr_error', None)
                    save_state(st)
                pending.pop(key)
                progressed = True
                print(f'[asr] 完成 {st.get(key, {}).get("source", key)[-40:]}', flush=True)
            elif stt in ('failed', 'error'):
                with _lock:
                    info = st.setdefault(key, {})
                    info['asr_error'] = json.dumps(j.get('error') or j, ensure_ascii=False)[:200]
                    info.pop('asr_task', None)
                    save_state(st)
                pending.pop(key)
                progressed = True
        if not progressed:
            idle_rounds += 1
        else:
            idle_rounds = 0
        time.sleep(20 if idle_rounds < 10 else 40)
    print(f'[asr] 本轮结束，仍未回收 {len(pending)} 个任务（下次运行会继续）', flush=True)


def worker(mode, q, st, results):
    while True:
        try:
            item = q.get_nowait()
        except queue.Empty:
            return
        p = item
        try:
            if mode == 'asr':
                r = do_asr(p, st)
            else:
                r = do_ocr(p, st, video_duration(p))
            results[p] = r
            print(f'[{mode}] {p.name[:40]} → {r}', flush=True)
        except Exception as e:
            results[p] = f'EXC {e}'
            print(f'[{mode}] {p.name[:40]} → EXC {type(e).__name__}: {e}', flush=True)


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else 'status'
    limit = 10 ** 9
    workers = 3
    for i, a in enumerate(sys.argv):
        if a == '--limit':
            limit = int(sys.argv[i + 1])
        if a == '--workers':
            workers = int(sys.argv[i + 1])

    vids = sorted([p for p in ROOT.rglob('*.mp4') if 'kb' not in p.parts])
    st = load_state()

    if mode == 'status':
        done_a = sum(1 for p in vids if (ASR_DIR / f'{key_of(p)}.json').exists())
        done_o = sum(1 for p in vids if (OCR_DIR / f'{key_of(p)}.json').exists())
        notes = sum(1 for p in NOTES.rglob('*.md')) if NOTES.exists() else 0
        print(f'视频 {len(vids)} | ASR 完成 {done_a} | OCR 完成 {done_o} | 笔记 {notes}')
        fails = {k: v for k, v in st.items() if v.get('asr_error')}
        if fails:
            print(f'ASR 失败 {len(fails)} 条')
        return

    if mode == 'asr':
        mins = 90
        first = None
        for i, a in enumerate(sys.argv):
            if a == '--minutes':
                mins = int(sys.argv[i + 1])
            if a == '--first':
                first = [x for x in sys.argv[i + 1].split(',') if x]
        collect_asr(mins, first)
        return

    if mode in ('ocr',):
        # OCR 必须先有 ASR 结果才值得做（节省配额），asr 阶段跳过
        todo = []
        for p in vids:
            key = key_of(p)
            f = (ASR_DIR if mode == 'asr' else OCR_DIR) / f'{key}.json'
            if not f.exists():
                todo.append(p)
        todo = todo[:limit]
        print(f'[{mode}] 待处理 {len(todo)} / 总 {len(vids)}，并发 {workers}', flush=True)
        q = queue.Queue()
        for p in todo:
            q.put(p)
        results = {}
        ths = [threading.Thread(target=worker, args=(mode, q, st, results)) for _ in range(workers)]
        [t.start() for t in ths]
        [t.join() for t in ths]
        ok = sum(1 for v in results.values() if v.startswith(('ok', 'cached')))
        bad = {str(k): v for k, v in results.items() if not v.startswith(('ok', 'cached'))}
        print(f'[{mode}] 成功 {ok} 失败 {len(bad)}')
        if bad:
            (KB / 'logs' / f'{mode}_fails.json').write_text(
                json.dumps(bad, ensure_ascii=False, indent=1), encoding='utf8')
            for k, v in list(bad.items())[:10]:
                print('  FAIL', Path(k).name[:40], v)
        return

    if mode == 'notes':
        import importlib.util
        spec = importlib.util.spec_from_file_location('v2m', KB / 'bin' / 'video2md.py')
        v2m = importlib.util.module_from_spec(spec); spec.loader.exec_module(v2m)
        n = 0
        for p in vids:
            key = key_of(p)
            a, o = ASR_DIR / f'{key}.json', OCR_DIR / f'{key}.json'
            if not a.exists():
                continue
            rel = p.relative_to(ROOT)
            course = rel.parts[1] if len(rel.parts) > 2 else rel.parts[0]
            outp = NOTES / course.replace('/', '_')
            outp.mkdir(parents=True, exist_ok=True)
            md = outp / (p.stem + '.md')
            try:
                v2m.build(p.stem, course, str(a), str(o) if o.exists() else None, str(md))
                n += 1
            except Exception as e:
                print('NOTE FAIL', p.name, e)
        print(f'生成笔记 {n} 篇 → {NOTES}')


if __name__ == '__main__':
    main()
