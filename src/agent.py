"""Representation-agnostic agent prompt, model call and action parsing (OpenAI)."""
import asyncio
import json
import random
import re
import time

import tiktoken
from openai import AsyncOpenAI, RateLimitError

ENC = tiktoken.get_encoding("o200k_base")
PRICES = {"gpt-4.1-mini": (0.40, 1.60), "gpt-4.1": (2.00, 8.00), "gpt-4.1-nano": (0.10, 0.40),
          "gpt-4o-mini": (0.15, 0.60), "gpt-4o": (2.50, 10.00)}
ELEMENT_ACTIONS = {"click", "hover", "type", "select", "check", "uncheck"}
ACTIONS = ELEMENT_ACTIONS | {"press", "scroll", "goto", "go_back", "tab_focus", "noop", "stop"}

SYSTEM = """You are an autonomous web agent. You complete a task by issuing one browser action per turn.

Each turn you receive the task, the current URL and open tabs, your previous actions, and a text
representation of the current page. Interactive elements in the page text carry an ID in square
brackets like [12]. Only use IDs that appear in the current page text.

Reply with a single JSON object: {"thought": "<brief reasoning>", "action": {...}}.
Available actions:
- {"type": "click", "element": "<id>"}
- {"type": "hover", "element": "<id>"}
- {"type": "type", "element": "<id>", "text": "<text>", "press_enter": true|false}   (replaces the field's content; no need to click it first)
- {"type": "select", "element": "<id>", "option": "<visible option text>"}   (for <select>/combobox elements)
- {"type": "check" | "uncheck", "element": "<id>"}
- {"type": "press", "key": "<key, e.g. Enter, Escape, PageDown>"}
- {"type": "scroll", "direction": "down" | "up"}
- {"type": "goto", "url": "<url>"}
- {"type": "go_back"}
- {"type": "tab_focus", "index": <tab index>}
- {"type": "stop", "task_type": "RETRIEVE|MUTATE|NAVIGATE",
   "status": "SUCCESS|NOT_FOUND_ERROR|ACTION_NOT_ALLOWED_ERROR|PERMISSION_DENIED_ERROR|DATA_VALIDATION_ERROR|UNKNOWN_ERROR",
   "retrieved_data": [<items>] or null, "error_details": null or "<explanation>"}

Task types: RETRIEVE = getting information is the main objective (retrieved_data must be a list, even for
one item; use simple values such as strings or numbers unless the task asks for objects); MUTATE =
creating/updating/deleting data is the objective; NAVIGATE = showing a specific page or search result is
the objective (stop while that page is open). Tasks phrased as open / go to / show / view / pull up / navigate
to a page are NAVIGATE, even if the page contains data. For MUTATE and NAVIGATE use retrieved_data null.
Use "stop" only when the task is finished, or when it is impossible (with the matching error status).

Rules: stay within the given sites; do not log out or switch accounts; avoid changing state unless the task
requires it; do not download files; never ask the user for input. If an action had no visible effect, try
something different rather than repeating it."""


def site_context(task, sites_cfg):
    parts = []
    for site in task["sites"]:
        cfg = sites_cfg.get(site)
        if not cfg:
            continue
        parts.append(f"- {cfg['platform']} ({cfg['url']}): {cfg['description']} "
                     f"You are already logged in as {cfg['user']}.")
    return "\n".join(parts)


def truncate(text, max_tokens):
    tokens = ENC.encode(text, disallowed_special=())
    if len(tokens) <= max_tokens:
        return text, len(tokens), False
    return ENC.decode(tokens[:max_tokens]) + "\n[... page text truncated ...]", len(tokens), True


def build_messages(task, sites_cfg, start_urls, url, tabs, history, page_text, max_page_tokens):
    text, n_tokens, truncated = truncate(page_text, max_page_tokens)
    hist = "\n".join(f"{i + 1}. {h}" for i, h in enumerate(history)) or "(none yet)"
    tab_lines = "\n".join(f"  [{i}]{' (current)' if cur else ''} {t}" for i, (t, cur) in enumerate(tabs))
    user = (f"## Task\n{task['intent']}\n\n## Sites\n{site_context(task, sites_cfg)}\n"
            f"Start URLs: {', '.join(start_urls)}\n\n## Previous actions\n{hist}\n\n"
            f"## Open tabs\n{tab_lines}\n\n## Current page: {url}\n{text}")
    return [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}], n_tokens, truncated


def parse(reply):
    try:
        obj = json.loads(reply)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", reply, re.S)
        if not match:
            raise ValueError("no JSON object in reply")
        obj = json.loads(match.group(0))
    action = obj.get("action")
    if isinstance(action, str):
        action = {"type": action, **{k: v for k, v in obj.items() if k not in ("thought", "action")}}
    if not isinstance(action, dict) or action.get("type") not in ACTIONS:
        raise ValueError(f"invalid action: {action!r}")
    if action["type"] in ELEMENT_ACTIONS:
        action["element"] = str(action.get("element", "")).strip("[] ")
    return obj.get("thought", ""), action


class Model:
    def __init__(self, name, max_output_tokens=600, temperature=0.0):
        self.client = AsyncOpenAI(max_retries=6, timeout=120)
        self.name, self.max_output_tokens, self.temperature = name, max_output_tokens, temperature

    async def __call__(self, messages):
        start = time.time()
        for attempt in range(12):
            try:
                resp = await self.client.chat.completions.create(
                    model=self.name, messages=messages, temperature=self.temperature,
                    max_tokens=self.max_output_tokens, response_format={"type": "json_object"})
                break
            except RateLimitError as e:
                if attempt == 11 or "insufficient_quota" in str(e) or "no credits" in str(e):
                    raise
                await asyncio.sleep(min(60, 2 ** attempt) + random.random())
        usage = resp.usage
        price_in, price_out = PRICES.get(self.name, (0, 0))
        cached = getattr(getattr(usage, "prompt_tokens_details", None), "cached_tokens", 0) or 0
        cost = ((usage.prompt_tokens - cached * 0.75) * price_in + usage.completion_tokens * price_out) / 1e6
        return resp.choices[0].message.content or "", {
            "model": resp.model, "prompt_tokens": usage.prompt_tokens, "cached_tokens": cached,
            "completion_tokens": usage.completion_tokens, "cost_usd": round(cost, 6),
            "latency_s": round(time.time() - start, 2), "finish_reason": resp.choices[0].finish_reason}


def describe(action, element_line=None):
    kind = action["type"]
    if kind in ELEMENT_ACTIONS:
        target = element_line or f"[{action.get('element')}]"
        extra = {"type": f" text={json.dumps(action.get('text', ''))}" + (" +Enter" if action.get("press_enter") else ""),
                 "select": f" option={json.dumps(action.get('option', ''))}"}.get(kind, "")
        return f"{kind} {target}{extra}"
    if kind == "goto":
        return f"goto {action.get('url')}"
    if kind == "scroll":
        return f"scroll {action.get('direction', 'down')}"
    if kind == "press":
        return f"press {action.get('key')}"
    if kind == "tab_focus":
        return f"tab_focus {action.get('index')}"
    return kind
