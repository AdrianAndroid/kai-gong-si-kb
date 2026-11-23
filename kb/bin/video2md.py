#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
video2md —— 课程视频 → Markdown 笔记
输入：mediakit-cli 的 asr.json / ocr.json（同一视频）
输出：结构化的 .md 文件

OCR 去噪策略（针对屏幕录制课程）：
  1. 按坐标丢弃固定 UI 区域：左上角水印、右上角台标、右下角时钟、底部任务栏
  2. 丢弃命中广告关键词的文本（加微信、招收代理、月入万、qq 等）
  3. 丢弃纯时间/日期/数字/标点碎片
  4. 相邻断言去重（同一行文字连续识别多次只保留首次）
"""
import json, re, sys, os

AD_PAT = re.compile(r'加微信|微信|招收代理|代理|月入万|月入|扫码|二维码|QQ|qq群|免费领取|私信|关注|点赞|课程优惠|最新好课')
JUNK_PAT = re.compile(r'^[\s\d:.,\-/|_、。，%()（）+"·]*$')
# 烧录时间戳水印：2-2115:11:03 / -21 15:11:0 / 15:11
STAMP_PAT = re.compile(r'^\s*[-\d]{0,6}\s*(?:[\d]{0,2}[-/])?[\d]{0,2}\s*\d{1,2}:\d{2}(:\d{2})?\s*$')
# 屏幕/窗口/讲师名等固定标签
UI_LABEL_PAT = re.compile(r'屏幕|桌面|共享|的幕|正在加载|窗口|任务栏|浏览器|开始菜单')
# 过碎的 UI 碎片（≤3 字且非中文短语）
FRAG_PAT = re.compile(r'^[\u4e00-\u9fa5]{0,2}$|^[\dA-Za-z+\-·|]{1,3}$')

def is_ui_noise(t, loc, w=1280, h=720):
    """按归一化坐标丢弃界面固定元素"""
    if not loc: return False
    y_top, y_bot = loc['top_left_y'], loc['bottom_right_y']
    x_left, x_right = loc['top_left_x'], loc['bottom_right_x']
    # 顶部 8%：水印/台标/浏览器标签
    if y_bot <= h * 0.09: return True
    # 底部 8%：任务栏/时间/系统托盘
    if y_top >= h * 0.92: return True
    # 右下角时钟区
    if y_top >= h * 0.90 and x_left >= w * 0.90: return True
    return False

def dedupe_subtitles(subs):
    """OCR 结果去噪 + 相邻去重"""
    out, last_text = [], ''
    for s in subs:
        t = s.get('subtitle_text', '').strip()
        if not t: continue
        if JUNK_PAT.match(t) or STAMP_PAT.match(t): continue
        if AD_PAT.search(t) or UI_LABEL_PAT.search(t): continue
        if is_ui_noise(t, s.get('text_location')): continue
        # 同一文字重复出现（OCR 每帧都在识别同一行）
        norm = re.sub(r'[^\u4e00-\u9fa5A-Za-z0-9]', '', t)
        if not norm or norm == last_text: continue
        # 纯 UI 碎片：短且不是完整语句（无标点、无数字语义）
        if len(norm) <= 3 and not re.search(r'[，。、：；！？]', t) and not re.search(r'\d', t):
            continue
        last_text = norm
        out.append({'t': round(s.get('start_time', 0)), 'text': t})
    return out

def asr_paragraphs(subs, gap=3.0, maxlen=180):
    """ASR 按时间间隔合并成自然段，去掉语气词碎片"""
    paras, buf, start, prev_end = [], [], None, None
    for s in subs:
        t = s.get('subtitle_text', '').strip()
        if not t: continue
        st = s.get('start_time', 0)
        if prev_end is not None and (st - prev_end > gap or len(''.join(buf)) > maxlen):
            paras.append((start, ''.join(buf)))
            buf, start = [], None
        if start is None: start = st
        buf.append(t)
        prev_end = s.get('end_time', st)
    if buf: paras.append((start, ''.join(buf)))
    return paras

def mmss(sec):
    sec = int(round(sec))
    return f'{sec//60:02d}:{sec%60:02d}'

def build(video_name, course, asr_json, ocr_json, out_path):
    a = json.load(open(asr_json)); o = json.load(open(ocr_json)) if ocr_json else {'subtitles': []}
    dur = a.get('duration', 0)
    paras = asr_paragraphs(a.get('subtitles', []))
    outline = dedupe_subtitles(o.get('subtitles', []))
    # 画面文字按时间聚合成"章节线索"（30 秒窗口内合并）
    windows, cur = [], []
    for it in outline:
        if cur and it['t'] - cur[-1]['t'] > 30:
            windows.append(cur); cur = []
        cur.append(it)
    if cur: windows.append(cur)

    lines = [f'# {video_name}', '']
    lines += [f'> 课程：{course}  ',
              f'> 时长：{mmss(dur)}  ',
              f'> 来源：`{os.path.basename(video_name)}`  ',
              f'> 识别：ASR 语音转写' + ('＋画面 OCR（已剔除水印/时钟/广告）' if ocr_json and windows else ''), '']
    if windows:
        lines += ['## 画面要点（按时间）', '']
        for w in windows:
            head = ' / '.join(dict.fromkeys(i['text'] for i in w[:6]))
            if len(head) > 160: head = head[:160] + '…'
            lines.append(f'- **[{mmss(w[0]["t"])}]** {head}')
        lines.append('')
    lines += ['## 正文转写', '']
    for st, txt in paras:
        lines.append(f'**[{mmss(st)}]** {txt}')
        lines.append('')
    open(out_path, 'w', encoding='utf8').write('\n'.join(lines))
    return dict(chars=sum(len(t) for _, t in paras), paras=len(paras), ocr_raw=len(o.get('subtitles', [])), ocr_kept=len(outline))

if __name__ == '__main__':
    print(build(*sys.argv[1:6]))
