"""Local web UI: Flask server + a single background worker that runs jobs one at a time."""
from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
import time
import uuid
import webbrowser
from dataclasses import dataclass, field

from flask import Flask, abort, jsonify, request, send_from_directory

from . import config, pipeline

STATIC = config.ROOT / "app" / "static"
app = Flask(__name__, static_folder=str(STATIC), static_url_path="/static")


@dataclass
class Job:
    id: str
    url: str
    options: dict
    status: str = "queued"          # queued | running | done | error | cancelled
    progress: float = 0.0
    stage: str = "Waiting in queue"
    logs: list = field(default_factory=list)
    project_id: str = ""
    title: str = ""
    error: str = ""
    created: float = field(default_factory=time.time)
    cancel: bool = False

    def public(self, with_logs: bool = True) -> dict:
        data = {k: v for k, v in self.__dict__.items() if k != "cancel"}
        if not with_logs:
            data["logs"] = self.logs[-1:]
        return data


class JobManager:
    def __init__(self):
        self.jobs: dict[str, Job] = {}
        self.queue: "queue.Queue[Job]" = queue.Queue()
        threading.Thread(target=self._worker, daemon=True).start()

    def submit(self, url: str, options: dict) -> Job:
        job = Job(id=uuid.uuid4().hex[:8], url=url, options=options)
        self.jobs[job.id] = job
        self.queue.put(job)
        return job

    def _worker(self):
        while True:
            job = self.queue.get()
            if job.cancel:
                job.status, job.stage = "cancelled", "Cancelled"
                continue
            job.status = "running"

            def log(msg: str, job=job):
                job.logs.append(f"{time.strftime('%H:%M:%S')}  {msg}")
                del job.logs[:-400]
                if msg.startswith("Video: "):
                    job.title = msg[7:]
                if msg.startswith("Project folder: "):
                    job.project_id = os.path.basename(msg[16:].strip())

            def progress(frac: float, stage: str, job=job):
                job.progress, job.stage = round(frac, 4), stage

            try:
                opts = pipeline.Options.from_dict(job.options)
                manifest = pipeline.process(job.url, opts, log=log, progress=progress,
                                            check_stop=lambda job=job: job.cancel)
                job.project_id = manifest["id"]
                job.status = "done"
                job.stage = "Done" if manifest.get("status") == "done" else "Done (some clips had errors)"
            except pipeline.Cancelled:
                job.status, job.stage = "cancelled", "Cancelled"
                log("Cancelled by user")
            except Exception as exc:
                job.status, job.error, job.stage = "error", str(exc), "Failed"
                log(f"ERROR: {exc}")


jobs = JobManager()


# --------------------------------------------------------------------------- routes

@app.get("/")
def index():
    return send_from_directory(STATIC, "index.html")


@app.get("/api/status")
def status():
    try:
        config.ffmpeg_bin()
        ffmpeg_ok = True
    except RuntimeError:
        ffmpeg_ok = False
    gkey, akey = config.groq_key(), config.anthropic_key()
    return jsonify({
        "groq_key_set": bool(gkey),
        "groq_key_hint": f"...{gkey[-4:]}" if gkey else "",
        "anthropic_key_set": bool(akey),
        "anthropic_key_hint": f"...{akey[-4:]}" if akey else "",
        "provider": config.llm_provider(),               # "" when no LLM key is set
        "provider_setting": os.getenv("LLM_PROVIDER", "auto") or "auto",
        "model": config.llm_model(),
        "groq_model": config.groq_model(),
        "anthropic_model": config.anthropic_model(),
        "whisper_model": config.whisper_model(),
        "ffmpeg": ffmpeg_ok,
        "output_dir": str(config.output_dir()),
    })


@app.post("/api/settings")
def settings():
    data = request.get_json(force=True) or {}
    mapping = {
        "groq_api_key": "GROQ_API_KEY", "groq_model": "GROQ_MODEL", "whisper_model": "GROQ_WHISPER_MODEL",
        "anthropic_api_key": "ANTHROPIC_API_KEY", "anthropic_model": "ANTHROPIC_MODEL",
    }
    for field_name, env_name in mapping.items():
        value = data.get(field_name)
        if isinstance(value, str) and value.strip():
            config.save_setting(env_name, value.strip())
    provider = str(data.get("provider", "")).strip().lower()
    if provider in config.PROVIDERS:
        config.save_setting("LLM_PROVIDER", provider)
    return status()


@app.post("/api/jobs")
def create_job():
    data = request.get_json(force=True) or {}
    url = str(data.get("url", "")).strip()
    if not url.startswith(("http://", "https://")):
        return jsonify({"error": "Please enter a valid YouTube URL"}), 400
    options = data.get("options") or {}
    try:
        pipeline.Options.from_dict(options)
    except (ValueError, TypeError) as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify(jobs.submit(url, options).public())


@app.get("/api/jobs")
def list_jobs():
    items = sorted(jobs.jobs.values(), key=lambda j: j.created, reverse=True)
    return jsonify([j.public(with_logs=False) for j in items])


@app.get("/api/jobs/<job_id>")
def get_job(job_id):
    job = jobs.jobs.get(job_id) or abort(404)
    return jsonify(job.public())


@app.post("/api/jobs/<job_id>/cancel")
def cancel_job(job_id):
    job = jobs.jobs.get(job_id) or abort(404)
    job.cancel = True
    if job.status == "queued":
        job.status, job.stage = "cancelled", "Cancelled"
    return jsonify(job.public())


@app.get("/api/projects")
def projects():
    return jsonify(pipeline.list_projects())


@app.get("/api/projects/<project_id>")
def project(project_id):
    try:
        return jsonify(pipeline.load_project(project_id))
    except FileNotFoundError:
        abort(404)


@app.post("/api/projects/<project_id>/shorts/<int:index>/regenerate")
def regenerate(project_id, index):
    try:
        return jsonify(pipeline.regenerate_metadata(project_id, index, log=lambda *_: None))
    except FileNotFoundError:
        abort(404)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.post("/api/projects/<project_id>/open")
def open_folder(project_id):
    """Open the project folder in the OS file manager (local use only)."""
    try:
        path = pipeline.project_path(project_id)
    except FileNotFoundError:
        abort(404)
    sub = request.args.get("sub", "")
    if sub:
        candidate = (path / sub).resolve()
        if path.resolve() in candidate.parents and candidate.exists():
            path = candidate
    if sys.platform == "win32":
        os.startfile(path)  # noqa: S606 - local desktop app
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])
    return jsonify({"ok": True})


@app.get("/files/<project_id>/<path:filename>")
def files(project_id, filename):
    try:
        path = pipeline.project_path(project_id)
    except FileNotFoundError:
        abort(404)
    return send_from_directory(path, filename, conditional=True)


def run_server(host: str = "127.0.0.1", port: int = 8765, open_browser: bool = True) -> None:
    url = f"http://{'localhost' if host in ('127.0.0.1', '0.0.0.0') else host}:{port}"
    print(f"ytct UI running at {url}  (Ctrl+C to stop)")
    print(f"Projects are saved in {config.output_dir()}")
    if config.llm_provider():
        print(f"Metadata LLM: {config.llm_provider()} ({config.llm_model()})")
    else:
        print("Note: no ANTHROPIC_API_KEY or GROQ_API_KEY set - add one in the UI (Settings) or in .env")
    if open_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    app.run(host=host, port=port, debug=False, threaded=True)
