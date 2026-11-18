"""Subtitle helpers: parse YouTube WebVTT captions, slice them per clip, write SRT."""
from __future__ import annotations

import html
import re
from pathlib import Path

_TAG = re.compile(r"<[^>]+>")


def _ts(value: str) -> float:
    parts = value.strip().replace(",", ".").split(":")
    seconds = 0.0
    for part in parts:
        seconds = seconds * 60 + float(part)
    return seconds


def parse_vtt(path: Path) -> list[dict]:
    """Parse a .vtt file into [{start, end, text}].

    YouTube auto-captions repeat the previous line in every cue ("rolling" captions),
    so lines that were emitted recently are dropped.
    """
    text = Path(path).read_text(encoding="utf-8", errors="replace").replace("\r", "")
    cues: list[dict] = []
    recent: list[str] = []
    for block in re.split(r"\n\s*\n", text):
        lines = block.strip().split("\n")
        idx = next((i for i, line in enumerate(lines) if "-->" in line), None)
        if idx is None:
            continue
        left, right = lines[idx].split("-->", 1)
        try:
            start, end = _ts(left), _ts(right.strip().split()[0])
        except (ValueError, IndexError):
            continue
        new_lines = []
        for raw in lines[idx + 1:]:
            line = html.unescape(_TAG.sub("", raw)).strip()
            if not line or line in recent[-3:]:
                continue
            recent.append(line)
            new_lines.append(line)
        if new_lines:
            cues.append({"start": start, "end": end, "text": " ".join(new_lines)})
    return cues


def slice_segments(cues: list[dict], start: float, end: float) -> list[dict]:
    """Cues that start inside [start, end), re-timed relative to the clip start."""
    out = []
    for cue in cues:
        if start <= cue["start"] < end:
            out.append({
                "start": round(cue["start"] - start, 3),
                "end": round(min(cue["end"], end) - start, 3),
                "text": cue["text"],
            })
    return out


def _srt_time(seconds: float) -> str:
    ms = int(round(max(seconds, 0) * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def to_srt(segments: list[dict]) -> str:
    blocks = []
    for i, seg in enumerate(segments, 1):
        blocks.append(f"{i}\n{_srt_time(seg['start'])} --> {_srt_time(seg['end'])}\n{seg['text']}\n")
    return "\n".join(blocks)


def to_text(segments: list[dict]) -> str:
    return " ".join(seg["text"] for seg in segments).strip()
