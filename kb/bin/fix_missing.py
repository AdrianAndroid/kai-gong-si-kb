#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""fix_missing.py —— 修 3 个常规解析失败的文件，把结果追加进 doc_chunks.jsonl

1) 流损坏 PDF：qpdf 修复 → pypdf 提取
2) zip 结构异常 docx：unzip + 解析 word/document.xml
3) 老式 .xls：xlrd
"""
import json, re, subprocess, tempfile, zipfile, hashlib
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).parent))
from prepare_docs import md5, clean, chunk_blocks, MAXLEN

ROOT = Path('/Volumes/wd/开公司')
KB = ROOT / 'kb'
OUT = KB / 'data' / 'doc_chunks.jsonl'


def pdf_recover(p: Path, source: str, course: str):
    from pypdf import PdfReader
    tmp = Path(tempfile.gettempdir()) / (md5(str(p)) + '_fix.pdf')
    subprocess.run(['qpdf', '--qdf', '--object-streams=disable', str(p), str(tmp)],
                   capture_output=True, timeout=120)
    target = tmp if tmp.exists() and tmp.stat().st_size > 1000 else p
    r = PdfReader(str(target), strict=False)
    pages = []
    for i, pg in enumerate(r.pages, 1):
        try:
            t = clean(pg.extract_text() or '')
        except Exception:
            t = ''
        if t:
            pages.append((i, t))
    if not pages:
        return []
    out, buf, sp = [], [], None
    for pg, t in pages:
        if sp is None:
            sp = pg
        buf.append(t)
        if len(clean('\n'.join(buf))) >= MAXLEN:
            out.append(dict(source=source, course=course, kind='pdf',
                            title_path=p.stem, loc=f'p{sp}-{pg}', text=clean('\n'.join(buf))))
            buf, sp = [], None
    if buf:
        out.append(dict(source=source, course=course, kind='pdf',
                        title_path=p.stem, loc=f'p{sp}', text=clean('\n'.join(buf))))
    return out


def docx_raw(p: Path, source: str, course: str):
    txt = ''
    with zipfile.ZipFile(str(p)) as z:
        names = [n for n in z.namelist() if n.endswith('document.xml')]
        if not names:
            return []
        xml = z.read(names[0]).decode('utf8', 'ignore')
    xml = re.sub(r'</w:p>', '\n', xml)
    xml = re.sub(r'<w:tab/>', '\t', xml)
    parts = re.findall(r'<w:t[^>]*>(.*?)</w:t>|(\n)', xml)
    for a, b in parts:
        txt += a if a else (b or '')
    txt = clean(re.sub(r'<[^>]+>', '', txt))
    if not txt:
        return []
    blocks = [('p', s, '') for s in re.split(r'\n+', txt) if s.strip()]
    chunks = chunk_blocks(blocks, source, course, 'doc')
    return chunks


def xls_read(p: Path, source: str, course: str):
    import xlrd
    wb = xlrd.open_workbook(str(p))
    out = []
    for sh in wb.sheets():
        rows = []
        for i in range(sh.nrows):
            cells = [str(c).strip() for c in sh.row_values(i) if str(c).strip()]
            if cells:
                rows.append(' | '.join(cells))
        text = clean('\n'.join(rows))
        for i in range(0, max(len(text), 1), MAXLEN):
            part = clean(text[i:i + MAXLEN])
            if part:
                out.append(dict(source=source, course=course, kind='sheet',
                                title_path=sh.name, loc='', text=part))
    return out


TARGETS = [
    ('7小辉-教你开公司/【小辉】自己动手注册公司资料包/3-给⾃⼰的公司起⼀个好名字.pdf', pdf_recover),
    ('88个创业全套必备资料合集-1/04品牌打造（重视品牌保护的企业走得更远）/专利小资料/判断能否申请专利.docx', docx_raw),
    ('88个创业全套必备资料合集-1/05合同协议大全（企业必备合同资源，模板范本直接套用）/27合同履约跟踪单.xls', xls_read),
]


def main():
    new = []
    for rel, fn in TARGETS:
        p = ROOT / rel
        course = rel.split('/')[0]
        try:
            chunks = fn(p, rel, course) or []
        except Exception as e:
            print(f'FAIL {rel}: {type(e).__name__}: {e}')
            continue
        for c in chunks:
            c['chars'] = len(c['text'])
            c['id'] = md5(c['source'] + c['title_path'] + c['text'][:200])
            new.append(c)
        print(f'{rel} → {len(chunks)} 块')
    with open(OUT, 'a', encoding='utf8') as f:
        for c in new:
            f.write(json.dumps(c, ensure_ascii=False) + '\n')
    print(f'追加 {len(new)} 块')


if __name__ == '__main__':
    main()
