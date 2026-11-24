#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""build_index.py —— 建混合检索索引（BM25 + 向量）

读 kb/data/doc_chunks.jsonl + video_chunks.jsonl
写 kb/data/index/{bm25.pkl, vectors.npz, meta.json}
"""
import json, pickle, re
from pathlib import Path
import numpy as np
import jieba
from rank_bm25 import BM25Okapi

ROOT = Path('/Volumes/wd/开公司')
KB = ROOT / 'kb'
DATA = KB / 'data'
IDX = DATA / 'index'
MODEL = 'BAAI/bge-small-zh-v1.5'

STOP = set('的 了 是 和 与 及 在 有 我 你 他 她 它 这 那 就 都 也 还 要 会 能 吗 呢 吧 啊 呀 个 上 下 不 没 很 很 多 一 二 三 怎么 什么 如何 哪些 可以 需要 应该 请问 一下 我们 他们 你们'.split())
NOISE = re.compile(r'^[\s\W_]+$')


def load_rows():
    rows = []
    for fn in ('doc_chunks.jsonl', 'video_chunks.jsonl'):
        p = DATA / fn
        if not p.exists():
            continue
        for line in p.read_text(encoding='utf8').splitlines():
            if line.strip():
                rows.append(json.loads(line))
    return rows


def tokens(text):
    t = [w.lower() for w in jieba.lcut_for_search(text)]
    return [w for w in t if w not in STOP and not NOISE.match(w)]


def main():
    IDX.mkdir(parents=True, exist_ok=True)
    rows = load_rows()
    print(f'载入 {len(rows)} 块（doc {sum(1 for r in rows if r["kind"]!="video")} / video {sum(1 for r in rows if r["kind"]=="video")}）')

    # BM25
    corpus = [tokens(' '.join([r['text'], r.get('title_path', ''), r.get('course', ''), Path(r['source']).stem]))
              for r in rows]
    bm25 = BM25Okapi(corpus)
    pickle.dump(dict(bm25=bm25, corpus=corpus), open(IDX / 'bm25.pkl', 'wb'))

    # 向量
    vecs = None
    try:
        from fastembed import TextEmbedding
        m = TextEmbedding(model_name=MODEL)
        embs = list(m.embed([r['text'][:512] for r in rows], batch_size=64))
        vecs = np.asarray(embs, dtype=np.float32)
        vecs /= (np.linalg.norm(vecs, axis=1, keepdims=True) + 1e-8)
        print(f'向量 {vecs.shape}')
    except Exception as e:
        print(f'[警告] 向量索引不可用，将退化为 BM25: {type(e).__name__}: {e}')

    if vecs is not None:
        np.savez_compressed(IDX / 'vectors.npz', vecs=vecs)
    meta = dict(n=len(rows), model=MODEL, has_vectors=vecs is not None,
                rows=[{k: r.get(k) for k in ('id', 'source', 'course', 'kind', 'title_path', 'loc', 'chars')}
                      for r in rows])
    (IDX / 'meta.json').write_text(json.dumps(meta, ensure_ascii=False), encoding='utf8')
    print(f'索引完成 → {IDX}')


if __name__ == '__main__':
    main()
