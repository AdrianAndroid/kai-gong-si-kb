#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""local_asr.py —— 本地 faster-whisper 兜底转写（云端配额受限时使用）

产出与云端 asr 相同结构的 JSON（kb/data/asr/<key>.json），build_chunks 可直接消费。

用法：
  python bin/local_asr.py                 # 补跑所有缺失集
  python bin/local_asr.py --model medium   # 换更大模型（更准更慢）
  python bin/local_asr.py --limit 2        # 只跑 2 集
"""
import sys, json, time, wave, subprocess, argparse
from pathlib import Path

ROOT = Path('/Volumes/wd/开公司')
KB = ROOT / 'kb'
DATA = KB / 'data'
ASR_DIR = DATA / 'asr'
WAV_DIR = DATA / 'wav'
STATE_F = KB / 'logs' / 'transcribe_state.json'

# 同音/近音纠错：这些是课程里高频且必须准确的词
FIX = [
    ('攻击金', '公积金'), ('住方', '住房'), ('认交', '认缴'), ('实交', '实缴'),
    ('增值說', '增值税'), ('增值睡', '增值税'), ('睡务', '税务'), ('税务局的', '税务局的'),
    ('一般纳睡人', '一般纳税人'), ('小归模', '小规模'), ('消项', '销项'), ('进项', '进项'),
    ('商标注册', '商标注册'), ('著佐权', '著作权'), ('注策', '注册'), ('法入', '法人'),
    ('监示', '监事'), ('章成', '章程'), ('竞业进止', '竞业禁止'), ('股份比力', '股份比例'),
]


def key_of(p: Path) -> str:
    import hashlib
    return hashlib.md5(str(p.relative_to(ROOT)).encode('utf8')).hexdigest()[:12]


def to_wav(p: Path, key: str) -> Path:
    WAV_DIR.mkdir(parents=True, exist_ok=True)
    f = WAV_DIR / f'{key}.wav'
    if f.exists() and f.stat().st_size > 1000:
        return f
    subprocess.run(['ffmpeg', '-y', '-v', 'error', '-i', str(p), '-vn', '-ac', '1',
                    '-ar', '16000', '-f', 'wav', str(f)], capture_output=True, timeout=900)
    return f


def load_wav(p: Path):
    import numpy as np
    w = wave.open(str(p))
    n = w.getnframes()
    d = w.readframes(n)
    w.close()
    return np.frombuffer(d, dtype=np.int16).astype('float32') / 32768.0


def fix_text(t: str) -> str:
    for a, b in FIX:
        t = t.replace(a, b)
    return t


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--model', default='small')
    ap.add_argument('--threads', type=int, default=8)
    ap.add_argument('--limit', type=int, default=10**9)
    ap.add_argument('--first', default='')
    a = ap.parse_args()

    vids = sorted([p for p in ROOT.rglob('*.mp4') if 'kb' not in p.parts])
    todo = [p for p in vids if not (ASR_DIR / f'{key_of(p)}.json').exists()]
    if a.first:
        keys = [x for x in a.first.split(',') if x]
        head = [p for p in todo if any(k in str(p) for k in keys)]
        todo = head + [p for p in todo if p not in head]
    todo = todo[:a.limit]
    print(f'[local] 待转写 {len(todo)} 集，模型 {a.model}，线程 {a.threads}', flush=True)

    from faster_whisper import WhisperModel
    t0 = time.time()
    model = WhisperModel(a.model, device='cpu', compute_type='int8', cpu_threads=a.threads)
    print(f'[local] 模型加载 {time.time()-t0:.0f}s', flush=True)

    st = json.loads(STATE_F.read_text(encoding='utf8')) if STATE_F.exists() else {}
    ASR_DIR.mkdir(parents=True, exist_ok=True)

    for i, p in enumerate(todo, 1):
        key = key_of(p)
        try:
            wav = to_wav(p, key)
            audio = load_wav(wav)
            dur = len(audio) / 16000
            t1 = time.time()
            segs, info = model.transcribe(audio, language='zh', vad_filter=True,
                                          beam_size=1, initial_prompt='以下是创业、注册公司、财税、股权、商标的课程录音。')
            subs = []
            for s in segs:
                text = fix_text(s.text.strip())
                if text:
                    subs.append(dict(start_time=round(float(s.start), 2),
                                     end_time=round(float(s.end), 2),
                                     subtitle_text=text))
            el = time.time() - t1
            out = dict(duration=round(dur, 2), subtitles=subs, engine='local-whisper-' + a.model)
            (ASR_DIR / f'{key}.json').write_text(json.dumps(out, ensure_ascii=False), encoding='utf8')
            info_st = st.setdefault(key, {})
            info_st['source'] = str(p.relative_to(ROOT))
            info_st['asr_status'] = 'done'
            info_st['asr_engine'] = 'local-' + a.model
            info_st.pop('asr_error', None)
            STATE_F.write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding='utf8')
            print(f'[{i}/{len(todo)}] {p.stem[:34]} 音频{dur:.0f}s 用时{el:.0f}s {dur/max(el,1):.1f}x 句{len(subs)}', flush=True)
        except Exception as e:
            print(f'[{i}/{len(todo)}] FAIL {p.stem[:30]} {type(e).__name__}: {e}', flush=True)
    print('[local] 完成', flush=True)


if __name__ == '__main__':
    main()
