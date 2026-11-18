"""Minimal Groq client (OpenAI-compatible REST API): Whisper transcription + JSON chat."""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Callable, Optional

import requests

from . import config

BASE_URL = "https://api.groq.com/openai/v1"
MAX_ATTEMPTS = 6


class GroqError(RuntimeError):
    pass


def _retry_after(resp: requests.Response) -> Optional[float]:
    value = resp.headers.get("retry-after")
    if not value:
        return None
    try:
        return min(float(value), 120.0)
    except ValueError:
        return None


def _request(path: str, log: Optional[Callable[[str], None]] = None, **kwargs) -> dict:
    key = config.groq_key()
    if not key:
        raise GroqError("GROQ_API_KEY is not set - add it in Settings (UI) or in the .env file")
    headers = {"Authorization": f"Bearer {key}"}
    if "json" in kwargs:
        headers["Content-Type"] = "application/json"

    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            resp = requests.post(BASE_URL + path, headers=headers, timeout=300, **kwargs)
        except requests.RequestException as exc:
            if attempt == MAX_ATTEMPTS:
                raise GroqError(f"Network error talking to Groq: {exc}") from exc
            time.sleep(2 ** attempt)
            continue

        if resp.status_code in (429, 500, 502, 503, 504) and attempt < MAX_ATTEMPTS:
            wait = _retry_after(resp) or min(60.0, 2.0 ** attempt)
            if log:
                log(f"  Groq returned {resp.status_code}, retrying in {wait:.0f}s ...")
            time.sleep(wait)
            continue
        if resp.status_code >= 400:
            try:
                detail = resp.json().get("error", {}).get("message") or resp.text
            except ValueError:
                detail = resp.text
            raise GroqError(f"Groq API error {resp.status_code}: {detail[:400]}")
        return resp.json()
    raise GroqError("Groq request failed after retries")


def transcribe(audio_path: Path, log: Optional[Callable[[str], None]] = None, language: str = "") -> dict:
    """Transcribe an audio file with Groq Whisper. Returns {text, language, segments:[{start,end,text}]}."""
    data = {
        "model": config.whisper_model(),
        "response_format": "verbose_json",
        "temperature": "0",
    }
    if language:
        data["language"] = language
    audio = Path(audio_path).read_bytes()  # bytes (not a handle) so retries can resend it
    files = {"file": (Path(audio_path).name, audio, "audio/mpeg")}
    result = _request("/audio/transcriptions", log=log, data=data, files=files)
    segments = [
        {"start": float(s.get("start", 0)), "end": float(s.get("end", 0)), "text": str(s.get("text", "")).strip()}
        for s in result.get("segments") or []
        if str(s.get("text", "")).strip()
    ]
    return {"text": str(result.get("text", "")).strip(), "language": result.get("language", ""), "segments": segments}


def chat_json(system: str, user: str, log: Optional[Callable[[str], None]] = None, temperature: float = 0.7) -> dict:
    """Run a chat completion that must return a JSON object."""
    payload = {
        "model": config.groq_model(),
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "temperature": temperature,
        "response_format": {"type": "json_object"},
    }
    result = _request("/chat/completions", log=log, json=payload)
    content = result["choices"][0]["message"]["content"] or ""
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", content, re.S)
        if match:
            return json.loads(match.group(0))
        raise GroqError(f"Model did not return valid JSON: {content[:300]}")
