#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""compress_videos.py —— 课程视频分级压缩

目标：把 121 集 5.8GB 压到可上传 GitHub 的量级（单文件 <100MB）。
- 普通集(<100MB)：1280 宽 / fps 24 / crf 30 → 体积约 1/4~1/3，字幕与 PPT 仍清晰
- 超大集(>100MB)：960 宽 / fps 15 / crf 34 + 码率上限（按 82MB 预算反算）→ 保证单文件不超 100MB

产出：kb/media/compressed/<原相对路径>.mp4
用法：
  python bin/compress_videos.py --only "6--二、方法篇"   # 试压单个
  python bin/compress_videos.py                          # 全量
"""
import argparse, json, os, shutil, subprocess, sys, time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path('/Volumes/wd/开公司')
OUT = ROOT / 'kb' / 'media' / 'compressed'
LOG = ROOT / 'kb' / 'logs' / 'compress.log'

STD = dict(w=1280, fps=24, crf=31, ab='64k')
BIG = dict(w=960, fps=15, crf=34, ab='48k', budget_mb=82)


def probe(f: Path):
    d = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
                        '-of', 'csv=p=0', str(f)], capture_output=True, text=True)
    try:
        return float(d.stdout.strip())
    except Exception:
        return 0.0


def encode(f: Path) -> str:
    rel = f.relative_to(ROOT)
    dst = (OUT / rel).with_suffix('.mp4')
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists() and dst.stat().st_size > 1000 and dst.stat().st_size < 100 * 1024 * 1024:
        return f'SKIP {rel} ({dst.stat().st_size/1e6:.1f}MB 已存在)'

    src_mb = f.stat().st_size / 1e6
    dur = probe(f)
    if src_mb > 100 and dur > 0:
        cfg = BIG
        # 82MB 预算 → 目标总码率(含音频)，0.88 安全系数
        total_kbps = BIG['budget_mb'] * 8 * 1024 / dur * 0.88
        maxrate = max(int(total_kbps - 48), 200)
        vf = f"scale={cfg['w']}:-2"
        vargs = ['-c:v', 'libx264', '-preset', 'fast', '-crf', str(cfg['crf']),
                 '-maxrate', f'{maxrate}k', '-bufsize', f'{maxrate*2}k']
        tag = 'BIG'
    else:
        cfg = STD
        vf = f"scale='min({cfg['w']},iw)':-2"
        vargs = ['-c:v', 'libx264', '-preset', 'fast', '-crf', str(cfg['crf'])]
        tag = 'STD'

    cmd = ['ffmpeg', '-y', '-v', 'error', '-i', str(f), '-vf', f'{vf},fps={cfg["fps"]}',
           *vargs, '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-b:a', cfg['ab'],
           '-ac', '1', '-movflags', '+faststart', str(dst)]

    t0 = time.time()
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        return f'FAIL {rel} :: {r.stderr.strip()[:160]}'
    el = time.time() - t0
    out_mb = dst.stat().st_size / 1e6
    if out_mb >= 100:
        return f'WARN {rel} 压完仍 {out_mb:.0f}MB（超限，需再压）'
    return (f'{tag} {src_mb:6.1f}MB → {out_mb:5.1f}MB  ({out_mb/src_mb*100:4.0f}%) '
            f'{dur/60:4.0f}分 用时{el/60:4.1f}分 {dur/el if el else 0:4.1f}x  {rel}')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--only', default='')
    ap.add_argument('--jobs', type=int, default=2)
    a = ap.parse_args()

    vids = sorted(p for p in ROOT.rglob('*.mp4') if 'kb' not in p.parts and 'compressed' not in p.parts)
    if a.only:
        vids = [p for p in vids if a.only in str(p)]
    if not vids:
        print('没有匹配的视频'); return
    OUT.mkdir(parents=True, exist_ok=True)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    print(f'待压 {len(vids)} 集，并行 {a.jobs}', flush=True)

    def work(p):
        line = encode(p)
        with open(LOG, 'a', encoding='utf8') as fp:
            fp.write(line + '\n')
        print(line, flush=True)
        return line

    t0 = time.time()
    with ThreadPoolExecutor(max_workers=a.jobs) as ex:
        list(ex.map(work, vids))
    tot = sum(p.stat().st_size for p in OUT.rglob('*.mp4'))
    print(f'[done] {len(vids)} 集，总 {tot/1e6:.0f}MB，耗时 {(time.time()-t0)/60:.0f} 分', flush=True)


if __name__ == '__main__':
    main()
