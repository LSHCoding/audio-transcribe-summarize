---
name: audio-transcribe-summarize
description: >
  把本地音频 / 视频（wav/mp3/m4a/flac/mp4/mov/webm 等 ffmpeg 支持格式、中文 / 英文 / 中英混杂、可一次传多个按顺序无缝拼接）一条龙转成两份
  Markdown：修订过的 `{name}_文字版.md` 与结构化的 `{name}_总结版.md`。ASR 用小米 MiMo 全模态 `mimo-v2.5`，
  修订与总结默认 `mimo-v2.5-pro`（按模型名前缀路由，可换 deepseek-）；修订专门处理中英混杂时的英文术语 / 缩写错转、同音字、错别字，
  按语义重新分段并加 `**{说话人}**：` 标签。Use whenever 用户要把录音 / 讲座 / 会议 / 访谈 / 课程 / 播客 / 视频
  转成文字版 / 逐字稿 / 字幕 / 会议纪要 / 访谈纪要 / 课程笔记，或抱怨直接 ASR 出来的字幕有错别字、英文术语被转错、中英混杂不准。
  不触发：只想 TTS；只想纯 ASR 不要修订与总结（直接调 MiMo API 更轻）。
---

# Audio Transcribe & Summarize（音频转文字 + 总结）

把音频 / 视频文件变成两份 Markdown：修订过的**文字版**和结构化的**总结版**。

## 何时被触发

- 用户给出本地音频 / 视频文件路径，说"把这段录音整理一下""转成文字加总结""写个会议纪要"
- 用户说"直接 ASR 出来的字幕有错别字 / 英文术语被转错了，帮我修一下并总结"
- 用户问"几个小时的播客 / 课程怎么变成可读笔记"

## 输入与输出

**输入**：

- 必填：音频或视频文件路径，**可一次给多个**（按顺序无缝拼接，输出文件名基于第一个文件 stem）。
  任何 ffmpeg 支持的格式：wav / mp3 / m4a / flac / aac / opus / mp4 / mov / webm 等。
- 可选：`--intro intro.md` 背景介绍（嘉宾名 / 主持人 / 议题 / 术语）—— 强烈建议传，用来对齐人名 / 术语
  写法，并让说话人标签（`**{说话人}**：`）尽量落到真名。
- 可选：`--out-dir <dir>` 输出目录（默认与第一个输入同目录）
- 可选：`--name <prefix>` 输出文件名前缀（默认为第一个输入文件 stem）

**输出**（两个 Markdown 文件，放在 `out_dir` 下）：

| 文件 | 内容 |
| --- | --- |
| `{name}_文字版.md` | 修订错字 / 中英术语、按语义分段、**每段开头标注说话人** 的完整转写文本 |
| `{name}_总结版.md` | 结构化总结：① 整体概览 ② 嘉宾观点分述 ③ 议题分述 ④ 共识/分歧/关键结论 ⑤ 行动项 ⑥ 关键人物/数字/术语 |

## 前置依赖（一次性配置）

```bash
# 1. 系统依赖
brew install ffmpeg                              # macOS；Linux: apt install ffmpeg / Windows: 见 https://ffmpeg.org
pip install "openai>=1.0"                        # OpenAI Python SDK，本流水线靠它走 OpenAI 兼容协议

# 2. API key（按你打算用哪个模型设；只用 MiMo 时 DeepSeek 那条可不设）
export MIMO_API_KEY=sk-...                       # 小米 MiMo（ASR + 默认修订 + 默认总结）
export DEEPSEEK_API_KEY=sk-...                   # 仅当 --correct-model / --summary-model 切到 deepseek-* 时需要
```

**API key 获取**：

- **MiMo（小米全模态）**：在 <https://mimo.xiaomi.com/> 注册并申请 API key。MiMo 是这个流水线唯一能省钱的关键 ——
  ASR 用 `mimo-v2.5`（多模态、便宜），修订 / 总结用 `mimo-v2.5-pro`（1T MoE、1M 上下文）。
- **DeepSeek（可选）**：在 <https://platform.deepseek.com/> 注册并申请 API key。仅当你把 `--correct-model` 或
  `--summary-model` 切到 `deepseek-*` 时才需要。

修订 / 总结的 client 按模型名前缀路由：`mimo-*` 走小米、`deepseek-*` 走 DeepSeek，
对应 env var 缺失会立即报错。

## 调用方式

执行时用项目用的 Python 解释器（确保已 `pip install openai`）。文档中统一写 `python3`，可换成
你 venv / conda 环境里的解释器路径。

脚本路径在本 plugin 内是 `${CLAUDE_PLUGIN_ROOT}/skills/audio-transcribe-summarize/scripts/pipeline.py`，
也可以从仓库根目录用相对路径 `skills/audio-transcribe-summarize/scripts/pipeline.py` 引用。

### 完整流水线（最常用）

```bash
# 输出落在音频同目录，文件名前缀 = 音频 stem
python3 ${CLAUDE_PLUGIN_ROOT}/skills/audio-transcribe-summarize/scripts/pipeline.py /path/audio.m4a

# 指定输出目录和文件名前缀
python3 ${CLAUDE_PLUGIN_ROOT}/skills/audio-transcribe-summarize/scripts/pipeline.py audio.mp3 \
    --out-dir ~/Notes --name 2026-06-19-周会
```

### 多文件输入（自己录的两段 / 人工切过的几段）

按命令行顺序拼接成一段后再走流水线。输出文件名基于第一个输入的 stem。

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/audio-transcribe-summarize/scripts/pipeline.py \
    part1.m4a part2.m4a part3.m4a \
    --intro intro.md --out-dir ~/Notes --name 2026-06-20-访谈
```

### 已有文字版，只重做总结（迭代 prompt 时常用）

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/audio-transcribe-summarize/scripts/pipeline.py \
    --from-text ~/Notes/2026-06-19-周会_文字版.md \
    --out-dir ~/Notes --name 2026-06-19-周会
```

### 带背景介绍跑（极强烈推荐——能彻底解决人名同音错字）

`intro.md` 可以是音频原页面摘抄、嘉宾名单、术语表等任何文本。修订步骤会用它来对齐
人名 / 机构 / 术语写法；总结步骤会用它锚定背景。

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/audio-transcribe-summarize/scripts/pipeline.py audio.m4a \
    --intro intro.md --out-dir ~/Notes --name 2026-06-19-AGI圆桌
```

也可与 `--from-text` 组合，已有文字版换个 intro 重新校对 + 重总结：

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/audio-transcribe-summarize/scripts/pipeline.py \
    --from-text ~/Notes/xxx_文字版.md --intro intro.md \
    --out-dir ~/Notes --name xxx
```

### 只转写不要总结

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/audio-transcribe-summarize/scripts/pipeline.py audio.mp3 --skip-summary
```

### 只转写不修订也不总结（对照原始 ASR）

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/audio-transcribe-summarize/scripts/pipeline.py audio.mp3 \
    --skip-correct --skip-summary
```

## CLI 参数速查

| 参数 | 默认 | 说明 |
| --- | --- | --- |
| `audio` | — | 输入音频 / 视频文件，可一次给多个（位置参数；用 `--from-text` 时可省） |
| `--from-text PATH` | — | 跳过 ASR + 修订，直接基于已有 Markdown 文字版重做总结 |
| `--intro PATH` | — | 背景介绍 Markdown（嘉宾名、机构、术语、议题等）；同时注入修订和总结两步，用来对齐人名 / 术语 / 说话人标签 |
| `--out-dir DIR` | 第一个输入同目录 | 输出目录 |
| `--name PREFIX` | 第一个输入 stem | 输出文件名前缀 |
| `--chunk-sec N` | `600` | ASR 目标切片秒数（10 分钟一段）；静音感知开启时实际切点在 ±30% 窗口内找最近静音 |
| `--no-silence-aware` | off | 关闭静音感知，按 `--chunk-sec` 硬切 |
| `--silence-db DB` | `-35` | silencedetect 噪声阈值（dB），环境吵杂可改 `-30` |
| `--min-silence-sec S` | `0.4` | 判定为静音所需最短持续时间 |
| `--asr-bitrate B` | `64k` | ASR 上传用 mp3 比特率（mono 16k） |
| `--asr-delay-sec S` | `0` | ASR 调用之间的固定等待秒数；遇限速拉开节奏可调 3-10 |
| `--correct-chunk-chars N` | `8000` | 修订时按字符数切块的上限 |
| `--correct-model M` | `mimo-v2.5-pro` | 修订模型；按前缀路由：`mimo-*` 走小米、`deepseek-*` 走 DeepSeek |
| `--summary-model M` | `mimo-v2.5-pro` | 总结模型；按前缀路由：`mimo-*` 走小米、`deepseek-*` 走 DeepSeek |
| `--skip-correct` | off | 跳过修订，直接用原始 ASR 文本进总结 |
| `--skip-summary` | off | 只做转写（+ 修订），不做总结 |

## 流水线行为说明

1. **ffmpeg 预处理**：任意输入 → 16 kHz mono WAV。视频文件会自动只取音轨。
   多文件输入时，每个先各自转 16 kHz mono WAV，再用 ffmpeg `concat` demuxer 无缝拼接成一段。
2. **切片**：时长 ≤ `--chunk-sec × 1.3` 时整段送入；否则进入静音感知切片 ——
   先用 `silencedetect` 找全部静音区间，再在每个目标切点（`chunk-sec` 倍数）的 ±30% 窗口内
   挑离目标最近的静音中点切，避开句子中间。窗口里找不到静音才退回硬切。
   `--no-silence-aware` 直接关掉静音感知，全部硬切。
3. **ASR**：用 `mimo-v2.5` 多模态模型做转写，每段以 mp3 mono 16k 64k base64 上传，关闭 thinking 求速度。
4. **修订**：默认 `mimo-v2.5-pro`（关闭 thinking 求速度）。按 8000 字符切块逐块修订。修订做三件事：
   ① 修错字 / 标点 / 英文术语写法；② 按语义重排段落（每段 80~400 字，同一人完整一问一答可更长）；
   ③ **每段开头加 `**{说话人}**：` 前缀**（背景介绍里点名了的用真名；没点名的用 `主持人` /
   `嘉宾A` / `嘉宾B` 兜底）。不动语序、不删内容、不改意思。
5. **总结**：默认 `mimo-v2.5-pro`（关闭 thinking，因为 MiMo 输出预算 32768 token，思考链会
   吃掉大头导致 6 节结构化总结被截断）。**流式调用**保活长连接，避免 SSL EOF / 中途断连。
   总结使用的 prompt 在 `references/summary_prompts.md`，可单独迭代。输出顺序：
   ① 整体概览（核心议题 + 一段连贯叙述）；
   ② **嘉宾观点分述**（每位嘉宾一节，至少 80 字；信息密的可到 300+ 字）；
   ③ 议题分述（同一议题下多方观点对比）；
   ④ 共识 / 分歧 / 关键结论；
   ⑤ 行动项；
   ⑥ 关键人物 / 数字 / 时间 / 术语。
   一小时讨论的正文目标 ≥ 1500 字。

## 难点处理

- **中英混杂 + 英文缩写错字**：修订 prompt 显式列出 GPU / API / Transformer / LLM / RAG / PyTorch / CUDA 等常见
  写法白名单，并要求保留双语原貌。其它领域术语依赖修订模型的世界知识。
- **人名同音变形**（瞬宇 vs 顺宇 vs 迅宇）：基础 prompt 没法消，因为模型不知道哪个是真名。
  解决：用 `--intro intro.md` 传背景介绍（嘉宾名单 / 议题 / 术语），修订和总结都会用它对齐写法。
- **说话人识别**：基于 LLM 推断，没有 audio-side diarization。强烈建议传 `--intro intro.md` 让说话人标签
  落到真名（`**广密**：`、`**课代表立正**：`）；不传则用 `**主持人**：` / `**嘉宾A**：` 兜底。
- **可读性分段**：修订 prompt 按说话人切换 + 语义切换重排段落，避免大段堆字。同一人完整一问一答语义块允许偏长。
- **超长音频 + 切片对齐**：默认 10 分钟一段，并用 ffmpeg `silencedetect` 在每个切点
  ±30% 窗口内挑最近的静音中点切，避开句子中间，前后段语义更连贯。
  修订按 8 千字切块；总结一次性吃完整文字版（mimo-v2.5-pro 上下文 1M、DeepSeek v4 上下文 1M，
  常规几小时录音的转写文本完全装得下）。若文字版 > 30 万字会打印警告。
- **再次迭代总结 prompt**：不必重新跑 ASR，用 `--from-text` 复用已有的文字版即可。

## 目录结构

```
audio-transcribe-summarize/                # plugin / 仓库根
├── .claude-plugin/
│   ├── plugin.json
│   └── marketplace.json
└── skills/
    └── audio-transcribe-summarize/        # 这个 skill
        ├── SKILL.md
        ├── references/
        │   └── summary_prompts.md         # 总结 SYSTEM / USER / USER_WITH_INTRO 三段，独立迭代
        └── scripts/
            └── pipeline.py                # 主入口，覆盖完整流水线和 --from-text 重总结分支
```

## 已知限制 / 排障

- **MiMo 全模态 `input_audio.data` 上限 10 MB**（base64 后）：
  - PCM WAV 16k mono 大约只能装 4 分钟 → 故本流水线一律转 **mp3 mono 16k 64k**（10 分钟 ≈ 640 KB，留足余量）。
  - 若仍想用更长切片，可降比特率：`--asr-bitrate 32k`，或留默认 `--chunk-sec 600` 不动。
- 自动重试覆盖超时 / 连接错误；其它错误（参数错、文件超 10MB）直接抛出，需要改参数。
- **说话人分离基于 LLM 推断**：本流水线没有调用专门的 diarization 服务，说话人标签由修订模型
  根据背景介绍和对话内容推断 —— 强烈建议传 `--intro intro.md`（嘉宾名单 / 主持人）让标签准确。
  没有背景介绍时退化为 `主持人` / `嘉宾A` / `嘉宾B` 兜底，效果与人物切换边界对话脚本工程相关。
- 时间戳信息会在转写阶段丢弃；如需带时间戳字幕，请改用 SRT 类工具。
