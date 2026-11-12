"""One door to a language model.

If ANTHROPIC_API_KEY is set, the call goes to the Anthropic API. Otherwise it goes to the `claude` command-line
program, if it is installed and signed in on the machine. HOMING_MODEL=off turns the model off (Homing then uses
its saved readings for the sample documents).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess


class ModelUnavailable(RuntimeError):
    pass


def _extract_json(text: str):
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    start = min((i for i in (text.find("{"), text.find("[")) if i >= 0), default=-1)
    if start < 0:
        raise ValueError("the model returned no JSON")
    return json.JSONDecoder().raw_decode(text[start:])[0]


def disabled() -> bool:
    return os.environ.get("HOMING_MODEL", "").strip().lower() in ("off", "0", "none", "false")


def available() -> bool:
    """Whether a model can be tried at all on this machine (it may still fail, for example when signed out)."""
    return not disabled() and bool(os.environ.get("ANTHROPIC_API_KEY") or shutil.which("claude"))


def ask(prompt: str, *, files: list[str] | None = None, model: str = "sonnet", timeout: int = 180) -> str:
    """Plain text answer. `files` are local images or PDFs the model should look at."""
    if disabled():
        raise ModelUnavailable("the model is turned off (HOMING_MODEL=off)")
    if os.environ.get("ANTHROPIC_API_KEY"):
        return _ask_api(prompt, files or [], model, timeout)
    exe = shutil.which("claude")
    if not exe:
        raise ModelUnavailable("no ANTHROPIC_API_KEY is set and the claude program is not installed")
    cmd = [exe, "-p", "--model", model, "--output-format", "json"]
    text = prompt
    if files:
        cmd += ["--allowedTools", "Read"]
        text += "\n\nRead these files before answering:\n" + "\n".join(os.path.abspath(f) for f in files)
    done = subprocess.run(cmd, input=text, capture_output=True, text=True, timeout=timeout, cwd="/tmp")
    if done.returncode != 0:
        raise ModelUnavailable(f"claude exited with {done.returncode}: {done.stderr[:300]}")
    out = json.loads(done.stdout)
    if out.get("is_error"):
        raise ModelUnavailable(str(out.get("result"))[:300])
    return out.get("result", "")


def ask_json(prompt: str, **kw):
    """Answer parsed as JSON. One retry if the first answer does not parse."""
    for attempt in range(2):
        text = ask(prompt + "\n\nAnswer with JSON only, no other text.", **kw)
        try:
            return _extract_json(text)
        except (ValueError, json.JSONDecodeError):
            if attempt:
                raise
    raise ValueError("unreachable")


_API_MODELS = {"haiku": "claude-haiku-5-5", "sonnet": "claude-sonnet-5-5", "opus": "claude-opus-5-5"}


def _ask_api(prompt: str, files: list[str], model: str, timeout: int) -> str:
    import base64
    import mimetypes

    import httpx

    content: list[dict] = []
    for f in files:
        mime = mimetypes.guess_type(f)[0] or "application/octet-stream"
        data = base64.standard_b64encode(open(f, "rb").read()).decode()
        kind = "document" if mime == "application/pdf" else "image"
        content.append({"type": kind, "source": {"type": "base64", "media_type": mime, "data": data}})
    content.append({"type": "text", "text": prompt})
    r = httpx.post("https://api.anthropic.com/v1/messages", timeout=timeout,
                   headers={"x-api-key": os.environ["ANTHROPIC_API_KEY"], "anthropic-version": "2023-06-01"},
                   json={"model": _API_MODELS.get(model, model), "max_tokens": 4000,
                         "messages": [{"role": "user", "content": content}]})
    r.raise_for_status()
    return "".join(b.get("text", "") for b in r.json()["content"])
