#!/usr/bin/env python
"""audio-to-text-and-summary 流水线。

阶段：
    1) ffmpeg 把任意音频/视频转成 16k mono WAV
    2) （可选）按时长切片
    3) 小米 MiMo `mimo-v2.5` 全模态模型逐片转写
    4) 修订错字并按语义重新分段（默认 mimo-v2.5-pro）
    5) 生成结构化总结：整体概览 + 嘉宾观点分述 + 议题分述 + 共识/分歧 + 行动项 + 关键人物/数字
       （默认 mimo-v2.5-pro）

依赖：
    pip install openai            # >= 1.0
    系统：ffmpeg、ffprobe（macOS: brew install ffmpeg）

环境变量：
    MIMO_API_KEY        小米 MiMo（ASR + 默认修订 + 默认总结）
    DEEPSEEK_API_KEY    仅当 --correct-model / --summary-model 切到 deepseek-* 时需要

用法示例：
    # 完整流水线，输出落在音频同目录、同 stem
    python pipeline.py /path/audio.m4a

    # 指定输出目录和文件名前缀
    python pipeline.py audio.mp3 --out-dir ~/Notes --name 2026-06-19-周会

    # 已有文字版，只重新生成总结
    python pipeline.py --from-text ~/Notes/2026-06-19-周会_文字版.md \\
                       --out-dir ~/Notes --name 2026-06-19-周会
"""

from __future__ import annotations

import argparse
import base64
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from openai import OpenAI, APITimeoutError, APIConnectionError


# ---------------------------------------------------------------------------
# Prompts

CORRECTION_SYSTEM_PROMPT = """\
你是一名专业的音频转写校对员。任务：修正转写错误，按语义重新分段，并给每段标注说话人。

字面修正（必做）：
- 同音字、错别字（如「在」「再」、「的」「得」「地」混用）
- 中英文混杂时的英文术语和缩写错写（GPU、API、Transformer、LLM、RAG、PyTorch、CUDA、
  RL、SFT、Distillation、Agent、MoE、scaling、long context 等保持标准写法）
- 标点缺失或错放
- 明显的语法错乱（仅当能确定正确写法时才改）

说话人标注（必做）：
- **每段开头加 `**{说话人}**：` 前缀**（说话人名加粗、紧跟全角冒号、然后是正文）。
- 说话人取名规则：
  - 背景介绍里点名了的人物（主持人、嘉宾、主讲人），用背景里的名字（如 `**广密**：` `**唐杰**：`）。
  - 背景里没点名但能从对话推断是主持人 / 提问者的，用 `**主持人**：`。
  - 其它无法对应到具体名字的嘉宾，按出场顺序用 `**嘉宾A**：` `**嘉宾B**：` 兜底。
- 同一说话人连续多段时，每段都重复一次标签（方便扫读，不要省略）。
- 自己拿不准是谁说的就保留兜底标签，不要乱猜真名。

按语义分段（必做）：
- 按下列线索重新分段：
  1. 说话人切换 → 必须另起一段
  2. 同一人长发言里出现明显的论点切换 / 举新例子 / 跳到子问题 / 上升到结论 → 另起一段
- 每段控制在 80~400 字之间；同一说话人完整的一问一答语义块允许更长。
- 段与段之间用 **一个空行** 分隔（Markdown 段落分隔）。

严格规则：
1. 只修字、改标点、加说话人标签、重排段落，**不删内容、不改意思、不改语序、不润色、不总结、不改写**
2. 保留口语化语气（"嗯"、"那个"、"对"），但可合并连续重复的语气词（"这个这个这个" → "这个"）
3. 不要给出修改说明，只输出修订并重新分段后的完整文本
4. 中英文混杂段落保持双语原貌
5. 拿不准的字 / 说话人保留原样或兜底标签，不要凭空补字 / 编名字
"""

CORRECTION_USER_TEMPLATE = """\
以下是音频转写文本，请按规则修正、加说话人标签后输出。仅输出修正后的全文，不要任何额外说明、标题或前后缀。

---转写文本开始---
{text}
---转写文本结束---"""


CORRECTION_USER_TEMPLATE_WITH_INTRO = """\
以下是这段音频的背景介绍 + 转写文本。请用背景介绍来校正人名 / 机构 / 术语的转写错误，
并把背景里点名的人物作为说话人标签的真名来源。然后按规则输出修正后的全文。仅输出全文，不要任何额外说明。

如果背景介绍里出现的人名 / 机构 / 术语在转写里被写成了同音的错别字，要替换为背景里的标准写法；
背景里没出现的内容不要凭空添加。

---背景介绍开始---
{intro}
---背景介绍结束---

---转写文本开始---
{text}
---转写文本结束---"""


# Summary prompt 抽到外部文件，方便用户单独迭代而不动 pipeline 代码。
_SUMMARY_PROMPTS_FILE = Path(__file__).resolve().parent.parent / "references" / "summary_prompts.md"


def _load_summary_prompts(path: Path) -> dict[str, str]:
    """从 references/summary_prompts.md 解析三个 section：SYSTEM / USER / USER_WITH_INTRO。

    分隔符是独占一行的 `# PROMPT: <NAME>`；正文里 `##` 标题不会误匹配。
    """
    raw = path.read_text(encoding="utf-8")
    sections: dict[str, str] = {}
    current_name: str | None = None
    current_lines: list[str] = []
    for line in raw.splitlines():
        if line.startswith("# PROMPT: "):
            if current_name is not None:
                sections[current_name] = "\n".join(current_lines).strip("\n")
            current_name = line[len("# PROMPT: "):].strip()
            current_lines = []
        elif current_name is not None:
            current_lines.append(line)
    if current_name is not None:
        sections[current_name] = "\n".join(current_lines).strip("\n")
    for required in ("SYSTEM", "USER", "USER_WITH_INTRO"):
        if required not in sections:
            sys.exit(f"[error] {path} 缺少 section `# PROMPT: {required}`")
    return sections


_SUMMARY_PROMPTS = _load_summary_prompts(_SUMMARY_PROMPTS_FILE)
SUMMARY_SYSTEM_PROMPT = _SUMMARY_PROMPTS["SYSTEM"]
SUMMARY_USER_TEMPLATE = _SUMMARY_PROMPTS["USER"]
SUMMARY_USER_TEMPLATE_WITH_INTRO = _SUMMARY_PROMPTS["USER_WITH_INTRO"]


# ---------------------------------------------------------------------------
# ffmpeg helpers

def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=True, capture_output=True, text=True)


def ensure_ffmpeg() -> None:
    for tool in ("ffmpeg", "ffprobe"):
        try:
            subprocess.run([tool, "-version"], check=True, capture_output=True)
        except (FileNotFoundError, subprocess.CalledProcessError):
            sys.exit(f"[error] 需要 {tool}，请先安装（macOS: brew install ffmpeg）")


def get_duration_sec(path: Path) -> float:
    out = _run([
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(path),
    ]).stdout.strip()
    return float(out)


def normalize_inputs_to_wav(srcs: list[Path], dst: Path) -> None:
    """把一个或多个输入音视频归一化为单一 16 kHz mono WAV，写到 dst。

    单文件：直接 ffmpeg 转码（顺带做格式归一化，保证后续步骤稳）。
    多文件：先各自转 16 kHz mono WAV 到临时目录，再用 ffmpeg concat demuxer 无缝拼接。
    任何 ffmpeg 支持的容器 / 编码（m4a / mp3 / wav / flac / aac / opus / mp4 / mov / webm / ...）都可。
    """
    if not srcs:
        raise ValueError("normalize_inputs_to_wav: 至少需要一个输入文件")

    if len(srcs) == 1:
        _run([
            "ffmpeg", "-y", "-i", str(srcs[0]),
            "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le",
            "-loglevel", "error",
            str(dst),
        ])
        return

    # 多文件 → 各自转 WAV → concat
    tmp_dir = dst.parent
    parts: list[Path] = []
    for i, src in enumerate(srcs):
        part = tmp_dir / f"_input_part_{i:04d}.wav"
        _run([
            "ffmpeg", "-y", "-i", str(src),
            "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le",
            "-loglevel", "error",
            str(part),
        ])
        parts.append(part)
    list_file = tmp_dir / "_concat_list.txt"
    list_file.write_text(
        "\n".join(f"file '{p.as_posix()}'" for p in parts) + "\n",
        encoding="utf-8",
    )
    _run([
        "ffmpeg", "-y", "-f", "concat", "-safe", "0",
        "-i", str(list_file),
        "-c", "copy",
        "-loglevel", "error",
        str(dst),
    ])


def encode_segment_to_mp3(
    src: Path, start: float, length: float | None, dst: Path,
    bitrate: str = "64k",
) -> None:
    """把 src 的 [start, start+length] 段转成 mp3 mono 16k bitrate（默认 64k）。
    length=None 表示到末尾。小米 ASR 限制 input_audio.data ≤ 10MB（base64），
    64k mp3 大约 8 KB/s → 10 分钟 ≈ 640 KB，留足余量。"""
    cmd = ["ffmpeg", "-y"]
    if start > 0:
        cmd += ["-ss", f"{start:.3f}"]
    cmd += ["-i", str(src)]
    if length is not None:
        cmd += ["-t", f"{length:.3f}"]
    cmd += [
        "-vn", "-ac", "1", "-ar", "16000", "-b:a", bitrate,
        "-codec:a", "libmp3lame",
        "-loglevel", "error",
        str(dst),
    ]
    _run(cmd)


def detect_silences(
    src_wav: Path,
    noise_db: int = -35,
    min_silence_sec: float = 0.4,
) -> list[tuple[float, float]]:
    """跑一次 ffmpeg silencedetect，解析 stderr 得到所有静音区间 (start, end)，单位秒。"""
    result = subprocess.run(
        ["ffmpeg", "-i", str(src_wav),
         "-af", f"silencedetect=noise={noise_db}dB:d={min_silence_sec}",
         "-f", "null", "-"],
        capture_output=True, text=True, check=False,
    )
    silences: list[tuple[float, float]] = []
    cur_start: float | None = None
    for line in result.stderr.splitlines():
        if "silence_start:" in line:
            try:
                cur_start = float(line.split("silence_start:")[1].strip().split()[0])
            except (ValueError, IndexError):
                cur_start = None
        elif "silence_end:" in line and cur_start is not None:
            try:
                end = float(line.split("silence_end:")[1].strip().split()[0])
                silences.append((cur_start, end))
            except (ValueError, IndexError):
                pass
            cur_start = None
    return silences


def plan_split_points(
    duration: float,
    silences: list[tuple[float, float]],
    target_sec: int,
    slack_ratio: float = 0.3,
    min_chunk_sec: float = 60.0,
) -> list[float]:
    """决定在哪些时间点切。

    每一段努力做到 ~target_sec：在 [cursor + target*(1-slack), cursor + target*(1+slack)] 内
    找一段静音，从静音中点切；找不到则硬切在 cursor + target。
    最后一段如果短于 min_chunk_sec，则不再切（合到上一段里）。
    """
    if duration <= target_sec * (1 + slack_ratio):
        return []

    splits: list[float] = []
    cursor = 0.0
    while duration - cursor > target_sec * (1 + slack_ratio):
        lo = cursor + target_sec * (1 - slack_ratio)
        hi = cursor + target_sec * (1 + slack_ratio)
        ideal = cursor + target_sec
        candidates = [(s + e) / 2 for s, e in silences if lo <= (s + e) / 2 <= hi]
        split = min(candidates, key=lambda t: abs(t - ideal)) if candidates else ideal
        splits.append(split)
        cursor = split
    return splits


def split_to_mp3_chunks(
    src: Path, splits: list[float], duration: float, out_dir: Path,
    bitrate: str = "64k",
) -> list[Path]:
    """按给定切点把 src 切成多个 mp3 段。splits 为空时整段转一个 mp3。"""
    boundaries = [0.0] + splits + [duration]
    chunk_paths: list[Path] = []
    for i in range(len(boundaries) - 1):
        start = boundaries[i]
        length = boundaries[i + 1] - start
        out = out_dir / f"chunk_{i:04d}.mp3"
        encode_segment_to_mp3(src, start, length, out, bitrate=bitrate)
        chunk_paths.append(out)
    return chunk_paths


# ---------------------------------------------------------------------------
# ASR

_MIME_BY_EXT = {
    ".mp3": "audio/mp3",
    ".wav": "audio/wav",
    ".m4a": "audio/m4a",
    ".aac": "audio/aac",
    ".flac": "audio/flac",
    ".ogg": "audio/ogg",
    ".opus": "audio/opus",
}


MULTIMODAL_ASR_PROMPT = (
    "请把这段音频的内容逐字转写成文字，只输出转写文字本身，不要加任何前缀、解释、标点修正、"
    "总结或换行说明。原文有中文、英文、方言或英文术语缩写都按听到的样子原样写出。"
)


def transcribe_chunk(
    client: OpenAI, audio_path: Path,
    model: str = "mimo-v2.5",
    max_retries: int = 3, initial_backoff: float = 10.0,
) -> str:
    """用 mimo-v2.5 多模态模型做 ASR。"""
    mime = _MIME_BY_EXT.get(audio_path.suffix.lower(), "audio/mp3")
    data = audio_path.read_bytes()
    b64_size_mb = len(data) * 4 / 3 / 1024 / 1024
    if b64_size_mb > 9.5:
        raise RuntimeError(
            f"chunk {audio_path.name} 编码后约 {b64_size_mb:.1f} MB，超过 ASR 10MB 上限；"
            f"减小 --chunk-sec 或降低 --asr-bitrate"
        )
    audio_b64 = base64.b64encode(data).decode()

    def _call() -> str:
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": [
                {"type": "text", "text": MULTIMODAL_ASR_PROMPT},
                {"type": "input_audio",
                 "input_audio": {"data": f"data:{mime};base64,{audio_b64}"}},
            ]}],
            max_completion_tokens=8192,
            extra_body={"thinking": {"type": "disabled"}},
        )
        return (resp.choices[0].message.content or "").strip()

    backoff = initial_backoff
    for attempt in range(1, max_retries + 1):
        try:
            return _call()
        except (APITimeoutError, APIConnectionError) as e:
            if attempt == max_retries:
                raise
            print(f"      [retry {attempt}/{max_retries}] {type(e).__name__}; "
                  f"sleep {backoff:.0f}s 后重试", flush=True)
            time.sleep(backoff)
            backoff = min(backoff * 2, 120.0)
    raise RuntimeError("transcribe_chunk: unreachable")


# ---------------------------------------------------------------------------
# Text chunking for correction step

def chunk_text(text: str, max_chars: int) -> list[str]:
    """按段落优先、句号兜底的方式把长文切成 <= max_chars 的块。"""
    if len(text) <= max_chars:
        return [text]

    paragraphs = [p for p in text.split("\n") if p.strip()]
    rough: list[str] = []
    buf: list[str] = []
    buf_len = 0
    for p in paragraphs:
        if buf and buf_len + len(p) > max_chars:
            rough.append("\n".join(buf))
            buf = [p]
            buf_len = len(p)
        else:
            buf.append(p)
            buf_len += len(p) + 1
    if buf:
        rough.append("\n".join(buf))

    final: list[str] = []
    for chunk in rough:
        if len(chunk) <= max_chars:
            final.append(chunk)
            continue
        # 单段超长：按句号 / 标点切
        cur = ""
        for ch in chunk:
            cur += ch
            if ch in "。！？!?" and len(cur) >= max_chars * 0.8:
                final.append(cur)
                cur = ""
        if cur:
            final.append(cur)
    return final


# ---------------------------------------------------------------------------
# Client routing (MiMo vs DeepSeek) for correction & summary

PROVIDERS = {
    "mimo": {
        "env": "MIMO_API_KEY",
        "base_url": "https://api.xiaomimimo.com/v1",
        "prefixes": ("mimo-",),
    },
    "deepseek": {
        "env": "DEEPSEEK_API_KEY",
        "base_url": "https://api.deepseek.com",
        "prefixes": ("deepseek-",),
    },
}


def provider_of(model: str) -> str:
    for name, cfg in PROVIDERS.items():
        if any(model.startswith(p) for p in cfg["prefixes"]):
            return name
    raise ValueError(
        f"无法识别模型 {model!r} 的厂商；目前仅支持前缀：mimo-、deepseek-"
    )


def get_client(model: str, cache: dict[str, OpenAI]) -> OpenAI:
    """按模型名前缀路由到对应厂商的 OpenAI 兼容 client，复用缓存。

    思考模式 + 长上下文的请求可能跑 1~3 分钟，默认的 timeout / retries 不够；
    这里调高重试和超时，避免 SSL EOF 之类的瞬断把整轮跑废。
    """
    name = provider_of(model)
    if name not in cache:
        cfg = PROVIDERS[name]
        key = os.environ.get(cfg["env"])
        if not key:
            sys.exit(f"[error] 环境变量 {cfg['env']} 未设置（{model} 需要它）")
        cache[name] = OpenAI(
            api_key=key, base_url=cfg["base_url"],
            timeout=600.0, max_retries=5,
        )
    return cache[name]


# ---------------------------------------------------------------------------
# Correction & Summary

_MODERATION_PATTERNS = (
    "considered high risk",
    "request was rejected",
    "I cannot",
    "I can't help",
    "无法处理",
    "无法完成",
    "不能完成",
    "违反",
)


def _looks_like_moderation_refusal(out: str, original_chars: int) -> bool:
    """检测整段输出是不是 MiMo / 兼容厂商的内容安全 / 拒绝模板，而不是真正的修订结果。

    判据：输出 < 原文 30% 且命中典型拒绝短语。
    """
    if not out:
        return True
    if len(out) >= original_chars * 0.3:
        return False
    low = out.lower()
    return any(p.lower() in low for p in _MODERATION_PATTERNS)


def correct_text(
    client: OpenAI, text: str, model: str, max_chars: int,
    intro: str | None = None,
) -> str:
    parts = chunk_text(text, max_chars)
    corrected: list[str] = []
    for i, part in enumerate(parts, 1):
        print(f"  修订 chunk {i}/{len(parts)} ({len(part)} chars)…", flush=True)
        if intro:
            user_content = CORRECTION_USER_TEMPLATE_WITH_INTRO.format(intro=intro, text=part)
        else:
            user_content = CORRECTION_USER_TEMPLATE.format(text=part)
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": CORRECTION_SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ],
            temperature=0.2,
            top_p=0.95,
            max_completion_tokens=32768,
            extra_body={"thinking": {"type": "disabled"}},
        )
        out = (resp.choices[0].message.content or "").strip()
        if _looks_like_moderation_refusal(out, len(part)):
            print(f"      ⚠️ chunk {i} 被内容安全策略拒绝（输出={out[:80]!r}），保留原始转写", flush=True)
            corrected.append(part)
        else:
            corrected.append(out)
    return "\n\n".join(corrected)


def summarize_text(
    client: OpenAI, text: str, model: str, intro: str | None = None,
) -> str:
    """流式 + 关闭 thinking。

    - 流式：让服务端在生成期间持续下发心跳，避免中间层 / NAT 闷断长连接。
    - 关闭 thinking：MiMo OpenAI 兼容下 `max_completion_tokens` 上限 32768，
      thinking on 时思考链会占掉大头预算，导致 6 节结构化总结写到第二节就截断。
      `mimo-v2.5-pro` 是 1T MoE，非思考态写结构化总结仍然够用。
    """
    if intro:
        user_content = SUMMARY_USER_TEMPLATE_WITH_INTRO.format(intro=intro, text=text)
    else:
        user_content = SUMMARY_USER_TEMPLATE.format(text=text)

    stream = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": SUMMARY_SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
        max_completion_tokens=32768,
        extra_body={"thinking": {"type": "disabled"}},
        stream=True,
    )
    parts: list[str] = []
    finish_reason: str | None = None
    for chunk in stream:
        if not chunk.choices:
            continue
        choice = chunk.choices[0]
        piece = getattr(choice.delta, "content", None)
        if piece:
            parts.append(piece)
        if choice.finish_reason:
            finish_reason = choice.finish_reason
    if finish_reason == "length":
        print(f"      ⚠️ 总结输出达到 max_completion_tokens 上限被截断；"
              f"可调小输入 / 简化 prompt", flush=True)
    return "".join(parts).strip()


# ---------------------------------------------------------------------------
# CLI

def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        description="音频 → 文字 → 修订 → 总结 一条龙",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("audio", nargs="*", type=Path,
                    help="输入音频/视频文件，可一次给多个（任何 ffmpeg 支持的格式：wav / mp3 / m4a / "
                         "flac / aac / opus / mp4 / mov / webm 等）。多个文件按命令行顺序无缝拼接后再走流水线。")
    ap.add_argument("--from-text", type=Path, default=None,
                    help="跳过 ASR + 修订，直接基于已有 Markdown 文字版重做总结")
    ap.add_argument("--intro", type=Path, default=None,
                    help="背景介绍 Markdown 文件路径；用于校正人名/术语和锚定总结。"
                         "对修订和总结两步都会注入")
    ap.add_argument("--out-dir", type=Path, default=None,
                    help="输出目录（默认与音频同目录；--from-text 时默认与文本同目录）")
    ap.add_argument("--name", default=None,
                    help="输出文件名前缀（默认为输入文件 stem，去掉「_文字版」后缀）")
    ap.add_argument("--chunk-sec", type=int, default=600,
                    help="ASR 目标切片秒数（默认 600=10 分钟）；启用静音感知时实际切点在 ±30%% 范围内的最近静音")
    ap.add_argument("--no-silence-aware", action="store_true",
                    help="关闭静音感知切片，直接按 --chunk-sec 硬切")
    ap.add_argument("--silence-db", type=int, default=-35,
                    help="silencedetect 的噪声阈值，单位 dB（默认 -35）；环境吵杂可调到 -30")
    ap.add_argument("--min-silence-sec", type=float, default=0.4,
                    help="判定为静音所需的最短持续时间（默认 0.4 秒）")
    ap.add_argument("--asr-bitrate", default="64k",
                    help="ASR 上传用 mp3 比特率，mono 16k（默认 64k；10MB 上限下 64k 可装 ~16 分钟）")
    ap.add_argument("--asr-delay-sec", type=float, default=0.0,
                    help="ASR 调用之间的固定等待秒数（默认 0）；遇到限速可调到 3-10 秒拉开节奏")
    ap.add_argument("--correct-chunk-chars", type=int, default=8000,
                    help="修订步骤的文本切片字符数（默认 8000）")
    ap.add_argument("--correct-model", default="mimo-v2.5-pro",
                    help="修订模型（默认 mimo-v2.5-pro；前缀 mimo- 走小米，deepseek- 走 DeepSeek）")
    ap.add_argument("--summary-model", default="mimo-v2.5-pro",
                    help="总结模型（默认 mimo-v2.5-pro；前缀 mimo- 走小米，deepseek- 走 DeepSeek）")
    ap.add_argument("--skip-correct", action="store_true",
                    help="跳过修订，直接用原始转写")
    ap.add_argument("--skip-summary", action="store_true",
                    help="只做转写和修订，不做总结")
    return ap.parse_args()


def main() -> None:
    args = parse_args()

    if not args.audio and not args.from_text:
        sys.exit("[error] 必须给出音频路径，或用 --from-text 指向已有文字版")

    # 修订 / 总结使用的 client 按模型前缀路由（mimo- 或 deepseek-），缓存复用
    client_cache: dict[str, OpenAI] = {}
    correct_client_provider = provider_of(args.correct_model)
    summary_client_provider = provider_of(args.summary_model)
    print(f"[config] 修订 = {args.correct_model} ({correct_client_provider})", flush=True)
    if not args.skip_summary:
        print(f"[config] 总结 = {args.summary_model} ({summary_client_provider})", flush=True)

    intro: str | None = None
    if args.intro:
        intro_path = args.intro.resolve()
        if not intro_path.exists():
            sys.exit(f"[error] 找不到 --intro 文件：{intro_path}")
        intro = intro_path.read_text(encoding="utf-8").strip()
        print(f"[intro] 已加载背景介绍 {intro_path.name}（{len(intro)} chars）", flush=True)

    # ---- 分支 A：跳过 ASR，直接 re-summarize 已有文字版 ----
    if args.from_text:
        src = args.from_text.resolve()
        if not src.exists():
            sys.exit(f"[error] 找不到 --from-text 指向的文件：{src}")
        out_dir = (args.out_dir or src.parent).resolve()
        out_dir.mkdir(parents=True, exist_ok=True)
        stem = src.stem
        if stem.endswith("_文字版"):
            stem = stem[: -len("_文字版")]
        name = args.name or stem
        text = src.read_text(encoding="utf-8")

        if args.skip_summary:
            print("[skip] --from-text + --skip-summary 没事可做")
            return

        print(f"[summary] 基于 {src.name} 生成总结…", flush=True)
        summary_client = get_client(args.summary_model, client_cache)
        summary = summarize_text(summary_client, text, args.summary_model, intro=intro)
        summary_md = out_dir / f"{name}_总结版.md"
        summary_md.write_text(f"# {name} — 总结\n\n{summary}\n", encoding="utf-8")
        print(f"完成：{summary_md}")
        return

    # ---- 分支 B：完整流水线 ----
    inputs = [p.resolve() for p in args.audio]
    for p in inputs:
        if not p.exists():
            sys.exit(f"[error] 找不到音频：{p}")

    mimo_key = os.environ.get("MIMO_API_KEY")
    if not mimo_key:
        sys.exit("[error] 环境变量 MIMO_API_KEY 未设置")
    mimo = OpenAI(api_key=mimo_key, base_url="https://api.xiaomimimo.com/v1")

    first = inputs[0]
    out_dir = (args.out_dir or first.parent).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    name = args.name or first.stem
    text_md = out_dir / f"{name}_文字版.md"
    summary_md = out_dir / f"{name}_总结版.md"

    ensure_ffmpeg()

    with tempfile.TemporaryDirectory(prefix="audio_to_summary_") as tmp:
        tmp = Path(tmp)
        if len(inputs) == 1:
            print(f"[1/4] 分析音频：{first.name}", flush=True)
        else:
            print(f"[1/4] 拼接 {len(inputs)} 个输入：{', '.join(p.name for p in inputs)}", flush=True)
        audio = tmp / "_normalized.wav"
        normalize_inputs_to_wav(inputs, audio)
        duration = get_duration_sec(audio)
        print(f"      时长 {duration:.1f}s（约 {duration/60:.1f} 分钟）", flush=True)

        if duration <= args.chunk_sec * 1.3:
            splits: list[float] = []
            print(f"[2/4] 短音频整段 → mp3 → ASR", flush=True)
        elif args.no_silence_aware:
            print(f"[2/4] 硬切（{args.chunk_sec}s/段） → mp3 → ASR", flush=True)
            splits = [float(args.chunk_sec * i)
                      for i in range(1, int(duration // args.chunk_sec) + 1)
                      if args.chunk_sec * i < duration - 30]
        else:
            print(f"[2/4] 静音感知切片（目标 {args.chunk_sec}s/段，silencedetect={args.silence_db}dB / {args.min_silence_sec}s）",
                  flush=True)
            silences = detect_silences(audio, args.silence_db, args.min_silence_sec)
            splits = plan_split_points(duration, silences, args.chunk_sec)
            if not splits and duration > args.chunk_sec * 1.3:
                print(f"      ⚠️ 未在窗口内找到合适静音点，回退硬切", flush=True)
                splits = [float(args.chunk_sec * i)
                          for i in range(1, int(duration // args.chunk_sec) + 1)
                          if args.chunk_sec * i < duration - 30]
            if splits:
                pts = ", ".join(f"{s/60:.1f}min" for s in splits)
                print(f"      切点：{pts}", flush=True)

        chunks = split_to_mp3_chunks(audio, splits, duration, tmp, bitrate=args.asr_bitrate)

        transcripts: list[str] = []
        for i, ch in enumerate(chunks, 1):
            size_kb = ch.stat().st_size / 1024
            print(f"      ASR chunk {i}/{len(chunks)}：{ch.name} ({size_kb:.0f} KB)", flush=True)
            if i > 1 and args.asr_delay_sec > 0:
                time.sleep(args.asr_delay_sec)
            transcripts.append(transcribe_chunk(mimo, ch))
        raw_text = "\n\n".join(t for t in transcripts if t)

    if args.skip_correct:
        corrected = raw_text
        print(f"[3/4] （跳过修订）", flush=True)
    else:
        tag = "（带背景）" if intro else ""
        print(f"[3/4] {correct_client_provider} 修订转写错字{tag}（{args.correct_model}）", flush=True)
        correct_client = get_client(args.correct_model, client_cache)
        corrected = correct_text(correct_client, raw_text, args.correct_model,
                                 args.correct_chunk_chars, intro=intro)

    text_md.write_text(f"# {name} — 文字版\n\n{corrected}\n", encoding="utf-8")
    print(f"      文字版已写入 {text_md}", flush=True)

    if args.skip_summary:
        print(f"[4/4] （跳过总结）", flush=True)
        print(f"\n完成：\n  文字版：{text_md}")
        return

    tag = "（带背景）" if intro else ""
    print(f"[4/4] {summary_client_provider} 生成总结{tag}（{args.summary_model}）", flush=True)
    if len(corrected) > 300_000:
        print(f"      ⚠️ 文本较长（{len(corrected)} 字符），单轮总结可能受输出 tokens 限制；"
              f"若需更详尽可手动分段后多次调用", flush=True)
    summary_client = get_client(args.summary_model, client_cache)
    summary = summarize_text(summary_client, corrected, args.summary_model, intro=intro)
    summary_md.write_text(f"# {name} — 总结\n\n{summary}\n", encoding="utf-8")
    print(f"      总结已写入 {summary_md}", flush=True)

    print(f"\n完成：\n  文字版：{text_md}\n  总结：  {summary_md}")


if __name__ == "__main__":
    main()
