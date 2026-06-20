# audio-transcribe-summarize

> A Claude Code [plugin](https://code.claude.com/docs/en/plugins) and standalone Python pipeline that turns local audio / video into two Markdown files: a **corrected transcript** with speaker tags, and a **structured summary** (overview · per-speaker views · per-topic views · agreements/disagreements · action items · key people & numbers).

[简体中文](./README.md) · English

## Why

Most ASR + LLM-summarize stacks (OpenAI Whisper API + GPT-4o, or commercial transcription services) cost a few US dollars per hour of audio. This pipeline uses **Xiaomi MiMo** by default — `mimo-v2.5` (multimodal ASR) for transcription and `mimo-v2.5-pro` (1T MoE, 1M context) for revision and summarization. Concretely:

- **~1.6 RMB (~$0.22) for 6–8 hours** of mixed Chinese / English audio, end to end.
- One pass produces both a cleaned-up transcript and a 1500+ character structured summary; you don't pay separately for ASR and summarization.
- DeepSeek is supported by model prefix if you'd rather use it for the revise / summarize steps.

## What you get

For every input audio / video file, two Markdown files in your chosen output directory:

| File | What's in it |
| --- | --- |
| `{name}_文字版.md` | Full transcript with typos / English-term miswrites fixed, re-segmented by semantics, every paragraph prefixed with `**{speaker}**：` |
| `{name}_总结版.md` | Structured summary — ① overview ② per-guest views ③ per-topic views ④ agreements / disagreements / conclusions ⑤ action items ⑥ key people / numbers / terms |

The full pipeline runs `ffmpeg → silence-aware chunking → mimo-v2.5 ASR → mimo-v2.5-pro revise → mimo-v2.5-pro summarize` and falls back to a hard split if no silence is found in the target window. It accepts any ffmpeg-supported format (wav / mp3 / m4a / flac / aac / opus / mp4 / mov / webm …) and can concatenate multiple files in order before running.

## Install

### Option A — as a Claude Code plugin (recommended)

In Claude Code:

```text
/plugin marketplace add LSHCoding/audio-transcribe-summarize
/plugin install audio-transcribe-summarize@audio-transcribe-summarize
```

After installation, the skill is auto-invoked when you ask Claude things like:

- "把这段录音整理成会议纪要"
- "Turn this podcast into a transcript and summary"
- "字幕里英文术语都被转错了，帮我修一下并写总结"

A concrete end-to-end example — drop both files in a folder, then in Claude Code:

```text
将播客 ./260620-SpaceX/口述SpaceX开发史-洪力德-商业访谈录.m4a 转写为文字版并进行总结，
播客的介绍在 ./260620-SpaceX/intro.md
```

Claude reads your prompt, picks this skill, and calls the bundled `pipeline.py` with the right `--intro` and `--out-dir` flags. You don't have to remember the CLI yourself.

### Option B — standalone CLI (no Claude Code required)

```bash
git clone https://github.com/LSHCoding/audio-transcribe-summarize.git
cd audio-transcribe-summarize

# System deps
brew install ffmpeg                    # macOS; Linux: apt install ffmpeg
pip install "openai>=1.0"              # OpenAI SDK >= 1.0

# Run the pipeline directly
python3 skills/audio-transcribe-summarize/scripts/pipeline.py /path/to/audio.m4a
```

## Configure

### Get your API keys

You need at least the **MiMo** key (default for all three stages). DeepSeek is only needed if you opt into `deepseek-*` models for revise / summarize.

- **Xiaomi MiMo** — sign up at <https://mimo.xiaomi.com/> and create an API key. This is the one that makes the pipeline cheap.
- **DeepSeek (optional)** — sign up at <https://platform.deepseek.com/> if you want `--correct-model deepseek-chat` or `--summary-model deepseek-chat`.

### Set environment variables

```bash
export MIMO_API_KEY=sk-...             # required
export DEEPSEEK_API_KEY=sk-...         # optional, only for deepseek-* models
```

Put these in `~/.zshrc` / `~/.bashrc` so they persist. Provider routing is by **model name prefix**: `mimo-*` calls Xiaomi, `deepseek-*` calls DeepSeek. If the matching env var is missing, the pipeline errors out immediately and tells you which one.

## Quick start

```bash
# 1. Full pipeline — outputs land next to the audio
python3 skills/audio-transcribe-summarize/scripts/pipeline.py meeting.m4a

# 2. With a background brief (strongly recommended for talks / interviews —
#    fixes guest-name homophones and gives speaker tags real names)
python3 skills/audio-transcribe-summarize/scripts/pipeline.py interview.m4a \
    --intro intro.md --out-dir ~/Notes --name 2026-06-20-interview

# 3. Concatenate multiple recordings in order, then run
python3 skills/audio-transcribe-summarize/scripts/pipeline.py \
    part1.m4a part2.m4a part3.m4a \
    --intro intro.md --out-dir ~/Notes --name 2026-06-20-roundtable

# 4. Already have the transcript — just regenerate the summary
#    (useful when iterating on the summary prompt)
python3 skills/audio-transcribe-summarize/scripts/pipeline.py \
    --from-text ~/Notes/2026-06-20-interview_文字版.md \
    --out-dir ~/Notes --name 2026-06-20-interview
```

The `--intro intro.md` file can be anything text — a description copied from the podcast page, a guest list, a glossary. The revision step uses it to fix name / term spellings; the summary step uses it to anchor structure.

## CLI reference

| Flag | Default | Meaning |
| --- | --- | --- |
| `audio` | — | One or more input files (any ffmpeg format). Multiple files are concatenated in order. |
| `--from-text PATH` | — | Skip ASR + revision, just regenerate the summary from an existing Markdown transcript. |
| `--intro PATH` | — | Background brief Markdown. Injected into both revision and summary. |
| `--out-dir DIR` | input dir | Output directory. |
| `--name PREFIX` | input stem | Output filename prefix. |
| `--chunk-sec N` | `600` | Target ASR chunk size in seconds (10 min). With silence-aware splitting, actual cut is within ±30% of the target, on the nearest silence midpoint. |
| `--no-silence-aware` | off | Disable silence-aware splitting; hard split at `--chunk-sec`. |
| `--silence-db DB` | `-35` | `silencedetect` noise threshold. Bump to `-30` for noisy recordings. |
| `--min-silence-sec S` | `0.4` | Minimum duration to count as silence. |
| `--asr-bitrate B` | `64k` | mp3 bitrate for ASR upload (mono 16k). |
| `--asr-delay-sec S` | `0` | Sleep between ASR calls. Set to 3–10 if you hit rate limits. |
| `--correct-chunk-chars N` | `8000` | Char-count chunk size for the revision step. |
| `--correct-model M` | `mimo-v2.5-pro` | Revision model. `mimo-*` → Xiaomi, `deepseek-*` → DeepSeek. |
| `--summary-model M` | `mimo-v2.5-pro` | Summary model. Same prefix routing. |
| `--skip-correct` | off | Skip revision; summarize the raw ASR output. |
| `--skip-summary` | off | Skip summary; only produce the transcript. |

## How it works

1. **ffmpeg normalize**: any input → 16 kHz mono WAV. Video files have only the audio track extracted. Multi-file inputs are concatenated via ffmpeg's `concat` demuxer after each is normalized.
2. **Silence-aware chunking**: scan for silences with `silencedetect`, then for each target cut point pick the nearest silence midpoint within a ±30% window. Falls back to hard split if no silence is found in the window.
3. **ASR**: each ~10-min mp3 chunk (mono 16k 64k) is base64-uploaded to `mimo-v2.5` with `thinking: disabled` for speed. The 10 MB upload cap is the reason the pipeline transcodes to mp3 instead of sending raw WAV.
4. **Revise**: text is chunked to 8000 chars and sent to `mimo-v2.5-pro` with a strict prompt that (a) fixes typos / English-term miswrites, (b) re-segments by semantics + speaker change, (c) prefixes every paragraph with `**{speaker}**：`. The prompt does **not** change word order, delete content, or paraphrase.
5. **Summarize**: the whole transcript goes to `mimo-v2.5-pro` in one streaming call (the 1M context window fits multi-hour transcripts; streaming keeps the long-lived connection alive). The summary prompt is in [`skills/audio-transcribe-summarize/references/summary_prompts.md`](./skills/audio-transcribe-summarize/references/summary_prompts.md) — edit it without touching the pipeline code.

## Limits

- **MiMo multimodal `input_audio.data` is capped at 10 MB after base64.** At mp3 mono 16k 64k that's roughly 16 min of audio per chunk — the default 10-min chunk leaves margin. Drop `--asr-bitrate 32k` if you want longer chunks.
- **Speaker diarization is LLM-inferred**, not audio-side. Pass `--intro intro.md` with a guest list to get real names; otherwise the pipeline falls back to `主持人 / 嘉宾A / 嘉宾B`.
- **Timestamps are dropped.** If you need SRT subtitles, use a different tool.
- Auto-retry covers timeouts / connection errors. Hard errors (bad args, chunk over 10 MB) abort with a clear message.

## License

MIT — see [LICENSE](./LICENSE).

## Acknowledgements

- [Xiaomi MiMo](https://mimo.xiaomi.com/) — the multimodal model that makes this pipeline economically viable.
- [DeepSeek](https://platform.deepseek.com/) — the alternative provider for the text steps.
- [ffmpeg](https://ffmpeg.org/) — does all the heavy lifting for format normalization and silence detection.
