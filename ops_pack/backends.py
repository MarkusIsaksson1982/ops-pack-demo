"""Pluggable model backends. Each returns a callable prompt -> text. No keys are read here: every CLI uses
its own login, and Ollama is a local HTTP server. Configure via environment variables, never hard-coded paths."""
from __future__ import annotations

import json
import os
import shlex
import subprocess
import tempfile
import urllib.request


def _cli(cmd: list[str], timeout: int, cwd: str | None = None) -> str:
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL, cwd=cwd)
    if p.returncode != 0 and not p.stdout.strip():
        raise RuntimeError((p.stderr or "")[-300:])
    return p.stdout


def opencode(model: str, timeout: int = 180):
    """OpenCode free models, e.g. opencode/big-pickle. OPS_OPENCODE_CMD may wrap the call in a sandbox,
    e.g. OPS_OPENCODE_CMD="./sbx.sh /tmp/work opencode" (the default is plain `opencode`)."""
    base = shlex.split(os.environ.get("OPS_OPENCODE_CMD", "opencode"))
    def run(prompt: str) -> str:
        with tempfile.TemporaryDirectory() as d:
            cmd = [c.replace("{workdir}", d) for c in base]
            return _cli(cmd + ["run", "-m", model, prompt], timeout, cwd=d)
    return run


def ollama(model: str, timeout: int = 300):
    host = os.environ.get("OLLAMA_HOST", "127.0.0.1:11434")
    url = f"http://{host}/api/chat" if not host.startswith("http") else f"{host}/api/chat"
    def run(prompt: str) -> str:
        body = json.dumps({"model": model, "stream": False, "format": "json", "options": {"temperature": 0},
                           "messages": [{"role": "user", "content": prompt}]}).encode()
        req = urllib.request.Request(url, body, {"content-type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.load(r)["message"]["content"]
    return run


def codex(model: str | None = None, timeout: int = 240):
    """Codex CLI (`codex exec`), low reasoning effort; uses the CLI's own login."""
    def run(prompt: str) -> str:
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "last.txt")
            cmd = ["codex", "exec", "--skip-git-repo-check", "-c", "model_reasoning_effort=low", "-o", out]
            if model:
                cmd += ["-m", model]
            _cli(cmd + [prompt], timeout, cwd=d)
            return open(out).read() if os.path.exists(out) else ""
    return run


def grok_build(model: str | None = None, timeout: int = 240):
    """Grok Build headless (`grok -p`), web search off, few turns; uses the CLI's own login."""
    def run(prompt: str) -> str:
        with tempfile.TemporaryDirectory() as d:
            raw = _cli(["grok", "-p", prompt, "--output-format", "json", "--cwd", d, "--no-auto-update",
                        "--disable-web-search", "--max-turns", "2"], timeout, cwd=d)
            try:
                j = json.loads(raw)
            except json.JSONDecodeError:
                return raw
            return j.get("result") or j.get("text") or ""
    return run


def fake(answers: dict | None = None):
    """Deterministic backend for tests: keyword heuristic, or a fixed answer map {substring: json}."""
    def run(prompt: str) -> str:
        text = prompt.split("REQUEST:", 1)[-1].lower()
        for k, v in (answers or {}).items():
            if k.lower() in text:
                return v
        team = "finance" if any(w in text for w in ("pay", "cost", "money", "bill", "charge")) else \
               "legal" if any(w in text for w in ("sign", "law", "clause", "liab", "consent")) else "bizops"
        return json.dumps({"team": team, "confidence": 0.7, "reason": "fake heuristic"})
    return run


def get(spec: str):
    """spec: fake | opencode:<model> | ollama:<model> | codex[:<model>] | grok-build[:<model>]"""
    kind, _, model = spec.partition(":")
    if kind == "fake":
        return fake()
    if kind == "opencode":
        return opencode(model if "/" in model else f"opencode/{model}")
    if kind == "ollama":
        return ollama(model)
    if kind == "codex":
        return codex(model or None)
    if kind in ("grok-build", "grok"):
        return grok_build(model or None)
    raise ValueError(f"unknown backend {spec!r}")
