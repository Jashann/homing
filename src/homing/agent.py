"""The desk agent: a small tool-using loop on the business's desk.

Each turn the model answers with one JSON object, either
    {"say": "one plain sentence on what it is doing and why", "tool": "<name>", "args": {...}}
or
    {"done": true, "summary": "..."}.
Code runs the tool, records the step and its result, and asks again, for at most `max_steps` tool calls. The model
chooses what to look at and what to ask for; the tools are plain code and decide what is allowed. A tool that refuses
raises Refused, and the refusal goes back to the model as the result ("Refused by code: ...").
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Callable

from . import llm

MAX_STEPS = 8


class Refused(Exception):
    """A tool refused the call. The message is a plain sentence that says why."""


class Tool:
    def __init__(self, name: str, args: str, about: str, fn: Callable[..., tuple[str, dict]]):
        self.name, self.args, self.about, self.fn = name, args, about, fn


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


RULES = """You are the desk agent of {business}, a business that must return uncollected seller payouts to their \
owners through PayPal before the money goes to the state as unclaimed property. You work one task at a time with \
the tools below. Plain code checks every tool call; if code refuses a call you get "Refused by code: ..." back. Never \
try to get around a refusal. A name alone is never enough to pay anyone. Only pay a claim whose checks approved it \
or that a person approved with a written reason.
Homing's fee is 10% of the payout, capped at $25, by design. It is Homing's fee to the business, not a PayPal fee, \
and is not a problem to report.

Tools (args in brackets):
{tools}

Reply with ONE JSON object and nothing else. Either
{{"say": "<one plain sentence: what you are doing and why>", "tool": "<tool name>", "args": {{...}}}}
or, when the task is finished,
{{"done": true, "summary": "<one or two plain sentences on what you did>"}}
Use at most {max_steps} tool calls. Do not repeat a call whose result you already have."""


def _prompt(business: str, tools: dict[str, Tool], task: str, steps: list[dict], max_steps: int) -> str:
    listing = "\n".join(f"- {t.name}({t.args}): {t.about}" for t in tools.values())
    out = [RULES.format(business=business, tools=listing, max_steps=max_steps), "", f"TASK: {task}", ""]
    if steps:
        out.append("What you have done so far:")
        for s in steps:
            if s.get("tool"):
                out.append(f"{s['n']}. {s['tool']}({json.dumps(s.get('args') or {}, ensure_ascii=False)}) -> "
                           f"{s['result']}")
        out.append("")
    left = max_steps - sum(1 for s in steps if s.get("tool"))
    out.append(f"Tool calls left: {left}. Your next JSON object:")
    return "\n".join(out)


def parse_reply(text: str, tools: dict[str, Tool]) -> dict:
    """The model's reply as {"done", "summary"} or {"say", "tool", "args"}. Raises ValueError with a plain reason."""
    try:
        obj = llm._extract_json(text or "")
    except (ValueError, json.JSONDecodeError):
        raise ValueError("the reply was not a JSON object")
    if isinstance(obj, list):
        obj = obj[0] if obj and isinstance(obj[0], dict) else None
    if not isinstance(obj, dict):
        raise ValueError("the reply was not a JSON object")
    if obj.get("done") is True:
        return {"done": True, "summary": str(obj.get("summary") or "").strip()}
    tool = obj.get("tool")
    if tool not in tools:
        raise ValueError(f"unknown tool {tool!r}")
    args = obj.get("args") or {}
    if not isinstance(args, dict):
        raise ValueError("args must be an object")
    say = " ".join(str(obj.get("say") or "").split())
    return {"done": False, "say": say, "tool": tool, "args": args}


def run_task(task: str, tools: dict[str, Tool], *, ask: Callable[[str], str], business: str = "the business",
             max_steps: int = MAX_STEPS, record: dict | None = None,
             on_step: Callable[[], None] | None = None) -> dict:
    """Works one task. `record` (a dict, filled in place so a poller can read it while the task runs) gets
    "steps", "status" ("running", "done", "stopped", "error") and "summary"."""
    rec = record if record is not None else {}
    rec.setdefault("steps", [])
    rec.update(status="running", summary=None, started_at=now())
    steps: list[dict] = rec["steps"]
    notify = on_step or (lambda: None)
    calls = 0
    while True:
        if calls >= max_steps:
            rec.update(status="stopped", summary=f"Stopped after {max_steps} tool calls (the step limit).",
                       ended_at=now())
            notify()
            return rec
        asked_at = now()
        prompt = _prompt(business, tools, task, steps, max_steps)
        reply, problem = None, ""
        for attempt in range(2):
            try:
                text = ask(prompt if not attempt else
                           prompt + f"\n\nYour last reply could not be used ({problem}). Reply with exactly one "
                                    "JSON object as described.")
                reply = parse_reply(text, tools)
                break
            except llm.ModelUnavailable as e:
                problem = f"the model could not be reached: {e}"
                break
            except ValueError as e:
                problem = str(e)
        if reply is None:
            steps.append({"n": len(steps) + 1, "say": "", "tool": None, "args": None, "outcome": "error",
                          "result": f"The model's reply could not be used ({problem}), so the task stopped.",
                          "asked_at": asked_at, "started_at": now(), "ended_at": now()})
            rec.update(status="error", summary="Stopped: the model's reply could not be used.", ended_at=now())
            notify()
            return rec
        if reply["done"]:
            rec.update(status="done", summary=reply["summary"] or "Done.", ended_at=now())
            notify()
            return rec
        calls += 1
        step = {"n": len(steps) + 1, "say": reply["say"], "tool": reply["tool"], "args": reply["args"],
                "outcome": "running", "result": "", "data": None, "asked_at": asked_at, "started_at": now(),
                "ended_at": None}
        steps.append(step)
        notify()
        tool = tools[reply["tool"]]
        try:
            summary, data = tool.fn(**reply["args"])
            step.update(outcome="allowed", result=summary, data=data)
        except Refused as e:
            step.update(outcome="refused", result=f"Refused by code: {e}")
        except TypeError as e:
            step.update(outcome="error", result=f"The tool was called with the wrong arguments ({e}).")
        except Exception as e:   # a tool failure is a result the model sees, never a crash
            step.update(outcome="error", result=f"The tool failed: {str(e)[:200]}")
        step["ended_at"] = now()
        notify()


def summarise(tasks: list[dict]) -> dict:
    """Counts for the summary card, from what the tools actually did."""
    steps = [s for t in tasks for s in t.get("steps") or []]
    ok = lambda name: sum(1 for s in steps if s.get("tool") == name and s["outcome"] == "allowed")  # noqa: E731
    looked = set()
    for s in steps:
        if s.get("outcome") == "allowed" and isinstance(s.get("data"), dict):
            for k in ("balance_id",):
                if s["data"].get(k):
                    looked.add(s["data"][k])
    confirmed = sum(1 for s in steps if s.get("tool") == "wait_for_paypal" and s["outcome"] == "allowed"
                    and (s.get("data") or {}).get("state") == "CLAIMED")
    return {"found": len(looked), "held": ok("hold"), "notices": ok("draft_notice"),
            "sent_to_person": ok("send_to_person"), "paid": confirmed, "pay_sent": ok("pay_claim"),
            "refused": sum(1 for s in steps if s["outcome"] == "refused"),
            "tool_calls": sum(1 for s in steps if s.get("tool"))}
