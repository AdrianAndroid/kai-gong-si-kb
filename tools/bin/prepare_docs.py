#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""prepare_docs.py —— 把 /Volumes/wd/开公司 下的文档类文件解析为带出处的文本块。

输出：kb/data/doc_chunks.jsonl
每条：{id, source, course, kind, title_path, loc, text, chars}
  source      相对路径（可直接定位原文件）
  title_path  标题面包屑，如 "第二章 股东权利 > 一、知情权"
  loc         pdf 为 "p12"，pptx 为 "slide 5"，其余为空
  kind        doc / pdf / sheet / slide / text
"""
import os, sys, json, hashlib, subprocess, re, traceback
from pathlib import Path

ROOT = Path('/Volumes/wd/开公司')
KB = ROOT / 'kb'
NORM = KB / 'data' / 'normalized'
OUT = KB / 'data' / 'doc_chunks.jsonl'
MAXLEN = 900
MINLEN = 60

SKIP_DIRS = {'kb', 'openspec', '.git', 'node_modules'}
DOC_EXT = {'.docx', '.doc', '.pdf', '.txt', '.md', '.xlsx', '.xls', '.pptx'}


def clean(t: str) -> str:
    t = t.replace('\u3000', ' ').replace('\xa0', ' ')
    t = re.sub(r'[ \t]+', ' ', t)
    t = re.sub(r'\n{3,}', '\n\n', t)
    return t.strip()


def md5(s: str) -> str:
    return hashlib.md5(s.encode('utf8')).hexdigest()[:10]


# ---------- .doc → .docx ----------
def to_docx(path: Path):
    """老式 .doc 转 docx，结果缓存到 normalized/。返回 docx 路径或 None"""
    NORM.mkdir(parents=True, exist_ok=True)
    cache = NORM / (md5(str(path)) + '.docx')
    if cache.exists() and cache.stat().st_size > 1000:
        return cache
    ok = False
    try:
        subprocess.run(['textutil', '-convert', 'docx', '-output', str(cache), str(path)],
                       check=True, capture_output=True, timeout=60)
        ok = cache.exists() and cache.stat().st_size > 1000
    except Exception:
        ok = False
    if not ok:
        try:  # LibreOffice 兜底
            subprocess.run(['soffice', '--headless', '--convert-to', 'docx', '--outdir', str(NORM), str(path)],
                           check=True, capture_output=True, timeout=120)
            cand = NORM / (path.stem + '.docx')
            if cand.exists():
                cand.replace(cache)
                ok = cache.exists()
        except Exception:
            ok = False
    return cache if ok else None


# ---------- 标题层级 ----------
HEAD_RE = re.compile(r'^(?:heading|标题)\s*([1-9])', re.I)
# 中文文档常见的手写章节号：第X章/第X条、一、、1.1、(一)
PSEUDO_HEAD = re.compile(
    r'^(?:第[一二三四五六七八九十百零]+[章节条款部分]|[一二三四五六七八九十]+[、．.]|'
    r'\d+(?:\.\d+)+(?=\s|[^\d])|\d+[、．]|[（(][一二三四五六七八九十]+[）)])\s*\S')


def heading_level(style_name: str) -> int:
    m = HEAD_RE.match(style_name or '')
    return int(m.group(1)) if m else 0


def iter_docx_blocks(doc):
    """按文档顺序产出 ('p', text, style) 与 ('table', rows)"""
    from docx.table import Table
    from docx.text.paragraph import Paragraph
    body = doc.element.body
    for child in body.iterchildren():
        tag = child.tag.split('}')[-1]
        if tag == 'p':
            p = Paragraph(child, doc)
            yield 'p', p.text, (p.style.name if p.style is not None else '')
        elif tag == 'tbl':
            t = Table(child, doc)
            rows = []
            for r in t.rows:
                cells = [clean(c.text).replace('\n', ' ') for c in r.cells]
                if any(cells):
                    rows.append(' | '.join(cells))
            yield 'table', '\n'.join(rows), ''


def chunk_blocks(blocks, source, course, kind, loc=''):
    """按标题边界 + 长度切块。无标准标题样式时用中文序号行兜底，
    仍无线索则以文件名作为标题路径，保证每条都有可读出处。"""
    chunks, stack, buf = [], {}, []
    path_of = Path(source).stem

    def flush():
        nonlocal buf
        text = clean('\n'.join(buf))
        if len(text) >= MINLEN or (text and len(chunks) < 5):
            chunks.append(dict(source=source, course=course, kind=kind,
                               title_path=path_of, loc=loc, text=text))
        buf = []

    for typ, text, style in blocks:
        text = clean(text)
        if not text:
            continue
        lvl = heading_level(style) if typ == 'p' else 0
        if not lvl and typ == 'p' and len(text) <= 40 and PSEUDO_HEAD.match(text):
            lvl = 1 if (text.startswith('第') or re.match(r'^[一二三四五六七八九十]+[、．.]', text)) else 2
        if lvl or (typ == 'p' and style.startswith(('Title', 'Subtitle'))):
            # 新标题：先收尾，再更新面包屑
            flush()
            stack[lvl or 1] = text[:60]
            for k in list(stack):
                if k > (lvl or 1):
                    stack.pop(k)
            path_of = ' > '.join(stack[k] for k in sorted(stack))
            continue
        # 超长段落单独切
        if len(text) > MAXLEN:
            for i in range(0, len(text), MAXLEN):
                buf.append(text[i:i + MAXLEN])
                if len(clean('\n'.join(buf))) >= MAXLEN:
                    flush()
            continue
        buf.append(text)
        if len(clean('\n'.join(buf))) >= MAXLEN:
            flush()
    flush()
    # 合并过短块到前一块
    merged = []
    for c in chunks:
        if merged and len(c['text']) < MINLEN and len(merged[-1]['text']) < MAXLEN * 1.5:
            merged[-1]['text'] += '\n' + c['text']
        else:
            merged.append(c)
    return merged


def parse_docx(path: Path, source: str, course: str):
    import docx
    d = docx.Document(str(path))
    return chunk_blocks(iter_docx_blocks(d), source, course, 'doc')


def parse_pdf(path: Path, source: str, course: str):
    from pypdf import PdfReader
    r = PdfReader(str(path))
    total = 0
    pages = []
    for i, pg in enumerate(r.pages, 1):
        try:
            t = clean(pg.extract_text() or '')
        except Exception:
            t = ''
        total += len(t)
        if t:
            pages.append((i, t))
    if total < 30:  # 无文字层
        return [], 'scanned'
    out = []
    buf, start_page = [], None

    def flush(pg):
        nonlocal buf, start_page
        text = clean('\n'.join(buf))
        if text:
            out.append(dict(source=source, course=course, kind='pdf',
                            title_path=path.stem, loc=f'p{start_page}-{pg}', text=text))
        buf = []

    cur = None
    for pg, t in pages:
        if cur is None:
            cur, start_page = pg, pg
        buf.append(t)
        if len(clean('\n'.join(buf))) >= MAXLEN:
            flush(pg)
            cur = None
    flush(cur or start_page)
    return out, ''


def parse_txt(path: Path, source: str, course: str):
    raw = path.read_text(encoding='utf8', errors='ignore')
    kind = 'text'
    if path.suffix.lower() == '.md':
        # 按 # 标题切
        blocks, cur_h, buf = [], path.stem, []
        for line in raw.split('\n'):
            if re.match(r'^#{1,4}\s+', line):
                if buf:
                    blocks.append(('p', '\n'.join(buf), 'Heading 1'))
                    buf = []
                blocks.append(('p', re.sub(r'^#+\s+', '', line), 'Heading 1'))
            else:
                buf.append(line)
        if buf:
            blocks.append(('p', '\n'.join(buf), ''))
        return chunk_blocks(blocks, source, course, kind)
    return chunk_blocks([('p', p, '') for p in re.split(r'\n\s*\n', raw) if p.strip()],
                        source, course, kind)


def parse_xlsx(path: Path, source: str, course: str):
    import openpyxl
    wb = openpyxl.load_workbook(str(path), read_only=True, data_only=True)
    out = []
    for ws in wb.worksheets:
        rows = []
        for r in ws.iter_rows(values_only=True):
            cells = [str(c).strip() for c in r if c is not None and str(c).strip()]
            if cells:
                rows.append(' | '.join(cells))
        if not rows:
            continue
        text = clean('\n'.join(rows))
        for i in range(0, max(len(text), 1), MAXLEN):
            part = clean(text[i:i + MAXLEN])
            if part:
                out.append(dict(source=source, course=course, kind='sheet',
                                title_path=f'{ws.title}', loc='', text=part))
    return out


def parse_pptx(path: Path, source: str, course: str):
    from pptx import Presentation
    prs = Presentation(str(path))
    out = []
    for i, slide in enumerate(prs.slides, 1):
        texts = []
        for sh in slide.shapes:
            if sh.has_text_frame:
                t = clean(sh.text_frame.text)
                if t:
                    texts.append(t)
        if texts:
            out.append(dict(source=source, course=course, kind='slide',
                            title_path=path.stem, loc=f'slide {i}',
                            text=clean('\n'.join(texts))))
    return out


def course_of(rel: Path) -> str:
    parts = rel.parts
    return parts[0] if len(parts) > 1 else ''


def main():
    files = []
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fn in filenames:
            p = Path(dirpath) / fn
            if p.suffix.lower() in DOC_EXT:
                files.append(p)
    files.sort()
    print(f'待解析文件: {len(files)}', flush=True)

    all_chunks, fails, scanned = [], [], []
    for i, p in enumerate(files, 1):
        rel = p.relative_to(ROOT)
        source = str(rel)
        course = course_of(rel)
        try:
            ext = p.suffix.lower()
            flag = ''
            if ext == '.doc':
                dx = to_docx(p)
                if dx is None:
                    fails.append((source, 'doc转换失败'))
                    continue
                chunks = parse_docx(dx, source, course)
            elif ext == '.docx':
                chunks = parse_docx(p, source, course)
            elif ext == '.pdf':
                chunks, flag = parse_pdf(p, source, course)
                if flag == 'scanned':
                    scanned.append(source)
                    print(f'  [无文字层] {source}', flush=True)
            elif ext in ('.txt', '.md'):
                chunks = parse_txt(p, source, course)
            elif ext in ('.xlsx', '.xls'):
                chunks = parse_xlsx(p, source, course) if ext == '.xlsx' else []
                if ext == '.xls':
                    fails.append((source, '老xls跳过'))
                    continue
            elif ext == '.pptx':
                chunks = parse_pptx(p, source, course)
            else:
                continue
            for c in chunks:
                c['chars'] = len(c['text'])
                c['id'] = md5(c['source'] + c['title_path'] + c['text'][:200])
            all_chunks.extend(chunks)
            if i % 25 == 0:
                print(f'  进度 {i}/{len(files)} 累计块 {len(all_chunks)}', flush=True)
        except Exception as e:
            fails.append((source, f'{type(e).__name__}: {e}'))
            traceback.print_exc()

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT, 'w', encoding='utf8') as f:
        for c in all_chunks:
            f.write(json.dumps(c, ensure_ascii=False) + '\n')

    (KB / 'logs').mkdir(parents=True, exist_ok=True)
    with open(KB / 'logs' / 'doc_fails.json', 'w', encoding='utf8') as f:
        json.dump(dict(fails=fails, scanned=scanned), f, ensure_ascii=False, indent=1)

    print(f'\n完成: {len(all_chunks)} 块, {sum(c["chars"] for c in all_chunks):,} 字')
    print(f'失败 {len(fails)} / 无文字层PDF {len(scanned)}')
    for s, e in fails[:10]:
        print('  FAIL', s, e)


if __name__ == '__main__':
    main()
