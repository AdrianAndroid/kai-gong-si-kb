#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ask.py —— 知识库检索（BM25 + 向量 RRF 融合）

用法：
  ask.py "两个人合伙开公司股权怎么分"
  ask.py "小规模纳税人转一般纳税人条件" --k 6
  ask.py "劳动合同必备条款" --course 人事 --json
  ask.py --list-courses
"""
import sys, json, pickle, argparse, re
from pathlib import Path
import numpy as np

ROOT = Path('/Volumes/wd/开公司')
IDX = ROOT / 'kb' / 'data' / 'index'
STOP = set('的 了 是 和 与 及 在 有 我 你 他 她 它 这 那 就 都 也 还 要 会 能 吗 呢 吧 啊 呀 个 上 下 不 没 很 多 一 二 三 怎么 什么 如何 哪些 可以 需要 应该 请问 一下 我们 他们 你们'.split())
NOISE = re.compile(r'^[\s\W_]+$')
RRF_K = 60

_model = None


def get_model():
    global _model
    if _model is None:
        from fastembed import TextEmbedding
        _model = TextEmbedding(model_name='BAAI/bge-small-zh-v1.5')
    return _model


def tokens(text):
    import jieba
    t = [w.lower() for w in jieba.lcut_for_search(text)]
    return [w for w in t if w not in STOP and not NOISE.match(w)]


class KB:
    def __init__(self):
        self.meta = json.loads((IDX / 'meta.json').read_text(encoding='utf8'))
        self.rows = self.meta['rows']
        with open(IDX / 'bm25.pkl', 'rb') as f:
            d = pickle.load(f)
        self.bm25, self.corpus = d['bm25'], d['corpus']
        self.vecs = None
        if self.meta.get('has_vectors') and (IDX / 'vectors.npz').exists():
            self.vecs = np.load(IDX / 'vectors.npz')['vecs']

    def search(self, q, k=8, course=None, kind=None, pool=40):
        n = len(self.rows)
        bm_scores = self.bm25.get_scores(tokens(q))
        order_b = np.argsort(bm_scores)[::-1][:pool]
        fused = {}
        for rank, i in enumerate(order_b):
            if bm_scores[i] > 0:
                fused[i] = fused.get(i, 0) + 1.0 / (RRF_K + rank + 1)
        if self.vecs is not None:
            qv = np.asarray(list(get_model().embed([q]))[0], dtype=np.float32)
            qv /= (np.linalg.norm(qv) + 1e-8)
            sims = self.vecs @ qv
            order_v = np.argsort(sims)[::-1][:pool]
            for rank, i in enumerate(order_v):
                fused[i] = fused.get(i, 0) + 1.0 / (RRF_K + rank + 1)
        # 过滤 + 同源去重（同一文件最多 2 块，保证 top-k 覆盖更多资料）
        cand, per_src = [], {}
        for i, s in sorted(fused.items(), key=lambda x: -x[1]):
            r = self.rows[i]
            if course and course not in (r['course'] + r['source']):
                continue
            if kind and r['kind'] != kind and not (kind == 'doc' and r['kind'] in ('doc', 'pdf', 'sheet', 'slide', 'text')):
                continue
            src = r['source']
            if per_src.get(src, 0) >= 2:
                continue
            per_src[src] = per_src.get(src, 0) + 1
            cand.append((s, int(i)))
            if len(cand) >= k:
                break
        return cand

    def text_of(self, i):
        # 文本只在 chunks 文件里，按需惰性读取
        if not hasattr(self, '_texts'):
            self._texts = {}
            for fn in ('doc_chunks.jsonl', 'video_chunks.jsonl'):
                p = ROOT / 'kb' / 'data' / fn
                if p.exists():
                    for line in p.read_text(encoding='utf8').splitlines():
                        if line.strip():
                            c = json.loads(line)
                            self._texts[c['id']] = c['text']
        return self._texts.get(self.rows[i]['id'], '')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('query', nargs='?', default='')
    ap.add_argument('--k', type=int, default=8)
    ap.add_argument('--course', default=None)
    ap.add_argument('--kind', default=None)
    ap.add_argument('--json', action='store_true')
    ap.add_argument('--chars', type=int, default=350)
    ap.add_argument('--list-courses', action='store_true')
    a = ap.parse_args()

    if not IDX.exists():
        print('索引不存在，请先运行 bin/build_index.py'); return
    kb = KB()

    if a.list_courses:
        from collections import Counter
        c = Counter(r['course'] for r in kb.rows)
        for k2, v in c.most_common():
            print(f'{v:5d}  {k2}')
        return

    if not a.query:
        ap.print_help(); return

    hits = kb.search(a.query, k=a.k, course=a.course, kind=a.kind)
    top = hits[0][0] if hits else 1.0
    out = []
    for s, i in hits:
        r = kb.rows[i]
        t = kb.text_of(i)
        short = t[:a.chars] + ('…' if len(t) > a.chars else '')
        out.append(dict(score=round(s / top, 3), source=r['source'], course=r['course'],
                        kind=r['kind'], title_path=r['title_path'], loc=r['loc'],
                        text=short))
    if a.json:
        print(json.dumps(out, ensure_ascii=False, indent=1)); return

    print(f'问题：{a.query}    命中 {len(out)} 条\n' + '=' * 70)
    for n, o in enumerate(out, 1):
        where = f"{o['title_path']}" + (f"  [{o['loc']}]" if o['loc'] else '')
        print(f"\n[{n}] {o['score']:.3f}  {o['kind']}  《{o['course']}》")
        print(f"    出处：{where}")
        print(f"    文件：{o['source']}")
        print('    ' + o['text'].replace('\n', '\n    '))


if __name__ == '__main__':
    main()
