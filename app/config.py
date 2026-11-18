"""Settings, read from environment / .env at call time so UI changes apply immediately."""
from __future__ import annotations

import os
import shutil
from pathlib import Path

from dotenv import load_dotenv, set_key

ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = ROOT / ".env"

load_dotenv(ENV_FILE)

DEFAULT_LLM_MODEL = "llama-3.3-70b-versatile"
DEFAULT_ANTHROPIC_MODEL = "claude-haiku-5-5"
DEFAULT_WHISPER_MODEL = "whisper-large-v3-turbo"
PROVIDERS = ("auto", "anthropic", "groq")


def groq_key() -> str:
    return os.getenv("GROQ_API_KEY", "").strip()


def anthropic_key() -> str:
    return os.getenv("ANTHROPIC_API_KEY", "").strip()


def groq_model() -> str:
    return os.getenv("GROQ_MODEL", "").strip() or DEFAULT_LLM_MODEL


def anthropic_model() -> str:
    return os.getenv("ANTHROPIC_MODEL", "").strip() or DEFAULT_ANTHROPIC_MODEL


def llm_provider() -> str:
    """Which LLM writes titles/descriptions/tags: '' if no key is set.

    LLM_PROVIDER=anthropic|groq forces one; 'auto' (default) uses whichever key is
    set, preferring Anthropic when both are.
    """
    choice = os.getenv("LLM_PROVIDER", "auto").strip().lower() or "auto"
    if choice == "anthropic":
        return "anthropic" if anthropic_key() else ""
    if choice == "groq":
        return "groq" if groq_key() else ""
    if anthropic_key():
        return "anthropic"
    if groq_key():
        return "groq"
    return ""


def llm_model() -> str:
    return anthropic_model() if llm_provider() == "anthropic" else groq_model()


def whisper_model() -> str:
    return os.getenv("GROQ_WHISPER_MODEL", "").strip() or DEFAULT_WHISPER_MODEL


def output_dir() -> Path:
    raw = os.getenv("OUTPUT_DIR", "").strip()
    path = Path(raw) if raw else ROOT / "projects"
    if not path.is_absolute():
        path = ROOT / path
    path.mkdir(parents=True, exist_ok=True)
    return path


def save_setting(name: str, value: str) -> None:
    """Persist a setting to .env and apply it to the running process."""
    ENV_FILE.touch(exist_ok=True)
    set_key(str(ENV_FILE), name, value, quote_mode="never")
    os.environ[name] = value


def ffmpeg_bin() -> str:
    path = os.getenv("FFMPEG_PATH", "").strip() or shutil.which("ffmpeg")
    if not path:
        raise RuntimeError("ffmpeg not found. Install it (winget install Gyan.FFmpeg) or set FFMPEG_PATH in .env")
    return path


def ffprobe_bin() -> str:
    path = os.getenv("FFPROBE_PATH", "").strip() or shutil.which("ffprobe")
    if not path:
        ff = Path(ffmpeg_bin())
        candidate = ff.with_name(ff.name.replace("ffmpeg", "ffprobe"))
        if candidate.exists():
            return str(candidate)
        raise RuntimeError("ffprobe not found (it ships with ffmpeg). Set FFPROBE_PATH in .env")
    return path
