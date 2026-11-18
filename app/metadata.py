"""Generate YouTube Shorts title / description / tags from a clip transcript.

Uses Claude (ANTHROPIC_API_KEY) or a Groq LLM (GROQ_API_KEY) - see config.llm_provider().
"""
from __future__ import annotations

from typing import Callable, Optional

from . import anthropic_api, config, groq_api

SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "description": {"type": "string"},
        "tags": {"type": "array", "items": {"type": "string"}},
        "hashtags": {"type": "array", "items": {"type": "string"}},
        "virality_score": {"type": "integer"},
        "reason": {"type": "string"},
    },
    "required": ["title", "description", "tags", "hashtags", "virality_score", "reason"],
    "additionalProperties": False,
}

SYSTEM_PROMPT = """You are an expert YouTube Shorts strategist and copywriter.
You receive the transcript of ONE ~30 second vertical clip cut from a longer YouTube video,
plus context about the source video. Write upload metadata for this short.

Rules:
- title: max 90 characters, a strong hook that matches what is actually said in the clip. No fake claims, no ALL CAPS sentences, at most one emoji.
- description: 2-4 short sentences that summarise the clip and invite viewers to watch/follow, then a blank line, then 3-5 relevant hashtags on one line (always include #shorts).
- tags: 10-15 search keywords/phrases (lowercase, no '#'), mixing broad and specific terms.
- hashtags: 3-5 hashtags (with '#'), always including #shorts.
- virality_score: integer 1-10, how likely this clip is to perform well as a standalone short (hook strength, self-contained, emotional/informative value).
- reason: one sentence explaining the score.
- If the transcript is empty or meaningless (music, silence), base the metadata on the source video context and give a low score.
- Write in the same language as the transcript.

Respond with ONLY a JSON object with exactly these keys:
{"title": str, "description": str, "tags": [str], "hashtags": [str], "virality_score": int, "reason": str}"""


def _clean_tags(tags) -> list[str]:
    if isinstance(tags, str):
        tags = tags.split(",")
    seen, out, total = set(), [], 0
    for tag in tags or []:
        tag = str(tag).strip().lstrip("#").strip()
        if not tag or tag.lower() in seen:
            continue
        # YouTube allows 500 characters of tags in total (commas included)
        if total + len(tag) + 1 > 480:
            break
        seen.add(tag.lower())
        out.append(tag)
        total += len(tag) + 1
    return out


def _clean_hashtags(tags) -> list[str]:
    if isinstance(tags, str):
        tags = tags.replace(",", " ").split()
    out = []
    for tag in tags or []:
        tag = "#" + str(tag).strip().lstrip("#").replace(" ", "")
        if len(tag) > 1 and tag.lower() not in (t.lower() for t in out):
            out.append(tag)
    if "#shorts" not in (t.lower() for t in out):
        out.append("#shorts")
    return out[:6]


def generate(transcript: str, context: dict, log: Optional[Callable[[str], None]] = None) -> dict:
    """context: source_title, channel, source_tags, clip_index, clip_count, start, end."""
    user = (
        f"Source video title: {context.get('source_title', '')}\n"
        f"Channel: {context.get('channel', '')}\n"
        f"Source video tags: {', '.join((context.get('source_tags') or [])[:20])}\n"
        f"Clip {context.get('clip_index')} of {context.get('clip_count')} "
        f"(from {context.get('start', 0):.0f}s to {context.get('end', 0):.0f}s of the source)\n\n"
        f"Clip transcript:\n\"\"\"\n{transcript.strip() or '(no speech detected)'}\n\"\"\""
    )
    provider = config.llm_provider()
    if provider == "anthropic":
        data = anthropic_api.chat_json(SYSTEM_PROMPT, user, SCHEMA, log=log)
    elif provider == "groq":
        data = groq_api.chat_json(SYSTEM_PROMPT, user, log=log)
    else:
        raise RuntimeError("No LLM key set - add ANTHROPIC_API_KEY or GROQ_API_KEY (Settings or .env)")

    title = str(data.get("title", "")).strip().strip('"')
    if len(title) > 100:
        title = title[:97].rstrip() + "..."
    try:
        score = max(1, min(10, int(data.get("virality_score", 0))))
    except (TypeError, ValueError):
        score = 0
    return {
        "title": title,
        "description": str(data.get("description", "")).strip(),
        "tags": _clean_tags(data.get("tags")),
        "hashtags": _clean_hashtags(data.get("hashtags")),
        "virality_score": score,
        "reason": str(data.get("reason", "")).strip(),
    }


def upload_text(meta: dict) -> str:
    """Plain-text block that is easy to copy/paste into YouTube Studio."""
    return (
        f"TITLE:\n{meta.get('title', '')}\n\n"
        f"DESCRIPTION:\n{meta.get('description', '')}\n\n"
        f"TAGS:\n{', '.join(meta.get('tags', []))}\n\n"
        f"HASHTAGS:\n{' '.join(meta.get('hashtags', []))}\n"
    )
