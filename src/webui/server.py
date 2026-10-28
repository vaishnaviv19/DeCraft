# src/webui/server.py  -- the DeCraft studio: upload PDFs (or a folder), watch them run, download decoys
"""A small local web app around `scripts/run_decraft.py`, built on the standard library only.

* Files are uploaded one request each (raw body, name in a header), so a whole folder of PDFs can be
  sent from the browser without a multipart parser. Only real PDFs (``%PDF`` header) are accepted.
* One worker thread runs the jobs in order, each as its own `run_decraft.py` process, so a crash
  in one document never takes the server down. Progress is read from the run's `logs.jsonl`.
* Results: the decoy PDF, side-by-side page previews, the gate summary, a zip of the run package,
  and one zip of every finished decoy of an upload batch.

Binds to 127.0.0.1 only: it is a local tool, not a public service.
"""
from __future__ import annotations

import hashlib
import io
import json
import mimetypes
import os
import queue
import re
import subprocess
import sys
import threading
import time
import uuid
import zipfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

ROOT = Path(__file__).resolve().parents[2]
STATIC = Path(__file__).resolve().parent / "static"
MAX_UPLOAD = 60 * 1024 * 1024                    # one PDF

STAGES = [("extract", "Reading the PDF"), ("infer", "Understanding the data"),
          ("privacy_plan", "Planning privacy"), ("generate", "Generating new data"),
          ("privacy_audit", "Privacy audit"), ("style", "Reading the style"),
          ("templates", "Mapping the layout"), ("figures", "Analysing figures"),
          ("prepare", "Preparing tables"), ("charts", "Drawing charts"), ("plan", "Planning sections"),
          ("write", "Writing the text"), ("fit", "Fitting text to boxes"), ("images", "Images"),
          ("render", "Rendering the PDF"), ("qa", "Quality checks"), ("package", "Packaging")]
STAGE_NAMES = [name for name, _ in STAGES]


def _safe_name(name: str) -> str:
    base = Path(name.replace("\\", "/")).name
    base = re.sub(r"[^A-Za-z0-9._ -]+", "_", base).strip(" .") or "document.pdf"
    return base if base.lower().endswith(".pdf") else base + ".pdf"


@dataclass
class Job:
    id: str
    batch: str
    name: str                        # the uploaded file name
    folder: str                      # its folder inside the uploaded folder ('' for single files)
    path: str                        # the stored upload
    sha: str
    llm: bool = False
    fresh: bool = False
    status: str = "queued"           # queued | running | done | failed
    created: float = field(default_factory=time.time)
    started: float | None = None
    finished: float | None = None
    run_dir: str | None = None
    pdf: str | None = None
    message: str = ""
    result: dict | None = None

    def public(self, artifacts: Path) -> dict:
        data = {k: v for k, v in asdict(self).items() if k not in {"path", "run_dir", "pdf", "result"}}
        data["progress"] = self.progress(artifacts)
        data["elapsed"] = round((self.finished or time.time()) - self.started, 1) if self.started else None
        data["has_pdf"] = bool(self.pdf and Path(self.pdf).exists())
        data["pages"] = self.page_count()
        data["summary"] = self.summary()
        return data

    def run_path(self, artifacts: Path) -> Path:
        return Path(self.run_dir) if self.run_dir else artifacts / "runs" / self.sha[:12]

    def progress(self, artifacts: Path) -> dict:
        """Stages finished in THIS job (log lines after it started) and the one running now."""
        if self.status == "queued" or not self.started:
            return {"done": 0, "total": len(STAGES), "current": None, "label": "Waiting in line"}
        log = self.run_path(artifacts) / "logs.jsonl"
        done, current = set(), None
        start = datetime.fromtimestamp(self.started - 2, tz=timezone.utc)
        if log.exists():
            for line in log.read_text(encoding="utf-8", errors="replace").splitlines():
                try:
                    event = json.loads(line)
                    at = datetime.fromisoformat(event.get("at", ""))
                except (ValueError, TypeError):
                    continue
                if at < start:
                    continue
                if event.get("event") == "stage_start":
                    current = event.get("stage")
                elif event.get("event") in ("stage_done", "stage_reused"):
                    done.add(event.get("stage"))
        if self.status in ("done", "failed") and self.pdf:
            done = set(STAGE_NAMES)
        labels = dict(STAGES)
        label = labels.get(current, "Starting") if self.status == "running" else \
            ("Finished" if self.status == "done" else self.message or "Stopped")
        return {"done": len(done & set(STAGE_NAMES)), "total": len(STAGES), "current": current, "label": label}

    def page_count(self) -> int:
        if not self.run_dir:
            return 0
        pages = Path(self.run_dir) / "render" / "pages"
        return len(list(pages.glob("p*.png"))) if pages.exists() else 0

    def summary(self) -> dict | None:
        if not self.result:
            return None
        gates = self.result.get("gates") or {}
        return {"status": self.result.get("status"), "gates_total": len(gates),
                "gates_passed": sum(1 for v in gates.values() if v),
                "failed_gates": [k for k, v in gates.items() if not v]}


class Studio:
    """Jobs, the upload store and the single worker."""

    def __init__(self, workdir: Path):
        self.workdir = Path(workdir)
        self.uploads = self.workdir / "uploads"
        self.artifacts = self.workdir / "artifacts"
        self.uploads.mkdir(parents=True, exist_ok=True)
        self.artifacts.mkdir(parents=True, exist_ok=True)
        self.jobs: dict[str, Job] = {}
        self.lock = threading.Lock()
        self.queue: queue.Queue[str] = queue.Queue()
        self._load()
        threading.Thread(target=self._worker, daemon=True).start()

    # ---------------------------------------------------------------- persistence
    def _index(self) -> Path:
        return self.workdir / "jobs.json"

    def _save(self) -> None:
        with self.lock:
            data = [asdict(job) for job in self.jobs.values()]
        tmp = self._index().with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
        tmp.replace(self._index())

    def _load(self) -> None:
        if not self._index().exists():
            return
        try:
            for item in json.loads(self._index().read_text(encoding="utf-8")):
                job = Job(**item)
                if job.status in ("queued", "running"):        # the server stopped: run it again
                    job.status, job.started, job.message = "queued", None, ""
                    self.queue.put(job.id)
                self.jobs[job.id] = job
        except (OSError, ValueError, TypeError):
            pass

    # ---------------------------------------------------------------- jobs
    def add(self, batch: str, name: str, folder: str, data: bytes, llm: bool, fresh: bool) -> Job:
        if not data.startswith(b"%PDF"):
            raise ValueError(f"{name} is not a PDF")
        job_id = uuid.uuid4().hex[:10]
        target = self.uploads / batch / job_id / _safe_name(name)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        job = Job(id=job_id, batch=batch, name=_safe_name(name), folder=folder, path=str(target),
                  sha=hashlib.sha256(data).hexdigest(), llm=llm, fresh=fresh)
        with self.lock:
            self.jobs[job_id] = job
        self._save()
        self.queue.put(job_id)
        return job

    def _worker(self) -> None:
        while True:
            job_id = self.queue.get()
            job = self.jobs.get(job_id)
            if job is None or job.status != "queued":
                continue
            try:
                self._run(job)
            except Exception as error:                     # never let the worker die
                job.status, job.message = "failed", f"{type(error).__name__}: {error}"
                job.finished = time.time()
            self._save()

    def _run(self, job: Job) -> None:
        job.status, job.started, job.message = "running", time.time(), ""
        self._save()
        command = [sys.executable, str(ROOT / "scripts" / "run_decraft.py"), job.path,
                   "--artifacts", str(self.artifacts), "--llm" if job.llm else "--no-llm"]
        if job.fresh:
            command.append("--fresh")
        env = dict(os.environ, MPLBACKEND="Agg", PYTHONIOENCODING="utf-8")
        log_path = Path(job.path).with_suffix(".log")
        with log_path.open("w", encoding="utf-8") as log:
            code = subprocess.call(command, cwd=str(ROOT), stdout=log, stderr=subprocess.STDOUT, env=env)
        output = log_path.read_text(encoding="utf-8", errors="replace")
        match = re.search(r"\[run\] directory: (.+)", output)
        job.run_dir = str((ROOT / match.group(1).strip()).resolve()) if match else str(job.run_path(self.artifacts))
        result_file = Path(job.run_dir) / "result.json"
        job.result = json.loads(result_file.read_text(encoding="utf-8")) if result_file.exists() else None
        final = Path(job.run_dir) / "report_output.pdf"
        job.pdf = str(final) if final.exists() else None
        job.finished = time.time()
        if job.pdf:
            job.status = "done"
            failed = (job.summary() or {}).get("failed_gates") or []
            job.message = "All quality gates passed" if not failed else f"{len(failed)} quality gate(s) need review"
        else:
            job.status = "failed"
            errors = [line for line in output.splitlines() if "Error" in line or "error" in line]
            job.message = (errors[-1] if errors else f"the run stopped (exit {code})")[:300]

    def public(self, batch: str | None = None) -> list[dict]:
        with self.lock:
            jobs = [j for j in self.jobs.values() if batch is None or j.batch == batch]
        return [j.public(self.artifacts) for j in sorted(jobs, key=lambda j: j.created)]

    def batch_zip(self, batch: str) -> bytes:
        """Every finished decoy of a batch, in the uploaded folder structure, plus a summary."""
        buffer = io.BytesIO()
        summary = []
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            for job in self.jobs.values():
                if job.batch != batch:
                    continue
                entry = {"file": job.name, "folder": job.folder, "status": job.status, "message": job.message}
                if job.pdf and Path(job.pdf).exists():
                    arcname = "/".join(p for p in (job.folder, decoy_name(job.name)) if p)
                    archive.write(job.pdf, arcname)
                    entry["decoy"] = arcname
                    entry["quality"] = job.summary()
                summary.append(entry)
            archive.writestr("decraft_summary.json", json.dumps(summary, indent=2))
        return buffer.getvalue()


def decoy_name(name: str) -> str:
    return re.sub(r"\.pdf$", "", name, flags=re.I) + "_decoy.pdf"


def keys_available() -> dict:
    return {"text": bool(os.environ.get("GROQ_API_KEY")),
            "vision": bool(os.environ.get("CF_ACCOUNT_ID") and os.environ.get("CF_API_TOKEN"))}


# ---------------------------------------------------------------- HTTP
def make_handler(studio: Studio):
    class Handler(BaseHTTPRequestHandler):
        server_version = "DeCraftStudio/1.0"

        def log_message(self, fmt, *args):            # quiet console
            pass

        # -- helpers
        def _send(self, status: int, body: bytes, kind: str, headers: dict | None = None) -> None:
            self.send_response(status)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def _json(self, data, status: int = 200) -> None:
            self._send(status, json.dumps(data).encode("utf-8"), "application/json")

        def _file(self, path: Path, download_name: str | None = None) -> None:
            if not path.exists() or not path.is_file():
                return self._json({"error": "not found"}, 404)
            kind = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
            headers = {"Content-Disposition": f'attachment; filename="{download_name}"'} if download_name else {}
            self._send(200, path.read_bytes(), kind, headers)

        # -- routes
        def do_GET(self):
            url = urlparse(self.path)
            parts = [unquote(p) for p in url.path.strip("/").split("/") if p]
            query = parse_qs(url.query)
            if not parts:
                return self._file(STATIC / "index.html")
            if parts[0] == "static" and len(parts) == 2:
                return self._file(STATIC / Path(parts[1]).name)
            if parts == ["api", "info"]:
                return self._json({"keys": keys_available(), "stages": [label for _, label in STAGES]})
            if parts == ["api", "jobs"]:
                return self._json(studio.public(query.get("batch", [None])[0]))
            if len(parts) >= 3 and parts[:2] == ["api", "jobs"]:
                job = studio.jobs.get(parts[2])
                if job is None:
                    return self._json({"error": "unknown job"}, 404)
                if len(parts) == 4 and parts[3] == "pdf" and job.pdf:
                    return self._file(Path(job.pdf), decoy_name(job.name))
                if len(parts) == 4 and parts[3] == "package" and job.run_dir:
                    return self._file(Path(job.run_dir) / "submission.zip",
                                      re.sub(r"\.pdf$", "", job.name, flags=re.I) + "_package.zip")
                if len(parts) == 4 and parts[3] == "log":
                    log = Path(job.path).with_suffix(".log")
                    return self._send(200, log.read_bytes() if log.exists() else b"", "text/plain; charset=utf-8")
                if len(parts) == 5 and parts[3] == "page" and job.run_dir and parts[4].isdigit():
                    side = query.get("side", ["decoy"])[0]
                    name = f"_source_p{parts[4]}.png" if side == "source" else f"p{parts[4]}.png"
                    return self._file(Path(job.run_dir) / "render" / "pages" / name)
            if len(parts) == 4 and parts[:2] == ["api", "batches"] and parts[3] == "zip":
                return self._send(200, studio.batch_zip(parts[2]), "application/zip",
                                  {"Content-Disposition": 'attachment; filename="decraft_decoys.zip"'})
            return self._json({"error": "not found"}, 404)

        def do_POST(self):
            url = urlparse(self.path)
            if url.path != "/api/upload":
                return self._json({"error": "not found"}, 404)
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0 or length > MAX_UPLOAD:
                return self._json({"error": "a PDF of up to 60 MB is expected"}, 413)
            data = self.rfile.read(length)
            name = unquote(self.headers.get("X-Filename", "document.pdf"))
            relative = unquote(self.headers.get("X-Relative-Path", "")).replace("\\", "/")
            folder = "/".join(re.sub(r"[^A-Za-z0-9._ -]+", "_", p)       # the file's folder in the upload
                              for p in relative.split("/")[:-1] if p not in ("", ".", ".."))
            batch = re.sub(r"[^A-Za-z0-9]", "", self.headers.get("X-Batch", "")) or uuid.uuid4().hex[:8]
            llm = self.headers.get("X-Use-Models") == "1"
            fresh = self.headers.get("X-Fresh") == "1"
            try:
                job = studio.add(batch, name, folder, data, llm, fresh)
            except ValueError as error:
                return self._json({"error": str(error)}, 400)
            return self._json(job.public(studio.artifacts), 201)

    return Handler


def serve(host: str = "127.0.0.1", port: int = 8765, workdir: Path | None = None) -> ThreadingHTTPServer:
    studio = Studio(workdir or ROOT / "artifacts_ui")
    server = ThreadingHTTPServer((host, port), make_handler(studio))
    server.studio = studio
    return server
