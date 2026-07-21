#!/usr/bin/env python3
"""
OWS Harness MCP Server — lets an LLM (Claude Code, Claude Desktop, or any MCP
client) drive the OWS debug harness: attach to the live browser, scrape claim
errors, hunt selectors, run individual fixes, and capture debug dumps.

Run (stdio transport — what MCP clients expect):
    python3 mcp_server.py

Register with Claude Code (already done via .mcp.json in this repo), or with
Claude Desktop by adding to its MCP settings:
    {"mcpServers": {"ows-harness": {"command": "python3",
                                    "args": ["/path/to/ows-bot/mcp_server.py"]}}}

Safety: like the interactive harness, this server never clicks Submit. Fixes
mutate the open claim form (that's their job), but submitting a claim stays a
human/bot decision outside the MCP surface.

Threading note: Playwright's sync API is single-threaded, while MCP tools run
on an asyncio loop with a thread pool. All browser state therefore lives on one
dedicated worker thread; every tool marshals its work onto that thread.
"""
from __future__ import annotations

import importlib
import os
import queue
import re
import threading
import time
from typing import Any, Optional

from dotenv import load_dotenv

load_dotenv()

from mcp.server.fastmcp import FastMCP, Image

import ows_bot
import ows_fixes
from ows_config import CONFIG, get_cdp_url, get_claude_model, get_stars_id

mcp = FastMCP(
    "ows-harness",
    instructions=(
        "Debug harness for the OWS warranty-claim bot. Typical flow: "
        "attach() to the live browser (or load_dump() on a saved HTML dump), "
        "scrape_errors() to see what's wrong with the open claim, "
        "find_inputs()/query_selector() to locate fields, run_fix()/prevalidate() "
        "to test fixes, screenshot()/save_debug() to capture state. "
        "The only irreversible action is submit_claim, and it is gated: it does "
        "nothing (a dry run) unless you pass confirm=true, and it refuses to "
        "submit a claim with outstanding errors unless you also pass force=true. "
        "Always show the user the claim state and get their go-ahead before "
        "calling submit_claim with confirm=true."
    ),
)


# ── Browser worker thread ────────────────────────────────────────────────────
# Owns the sync Playwright connection. All tools funnel through .call().

class BrowserWorker:
    def __init__(self) -> None:
        self._jobs: queue.Queue = queue.Queue()
        self.pw = None
        self.browser = None
        self.context = None
        self.page = None
        self.mode: Optional[str] = None  # "live" | "offline"
        threading.Thread(target=self._run, name="pw-worker", daemon=True).start()

    def _run(self) -> None:
        while True:
            func, args, box = self._jobs.get()
            try:
                box["result"] = func(*args)
            except BaseException as e:  # surfaced to the caller thread
                box["error"] = e
            box["done"].set()

    def call(self, func, *args) -> Any:
        box: dict = {"done": threading.Event()}
        self._jobs.put((func, args, box))
        box["done"].wait()
        if "error" in box:
            raise box["error"]
        return box["result"]

    # ── connection management (run on worker thread via .call) ──────────────

    def _teardown(self) -> None:
        for closer in (lambda: self.browser.close() if self.mode == "offline" else None,
                       lambda: self.pw.stop()):
            try:
                closer()
            except Exception:
                pass
        self.pw = self.browser = self.context = self.page = None
        self.mode = None

    def _attach_live(self, cdp_url: str) -> dict:
        from playwright.sync_api import sync_playwright
        self._teardown()
        self.pw = sync_playwright().start()
        try:
            self.browser = self.pw.chromium.connect_over_cdp(cdp_url)
        except Exception as e:
            self._teardown()
            raise RuntimeError(
                f"Could not attach to {cdp_url}: {e}. "
                "Start Chromium with: chromium --remote-debugging-port=9222") from e
        if not self.browser.contexts:
            self._teardown()
            raise RuntimeError("No browser contexts found in the target browser.")
        self.context = self.browser.contexts[0]
        self.page = ows_bot.pick_ows_page(self.context)
        if not self.page:
            self._teardown()
            raise RuntimeError("No OWS tab found. Open OWS in the browser first.")
        self.page.set_default_timeout(CONFIG["timeouts"]["default_ms"])
        self.mode = "live"
        return self._status()

    def _load_dump(self, html_path: str) -> dict:
        import shutil
        from playwright.sync_api import sync_playwright
        self._teardown()
        self.pw = sync_playwright().start()
        browser = None
        try:
            browser = self.pw.chromium.launch(headless=True)
        except Exception:
            for exe in ("chromium", "chromium-browser", "google-chrome",
                        "google-chrome-stable", "/opt/pw-browsers/chromium"):
                path = shutil.which(exe) or (exe if os.path.isfile(exe) else None)
                if path:
                    try:
                        browser = self.pw.chromium.launch(headless=True, executable_path=path)
                        break
                    except Exception:
                        continue
        if browser is None:
            self._teardown()
            raise RuntimeError("Could not launch a local Chromium. "
                               "Install one with: python3 -m playwright install chromium")
        self.browser = browser
        self.context = browser.new_context()
        self.page = self.context.new_page()
        self.page.goto(f"file://{os.path.abspath(html_path)}")
        self.mode = "offline"
        return self._status()

    def _status(self) -> dict:
        if not self.page:
            return {"attached": False,
                    "hint": "Call attach() for the live browser or load_dump() for a saved dump."}
        title = ""
        try:
            title = self.page.title()
        except Exception:
            pass
        return {
            "attached": True,
            "mode": self.mode,
            "page_title": title,
            "page_url": self.page.url[:200],
            "frame_count": len(ows_bot.all_frames(self.page)),
        }

    def require_page(self):
        if not self.page:
            raise RuntimeError("Not attached to a page — call attach() (live browser) "
                               "or load_dump() (saved HTML dump) first.")
        return self.page


worker = BrowserWorker()


# ── Connection tools ─────────────────────────────────────────────────────────

@mcp.tool()
def attach(cdp_url: str = "") -> dict:
    """Attach to the live OWS browser over CDP. Picks the tab most likely to be
    OWS. Call this before any page tool. Omit cdp_url to use the configured one
    (config.toml / CDP_URL env). Reattaching replaces the current connection."""
    return worker.call(worker._attach_live, cdp_url or get_cdp_url())


@mcp.tool()
def load_dump(html_path: str) -> dict:
    """Load a saved debug HTML dump (logs/YYYY-MM-DD/debug/ows_debug_*.html)
    into a local headless browser for offline inspection. Dumps flatten all
    frames into one document, so selector queries work but clicking Pega
    buttons has no effect (there is no server behind the page)."""
    if not os.path.isfile(html_path):
        raise RuntimeError(f"File not found: {html_path}")
    return worker.call(worker._load_dump, html_path)


@mcp.tool()
def detach() -> dict:
    """Disconnect from the browser (live or offline). The browser itself keeps
    running; only this server's connection is closed."""
    worker.call(worker._teardown)
    return {"attached": False}


@mcp.tool()
def status() -> dict:
    """Current connection status: mode (live/offline), page title/URL, frame count."""
    return worker.call(worker._status)


# ── Inspection tools ─────────────────────────────────────────────────────────

@mcp.tool()
def list_pages() -> list[dict]:
    """List all open browser tabs (live mode) with index, title, and URL."""
    def impl():
        page = worker.require_page()
        ctx = worker.context
        out = []
        for i, pg in enumerate(ctx.pages):
            title = ""
            try:
                title = pg.title()
            except Exception:
                pass
            out.append({"index": i, "title": title[:80], "url": pg.url[:150],
                        "is_current": pg is page})
        return out
    return worker.call(impl)


@mcp.tool()
def list_frames() -> list[dict]:
    """List all frames on the current page. OWS is a Pega app made of nested
    iframes — the claim form, message area, and tab strip live in different frames."""
    def impl():
        page = worker.require_page()
        return [{"index": i, "url": fr.url[:150]}
                for i, fr in enumerate(ows_bot.all_frames(page))]
    return worker.call(impl)


@mcp.tool()
def scrape_errors() -> dict:
    """Scrape the open claim for OWS message codes across all frames. Returns
    error codes, full message texts, whether DEC0007 (pre-validation success)
    is present, lock status, and which errors have registered auto-fixes."""
    def impl():
        page = worker.require_page()
        details = ows_bot.scrape_claim_errors(page)
        errors = sorted(set(details["error_codes"]))
        return {
            "has_dec0007": details["has_dec0007"],
            "is_locked": details["is_locked"],
            "error_codes": errors,
            "all_codes": sorted(set(details["all_codes"])),
            "messages": sorted(set(details["all_messages"])),
            "auto_fixable": [c for c in errors if c.upper() in ows_fixes.ERROR_FIXES],
            "no_fix_registered": [c for c in errors if c.upper() not in ows_fixes.ERROR_FIXES],
        }
    return worker.call(impl)


@mcp.tool()
def query_selector(selector: str, limit: int = 10) -> list[dict]:
    """Query a CSS selector in every frame. Returns per-match tag, name, id,
    visibility, and current value/text. Use this to develop selectors for new
    fixes — match Pega inputs on stable name fragments like
    input[name*='ProgramCode'], never on full names ($l1 row indices change)."""
    def impl():
        page = worker.require_page()
        results = []
        for i, fr in enumerate(ows_bot.all_frames(page)):
            try:
                elements = fr.locator(selector)
                count = elements.count()
            except Exception as e:
                raise RuntimeError(f"Selector error: {e}") from e
            if count == 0:
                continue
            for j in range(min(count, limit)):
                el = elements.nth(j)
                info = {"frame": i, "match": j, "of": count}
                try:
                    info["tag"] = el.evaluate("el => el.tagName").lower()
                    info["name"] = el.get_attribute("name") or ""
                    info["id"] = el.get_attribute("id") or ""
                    info["visible"] = el.is_visible()
                    try:
                        info["value"] = el.input_value(timeout=300)
                    except Exception:
                        try:
                            info["text"] = el.inner_text(timeout=300).strip()[:100]
                        except Exception:
                            pass
                except Exception as e:
                    info["error"] = str(e)[:100]
                results.append(info)
        return results
    return worker.call(impl)


@mcp.tool()
def find_inputs(pattern: str) -> list[dict]:
    """Find every input/select/textarea whose name or id contains the given
    substring (case-insensitive), across all frames. Faster than guessing
    selectors — e.g. find_inputs('Approval') locates the Approval Code field."""
    def impl():
        page = worker.require_page()
        pat = pattern.lower()
        results = []
        for i, fr in enumerate(ows_bot.all_frames(page)):
            try:
                hits = fr.evaluate(
                    """(pat) => {
                        const out = [];
                        document.querySelectorAll('input, select, textarea').forEach(el => {
                            const name = (el.name || '');
                            const id = (el.id || '');
                            if (name.toLowerCase().includes(pat) || id.toLowerCase().includes(pat)) {
                                out.push({tag: el.tagName.toLowerCase(), name, id,
                                          type: el.type || '',
                                          value: (el.value || '').substring(0, 80)});
                            }
                        });
                        return out;
                    }""",
                    pat,
                )
            except Exception:
                continue
            for h in hits:
                h["frame"] = i
                results.append(h)
        return results
    return worker.call(impl)


@mcp.tool()
def read_comments() -> str:
    """Read the Technician Comments textarea from the open claim. Comments
    often contain the data fixes need (condition codes, VINs, part numbers,
    validation codes)."""
    def impl():
        worker.require_page()
        return ows_fixes.read_technician_comments(worker.page) or ""
    return worker.call(impl)


# ── Interaction tools (dictated, click-by-click) ─────────────────────────────
# These are raw actions for walking through a fix by hand: fill a field, click
# a button, pick a dropdown. They mirror exactly what the fix functions in
# ows_fixes.py do (scan all frames, act on the first visible match), so the
# steps that work here translate 1:1 into a baked-in fix. They mutate the open
# form but never submit — that stays submit_claim.

@mcp.tool()
def fill_field(selector: str, value: str, press_tab: bool = False) -> dict:
    """Type a value into the first visible input/textarea matching a CSS
    selector, across all frames. The building block for walking a fix
    click-by-click. Match on stable name fragments
    (e.g. input[name*='ApprovalCode']), never full Pega names ($l1 changes).
    Set press_tab=true to Tab out afterwards (commits the value in some Pega
    fields). Returns the frame/name/id it acted on so the step can be
    transcribed verbatim into a fix function."""
    def impl():
        page = worker.require_page()
        for i, fr in enumerate(ows_bot.all_frames(page)):
            try:
                loc = fr.locator(selector).first
                if loc.count() > 0 and loc.is_visible():
                    loc.click()
                    loc.fill(value)
                    if press_tab:
                        loc.press("Tab")
                    time.sleep(0.3)
                    return {"ok": True, "frame": i, "selector": selector,
                            "name": loc.get_attribute("name") or "",
                            "id": loc.get_attribute("id") or "", "value": value}
            except Exception:
                continue
        return {"ok": False, "selector": selector,
                "error": "No visible input matched in any frame."}
    return worker.call(impl)


@mcp.tool()
def click_element(selector: str) -> dict:
    """Click the first visible element matching a CSS selector, across all
    frames — buttons, radios, 'Add a row' links, tab icons, etc. The building
    block for walking a fix click-by-click. Returns the frame/name/id clicked
    so the step can be transcribed into a fix function."""
    def impl():
        page = worker.require_page()
        for i, fr in enumerate(ows_bot.all_frames(page)):
            try:
                loc = fr.locator(selector).first
                if loc.count() > 0 and loc.is_visible():
                    loc.scroll_into_view_if_needed()
                    loc.click()
                    time.sleep(0.3)
                    return {"ok": True, "frame": i, "selector": selector,
                            "name": loc.get_attribute("name") or "",
                            "id": loc.get_attribute("id") or ""}
            except Exception:
                continue
        return {"ok": False, "selector": selector,
                "error": "No visible element matched in any frame."}
    return worker.call(impl)


@mcp.tool()
def select_option(selector: str, value: str = "", label: str = "") -> dict:
    """Choose an option in the first visible <select> matching a CSS selector,
    across all frames (e.g. the Claim Type dropdown, select#ClaimTypeDesc).
    Pass value= (the option's value attribute) or label= (its visible text).
    The building block for walking a fix click-by-click."""
    if not value and not label:
        raise RuntimeError("Pass value= (option value) or label= (visible text).")

    def impl():
        page = worker.require_page()
        for i, fr in enumerate(ows_bot.all_frames(page)):
            try:
                loc = fr.locator(selector).first
                if loc.count() > 0 and loc.is_visible():
                    if value:
                        loc.select_option(value=value)
                    else:
                        loc.select_option(label=label)
                    time.sleep(0.5)
                    return {"ok": True, "frame": i, "selector": selector,
                            "selected": value or label}
            except Exception:
                continue
        return {"ok": False, "selector": selector,
                "error": "No visible <select> matched in any frame."}
    return worker.call(impl)


@mcp.tool()
def screenshot(full_page: bool = False) -> Image:
    """Take a PNG screenshot of the current page. Set full_page=true to capture
    the whole scrollable page instead of the viewport."""
    def impl():
        page = worker.require_page()
        return page.screenshot(full_page=full_page)
    return Image(data=worker.call(impl), format="png")


@mcp.tool()
def save_debug(label: str = "mcp") -> dict:
    """Save a full-page screenshot + all-frames HTML dump to
    logs/YYYY-MM-DD/debug/ (same format the bot writes on failures)."""
    def impl():
        page = worker.require_page()
        ows_bot.save_debug(page, label)
        debug_dir = os.path.join(ows_bot.daily_log_dir(), "debug")
        safe = re.sub(r"[^a-zA-Z0-9_.-]+", "_", label).strip("_")
        files = sorted(f for f in os.listdir(debug_dir) if safe in f)[-2:]
        return {"debug_dir": debug_dir, "files": files}
    return worker.call(impl)


# ── Fix tools ────────────────────────────────────────────────────────────────

@mcp.tool()
def list_fixes() -> list[dict]:
    """List every registered error auto-fix: error code, function name, and a
    one-line summary. Works without being attached to a browser."""
    out = []
    for code in sorted(ows_fixes.ERROR_FIXES):
        func = ows_fixes.ERROR_FIXES[code]
        doc = (func.__doc__ or "").strip().splitlines()
        out.append({"code": code, "function": func.__name__,
                    "summary": doc[0].strip() if doc else ""})
    return out


@mcp.tool()
def run_fix(error_code: str) -> dict:
    """Run the single fix registered for an error code against the open claim.
    Returns whether the fix reported success. Does NOT PreValidate or submit —
    call prevalidate() then scrape_errors() afterwards to see the effect."""
    import time as _time
    code = error_code.upper()
    func = ows_fixes.ERROR_FIXES.get(code)
    if not func:
        raise RuntimeError(f"No fix registered for '{code}'. Use list_fixes().")

    def impl():
        page = worker.require_page()
        start = _time.time()
        result = func(page, page.main_frame)
        return {"error_code": code, "function": func.__name__,
                "applied": bool(result), "seconds": round(_time.time() - start, 1),
                "next_step": "Call prevalidate() then scrape_errors() to verify."}
    return worker.call(impl)


@mcp.tool()
def run_fix_loop(error_codes: list[str]) -> dict:
    """Run the bot's full try_fix_errors loop (fix → PreValidate → re-scrape,
    bottom-up, up to 5 rounds) for the given error codes. This is exactly what
    the bot does on a live claim; it can take a few minutes. Never submits."""
    codes = [c.upper() for c in error_codes]

    def impl():
        page = worker.require_page()
        fixed, post = ows_fixes.try_fix_errors(page, page.main_frame, codes)
        result = {"fixed": fixed}
        if post:
            result.update({
                "remaining_error_codes": sorted(set(post["error_codes"])),
                "has_dec0007": post["has_dec0007"],
                "messages": sorted(set(post["all_messages"])),
            })
        else:
            result["note"] = "No fixes were applied."
        return result
    return worker.call(impl)


@mcp.tool()
def prevalidate() -> dict:
    """Click the PreValidate button on the open claim and wait for validation
    to complete (up to 45s), then re-scrape the resulting message codes."""
    def impl():
        page = worker.require_page()
        clicked = ows_fixes.click_prevalidate(page)
        details = ows_fixes.scrape_errors_quick(page)
        return {"prevalidate_clicked": clicked,
                "has_dec0007": details["has_dec0007"],
                "error_codes": sorted(set(details["error_codes"])),
                "messages": sorted(set(details["all_messages"]))}
    return worker.call(impl)


@mcp.tool()
def reload_fixes() -> dict:
    """Re-import ows_fixes.py after it has been edited, so run_fix/list_fixes
    pick up new or changed fix functions without restarting the server."""
    importlib.reload(ows_fixes)
    return {"reloaded": True, "fix_count": len(ows_fixes.ERROR_FIXES)}


@mcp.tool()
def submit_claim(confirm: bool = False, force: bool = False) -> dict:
    """Submit the open claim — the ONE irreversible action on this server.

    By default this is a DRY RUN: it reports the claim state (error codes,
    DEC0007, lock) and what would block a submit, and clicks nothing. Pass
    confirm=true to actually click Submit. As a safety gate it refuses to
    submit a claim that has outstanding error codes, no DEC0007 pre-validation,
    or is locked by Ford's system — pass force=true to override that gate.

    Always surface the claim state to the human and get their go-ahead before
    calling this with confirm=true."""
    import time as _time

    def impl():
        page = worker.require_page()
        details = ows_bot.scrape_claim_errors(page)
        errors = sorted(set(details["error_codes"]))
        preview = {
            "error_codes": errors,
            "has_dec0007": details["has_dec0007"],
            "is_locked": details["is_locked"],
        }
        blocked = []
        if errors:
            blocked.append(f"outstanding error codes: {errors}")
        if not details["has_dec0007"]:
            blocked.append("no DEC0007 pre-validation success")
        if details["is_locked"]:
            blocked.append("claim is locked by Ford's system")

        if not confirm:
            hint = "Dry run — pass confirm=true to submit."
            if blocked:
                hint += " Blocking conditions are present; a real submit would also need force=true."
            return {"submitted": False, "dry_run": True, "would_block": blocked,
                    "hint": hint, **preview}

        if blocked and not force:
            return {"submitted": False, "blocked": blocked,
                    "hint": "Resolve these first, or pass force=true to submit anyway.",
                    **preview}

        # Click the Submit button in whichever frame has it
        clicked = False
        for fr in ows_bot.all_frames(page):
            try:
                btn = fr.locator(ows_bot.SUBMIT_BTN_SEL).first
                if btn.count() > 0 and btn.is_visible():
                    btn.scroll_into_view_if_needed()
                    btn.click()
                    clicked = True
                    break
            except Exception:
                continue
        if not clicked:
            return {"submitted": False, "error": "Submit button not found.", **preview}

        # Wait for the confirmation text or a post-submit error
        deadline = _time.time() + CONFIG["timeouts"]["default_ms"] / 1000
        while _time.time() < deadline:
            post_html = ""
            for fr in ows_bot.all_frames(page):
                try:
                    post_html += fr.content()
                except Exception:
                    pass
            if ows_bot.SUBMITTED_TEXT in post_html:
                return {"submitted": True, "confirmed": True,
                        "message": ows_bot.SUBMITTED_TEXT, **preview}
            post = ows_bot.scrape_claim_errors(page)
            if post["error_codes"]:
                return {"submitted": True, "confirmed": False,
                        "post_submit_errors": sorted(set(post["error_codes"])),
                        "messages": sorted(set(post["all_messages"]))}
            _time.sleep(0.5)
        return {"submitted": True, "confirmed": False,
                "note": f"'{ows_bot.SUBMITTED_TEXT}' not seen within the timeout — "
                        "check the page.", **preview}
    return worker.call(impl)


# ── Config / offline tools ───────────────────────────────────────────────────

@mcp.tool()
def scrape_dump(html_path: str) -> dict:
    """Run the error scraper over a saved debug HTML dump without any browser.
    Fastest way to see what codes/messages a past failure contained and whether
    fixes exist for them."""
    with open(html_path, "r", encoding="utf-8", errors="replace") as f:
        html = f.read()
    all_messages = re.findall(r"([A-Z]{2,6}\d{2,5}\s*-\s*[^<\"]{0,300})", html)
    all_codes = sorted(set(re.findall(r"[A-Z]{2,6}\d{2,5}", " ".join(all_messages))))
    false_positives = {c.upper() for c in CONFIG["scrape"]["false_positive_codes"]}
    error_codes = [c for c in all_codes
                   if c != "DEC0007" and c not in false_positives
                   and not c.startswith("ESPA")]
    return {
        "file": html_path,
        "has_dec0007": "DEC0007" in all_codes,
        "is_locked": "is currently being modified" in html,
        "error_codes": error_codes,
        "messages": sorted(set(m.strip() for m in all_messages)),
        "auto_fixable": [c for c in error_codes if c in ows_fixes.ERROR_FIXES],
        "no_fix_registered": [c for c in error_codes if c not in ows_fixes.ERROR_FIXES],
    }


@mcp.tool()
def list_debug_dumps(date: str = "") -> list[dict]:
    """List saved debug dumps (HTML + screenshots). Pass date as YYYY-MM-DD, or
    omit for all dates. Use scrape_dump()/load_dump() on the returned paths."""
    base = CONFIG["logging"]["base_dir"]
    out = []
    if not os.path.isdir(base):
        return out
    for day in sorted(os.listdir(base)):
        if date and day != date:
            continue
        debug_dir = os.path.join(base, day, "debug")
        if not os.path.isdir(debug_dir):
            continue
        for f in sorted(os.listdir(debug_dir)):
            path = os.path.join(debug_dir, f)
            out.append({"date": day, "file": f, "path": path,
                        "size_kb": round(os.path.getsize(path) / 1024, 1)})
    return out


@mcp.tool()
def get_config() -> dict:
    """Effective bot configuration summary (config.toml + config.local.toml +
    env): technician, STARS ID presence, recalls, error lists, rates."""
    tech = None
    for t in CONFIG["technicians"].get("list", []):
        if t.get("key") == CONFIG["technicians"].get("default"):
            tech = t
            break
    return {
        "cdp_url": get_cdp_url(),
        "claude_model": get_claude_model(),
        "default_technician": tech.get("name") if tech else None,
        "stars_id_configured": bool(get_stars_id()),
        "technician_keys": [t.get("key") for t in CONFIG["technicians"].get("list", [])],
        "registered_fixes": len(ows_fixes.ERROR_FIXES),
        "user_review_errors": sorted(CONFIG["user_review"]["errors"]),
        "recall_subcodes": sorted(CONFIG["recalls"].keys()),
        "test_drive_ops": sorted(CONFIG["subcodes"]["test_drive_ops"]),
        "approval_codes": dict(CONFIG["approval_codes"]),
        "rental": dict(CONFIG["rental"]),
    }


if __name__ == "__main__":
    mcp.run()  # stdio transport
