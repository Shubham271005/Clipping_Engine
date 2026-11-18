"""Job manager: runs engine CLI commands as subprocesses with live progress, logs, pause/resume/cancel.

Heavy jobs (process / rerender / render_candidate) run one at a time (GPU + disk bound).
Light jobs (publish, schedule, analytics, test uploads) start immediately.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

import psutil

from src import db
from src.common import DIRS, ROOT, get_logger

log = get_logger("jobs")
HEAVY = {"process", "rerender", "render_candidate"}
PY = sys.executable
JOB_LOGS = DIRS["logs"] / "jobs"
JOB_LOGS.mkdir(exist_ok=True)

# (regex, stage label, progress function(match) -> percent)
STAGES = [
    (re.compile(r"Downloading "), "Downloading", lambda m: 3),
    (re.compile(r"(Ingested|Source already ingested)"), "Ingested", lambda m: 8),
    (re.compile(r"Whisper .* on (CUDA|CPU)"), "Loading speech model", lambda m: 10),
    (re.compile(r"Transcribing chunk (\d+)/(\d+)"), "Transcribing",
     lambda m: 10 + 30 * (int(m[1]) - 1) / int(m[2])),
    (re.compile(r"Transcript: "), "Transcribed", lambda m: 40),
    (re.compile(r"Analysing audio"), "Analysing audio", lambda m: 42),
    (re.compile(r"Analysing visual"), "Analysing visuals", lambda m: 46),
    (re.compile(r"Detecting facecam"), "Detecting facecam", lambda m: 52),
    (re.compile(r"Found (\d+) raw candidates"), "Candidates found", lambda m: 56),
    (re.compile(r"AI reviewing candidates (\d+)-\d+ of (\d+)"), "AI review",
     lambda m: 58 + 17 * (int(m[1]) - 1) / max(1, int(m[2]))),
    (re.compile(r"Review: "), "Reviewed", lambda m: 75),
    (re.compile(r"--- Clip (\d+)/(\d+)"), "Rendering clips",
     lambda m: 75 + 23 * (int(m[1]) - 1) / max(1, int(m[2]))),
    (re.compile(r"Rendered "), "Rendered", lambda m: 92),
    (re.compile(r"(Re-rendered|Manual clip)"), "Finishing", lambda m: 98),
    (re.compile(r"Scheduled "), "Scheduling", lambda m: 99),
]
SRC_RE = re.compile(r"\b(src_[0-9a-f]{10})\b")


def _cmd(kind: str, args: dict) -> list[str]:
    main = str(ROOT / "main.py")
    if kind == "process":
        c = [PY, main, "--profile", args["profile"], "--url", args["url"]]
        for k in ("streamer", "game", "rights", "auth_ref", "authorized_on", "restrictions"):
            if args.get(k):
                c += ["--" + k.replace("_", "-"), str(args[k])]
        for k in ("max_clips", "min_score", "force_top"):
            if args.get(k) not in (None, "", 0):
                c += ["--" + k.replace("_", "-"), str(args[k])]
        if args.get("commercial"):
            c.append("--commercial")
        if args.get("no_llm"):
            c.append("--no-llm")
        return c
    if kind == "rerender":
        c = [PY, main, "rerender", args["clip_id"]]
        for k in ("start", "end"):
            if args.get(k) is not None:
                c += [f"--{k}", str(args[k])]
        if args.get("hook") is not None:
            c += ["--hook", args["hook"]]
        if args.get("corrections") is not None:
            c += ["--corrections", json.dumps(args["corrections"], ensure_ascii=False)]
        return c
    if kind == "render_candidate":
        return [PY, main, "render-candidate", str(args["candidate_id"])]
    if kind == "publish":
        return [PY, main, "publish-due"]
    if kind == "analytics":
        return [PY, main, "analytics", "fetch"]
    if kind == "test_upload":
        return [PY, main, "test-upload", args["platform"], "--clip", args["clip_id"]]
    raise ValueError(f"unknown job kind {kind}")


class JobManager:
    def __init__(self):
        self.procs: dict[int, subprocess.Popen] = {}
        self.offsets: dict[int, int] = {}
        self.lock = threading.Lock()
        self._recover()
        threading.Thread(target=self._loop, daemon=True, name="job-loop").start()

    # ---------------------------------------------------------------- public API
    def submit(self, kind: str, args: dict, label: str) -> int:
        _cmd(kind, args)  # validate early
        jid = db.execute("INSERT INTO jobs (kind,label,args_json,status,progress,stage,created_at) "
                         "VALUES (?,?,?,?,?,?,?)", (kind, label, json.dumps(args), "queued", 0, "Queued", db.now_iso()))
        return jid

    def pause(self, jid: int) -> None:
        self._signal(jid, "suspend", "paused", expect="running")

    def resume(self, jid: int) -> None:
        self._signal(jid, "resume", "running", expect="paused")

    def cancel(self, jid: int) -> None:
        j = db.one("SELECT * FROM jobs WHERE id=?", (jid,))
        if not j:
            raise ValueError("unknown job")
        if j["status"] == "queued":
            db.execute("UPDATE jobs SET status='cancelled', finished_at=? WHERE id=?", (db.now_iso(), jid))
            return
        if j["status"] not in ("running", "paused"):
            raise ValueError(f"job is {j['status']}")
        for p in self._tree(j["pid"]):
            try:
                p.kill()
            except psutil.Error:
                pass
        db.execute("UPDATE jobs SET status='cancelled', stage='Cancelled', finished_at=? WHERE id=?", (db.now_iso(), jid))

    def read_log(self, jid: int, offset: int = 0, max_bytes: int = 200_000) -> dict:
        j = db.one("SELECT log_path FROM jobs WHERE id=?", (jid,))
        if not j or not j["log_path"] or not Path(j["log_path"]).exists():
            return {"offset": 0, "text": ""}
        with open(j["log_path"], "rb") as f:
            f.seek(offset)
            data = f.read(max_bytes)
        return {"offset": offset + len(data), "text": data.decode("utf-8", "replace")}

    # ---------------------------------------------------------------- internals
    def _tree(self, pid):
        try:
            p = psutil.Process(pid)
            return p.children(recursive=True) + [p]
        except (psutil.Error, TypeError):
            return []

    def _signal(self, jid, action, new_status, expect):
        j = db.one("SELECT * FROM jobs WHERE id=?", (jid,))
        if not j or j["status"] != expect:
            raise ValueError(f"job must be {expect} (is {j and j['status']})")
        procs = self._tree(j["pid"])
        if not procs:
            raise ValueError("process not found")
        for p in procs:
            try:
                getattr(p, action)()
            except psutil.Error:
                pass
        db.execute("UPDATE jobs SET status=?, stage=? WHERE id=?",
                   (new_status, "Paused" if action == "suspend" else "Resumed", jid))

    def _recover(self):
        for j in db.rows("SELECT * FROM jobs WHERE status IN ('running','paused')"):
            alive = j["pid"] and psutil.pid_exists(j["pid"])
            if not alive:
                db.execute("UPDATE jobs SET status='interrupted', finished_at=? WHERE id=?", (db.now_iso(), j["id"]))

    def _start(self, j: dict):
        args = json.loads(j["args_json"])
        logp = JOB_LOGS / f"job_{j['id']}.log"
        env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1"}
        f = open(logp, "ab")
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        p = subprocess.Popen(_cmd(j["kind"], args), cwd=str(ROOT), stdout=f, stderr=subprocess.STDOUT,
                             env=env, creationflags=flags)
        f.close()
        self.procs[j["id"]] = p
        self.offsets[j["id"]] = 0
        db.execute("UPDATE jobs SET status='running', pid=?, log_path=?, started_at=?, stage='Starting', progress=1 "
                   "WHERE id=?", (p.pid, str(logp), db.now_iso(), j["id"]))
        log.info("Job %d started: %s", j["id"], j["label"])

    def _update_progress(self, j: dict):
        out = self.read_log(j["id"], self.offsets.get(j["id"], 0))
        if not out["text"]:
            return
        self.offsets[j["id"]] = out["offset"]
        prog, stage, sid = j["progress"] or 0, j["stage"], j["source_id"]
        for line in out["text"].splitlines():
            for rx, label, fn in STAGES:
                m = rx.search(line)
                if m:
                    prog, stage = max(prog, fn(m)), label
            if not sid:
                m = SRC_RE.search(line)
                if m:
                    sid = m[1]
        db.execute("UPDATE jobs SET progress=?, stage=?, source_id=? WHERE id=?", (round(prog, 1), stage, sid, j["id"]))

    def _finish(self, j: dict, code: int | None):
        text = Path(j["log_path"]).read_text(encoding="utf-8", errors="replace") if j["log_path"] else ""
        result = None
        m = re.search(r"^\{\n.*?^\}", text, re.S | re.M)
        if m:
            try:
                result = json.loads(m.group(0))
            except json.JSONDecodeError:
                pass
        failed = (code not in (0, None)) or "Traceback (most recent call last)" in text
        cur = db.one("SELECT status FROM jobs WHERE id=?", (j["id"],))["status"]
        if cur == "cancelled":
            status = "cancelled"
        else:
            status = "failed" if failed else "done"
        err = None
        if failed:
            lines = [l for l in text.strip().splitlines() if l.strip()]
            err = lines[-1][:300] if lines else "failed"
        db.execute("UPDATE jobs SET status=?, progress=?, stage=?, finished_at=?, result_json=? WHERE id=?",
                   (status, 100 if status == "done" else j["progress"],
                    {"done": "Completed", "failed": err or "Failed"}.get(status, "Cancelled"),
                    db.now_iso(), json.dumps(result) if result else None, j["id"]))
        self.procs.pop(j["id"], None)
        log.info("Job %d %s", j["id"], status)

    def _loop(self):
        while True:
            try:
                with self.lock:
                    self._tick()
            except Exception as e:  # never let the loop die
                log.error("job loop error: %s", e)
            time.sleep(1)

    def _tick(self):
        active = db.rows("SELECT * FROM jobs WHERE status IN ('running','paused')")
        for j in active:
            p = self.procs.get(j["id"])
            self._update_progress(j)
            if p is not None:
                code = p.poll()
                if code is not None:
                    self._finish(db.one("SELECT * FROM jobs WHERE id=?", (j["id"],)), code)
            elif not (j["pid"] and psutil.pid_exists(j["pid"])):
                self._finish(db.one("SELECT * FROM jobs WHERE id=?", (j["id"],)), None)
        heavy_busy = any(j["kind"] in HEAVY for j in db.rows("SELECT kind FROM jobs WHERE status IN ('running','paused')"))
        for j in db.rows("SELECT * FROM jobs WHERE status='queued' ORDER BY id"):
            if j["kind"] in HEAVY:
                if heavy_busy:
                    continue
                heavy_busy = True
            self._start(j)
