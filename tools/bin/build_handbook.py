#!/usr/bin/env python3
"""build_handbook.py — 从 docs/ 生成直读版 handbook/。

用法:  python3 tools/bin/build_handbook.py [--root 仓库根目录]
产物:  handbook/00-序与使用说明.md … handbook/10-附录-清单与模板.md
       handbook/开公司完全手册.md  (单文件)
       handbook/开公司完全手册.txt (纯文本)
"""

import argparse
import os
import re
import sys
from pathlib import Path

# ── 章节顺序 ──────────────────────────────────────────────────────
CHAPTERS = [
    ("00-序与使用说明", ["docs/index.md", "docs/00-新手路线.md"]),
    ("01-注册登记",     ["docs/01-注册登记/index.md"]),
    ("02-财务合规",     ["docs/02-财务合规/index.md"]),
    ("03-股权分配",     ["docs/03-股权分配/index.md"]),
    ("04-人力管理",     ["docs/04-人力管理/index.md"]),
    ("05-知识产权",     ["docs/05-知识产权/index.md"]),
    ("06-创业融资",     ["docs/06-创业融资/index.md"]),
    ("07-风险意识",     ["docs/07-风险意识/index.md"]),
    ("08-变更与注销",   ["docs/08-变更与注销/index.md"]),
    ("09-ICP备案与上线", ["docs/09-ICP备案与上线/index.md"]),
    ("10-附录-清单与模板", [
        "docs/checklists/注册公司材料清单.md",
        "docs/checklists/税务日历.md",
        "docs/checklists/用工合规自查.md",
        "docs/templates/index.md",
    ]),
]

AUTO_HEADER = "<!-- AUTO-GENERATED from docs/，勿手改 -->\n"

# ── 工具函数 ──────────────────────────────────────────────────────

def strip_front_matter(text: str) -> str:
    """去掉 YAML front-matter（--- ... ---）。"""
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            text = text[end + 4:].lstrip("\n")
    return text


def strip_mkdocs_syntax(text: str) -> str:
    """去掉 mkdocs 专有语法: !!! note, === "tab", ???  等。"""
    lines = text.split("\n")
    out = []
    skip_block = False
    for line in lines:
        # !!! note / ??? note / ???+ note 等 admonition
        if re.match(r'^\s*(!!!|\?\?\?\+?)\s', line):
            skip_block = True
            continue
        # tab 切换块
        if re.match(r'^\s*===\s+"', line):
            skip_block = True
            continue
        if skip_block:
            # 跳过缩进的附属行
            if line.strip() == "" or line.startswith("    ") or line.startswith("\t"):
                continue
            skip_block = False
        out.append(line)
    return "\n".join(out)


def strip_toc_links(text: str) -> str:
    """删掉「返回目录」「回到顶部」类链接。"""
    text = re.sub(r'\[返回目录\]\([^)]*\)', '', text)
    text = re.sub(r'\[回到顶部\]\([^)]*\)', '', text)
    return text


def build_anchor_map(root: Path) -> dict:
    """扫描所有 docs 页面，建立 heading → 章节号.小节号 的映射。"""
    anchor_map = {}  # (file_rel, anchor) -> "1.3 注册资本"
    for ch_num, (ch_name, pages) in enumerate(CHAPTERS):
        sec = 0
        for page_rel in pages:
            p = root / page_rel
            if not p.exists():
                continue
            text = p.read_text("utf-8")
            text = strip_front_matter(text)
            for line in text.split("\n"):
                m = re.match(r'^(#{2,3})\s+(.+)', line)
                if m:
                    level = len(m.group(1))
                    heading = m.group(2).strip()
                    if level == 2:
                        sec += 1
                        label = f"{ch_num}.{sec} {heading}"
                    else:
                        label = f"{ch_num}.{sec} {heading}"
                    anchor = heading_to_anchor(heading)
                    anchor_map[(page_rel, anchor)] = label
                    anchor_map[(page_rel, heading)] = label
    return anchor_map


def heading_to_anchor(heading: str) -> str:
    """把标题转成 markdown 锚点格式（简化版）。"""
    anchor = heading.lower().strip()
    anchor = re.sub(r'[^\w\u4e00-\u9fff\s-]', '', anchor)
    anchor = re.sub(r'\s+', '-', anchor)
    return anchor


def degrade_links(text: str, current_page: str, anchor_map: dict) -> str:
    """把相对路径链接降级为文字指引。

    [注册资本](01-注册登记/index.md#注册资本) → （见 1.3 注册资本）
    [文本](../other.md) → （见 文本）
    外部 URL 保留原样。
    """
    def replacer(m):
        display = m.group(1)
        target = m.group(2)
        # 外部链接保留
        if target.startswith("http://") or target.startswith("http://"):
            return m.group(0)
        # 图片保留
        if m.group(0).startswith("!["):
            return m.group(0)
        # 锚点链接
        if "#" in target:
            path, anchor = target.split("#", 1)
        else:
            path, anchor = target, None
        # 尝试匹配 anchor_map
        if anchor:
            # 归一化路径
            for (f, a), label in anchor_map.items():
                if a == anchor or a == anchor.lower():
                    return f"（见 {label}）"
        # 没找到精确匹配，降级为文字
        if display and display != target:
            return f"（见 {display}）"
        return f"（见 {display}）"

    # 匹配 [text](url) 格式，排除图片 ![text](url)
    text = re.sub(r'(?<!\!)\[([^\]]+)\]\(([^)]+)\)', replacer, text)
    return text


def text_word_count(text: str) -> int:
    """统计字数（中文字符 + 英文单词）。"""
    # 去掉 markdown 标记
    clean = re.sub(r'[#*`|>\-\[\]()!]', ' ', text)
    # 中文字符
    cn = len(re.findall(r'[\u4e00-\u9fff]', clean))
    # 英文单词
    en = len(re.findall(r'[a-zA-Z]+', clean))
    return cn + en


def to_plain_text(text: str) -> str:
    """markdown → 纯文本（去格式）。"""
    # 去掉 HTML 标签
    text = re.sub(r'<[^>]+>', '', text)
    # 去掉链接，保留文字
    text = re.sub(r'\[([^\]]+)\]\([^)]*\)', r'\1', text)
    # 去掉图片
    text = re.sub(r'!\[([^\]]*)\]\([^)]*\)', r'[\1]', text)
    # 标题去 #
    text = re.sub(r'^#{1,6}\s+', '', text, flags=re.MULTILINE)
    # 粗体/斜体
    text = re.sub(r'\*\*([^*]+)\*\*', r'\1', text)
    text = re.sub(r'\*([^*]+)\*', r'\1', text)
    text = re.sub(r'`([^`]+)`', r'\1', text)
    # 表格分隔行
    text = re.sub(r'^\|[\s\-\|:]+\|$', '', text, flags=re.MULTILINE)
    # 表格竖线转空格
    text = re.sub(r'\|', ' ', text)
    # 多余空行
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


# ── 主流程 ────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="从 docs/ 生成 handbook/")
    parser.add_argument("--root", default=str(Path(__file__).resolve().parent.parent.parent), help="仓库根目录")
    args = parser.parse_args()
    root = Path(args.root)
    docs_dir = root / "docs"
    handbook_dir = root / "handbook"
    handbook_dir.mkdir(exist_ok=True)

    anchor_map = build_anchor_map(root)

    chapter_texts = {}  # ch_name -> processed text
    total_docs_wc = 0
    total_handbook_wc = 0

    for ch_name, pages in CHAPTERS:
        parts = []
        for page_rel in pages:
            p = root / page_rel
            if not p.exists():
                print(f"  ⚠ 跳过不存在的文件: {page_rel}", file=sys.stderr)
                continue
            raw = p.read_text("utf-8")
            total_docs_wc += text_word_count(raw)

            text = strip_front_matter(raw)
            text = strip_mkdocs_syntax(text)
            text = strip_toc_links(text)
            text = degrade_links(text, page_rel, anchor_map)

            parts.append(text.strip())

        combined = "\n\n---\n\n".join(parts)
        chapter_texts[ch_name] = combined
        total_handbook_wc += text_word_count(combined)

    # ── 写分章 md ──
    for ch_name, _ in CHAPTERS:
        body = chapter_texts.get(ch_name, "")
        out = handbook_dir / f"{ch_name}.md"
        out.write_text(AUTO_HEADER + "\n" + body + "\n", "utf-8")
        print(f"  ✓ {out.relative_to(root)}")

    # ── 写单文件 md ──
    all_md = AUTO_HEADER + "\n# 开公司完全手册\n\n"
    all_md += "> 本手册由 docs/ 自动生成，线性阅读无需跳转。\n\n"
    for ch_name, _ in CHAPTERS:
        body = chapter_texts.get(ch_name, "")
        all_md += f"\n\n---\n\n{body}\n"
    single_md = handbook_dir / "开公司完全手册.md"
    single_md.write_text(all_md, "utf-8")
    print(f"  ✓ {single_md.relative_to(root)}")

    # ── 写单文件 txt ──
    txt = to_plain_text(all_md)
    # 验证：txt 中不应有 ]( 和 {{
    assert "](" not in txt, "TXT 中残留 markdown 链接 ]("
    assert "{{" not in txt, "TXT 中残留模板语法 {{{{"
    single_txt = handbook_dir / "开公司完全手册.txt"
    single_txt.write_text(txt, "utf-8")
    print(f"  ✓ {single_txt.relative_to(root)}")

    # ── 字数对比 ──
    diff_pct = abs(total_docs_wc - total_handbook_wc) / max(total_docs_wc, 1) * 100
    print(f"\n📊 字数对比:")
    print(f"   docs 总字数:     {total_docs_wc:,}")
    print(f"   handbook 总字数: {total_handbook_wc:,}")
    print(f"   差值:            {diff_pct:.1f}%  {'✅ < 5%' if diff_pct < 5 else '❌ ≥ 5%'}")

    if diff_pct >= 5:
        sys.exit(1)


if __name__ == "__main__":
    main()
