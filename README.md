# ytct: YouTube → Shorts

A local app that turns a YouTube video into vertical **9:16 shorts** (30 s by default) and saves each one with:

- the clip (`1080x1920` MP4, H.264/AAC)
- a transcript (`transcript.txt`) and timed captions (`transcript.srt`)
- a **title, description, tags and hashtags** written by Claude (Anthropic) or a Groq LLM, plus a 1–10 "virality" score (`metadata.json`, `upload.txt`)

You can use it from a dark-theme web UI or from the command line.

## Requirements

- Python 3.10+
- ffmpeg on PATH (`winget install Gyan.FFmpeg`)
- Node.js or Deno on PATH (yt-dlp needs a JS runtime for YouTube)
- An API key: **Anthropic** (https://console.anthropic.com) and/or **Groq** (free at https://console.groq.com/keys).
  - Either key gives you AI titles, descriptions and tags. Claude uses `claude-haiku-5-5` by default.
  - Only Groq does speech-to-text (Whisper). With just an Anthropic key, transcripts come from the video's YouTube captions.
  - With no key you still get clips and YouTube-caption transcripts, but no AI metadata.

## Setup

**Windows**

```bash
python -m venv .venv
```
```bash
.venv\Scripts\python.exe -m pip install -r requirements.txt
```
```bash
copy .env.example .env
```

Or just double-click `start.bat`, which sets everything up on first run and opens the UI.

**macOS / Linux**

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt && cp .env.example .env
```

Then put your key(s) in `.env` (`ANTHROPIC_API_KEY=sk-ant-...` and/or `GROQ_API_KEY=gsk_...`), or paste them into **Settings** in the UI. `.env` is git-ignored, so your keys are never committed.

The commands below use the Windows path `.venv\Scripts\python.exe`. On macOS/Linux use `.venv/bin/python` instead.

## Web UI

```bash
.venv\Scripts\python.exe ytct.py serve
```
This opens http://localhost:8765. Paste a URL, choose options, and click **Create shorts**. You get live progress and a log. When it finishes, open the project to watch each short and copy its title, description or tags, or regenerate them.

## CLI (no UI)

```bash
.venv\Scripts\python.exe ytct.py run "https://www.youtube.com/watch?v=VIDEO_ID"
```

Options for `run`:

| flag | default | meaning |
|---|---|---|
| `--clip 30` | 30 | clip length in seconds |
| `--mode crop\|blur` | crop | `crop` fills the frame with a center crop; `blur` fits the whole frame on a blurred background |
| `--transcriber auto\|groq\|youtube\|none` | auto | `auto` uses Groq Whisper when a key is set, otherwise YouTube captions |
| `--max-clips N` | all | stop after N shorts |
| `--start 1:30 --end 10:00` | whole video | process only part of the video |
| `--language en` | auto | transcription language hint |
| `--min-last 10` | 10 | drop a leftover piece at the end shorter than this (seconds) |
| `--width 720` | 1080 | output width (height = width × 16/9) |
| `--no-meta` | | skip the LLM title, description and tags |
| `--force` | | redo every step instead of resuming |

Other commands:

```bash
.venv\Scripts\python.exe ytct.py list
```
```bash
.venv\Scripts\python.exe ytct.py show <project-id>
```
```bash
.venv\Scripts\python.exe ytct.py regen <project-id> -n 3
```

Runs can be resumed. If you re-run the same URL, steps that already finished (download, cuts, transcripts, metadata) are skipped, so an interrupted job picks up where it stopped.

## Output layout

```
projects/<video-title>-<video-id>/
  project.json              manifest: source info, options, list of shorts
  source.mp4                downloaded video
  shorts/short_001/
    short_001.mp4
    transcript.txt
    transcript.srt
    metadata.json           title, description, tags, hashtags, virality_score, reason
    upload.txt              copy/paste block for YouTube Studio
```

## Configuration (`.env`)

| variable | default |
|---|---|
| `ANTHROPIC_API_KEY` | enables Claude for metadata |
| `ANTHROPIC_MODEL` | `claude-haiku-5-5` |
| `GROQ_API_KEY` | enables Whisper transcripts and Groq metadata |
| `GROQ_MODEL` | `llama-3.3-70b-versatile` |
| `LLM_PROVIDER` | `auto`: whichever key is set, Claude if both are. Use `anthropic` or `groq` to force one. |
| `GROQ_WHISPER_MODEL` | `whisper-large-v3-turbo` |
| `OUTPUT_DIR` | `projects` |
| `FFMPEG_PATH` / `FFPROBE_PATH` | found on PATH |

## Notes

- Rate limits are handled automatically: Groq and Anthropic requests wait and retry on HTTP 429.
- A final piece shorter than 10 s is dropped.
- The server listens on `127.0.0.1` only. It is meant for your own machine.
- Only process videos you have the rights to reuse.
