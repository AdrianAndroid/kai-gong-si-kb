# 设计

## 决策 1：视频转写走 mediakit 云端，不本地跑 whisper

机器是 Intel i7-8700B（6 核 12 线程，无 Apple Silicon 加速）。本地 faster-whisper medium 在纯 CPU 上大约 1–1.5 倍实时，20 小时音频要跑 15 小时以上，且中文标点和术语错得更多。
`mediakit-cli video asr-subtitles` 异步云端识别，中文质量更好，且同源可调 `video-ocr` 拿到画面文字——这一步同时解决"PPT 内容只在画面上"的漏检问题。
代价：音频要上传云端；单集任务异步需轮询。

## 决策 2：OCR 必须切片

`video-ocr` 限制单文件 ≤ 10 分钟，而 121 集里有 56 集超过 10 分钟（最长 33.6 分钟）。
方案：`ffmpeg -c copy` 无损切片到 9 分钟一段（不重编码，快且不损画质），OCR 后把分段偏移量加回原始时间戳。

## 决策 3：文档按结构切、视频按语义切

- 文档：以标题层级为边界切块（法律条款、模板不能从中间断），单块上限约 800 字，超长按段落再分，保留「文件路径 + 标题路径 + 页码」。
- 视频：转写段落按时间间隔（>3s 空白）与长度（180 字）合并，再按 30–60 秒窗口聚合，块内保留起始 mm:ss。

## 决策 4：BM25 + 向量混合检索

- BM25（jieba 分词）负责精确术语命中：`认缴`、`一般纳税人`、`ICP`、`竞业限制` 这类词必须打中。
- 向量（bge-small-zh-v1.5）负责口语化提问：`我开公司要准备多少钱` 对应 `注册资本`、`实缴`。
- 两路结果用 RRF（倒数排名融合）合并，取 top-k 交给上层作答。
- 向量不可用时自动退化为 BM25 + 字符 n-gram，不影响可用性。

## 决策 5：入口是对话，不是网页

封装用户级 Skill `company-kb`，约定：任何关于开公司的问题，先跑 `kb/bin/ask.py` 取证据，再基于证据作答并标注出处。
不引入常驻服务，不占端口，不需要额外登录；脚本调用毫秒级。

## 数据结构

```
kb/
├── bin/            # prepare_docs.py / transcribe.py / build_index.py / ask.py
├── data/
│   ├── normalized/ # .doc → .docx 转换产物
│   ├── doc_chunks.jsonl
│   ├── video_chunks.jsonl
│   └── index/      # bm25.pkl / vectors.npy / meta.json
├── notes/          # 每集视频的 Markdown 笔记（人可读）
└── logs/           # 转写任务日志、失败清单（支持断点续跑）
```
