"""Keep one model interpreter alive and exchange one JSON reply per request."""

import json
import os
import queue
import subprocess
import sys
import threading
from pathlib import Path

from wrs_agent.executor import SkillFailure

WORKERS = Path(__file__).with_name("workers")


def vision_python():
    chosen = os.environ.get("WRS_VISION_PYTHON", "").strip()
    return chosen or sys.executable


class ModelWorker:
    def __init__(self, script, name):
        self.script = WORKERS / script
        self.name = name
        self.python = vision_python()
        self.proc = None
        self._lines = queue.Queue()
        self._lock = threading.Lock()

    def start(self):
        if not self.script.is_file():
            raise RuntimeError("vision_worker_missing")
        self.proc = subprocess.Popen(
            [self.python, "-u", str(self.script)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            cwd=str(self.script.parent),
        )
        threading.Thread(target=self._read_stdout, name=f"{self.name}-out", daemon=True).start()
        threading.Thread(target=self._drain_stderr, name=f"{self.name}-err", daemon=True).start()
        try:
            ready = self._next(30)
        except Exception:
            self.close()
            raise
        if not isinstance(ready, dict) or not ready.get("ready"):
            self.close()
            raise RuntimeError("vision_worker_not_ready")

    def request(self, payload, timeout=110):
        with self._lock:
            if self.proc is None or self.proc.poll() is not None:
                raise SkillFailure("vision_worker_exited")
            text = json.dumps(payload, ensure_ascii=False)
            self.proc.stdin.write(text + "\n")
            self.proc.stdin.flush()
            reply = self._next(timeout)
        if not isinstance(reply, dict):
            raise SkillFailure("vision_protocol")
        return reply

    def close(self):
        proc = self.proc
        self.proc = None
        if proc is None or proc.poll() is not None:
            return
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)

    def _next(self, timeout):
        try:
            line = self._lines.get(timeout=timeout)
        except queue.Empty:
            raise SkillFailure("vision_timeout") from None
        if line is None:
            raise SkillFailure("vision_worker_exited")
        try:
            return json.loads(line)
        except json.JSONDecodeError:
            raise SkillFailure("vision_protocol") from None

    def _read_stdout(self):
        try:
            for line in self.proc.stdout:
                self._lines.put(line)
        finally:
            self._lines.put(None)

    def _drain_stderr(self):
        for line in self.proc.stderr:
            text = line.rstrip()
            if text:
                print(f"[{self.name}] {text}", flush=True)
