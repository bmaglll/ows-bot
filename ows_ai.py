#!/usr/bin/env python3
"""
Provider-agnostic LLM inference for AI-assisted fixes.

A couple of fixes (condition code for ROV0068, causal part for SUB0003) fall
back to an LLM when regex can't read the value out of the technician comments.
Which LLM is configurable in config.toml → [ai]; Claude is just the default:

    [ai]
    provider = "anthropic"   # anthropic | openai | gemini | ollama | none
    model    = "claude-haiku-4-5-20251001"
    # base_url = "http://localhost:11434/v1"   # OpenAI-compatible endpoints

API keys come from the environment (.env):
    anthropic → ANTHROPIC_API_KEY
    openai    → OPENAI_API_KEY   (also any OpenAI-compatible endpoint via base_url)
    gemini    → GEMINI_API_KEY or GOOGLE_API_KEY
    ollama    → none (local)

infer(prompt, max_tokens) returns the model's text answer, or None if the
provider is "none", unconfigured, its package isn't installed, or the call
errors. Callers already treat None as "couldn't determine" and carry on, so AI
inference is always optional — a missing package or key just skips it.
"""
from __future__ import annotations

import json
import os
import urllib.request

from ows_config import get_ai_config


def log(msg: str) -> None:
    print(f"[OWS][AI] {msg}", flush=True)


def infer(prompt: str, max_tokens: int = 20) -> str | None:
    cfg = get_ai_config()
    provider, model, base_url = cfg["provider"], cfg["model"], cfg["base_url"]

    if provider in ("", "none", "off", "disabled"):
        return None
    if not model and provider != "ollama":
        log(f"No model set for provider '{provider}' — set [ai].model in config.toml.")
        return None

    try:
        if provider == "anthropic":
            return _anthropic(prompt, model, max_tokens)
        if provider in ("openai", "openai_compatible", "compatible"):
            return _openai(prompt, model, max_tokens, base_url)
        if provider in ("gemini", "google"):
            return _gemini(prompt, model, max_tokens)
        if provider == "ollama":
            return _ollama(prompt, model or "llama3", max_tokens, base_url)
        log(f"Unknown AI provider '{provider}'. Use anthropic | openai | gemini | ollama | none.")
        return None
    except Exception as e:
        log(f"{provider} call failed: {e}")
        return None


def _anthropic(prompt: str, model: str, max_tokens: int) -> str | None:
    if not os.getenv("ANTHROPIC_API_KEY"):
        log("ANTHROPIC_API_KEY not set — skipping AI inference.")
        return None
    try:
        import anthropic
    except ImportError:
        log("anthropic package not installed — skipping (pip install anthropic).")
        return None
    client = anthropic.Anthropic()
    resp = client.messages.create(
        model=model, max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    )
    return "".join(b.text for b in resp.content
                   if getattr(b, "type", "") == "text").strip()


def _openai(prompt: str, model: str, max_tokens: int, base_url: str) -> str | None:
    # base_url set → an OpenAI-compatible endpoint (may not need a key)
    if not base_url and not os.getenv("OPENAI_API_KEY"):
        log("OPENAI_API_KEY not set — skipping AI inference.")
        return None
    try:
        import openai
    except ImportError:
        log("openai package not installed — skipping (pip install openai).")
        return None
    client = openai.OpenAI(base_url=base_url or None,
                           api_key=os.getenv("OPENAI_API_KEY") or "not-needed")
    resp = client.chat.completions.create(
        model=model, max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    )
    return (resp.choices[0].message.content or "").strip()


def _gemini(prompt: str, model: str, max_tokens: int) -> str | None:
    key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if not key:
        log("GEMINI_API_KEY / GOOGLE_API_KEY not set — skipping AI inference.")
        return None
    try:
        import google.generativeai as genai
    except ImportError:
        log("google-generativeai not installed — skipping "
            "(pip install google-generativeai).")
        return None
    genai.configure(api_key=key)
    gm = genai.GenerativeModel(model)
    resp = gm.generate_content(
        prompt, generation_config={"max_output_tokens": max_tokens})
    return (resp.text or "").strip()


def _ollama(prompt: str, model: str, max_tokens: int, base_url: str) -> str | None:
    url = (base_url or "http://localhost:11434").rstrip("/")
    if url.endswith("/v1"):  # tolerate an OpenAI-compat base_url
        url = url[:-3]
    body = json.dumps({
        "model": model, "prompt": prompt, "stream": False,
        "options": {"num_predict": max_tokens},
    }).encode()
    req = urllib.request.Request(
        url + "/api/generate", data=body,
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        data = json.loads(r.read().decode())
    return (data.get("response") or "").strip()


if __name__ == "__main__":
    # Quick check: python3 ows_ai.py "your prompt here"
    import sys
    cfg = get_ai_config()
    print(f"Provider: {cfg['provider']}  model: {cfg['model'] or '(default)'}  "
          f"base_url: {cfg['base_url'] or '(none)'}")
    test = sys.argv[1] if len(sys.argv) > 1 else "Reply with the single word OK."
    print("Result:", infer(test, max_tokens=20))
