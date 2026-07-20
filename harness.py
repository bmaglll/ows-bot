#!/usr/bin/env python3
"""
OWS Debug Harness — interactive shell for debugging the bot and developing
new error fixes without running a full RO batch.

Modes:
    python3 harness.py                    # attach to live browser (CDP) — interactive shell
    python3 harness.py --html DUMP.html   # load a saved debug HTML dump in a local
                                          # headless browser and use the same shell
    python3 harness.py --scrape DUMP.html # just run the error scraper on a dump (no browser)
    python3 harness.py --list-fixes       # list all registered error fixes

Typical workflow for adding a new error fix:
    1. Run the bot until it hits the unknown error — it saves a screenshot +
       HTML dump under logs/YYYY-MM-DD/debug/.
    2. `python3 harness.py --scrape logs/.../ows_debug_error_XXX.html`
       to confirm which codes/messages the scraper sees.
    3. `python3 harness.py --html logs/.../ows_debug_error_XXX.html`
       then use `sel` / `inputs` to find working selectors for the fields
       the fix needs to touch.
    4. Write fix_xxx() in ows_fixes.py and register it in ERROR_FIXES.
    5. Re-open the claim in OWS, run `python3 harness.py`, and use
       `fix XXX0001` + `prevalidate` + `scrape` to test it live.
       Use `reload` after editing ows_fixes.py — no need to restart the shell.
"""
from __future__ import annotations

import argparse
import importlib
import os
import re
import shlex
import sys
import time

from dotenv import load_dotenv

load_dotenv()

from playwright.sync_api import sync_playwright, Page

import ows_bot
import ows_fixes
from ows_config import CONFIG, get_cdp_url, get_stars_id, get_claude_model

try:
    import readline  # noqa: F401  (command history / line editing in the shell)
except ImportError:
    pass


def p(msg: str = "") -> None:
    print(msg, flush=True)


# ── Commands ─────────────────────────────────────────────────────────────────

HELP = """\
Commands:
  help                Show this help
  frames              List all frames on the page (index, url)
  pages               List all open browser tabs
  scrape              Run the error scraper (codes, messages, DEC0007, lock)
  fixes               List registered error fixes
  fix CODE            Run the fix registered for CODE (e.g. `fix SUB0006`)
  tryfix CODE [...]   Run the full try_fix_errors loop (fix + PreValidate + re-scrape)
  prevalidate         Click the PreValidate button and wait for the result
  sel SELECTOR        Query a CSS selector in every frame; print matches
  inputs PATTERN      List <input> elements whose name/id contains PATTERN
  comments            Print the Technician Comments textarea contents
  dump [LABEL]        Save a screenshot + full HTML dump to logs/…/debug/
  config              Print effective config summary
  reload              Reload ows_fixes.py (after editing it) without restarting
  quit / exit         Leave the shell
"""


def cmd_frames(page: Page) -> None:
    frames = ows_bot.all_frames(page)
    p(f"{len(frames)} frame(s):")
    for i, fr in enumerate(frames):
        p(f"  [{i:2d}] {fr.url[:110]}")


def cmd_pages(context) -> None:
    p(f"{len(context.pages)} open tab(s):")
    for i, pg in enumerate(context.pages):
        title = ""
        try:
            title = pg.title()
        except Exception:
            pass
        p(f"  [{i:2d}] {title[:40]!r} — {pg.url[:90]}")


def cmd_scrape(page: Page) -> None:
    details = ows_bot.scrape_claim_errors(page)
    p(f"has_dec0007 : {details['has_dec0007']}")
    p(f"is_locked   : {details['is_locked']}")
    p(f"error_codes : {sorted(set(details['error_codes']))}")
    p(f"all_codes   : {sorted(set(details['all_codes']))}")
    p("messages:")
    for msg in details["all_messages"]:
        p(f"  → {msg}")
    if not details["all_messages"]:
        p("  (none)")


def cmd_fixes() -> None:
    p(f"{len(ows_fixes.ERROR_FIXES)} registered fixes:")
    for code in sorted(ows_fixes.ERROR_FIXES):
        func = ows_fixes.ERROR_FIXES[code]
        doc = (func.__doc__ or "").strip().splitlines()
        summary = doc[0].strip() if doc else ""
        p(f"  {code:9s} {func.__name__:32s} {summary[:70]}")


def cmd_fix(page: Page, code: str) -> None:
    code = code.upper()
    func = ows_fixes.ERROR_FIXES.get(code)
    if not func:
        p(f"No fix registered for '{code}'. Use `fixes` to list available ones.")
        return
    p(f"Running {func.__name__} for {code} …")
    start = time.time()
    try:
        result = func(page, page.main_frame)
    except Exception as e:
        p(f"Fix raised an exception after {time.time()-start:.1f}s: {e!r}")
        return
    p(f"Fix returned {result} in {time.time()-start:.1f}s.")
    p("Tip: run `prevalidate` then `scrape` to see the effect.")


def cmd_tryfix(page: Page, codes: list[str]) -> None:
    codes = [c.upper() for c in codes]
    p(f"Running try_fix_errors for {codes} …")
    fixed, post = ows_fixes.try_fix_errors(page, page.main_frame, codes)
    p(f"fixed: {fixed}")
    if post:
        p(f"post-fix error_codes: {post['error_codes']}")
        p(f"post-fix has_dec0007: {post['has_dec0007']}")
        for msg in post["all_messages"]:
            p(f"  → {msg}")
    else:
        p("post-fix details: None (no fixes were applied)")


def cmd_sel(page: Page, selector: str) -> None:
    total = 0
    for i, fr in enumerate(ows_bot.all_frames(page)):
        try:
            elements = fr.locator(selector)
            count = elements.count()
        except Exception as e:
            p(f"  [frame {i}] selector error: {e}")
            return
        if count == 0:
            continue
        total += count
        p(f"[frame {i}] {count} match(es) — {fr.url[:80]}")
        for j in range(min(count, 10)):
            el = elements.nth(j)
            try:
                tag = el.evaluate("el => el.tagName")
                name = el.get_attribute("name") or ""
                el_id = el.get_attribute("id") or ""
                visible = el.is_visible()
                value = ""
                try:
                    value = el.input_value(timeout=300)
                except Exception:
                    try:
                        value = el.inner_text(timeout=300).strip()[:60]
                    except Exception:
                        pass
                p(f"  [{j}] <{tag.lower()}> name={name!r} id={el_id!r} "
                  f"visible={visible} value={value!r}")
            except Exception as e:
                p(f"  [{j}] (could not inspect: {e})")
        if count > 10:
            p(f"  … and {count - 10} more")
    if total == 0:
        p("No matches in any frame.")


def cmd_inputs(page: Page, pattern: str) -> None:
    pattern_lower = pattern.lower()
    total = 0
    for i, fr in enumerate(ows_bot.all_frames(page)):
        try:
            hits = fr.evaluate(
                """(pat) => {
                    const out = [];
                    document.querySelectorAll('input, select, textarea').forEach(el => {
                        const name = (el.name || '');
                        const id = (el.id || '');
                        if (name.toLowerCase().includes(pat) || id.toLowerCase().includes(pat)) {
                            out.push({tag: el.tagName, name, id, type: el.type || '',
                                      value: (el.value || '').substring(0, 60)});
                        }
                    });
                    return out;
                }""",
                pattern_lower,
            )
        except Exception:
            continue
        if hits:
            total += len(hits)
            p(f"[frame {i}] {len(hits)} match(es) — {fr.url[:80]}")
            for h in hits[:25]:
                p(f"  <{h['tag'].lower()} type={h['type']!r}> name={h['name']!r} "
                  f"id={h['id']!r} value={h['value']!r}")
            if len(hits) > 25:
                p(f"  … and {len(hits) - 25} more")
    if total == 0:
        p(f"No input/select/textarea matching '{pattern}' in any frame.")


def cmd_comments(page: Page) -> None:
    text = ows_fixes.read_technician_comments(page)
    if text:
        p("Technician Comments:")
        p(text)
    else:
        p("Technician Comments textarea not found or empty.")


def cmd_config() -> None:
    tech = None
    for t in CONFIG["technicians"].get("list", []):
        if t.get("key") == CONFIG["technicians"].get("default"):
            tech = t
            break
    p(f"CDP URL            : {get_cdp_url()}")
    p(f"Claude model       : {get_claude_model()}")
    p(f"Default technician : {tech.get('name') if tech else '(none)'}")
    p(f"STARS ID           : {get_stars_id() or '(not set)'}")
    p(f"Technicians        : {[t.get('key') for t in CONFIG['technicians'].get('list', [])]}")
    p(f"Registered fixes   : {len(ows_fixes.ERROR_FIXES)}")
    p(f"User-review errors : {len(CONFIG['user_review']['errors'])}")
    p(f"Recall lookups     : {sorted(CONFIG['recalls'].keys())}")
    p(f"Test drive ops     : {sorted(CONFIG['subcodes']['test_drive_ops'])}")
    p("(run `python3 ows_config.py` for the full merged config)")


def cmd_reload() -> None:
    importlib.reload(ows_fixes)
    p(f"Reloaded ows_fixes.py — {len(ows_fixes.ERROR_FIXES)} fixes registered.")


# ── Shell loop ───────────────────────────────────────────────────────────────

def shell(page: Page, context) -> None:
    p("\nOWS Debug Harness — type `help` for commands, `quit` to exit.")
    while True:
        try:
            line = input("ows> ").strip()
        except (EOFError, KeyboardInterrupt):
            p("")
            break
        if not line:
            continue
        try:
            parts = shlex.split(line)
        except ValueError:
            parts = line.split()
        cmd, args = parts[0].lower(), parts[1:]

        try:
            if cmd in ("quit", "exit", "q"):
                break
            elif cmd == "help":
                p(HELP)
            elif cmd == "frames":
                cmd_frames(page)
            elif cmd == "pages":
                cmd_pages(context)
            elif cmd == "scrape":
                cmd_scrape(page)
            elif cmd == "fixes":
                cmd_fixes()
            elif cmd == "fix":
                if not args:
                    p("Usage: fix CODE   (e.g. `fix SUB0006`)")
                else:
                    cmd_fix(page, args[0])
            elif cmd == "tryfix":
                if not args:
                    p("Usage: tryfix CODE [CODE …]")
                else:
                    cmd_tryfix(page, args)
            elif cmd == "prevalidate":
                ows_fixes.click_prevalidate(page)
            elif cmd == "sel":
                if not args:
                    p("Usage: sel SELECTOR   (quote selectors with spaces)")
                else:
                    cmd_sel(page, " ".join(args))
            elif cmd == "inputs":
                if not args:
                    p("Usage: inputs PATTERN   (e.g. `inputs ProgramCode`)")
                else:
                    cmd_inputs(page, args[0])
            elif cmd == "comments":
                cmd_comments(page)
            elif cmd == "dump":
                label = args[0] if args else "harness"
                ows_bot.save_debug(page, label)
                p(f"Saved screenshot + HTML dump under "
                  f"{ows_bot.daily_log_dir()}/debug/ (label: {label})")
            elif cmd == "config":
                cmd_config()
            elif cmd == "reload":
                cmd_reload()
            else:
                p(f"Unknown command '{cmd}' — type `help`.")
        except Exception as e:
            p(f"Command failed: {e!r}")


# ── Entry points ─────────────────────────────────────────────────────────────

def scrape_dump_offline(path: str) -> None:
    """Run the message-code scraper directly on a saved HTML dump (no browser)."""
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        html = f.read()
    all_messages = re.findall(r"([A-Z]{2,6}\d{2,5}\s*-\s*[^<\"]{0,300})", html)
    all_codes = sorted(set(re.findall(r"[A-Z]{2,6}\d{2,5}", " ".join(all_messages))))
    false_positives = {c.upper() for c in CONFIG["scrape"]["false_positive_codes"]}
    error_codes = [c for c in all_codes
                   if c != "DEC0007" and c not in false_positives
                   and not c.startswith("ESPA")]
    p(f"File        : {path}")
    p(f"has_dec0007 : {'DEC0007' in all_codes}")
    p(f"is_locked   : {'is currently being modified' in html}")
    p(f"error_codes : {error_codes}")
    p(f"all_codes   : {all_codes}")
    p("messages:")
    for msg in all_messages:
        p(f"  → {msg.strip()}")
    if not all_messages:
        p("  (none)")
    fixable = [c for c in error_codes if c in ows_fixes.ERROR_FIXES]
    unfixable = [c for c in error_codes if c not in ows_fixes.ERROR_FIXES]
    p(f"\nAuto-fixable   : {fixable or '(none)'}")
    p(f"No fix yet     : {unfixable or '(none)'}")


def run_live() -> None:
    cdp_url = get_cdp_url()
    p(f"Attaching to browser at {cdp_url} …")
    with sync_playwright() as pw:
        try:
            browser = pw.chromium.connect_over_cdp(cdp_url)
        except Exception as e:
            p(f"FATAL: could not attach to {cdp_url}: {e}")
            p("Start Chromium with:  chromium --remote-debugging-port=9222")
            sys.exit(2)
        if not browser.contexts:
            p("FATAL: no browser contexts found.")
            sys.exit(2)
        context = browser.contexts[0]
        page = ows_bot.pick_ows_page(context)
        if not page:
            p("FATAL: no OWS tab found.")
            sys.exit(2)
        page.set_default_timeout(CONFIG["timeouts"]["default_ms"])
        title = ""
        try:
            title = page.title()
        except Exception:
            pass
        p(f"Attached to tab: {title!r} — {page.url[:90]}")
        shell(page, context)


def run_offline(html_path: str) -> None:
    if not os.path.isfile(html_path):
        p(f"FATAL: file not found: {html_path}")
        sys.exit(2)
    p(f"Loading dump into a local headless browser: {html_path}")
    p("NOTE: saved dumps flatten all frames into one document, so everything")
    p("shows up in frame 0. Selector queries (`sel`, `inputs`, `scrape`) work;")
    p("fixes that click server-side Pega buttons will not have any effect.")
    with sync_playwright() as pw:
        browser = None
        try:
            browser = pw.chromium.launch(headless=True)
        except Exception:
            # Playwright's bundled browser missing — fall back to a system Chromium
            import shutil
            for exe in ("chromium", "chromium-browser", "google-chrome",
                        "google-chrome-stable", "/opt/pw-browsers/chromium"):
                path = shutil.which(exe) or (exe if os.path.isfile(exe) else None)
                if path:
                    try:
                        browser = pw.chromium.launch(headless=True, executable_path=path)
                        p(f"(using system browser: {path})")
                        break
                    except Exception:
                        continue
        if browser is None:
            p("FATAL: could not launch a local Chromium.")
            p("Install one with:  python3 -m playwright install chromium")
            sys.exit(2)
        context = browser.new_context()
        page = context.new_page()
        page.goto(f"file://{os.path.abspath(html_path)}")
        shell(page, context)
        browser.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="OWS debug harness — see module docstring for the fix-development workflow.")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--html", metavar="DUMP",
                       help="load a saved debug HTML dump in a headless browser")
    group.add_argument("--scrape", metavar="DUMP",
                       help="run the error scraper on a saved HTML dump (no browser)")
    group.add_argument("--list-fixes", action="store_true",
                       help="list registered error fixes and exit")
    args = parser.parse_args()

    if args.list_fixes:
        cmd_fixes()
    elif args.scrape:
        scrape_dump_offline(args.scrape)
    elif args.html:
        run_offline(args.html)
    else:
        run_live()


if __name__ == "__main__":
    main()
