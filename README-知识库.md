# 开公司知识库

把 `/Volumes/wd/开公司` 下的课程资料变成**能问、能溯源**的知识库。

## 怎么用

**方式一：直接在这里问（推荐）**

在对话里随口问，例如：

- 两个人合伙开公司，股权怎么分才不打架？
- 小规模纳税人什么时候要转一般纳税人？
- 竞业限制协议有没有现成模板？
- ICP 备案要先准备什么？

回答会给结论 + 要点，并标注出处（`《课程名》/ 集名 / 03:12` 或 `文件路径`），可以直接跳回原片或原文件核对。

**方式二：双击打开终端问答**

`开公司知识库.command`——双击即打开循环问答界面，输入问题回车出结果，`q` 退出。

**方式三：命令行**

```bash
bash kb/bin/kb.sh "注册资本写多少合适" --k 8
bash kb/bin/kb.sh "劳动合同必备条款" --course 人事
bash kb/bin/kb.sh "股权激励方式" --kind video
bash kb/bin/kb.sh --list-courses        # 看有哪些课程与合集
```

## 资料覆盖

| 类型 | 数量 | 处理方式 |
|---|---|---|
| 文档（docx/doc/pdf/xlsx/pptx） | 152 份 | python-docx / pypdf / openpyxl 解析，按标题层级切块 |
| 课程视频 | 121 集 / 20.0 小时 | mediakit 云端 ASR 转写 + 画面 OCR，保留 mm:ss |
| 视频笔记 | 每集一份 Markdown | 见 `kb/notes/`，可直接阅读 |

## 结构

```
开公司知识库.command   双击入口
kb/
├── bin/               prepare_docs.py  build_chunks.py  build_index.py
│                      transcribe.py    video2md.py      ask.py  kb.sh  settle.sh
├── data/
│   ├── doc_chunks.jsonl       文档文本块
│   ├── video_chunks.jsonl     视频文本块（ASR 段落 + 同期画面文字）
│   ├── asr/  ocr/             转写原始结果（断点续跑用）
│   ├── normalized/            .doc → .docx 转换缓存
│   ├── segs/                  长视频 OCR 切片
│   └── index/                 bm25.pkl + vectors.npz + meta.json
├── notes/             每集视频 Markdown 笔記
└── logs/              进度、失败清单、轮询日志
```

## 检索原理

1. 中文分词（jieba）建 **BM25** 索引，负责术语精确命中（认缴、一般纳税人、竞业限制…）
2. **bge-small-zh-v1.5** 向量索引（512 维），负责口语化提问（"我开公司要准备多少钱"）
3. 两路用 RRF 融合，同源文件最多取 2 块，保证 top-k 覆盖不同资料

## 增量维护

新增文件后：

```bash
KB=/Volumes/wd/开公司/kb
$KB/.venv/bin/python $KB/bin/prepare_docs.py      # 解析文档
$KB/.venv/bin/python $KB/bin/transcribe.py asr    # 新视频转写（自动跳过已完成）
$KB/.venv/bin/python $KB/bin/transcribe.py notes  # 生成笔记
$KB/.venv/bin/python $KB/bin/build_chunks.py      # 视频切块
$KB/.venv/bin/python $KB/bin/build_index.py       # 重建索引（秒级~分钟级）
$KB/.venv/bin/python $KB/bin/transcribe.py status # 看进度
```

## 已知边界

- **画面 OCR 是长尾**：单集约 25 分钟云端处理，121 集需要多轮后台跑；未完成时知识库仍可用（ASR 文本已覆盖绝大部分内容），OCR 完成后重建索引即可增强。
- 视频/文档内容录制于 2022 年前后，**税率、补贴额度、办事流程有实效性**，办理前需核对当地最新规定。
- 152 份文档中：
  - `【小辉】自己动手注册公司资料包/3-给⾃⼰的公司起⼀个好名字.pdf` 文件本身不是 PDF（加密/损坏数据，10.8MB），无法解析；
  - 其余 151 份全部入库。
- 检索是"证据召回"，最终结论由模型阅读证据后给出；不做税务筹划的法律意见。
