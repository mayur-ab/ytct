"""The processing pipeline: download -> cut 9:16 clips -> transcribe -> LLM metadata (Claude or Groq).

Project layout (one folder per source video):

    projects/<slug>-<video_id>/
        project.json            manifest of the whole run
        source.mp4              downloaded video
        shorts/short_001/
            short_001.mp4       1080x1920 clip
            transcript.txt      plain transcript
            transcript.srt      timed captions (relative to the clip)
            metadata.json       title, description, tags, hashtags, score
            upload.txt          copy/paste block for YouTube Studio
"""
from __future__ import annotations

import json
import math
import re
import shutil
import subprocess
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

import yt_dlp

from . import captions, config, groq_api, metadata

Log = Callable[[str], None]
Progress = Callable[[float, str], None]
StopCheck = Callable[[], bool]

VIDEO_EXTS = (".mp4", ".mkv", ".webm", ".mov")


class Cancelled(Exception):
    pass


@dataclass
class Options:
    clip_seconds: float = 30
    mode: str = "crop"            # crop = fill frame (center crop) | blur = full frame on blurred background
    transcriber: str = "auto"     # auto | groq | youtube | none
    max_clips: int = 0            # 0 = no limit
    start: float = 0              # only process this part of the source (seconds)
    end: float = 0                # 0 = until the end
    min_last: float = 10          # drop a final clip shorter than this
    width: int = 1080             # output is width x (width*16/9)
    metadata: bool = True         # call the LLM for title/description/tags
    language: str = ""            # optional transcription language hint (e.g. "en")
    force: bool = False           # redo steps even if their output exists

    @classmethod
    def from_dict(cls, data: dict) -> "Options":
        opts = cls()
        for key, value in (data or {}).items():
            if hasattr(opts, key) and value not in (None, ""):
                cur = getattr(opts, key)
                if isinstance(cur, bool):
                    value = value if isinstance(value, bool) else str(value).lower() in ("1", "true", "yes", "on")
                elif isinstance(cur, (int, float)) and not isinstance(value, (int, float)):
                    value = parse_time(value) if key in ("start", "end") else type(cur)(float(value))
                setattr(opts, key, value)
        if opts.mode not in ("crop", "blur"):
            raise ValueError("mode must be 'crop' or 'blur'")
        if opts.transcriber not in ("auto", "groq", "youtube", "none"):
            raise ValueError("transcriber must be auto, groq, youtube or none")
        if not 5 <= float(opts.clip_seconds) <= 180:
            raise ValueError("clip length must be between 5 and 180 seconds")
        opts.width = int(opts.width) // 2 * 2
        return opts


# --------------------------------------------------------------------------- helpers

def parse_time(value) -> float:
    """'90', '1:30', '01:01:30' -> seconds."""
    if isinstance(value, (int, float)):
        return float(value)
    value = str(value).strip()
    if not value:
        return 0.0
    seconds = 0.0
    for part in value.split(":"):
        seconds = seconds * 60 + float(part)
    return seconds


def fmt_time(seconds: float) -> str:
    seconds = int(round(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def slugify(text: str, limit: int = 50) -> str:
    text = re.sub(r"[^\w\s-]", "", text, flags=re.UNICODE).strip().lower()
    text = re.sub(r"[\s_-]+", "-", text)
    return text[:limit].strip("-") or "video"


def _run(cmd: list[str]) -> None:
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        tail = "\n".join(proc.stderr.strip().splitlines()[-8:])
        raise RuntimeError(f"ffmpeg failed:\n{tail}")


def probe(path: Path) -> dict:
    cmd = [config.ffprobe_bin(), "-v", "error", "-show_entries", "format=duration:stream=codec_type",
           "-of", "json", str(path)]
    out = subprocess.run(cmd, capture_output=True, text=True, check=True).stdout
    data = json.loads(out)
    return {
        "duration": float(data.get("format", {}).get("duration") or 0),
        "has_audio": any(s.get("codec_type") == "audio" for s in data.get("streams", [])),
    }


def write_json(path: Path, data: dict) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def read_json(path: Path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _js_runtimes() -> dict:
    """YouTube needs a JS runtime for yt-dlp; enable whichever ones are installed."""
    found = {name: {} for name in ("deno", "node", "bun") if shutil.which(name)}
    return found or {"deno": {}}


# --------------------------------------------------------------------------- download

def _ydl_base() -> dict:
    return {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "noprogress": True,
        "js_runtimes": _js_runtimes(),
        "ffmpeg_location": str(Path(config.ffmpeg_bin()).parent),
    }


def fetch_info(url: str) -> dict:
    with yt_dlp.YoutubeDL({**_ydl_base(), "skip_download": True}) as ydl:
        info = ydl.extract_info(url, download=False)
    if info.get("_type") == "playlist":
        entries = [e for e in info.get("entries") or [] if e]
        if not entries:
            raise RuntimeError("Playlist has no videos")
        info = entries[0]
    return info


def project_dir_for(info: dict) -> Path:
    return config.output_dir() / f"{slugify(info.get('title', ''))}-{info['id']}"


def find_source(pdir: Path) -> Optional[Path]:
    for ext in VIDEO_EXTS:
        path = pdir / f"source{ext}"
        if path.exists():
            return path
    return None


def download_video(url: str, pdir: Path, log: Log, progress: Progress, check_stop: StopCheck) -> Path:
    last = {"t": 0.0}

    def hook(d):
        if check_stop():
            raise Cancelled()
        if d.get("status") == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            done = d.get("downloaded_bytes") or 0
            if total and time.time() - last["t"] > 0.5:
                last["t"] = time.time()
                progress(0.02 + 0.18 * done / total, f"Downloading {done / total:.0%}")

    opts = {
        **_ydl_base(),
        "format": "bv*[height<=1080][ext=mp4]+ba[ext=m4a]/bv*[height<=1080]+ba/b[height<=1080]/b",
        "merge_output_format": "mp4",
        "outtmpl": str(pdir / "source.%(ext)s"),
        "progress_hooks": [hook],
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.download([url])
    src = find_source(pdir)
    if not src:
        raise RuntimeError("Download finished but no video file was found")
    return src


def download_captions(url: str, info: dict, pdir: Path, log: Log) -> Optional[Path]:
    """Fetch YouTube's own subtitles (manual first, then auto-generated) as .vtt."""
    existing = sorted(pdir.glob("source*.vtt"))
    if existing:
        return existing[0]
    lang = (info.get("language") or "en").split("-")[0]
    manual = info.get("subtitles") or {}
    auto = info.get("automatic_captions") or {}

    def pick(tracks: dict) -> Optional[str]:
        for key in (lang, f"{lang}-orig", "en", "en-orig"):
            if key in tracks:
                return key
        return next((k for k in tracks if k.startswith(lang) or k.startswith("en")), None)

    key, is_auto = pick(manual), False
    if not key:
        key, is_auto = pick(auto), True
    if not key:
        log("  No YouTube captions available for this video")
        return None
    opts = {
        **_ydl_base(),
        "skip_download": True,
        "writesubtitles": not is_auto,
        "writeautomaticsub": is_auto,
        "subtitleslangs": [key],
        "subtitlesformat": "vtt",
        "outtmpl": str(pdir / "source.%(ext)s"),
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.download([url])
    found = sorted(pdir.glob("source*.vtt"))
    if found:
        log(f"  Using YouTube {'auto-generated' if is_auto else 'uploaded'} captions ({key})")
    return found[0] if found else None


# --------------------------------------------------------------------------- video

def cut_clip(src: Path, dst: Path, start: float, duration: float, mode: str, width: int, has_audio: bool) -> None:
    w, h = width, int(round(width * 16 / 9 / 2)) * 2
    cmd = [config.ffmpeg_bin(), "-y", "-hide_banner", "-loglevel", "error",
           "-ss", f"{start:.3f}", "-i", str(src), "-t", f"{duration:.3f}"]
    if mode == "blur":
        sw, sh = w // 4 // 2 * 2, h // 4 // 2 * 2
        graph = (
            f"[0:v]split=2[bg][fg];"
            f"[bg]scale={sw}:{sh}:force_original_aspect_ratio=increase,crop={sw}:{sh},boxblur=12:2,"
            f"scale={w}:{h}[bgb];"
            f"[fg]scale={w}:{h}:force_original_aspect_ratio=decrease[fgs];"
            f"[bgb][fgs]overlay=(W-w)/2:(H-h)/2,setsar=1,format=yuv420p[v]"
        )
        cmd += ["-filter_complex", graph, "-map", "[v]"]
    else:
        vf = f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},setsar=1,format=yuv420p"
        cmd += ["-vf", vf, "-map", "0:v:0"]
    if has_audio:
        cmd += ["-map", "0:a:0", "-c:a", "aac", "-b:a", "160k"]
    cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "21", "-movflags", "+faststart", str(dst)]
    _run(cmd)


def extract_audio(video: Path, dst: Path) -> None:
    _run([config.ffmpeg_bin(), "-y", "-hide_banner", "-loglevel", "error", "-i", str(video),
          "-vn", "-ac", "1", "-ar", "16000", "-c:a", "libmp3lame", "-b:a", "48k", str(dst)])


# --------------------------------------------------------------------------- main entry

def _noop(*_a, **_k):
    return False


def process(url: str, opts: Options, log: Log = print, progress: Progress = _noop,
            check_stop: StopCheck = _noop) -> dict:
    """Run the full pipeline. Safe to re-run: finished steps are skipped unless opts.force."""

    def stop():
        if check_stop():
            raise Cancelled()

    t0 = time.time()
    progress(0.0, "Fetching video info")
    log(f"Fetching info: {url}")
    info = fetch_info(url)
    pdir = project_dir_for(info)
    pdir.mkdir(parents=True, exist_ok=True)
    log(f"Video: {info.get('title')}  ({fmt_time(info.get('duration') or 0)})")
    log(f"Project folder: {pdir}")

    manifest_path = pdir / "project.json"
    manifest = read_json(manifest_path, {}) or {}
    manifest.update({
        "id": pdir.name,
        "url": info.get("webpage_url") or url,
        "video_id": info["id"],
        "title": info.get("title", ""),
        "channel": info.get("channel") or info.get("uploader") or "",
        "source_tags": (info.get("tags") or [])[:30],
        "thumbnail": info.get("thumbnail", ""),
        "created": manifest.get("created") or datetime.now().isoformat(timespec="seconds"),
        "updated": datetime.now().isoformat(timespec="seconds"),
        "options": asdict(opts),
        "status": "processing",
    })
    write_json(manifest_path, manifest)

    # 1) download
    stop()
    src = None if opts.force else find_source(pdir)
    if src:
        log("Source already downloaded - skipping download")
    else:
        log("Downloading video ...")
        src = download_video(url, pdir, log, progress, check_stop)
    progress(0.2, "Analysing video")
    meta = probe(src)
    duration = meta["duration"]
    manifest["duration"] = duration

    # 2) plan clips
    begin = max(0.0, opts.start)
    finish = min(duration, opts.end) if opts.end else duration
    if finish - begin < 1:
        raise RuntimeError("Selected time range is empty")
    clip_len = float(opts.clip_seconds)
    ranges = []
    t = begin
    while t < finish - 0.5:
        end = min(t + clip_len, finish)
        piece = end - t
        # only a leftover partial piece at the end can be dropped, never a full-length clip
        if ranges and piece < clip_len and piece < min(opts.min_last, clip_len):
            break
        ranges.append((round(t, 3), round(end, 3)))
        t = end
    if opts.max_clips:
        ranges = ranges[: opts.max_clips]
    log(f"Creating {len(ranges)} short(s) of {clip_len:g}s, framing: {opts.mode}")

    # 3) transcript source
    transcriber = opts.transcriber
    if transcriber == "auto":
        transcriber = "groq" if config.groq_key() else "youtube"
    if not meta["has_audio"]:
        transcriber = "none"
        log("Video has no audio track - skipping transcripts")
    yt_cues: list[dict] = []
    if transcriber == "youtube":
        vtt = download_captions(url, info, pdir, log)
        yt_cues = captions.parse_vtt(vtt) if vtt else []
    log(f"Transcripts: {transcriber}" + (" (Groq Whisper)" if transcriber == "groq" else ""))
    provider = config.llm_provider()
    do_meta = opts.metadata and bool(provider)
    if opts.metadata and not do_meta:
        log("No ANTHROPIC_API_KEY or GROQ_API_KEY set - skipping title/description/tags generation")
    elif do_meta:
        log(f"Metadata: {provider} ({config.llm_model()})")

    shorts_dir = pdir / "shorts"
    shorts_dir.mkdir(exist_ok=True)
    shorts, errors = [], 0
    for i, (cs, ce) in enumerate(ranges, 1):
        stop()
        name = f"short_{i:03d}"
        cdir = shorts_dir / name
        cdir.mkdir(exist_ok=True)
        video = cdir / f"{name}.mp4"
        base = 0.2 + 0.8 * (i - 1) / len(ranges)
        step = 0.8 / len(ranges)
        label = f"Short {i}/{len(ranges)} [{fmt_time(cs)}-{fmt_time(ce)}]"
        entry = {"index": i, "name": name, "start": cs, "end": ce, "dir": f"shorts/{name}",
                 "video": f"shorts/{name}/{name}.mp4"}

        try:
            # cut
            info_path = cdir / "clip.json"
            prev = read_json(info_path, {}) or {}
            same_cut = prev.get("start") == cs and prev.get("end") == ce and prev.get("mode") == opts.mode \
                and prev.get("width") == opts.width
            if opts.force or not video.exists() or not same_cut:
                progress(base, f"{label}: cutting")
                cut_clip(src, video, cs, ce - cs, opts.mode, opts.width, meta["has_audio"])
                write_json(info_path, {"start": cs, "end": ce, "mode": opts.mode, "width": opts.width})
                for stale in ("transcript.txt", "transcript.srt", "metadata.json", "upload.txt"):
                    (cdir / stale).unlink(missing_ok=True)
            log(f"{label}: video ready")

            # transcript
            stop()
            txt_path, srt_path = cdir / "transcript.txt", cdir / "transcript.srt"
            if transcriber != "none" and (opts.force or not txt_path.exists()):
                progress(base + step * 0.4, f"{label}: transcribing")
                if transcriber == "groq":
                    audio = cdir / "audio.mp3"
                    extract_audio(video, audio)
                    try:
                        result = groq_api.transcribe(audio, log=log, language=opts.language)
                    finally:
                        audio.unlink(missing_ok=True)
                    segments = result["segments"]
                    text = result["text"] or captions.to_text(segments)
                else:
                    segments = captions.slice_segments(yt_cues, cs, ce)
                    text = captions.to_text(segments)
                txt_path.write_text(text + "\n", encoding="utf-8")
                srt_path.write_text(captions.to_srt(segments), encoding="utf-8")
                log(f"{label}: transcript ({len(text.split())} words)")
            transcript = txt_path.read_text(encoding="utf-8").strip() if txt_path.exists() else ""

            # metadata
            stop()
            meta_path = cdir / "metadata.json"
            if do_meta and (opts.force or not meta_path.exists()):
                progress(base + step * 0.75, f"{label}: writing title & tags")
                m = generate_clip_metadata(manifest, transcript, i, len(ranges), cs, ce, log)
                write_json(meta_path, m)
                (cdir / "upload.txt").write_text(metadata.upload_text(m), encoding="utf-8")
                log(f"{label}: \"{m['title']}\" (score {m['virality_score']}/10)")
            entry["title"] = (read_json(meta_path, {}) or {}).get("title", "")
        except Cancelled:
            raise
        except Exception as exc:  # keep going with the other clips
            errors += 1
            entry["error"] = str(exc)
            log(f"{label}: ERROR {exc}")
        shorts.append(entry)
        manifest["shorts"] = shorts
        write_json(manifest_path, manifest)

    # drop folders of clips that are no longer part of this run (e.g. a shorter re-run)
    keep = {s["name"] for s in shorts}
    for extra in shorts_dir.glob("short_*"):
        if extra.is_dir() and extra.name not in keep and opts.force:
            shutil.rmtree(extra, ignore_errors=True)

    manifest["status"] = "done" if not errors else "done_with_errors"
    manifest["updated"] = datetime.now().isoformat(timespec="seconds")
    write_json(manifest_path, manifest)
    progress(1.0, "Done")
    log(f"Finished {len(shorts)} short(s) in {time.time() - t0:.0f}s" + (f", {errors} error(s)" if errors else ""))
    log(f"Saved to: {pdir}")
    return manifest


def generate_clip_metadata(manifest: dict, transcript: str, index: int, count: int,
                           start: float, end: float, log: Optional[Log] = None) -> dict:
    m = metadata.generate(transcript, {
        "source_title": manifest.get("title", ""),
        "channel": manifest.get("channel", ""),
        "source_tags": manifest.get("source_tags", []),
        "clip_index": index, "clip_count": count, "start": start, "end": end,
    }, log=log)
    m.update({"provider": config.llm_provider(), "model": config.llm_model(), "generated": datetime.now().isoformat(timespec="seconds"),
              "start": start, "end": end, "source_url": manifest.get("url", "")})
    return m


# --------------------------------------------------------------------------- project access

def project_path(project_id: str) -> Path:
    if not re.fullmatch(r"[\w.-]+", project_id or ""):
        raise FileNotFoundError(project_id)
    path = config.output_dir() / project_id
    if not (path / "project.json").exists():
        raise FileNotFoundError(project_id)
    return path


def list_projects() -> list[dict]:
    out = []
    for manifest_path in config.output_dir().glob("*/project.json"):
        m = read_json(manifest_path)
        if not m:
            continue
        out.append({k: m.get(k) for k in ("id", "title", "channel", "url", "thumbnail", "duration",
                                          "created", "updated", "status")}
                   | {"count": len(m.get("shorts") or [])})
    return sorted(out, key=lambda p: p.get("updated") or "", reverse=True)


def load_project(project_id: str) -> dict:
    pdir = project_path(project_id)
    m = read_json(pdir / "project.json", {})
    shorts = []
    for s in m.get("shorts") or []:
        cdir = pdir / s["dir"]
        txt = cdir / "transcript.txt"
        shorts.append({
            **s,
            "transcript": txt.read_text(encoding="utf-8").strip() if txt.exists() else "",
            "metadata": read_json(cdir / "metadata.json"),
            "exists": (pdir / s["video"]).exists(),
        })
    m["shorts"] = shorts
    m["path"] = str(pdir)
    return m


def regenerate_metadata(project_id: str, index: int, log: Log = print) -> dict:
    pdir = project_path(project_id)
    m = read_json(pdir / "project.json", {})
    shorts = m.get("shorts") or []
    entry = next((s for s in shorts if s["index"] == index), None)
    if not entry:
        raise FileNotFoundError(f"short {index}")
    cdir = pdir / entry["dir"]
    txt = cdir / "transcript.txt"
    transcript = txt.read_text(encoding="utf-8") if txt.exists() else ""
    meta = generate_clip_metadata(m, transcript, index, len(shorts), entry["start"], entry["end"], log)
    write_json(cdir / "metadata.json", meta)
    (cdir / "upload.txt").write_text(metadata.upload_text(meta), encoding="utf-8")
    entry["title"] = meta["title"]
    write_json(pdir / "project.json", m)
    return meta
