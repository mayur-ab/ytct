#!/usr/bin/env python
"""ytct - YouTube video -> 9:16 shorts + transcripts + AI-written (Claude or Groq) titles/descriptions/tags.

Usage:
  python ytct.py run <youtube-url> [options]   process a video without the UI
  python ytct.py serve [--port 8765]           start the web UI
  python ytct.py list                          list processed projects
  python ytct.py show <project-id>             print the shorts + metadata of a project
  python ytct.py regen <project-id> [-n N]     regenerate title/description/tags
"""
from __future__ import annotations

import argparse
import sys

from app import config, pipeline


def _print_progress(fraction: float, stage: str) -> None:
    if not sys.stdout.isatty():  # piped/logged output: log lines only, no progress bar
        return
    width = 28
    filled = int(width * fraction)
    sys.stdout.write(f"\r[{'#' * filled}{'.' * (width - filled)}] {fraction:5.0%}  {stage[:60]:<60}")
    sys.stdout.flush()
    if fraction >= 1:
        sys.stdout.write("\n")


def cmd_run(args) -> int:
    opts = pipeline.Options.from_dict({
        "clip_seconds": args.clip, "mode": args.mode, "transcriber": args.transcriber,
        "max_clips": args.max_clips, "start": args.start, "end": args.end, "width": args.width,
        "metadata": not args.no_meta, "language": args.language, "force": args.force,
        "min_last": args.min_last,
    })

    def log(msg: str) -> None:
        if sys.stdout.isatty():
            sys.stdout.write("\r" + " " * 100 + "\r")
        sys.stdout.write(msg + "\n")
        sys.stdout.flush()

    try:
        manifest = pipeline.process(args.url, opts, log=log, progress=_print_progress)
    except KeyboardInterrupt:
        print("\nCancelled. Re-run the same command to resume where it stopped.")
        return 130
    except Exception as exc:
        print(f"\nERROR: {exc}")
        return 1
    print(f"\nProject id: {manifest['id']}")
    return 0 if manifest.get("status") == "done" else 2


def cmd_serve(args) -> int:
    from app.server import run_server
    run_server(host=args.host, port=args.port, open_browser=not args.no_browser)
    return 0


def cmd_list(_args) -> int:
    projects = pipeline.list_projects()
    if not projects:
        print(f"No projects yet in {config.output_dir()}")
        return 0
    for p in projects:
        print(f"{p['id']}\n    {p['title']}  |  {p['count']} shorts  |  {p['status']}  |  {p['updated']}")
    return 0


def cmd_show(args) -> int:
    project = pipeline.load_project(args.project)
    print(f"{project['title']}\n{project['url']}\n{project['path']}\n")
    for s in project["shorts"]:
        meta = s.get("metadata") or {}
        print(f"#{s['index']:03d}  {pipeline.fmt_time(s['start'])}-{pipeline.fmt_time(s['end'])}  "
              f"score {meta.get('virality_score', '-')}/10")
        print(f"   title: {meta.get('title', '-')}")
        print(f"   tags : {', '.join(meta.get('tags', []))}")
        if s.get("error"):
            print(f"   ERROR: {s['error']}")
    return 0


def cmd_regen(args) -> int:
    project = pipeline.load_project(args.project)
    indexes = [args.n] if args.n else [s["index"] for s in project["shorts"]]
    for i in indexes:
        meta = pipeline.regenerate_metadata(args.project, i, log=print)
        print(f"#{i:03d}: {meta['title']}  (score {meta['virality_score']}/10)")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="ytct", description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="process a YouTube video")
    r.add_argument("url")
    r.add_argument("--clip", type=float, default=30, help="clip length in seconds (default 30)")
    r.add_argument("--mode", choices=["crop", "blur"], default="crop",
                   help="crop = fill 9:16 by center crop, blur = whole frame on blurred background")
    r.add_argument("--transcriber", choices=["auto", "groq", "youtube", "none"], default="auto",
                   help="auto = Groq Whisper if a key is set, else YouTube captions")
    r.add_argument("--max-clips", type=int, default=0, help="stop after N clips (0 = all)")
    r.add_argument("--start", default="0", help="start time in source, e.g. 90 or 1:30")
    r.add_argument("--end", default="0", help="end time in source (0 = end of video)")
    r.add_argument("--min-last", type=float, default=10,
                   help="drop a final leftover piece shorter than this many seconds (default 10)")
    r.add_argument("--width", type=int, default=1080, help="output width; height = width*16/9")
    r.add_argument("--language", default="", help="transcription language hint, e.g. en, hi")
    r.add_argument("--no-meta", action="store_true", help="skip AI title/description/tags")
    r.add_argument("--force", action="store_true", help="redo everything instead of resuming")
    r.set_defaults(func=cmd_run)

    s = sub.add_parser("serve", help="start the web UI")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8765)
    s.add_argument("--no-browser", action="store_true")
    s.set_defaults(func=cmd_serve)

    sub.add_parser("list", help="list projects").set_defaults(func=cmd_list)

    sh = sub.add_parser("show", help="show a project's shorts")
    sh.add_argument("project")
    sh.set_defaults(func=cmd_show)

    g = sub.add_parser("regen", help="regenerate metadata with the configured LLM")
    g.add_argument("project")
    g.add_argument("-n", type=int, default=0, help="only this short number")
    g.set_defaults(func=cmd_regen)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
