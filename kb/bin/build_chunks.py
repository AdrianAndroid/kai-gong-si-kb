#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""build_chunks.py —— 由 ASR/OCR 结果生成视频文本块。

输出 kb/data/video_chunks.jsonl
每块：{id, source, course, kind:'video', title_path:'课程 > 集名', loc:'mm:ss', text, chars, ocr}
"""
import json, re, importlib.util
from pathlib import Path

ROOT = Path('/Volumes/wd/开公司')
KB = ROOT / 'kb'
DATA = KB / 'data'
ASR = DATA / 'asr'
OCR = DATA / 'ocr'
OUT = DATA / 'video_chunks.jsonl'

spec = importlib.util.spec_from_file_location('v2m', KB / 'bin' / 'video2md.py')
v2m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(v2m)

MAXLEN = 260
GAP = 3.0


def mmss(sec):
    sec = int(round(sec))
    return f'{sec // 60:02d}:{sec % 60:02d}'


def paragraphs(subs):
    paras, buf, start, prev_end = [], [], None, None
    for s in subs:
        t = (s.get('subtitle_text') or '').strip()
        if not t:
            continue
        st = s.get('start_time', 0)
        if prev_end is not None and (st - prev_end > GAP or len(''.join(buf)) > MAXLEN):
            paras.append((start, prev_end, ''.join(buf)))
            buf, start = [], None
        if start is None:
            start = st
        buf.append(t)
        prev_end = s.get('end_time', st)
    if buf:
        paras.append((start, prev_end, ''.join(buf)))
    return paras


def load_meta():
    """从 transcribe_state 反查 key → 源路径/课程"""
    st = json.loads((KB / 'logs' / 'transcribe_state.json').read_text(encoding='utf8'))
    return {k: v.get('source', '') for k, v in st.items() if v.get('source')}


def main():
    meta = load_meta()
    rows = []
    for f in sorted(ASR.glob('*.json')):
        key = f.stem
        src = meta.get(key)
        if not src:
            continue
        a = json.loads(f.read_text(encoding='utf8'))
        o = OCR / f'{key}.json'
        ocr_items = []
        if o.exists():
            try:
                ocr_items = v2m.dedupe_subtitles(json.loads(o.read_text(encoding='utf8')).get('subtitles', []))
            except Exception:
                ocr_items = []
        rel = Path(src)
        course = rel.parts[1] if len(rel.parts) > 2 else rel.parts[0]
        stem = rel.stem
        for i, (st, en, txt) in enumerate(paragraphs(a.get('subtitles', []))):
            txt = txt.strip()
            if len(txt) < 8:
                continue
            near = [c['text'] for c in ocr_items if st - 5 <= c['t'] <= (en or st) + 15]
            near = list(dict.fromkeys(near))[:6]
            body = txt
            if near:
                body += '\n画面文字：' + ' / '.join(near)
            rows.append(dict(id=f'{key}-{i:04d}', source=src, course=course, kind='video',
                             title_path=f'{course} > {stem}', loc=mmss(st), text=body, chars=len(body),
                             ocr=bool(near)))
    OUT.write_text('\n'.join(json.dumps(r, ensure_ascii=False) for r in rows), encoding='utf8')
    print(f'视频块 {len(rows)} 条，其中带画面文字 {sum(1 for r in rows if r["ocr"])} 条 → {OUT}')


if __name__ == '__main__':
    main()
