# audio-transcribe-summarize

> 一个 Claude Code [plugin](https://code.claude.com/docs/zh-CN/plugins) + 纯命令行 Python 流水线，把本地音频 / 视频转成两份 Markdown：一份是修订过、带说话人标签的**文字版**，一份是结构化的**总结版**（整体概览 · 嘉宾观点分述 · 议题分述 · 共识/分歧/结论 · 行动项 · 关键人物与数字）。

[English](./README.md) · 简体中文

## 为什么用它

主流方案（Whisper API + GPT-4o，或商业转写服务）一小时音频要花几块美元。这套流水线默认用**小米 MiMo**：ASR 用 `mimo-v2.5`（多模态），修订与总结用 `mimo-v2.5-pro`（1T MoE，1M 上下文）。算下来：

- **8～10 小时中英混杂音频，全流程不到 1.6 元人民币**（约 0.22 美元）。
- 一次跑出两份成品：清洗过的文字版 + 1500 字以上的结构化总结，不用分两次付费。
- 也支持 DeepSeek —— 修订 / 总结模型换成 `deepseek-*` 前缀就会自动走 DeepSeek。

## 跑完得到什么

每个输入音频 / 视频文件，会在指定目录下产出两份 Markdown：

| 文件 | 内容 |
| --- | --- |
| `{name}_文字版.md` | 修过错字 / 中英术语错写，按语义重排段落，**每段开头加 `**{说话人}**：` 标签**的完整转写 |
| `{name}_总结版.md` | 结构化总结：① 整体概览 ② 嘉宾观点分述 ③ 议题分述 ④ 共识/分歧/关键结论 ⑤ 行动项 ⑥ 关键人物/数字/术语 |

完整流水线是 `ffmpeg → 静音感知切片 → mimo-v2.5 ASR → mimo-v2.5-pro 修订 → mimo-v2.5-pro 总结`，目标切点窗口里找不到静音才会退回硬切。支持任意 ffmpeg 能读的格式（wav / mp3 / m4a / flac / aac / opus / mp4 / mov / webm …），也支持一次传多个文件按顺序拼接后再走流水线。

## 安装

### 方案 A —— 作为 Claude Code plugin 安装（推荐）

在 Claude Code 里：

```text
/plugin marketplace add LSHCoding/audio-transcribe-summarize
/plugin install audio-transcribe-summarize@audio-transcribe-summarize
```

装好后，你说下面这类话时 skill 会自动触发：

- 「把这段录音整理成会议纪要」
- 「这个播客转成文字版和总结」
- 「字幕里英文术语都被转错了，帮我修一下并写总结」

一个完整的端到端例子 —— 把音频和背景介绍放在同一个文件夹里，然后在 Claude Code 里说：

```text
将播客 ./260620-SpaceX/口述SpaceX开发史-洪力德-商业访谈录.m4a 转写为文字版并进行总结，
播客的介绍在 ./260620-SpaceX/intro.md
```

Claude 自己挑出这个 skill，组好 `--intro` 和 `--out-dir` 调 `pipeline.py`。你不用记 CLI 参数。

### 方案 B —— 直接当 CLI 用（不依赖 Claude Code）

```bash
git clone https://github.com/LSHCoding/audio-transcribe-summarize.git
cd audio-transcribe-summarize

# 系统依赖
brew install ffmpeg                    # macOS；Linux: apt install ffmpeg
pip install "openai>=1.0"              # 走 OpenAI 兼容协议，SDK >= 1.0

# 直接跑
python3 skills/audio-transcribe-summarize/scripts/pipeline.py /path/to/audio.m4a
```

## 配置

### 申请 API key

**至少要有 MiMo key**（三个阶段默认都用它）。只有当你把修订 / 总结模型切到 `deepseek-*` 时，才需要 DeepSeek key。

- **小米 MiMo**：到 <https://mimo.xiaomi.com/> 注册账号、申请 API key。流水线之所以便宜，关键就是它。
- **DeepSeek（可选）**：到 <https://platform.deepseek.com/> 注册并申请，仅当用 `--correct-model deepseek-chat` 或 `--summary-model deepseek-chat` 时需要。

### 设环境变量

```bash
export MIMO_API_KEY=sk-...             # 必填
export DEEPSEEK_API_KEY=sk-...         # 可选，仅 deepseek-* 模型用
```

写到 `~/.zshrc` / `~/.bashrc` 里持久化。Provider 路由按**模型名前缀**走：`mimo-*` 调小米、`deepseek-*` 调 DeepSeek，对应的 env 没设会立即报错并告诉你缺哪个。

## 上手示例

```bash
# 1. 完整流水线 —— 输出落在音频同目录
python3 skills/audio-transcribe-summarize/scripts/pipeline.py meeting.m4a

# 2. 带背景介绍（强烈推荐：能解决嘉宾人名同音错字，让说话人标签落到真名）
python3 skills/audio-transcribe-summarize/scripts/pipeline.py interview.m4a \
    --intro intro.md --out-dir ~/Notes --name 2026-06-20-访谈

# 3. 多段录音按顺序拼接后跑
python3 skills/audio-transcribe-summarize/scripts/pipeline.py \
    part1.m4a part2.m4a part3.m4a \
    --intro intro.md --out-dir ~/Notes --name 2026-06-20-圆桌

# 4. 已经有文字版，只重新生成总结（迭代总结 prompt 时用）
python3 skills/audio-transcribe-summarize/scripts/pipeline.py \
    --from-text ~/Notes/2026-06-20-访谈_文字版.md \
    --out-dir ~/Notes --name 2026-06-20-访谈
```

`--intro intro.md` 可以是任何文本 —— 节目主页摘抄、嘉宾名单、术语表都行。修订阶段拿它对齐人名 / 术语写法，总结阶段拿它锚定背景结构。

## CLI 参数速查

| 参数 | 默认 | 说明 |
| --- | --- | --- |
| `audio` | — | 输入音视频文件，可以一次给多个（任意 ffmpeg 支持的格式；多个按顺序拼接） |
| `--from-text PATH` | — | 跳过 ASR + 修订，直接基于已有 Markdown 文字版重做总结 |
| `--intro PATH` | — | 背景介绍 Markdown，会同时注入修订和总结两步 |
| `--out-dir DIR` | 输入同目录 | 输出目录 |
| `--name PREFIX` | 输入 stem | 输出文件名前缀 |
| `--chunk-sec N` | `600` | ASR 目标切片秒数（10 分钟）；静音感知开启时实际切点在 ±30% 范围内的最近静音中点 |
| `--no-silence-aware` | off | 关闭静音感知，按 `--chunk-sec` 硬切 |
| `--silence-db DB` | `-35` | silencedetect 噪声阈值，环境吵杂可改 `-30` |
| `--min-silence-sec S` | `0.4` | 判定为静音所需最短持续时间 |
| `--asr-bitrate B` | `64k` | ASR 上传用 mp3 比特率（mono 16k） |
| `--asr-delay-sec S` | `0` | ASR 调用之间的固定等待秒数；遇限速调到 3-10 |
| `--correct-chunk-chars N` | `8000` | 修订步骤的文本切片字符数 |
| `--correct-model M` | `mimo-v2.5-pro` | 修订模型；`mimo-*` 走小米、`deepseek-*` 走 DeepSeek |
| `--summary-model M` | `mimo-v2.5-pro` | 总结模型，前缀路由规则同上 |
| `--skip-correct` | off | 跳过修订，直接用原始 ASR 文本进总结 |
| `--skip-summary` | off | 只做转写（+ 修订），不做总结 |

## 流水线怎么跑的

1. **ffmpeg 预处理**：任意输入 → 16 kHz mono WAV。视频自动只取音轨。多文件输入时各自先归一化再用 ffmpeg `concat` demuxer 拼接。
2. **静音感知切片**：用 `silencedetect` 扫一遍所有静音区间；对每个目标切点（`chunk-sec` 倍数），在 ±30% 窗口里找离目标最近的静音中点切。窗口里没有静音才退回硬切。
3. **ASR**：每个 ~10 分钟 mp3 段（mono 16k 64k）base64 上传到 `mimo-v2.5`，关闭 thinking 求速度。流水线一律转 mp3 而不直传 WAV，就是因为 MiMo 单次上传上限 10 MB。
4. **修订**：文字按 8000 字符切块，送到 `mimo-v2.5-pro`，prompt 严格规定三件事：① 修错字 / 中英术语；② 按说话人切换 + 语义切换重排段落；③ 每段开头加 `**{说话人}**：`。不动语序、不删内容、不改写。
5. **总结**：完整文字版**一次性**送到 `mimo-v2.5-pro`，**流式**调用（1M 上下文装得下几小时录音，流式保活避免 SSL EOF）。总结 prompt 放在 [`skills/audio-transcribe-summarize/references/summary_prompts.md`](./skills/audio-transcribe-summarize/references/summary_prompts.md)，单独迭代不影响 pipeline 代码。

## 已知限制

- **MiMo 全模态 `input_audio.data` 上限 10 MB**（base64 后）。mp3 mono 16k 64k 大约 16 分钟一个 chunk，默认 10 分钟留足余量。想要更长 chunk 可以降到 `--asr-bitrate 32k`。
- **说话人分离基于 LLM 推断**，没有 audio-side diarization。强烈建议传 `--intro intro.md` 把嘉宾名单 / 主持人给出来，标签会落到真名；不传则用 `主持人 / 嘉宾A / 嘉宾B` 兜底。
- **时间戳会被丢弃**。要带时间戳的字幕请用 SRT 类工具。
- 自动重试覆盖超时 / 连接错误。硬错误（参数错、单 chunk 超 10 MB）会直接报错给你看。

## License

MIT —— 见 [LICENSE](./LICENSE)。

## 致谢

- [小米 MiMo](https://mimo.xiaomi.com/) —— 让整条流水线经济上可行的多模态模型。
- [DeepSeek](https://platform.deepseek.com/) —— 文本步骤的备选 provider。
- [ffmpeg](https://ffmpeg.org/) —— 格式归一化、静音检测的底座。
