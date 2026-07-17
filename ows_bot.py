#!/usr/bin/env python3
"""
OWS Playwright bot (CDP attach) - Corrected Version
Attaches to a running Chromium with --remote-debugging-port=9222.
Assumes user is already logged into OWS and Claim Status Report is open.
Usage:
    python3 ows_bot.py 513271
    python3 ows_bot.py          # will prompt
"""
from __future__ import annotations
import csv
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
from typing import Optional
from playwright.sync_api import (
    sync_playwright,
    TimeoutError as PWTimeoutError,
    Page,
    Frame,
)
from dotenv import load_dotenv
load_dotenv()
from ows_fixes import try_fix_errors
# ── Config ────────────────────────────────────────────────────────────────────
CDP_URL            = os.getenv("CDP_URL", "http://127.0.0.1:9222")
DEFAULT_TIMEOUT_MS = 30_000
SHORT_TIMEOUT_MS   = 5_000
POLL_INTERVAL_S    = 2.0   # seconds between "Paid" status polls
POLL_TIMEOUT_S     = 30    # max seconds to poll before moving on
LOG_BASE           = "logs"             # Base log directory
MAX_PAID_POLLS     = 30    # give up after this many polls (~60 s)
# Error codes that need user review — skip instead of stopping
USER_REVIEW_ERRORS = {"RDC0002", "FSA0001", "FSA0003", "FSA0022", "ROV0039", "MCA0004", "TOT0001", "RVC0011", "SUB0001", "SUB0003", "SUB0021", "SCCK112", "SCCK109", "SCCK075", "SCCK069", "SCCK062", "SCCK055", "SCCK050", "SCCK046", "SCCK012", "SCCK602", "BES0206", "BES0254", "SFRNG81", "PAC1023", "LAB0003", "ATT0001", "SUB0005", "SDCU903", "SFRU462", "MIS0005", "SFRU330", "SFRUC13", "EFC16335", "SUB0013", "ROV0068", "SFRUD96", "ROV0024", "SCCK005", "SCCK012", "LAB0005", "SFRU364", "BOM0002", "SFRNG46", "RVC0013", "SFRU733", "PAC1023", "TV0001", "MIS0002", "SFPNK20"}
# Human-readable notes for user review errors (shown in log output)
USER_REVIEW_NOTES = {
    "ATT0001": "Requires document attachment",
}
# Selectors confirmed against live DOM
RO_INPUT_SEL      = "input#RepairOrderNumber"
INQUIRE_BTN_SEL   = "button:has-text('Inquire')"
# The claim row selector (first data table — WORK IN PROCESS section)
# PEGA generates rows with id like "$PClaimRecords$ppxResults$l1"
CLAIM_ROW_SEL     = "tr[id*='ClaimRecords'][id*='ppxResults']"
# Pre-validation success message (exact substring found in page)
PREVALIDATION_TEXT = "DEC0007 - CLAIM"  # Partial match to handle variations
# Submit button selector (try onclick attribute first, fallback to text)
SUBMIT_BTN_SEL    = "button[onclick*='SubmitClaimUnit'], button:has-text('Submit')"
PREVALIDATE_BTN_SEL = "button[onclick*='PreValidate'], button:has-text('PreValidate')"
# "The repair line has been submitted" confirmation text
SUBMITTED_TEXT    = "The repair line has been submitted"
# Close button for the claim tab (the ✕ on the tab strip)
CLOSE_TAB_SEL     = "button.container-close, button[id='container_close']"
# ── Logging ───────────────────────────────────────────────────────────────────
def log(msg: str) -> None:
    print(f"[OWS] {msg}", flush=True)
def die(msg: str, code: int = 2) -> None:
    print(f"[OWS][FATAL] {msg}", flush=True)
    sys.exit(code)

def save_debug(page: Page, label: str) -> None:
    safe = re.sub(r"[^a-zA-Z0-9_.-]+", "_", label).strip("_")
    ts   = time.strftime("%Y%m%d-%H%M%S")
    debug_dir = os.path.join(daily_log_dir(), "debug")
    os.makedirs(debug_dir, exist_ok=True)
    try:
        page.screenshot(path=os.path.join(debug_dir, f"ows_debug_{safe}_{ts}.png"), full_page=True)
    except Exception as e:
        log(f"Screenshot failed: {e}")
    try:
        # Dump ALL frames, not just top-level page
        combined_html = ""
        for i, fr in enumerate(all_frames(page)):
            try:
                combined_html += f"\n<!-- === FRAME {i}: {fr.url} === -->\n"
                combined_html += fr.content()
            except:
                combined_html += f"\n<!-- === FRAME {i}: FAILED TO READ === -->\n"
        with open(os.path.join(debug_dir, f"ows_debug_{safe}_{ts}.html"), "w", encoding="utf-8") as f:
            f.write(combined_html)
    except Exception as e:
        log(f"HTML dump failed: {e}")

def daily_log_dir() -> str:
    """Return today's log directory (logs/YYYY-MM-DD/), creating it if needed."""
    today = datetime.now().strftime("%Y-%m-%d")
    d = os.path.join(LOG_BASE, today)
    os.makedirs(d, exist_ok=True)
    return d


def log_result(ro: str, status: str) -> None:
    """Append RO result to daily rolling log file (logs/YYYY-MM-DD/ows_log.csv)."""
    log_dir = daily_log_dir()
    log_file = os.path.join(log_dir, "ows_log.csv")
    write_header = not os.path.exists(log_file)
    with open(log_file, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if write_header:
            writer.writerow(["timestamp", "ro", "line", "status"])
        # Split "513137-02" into ro_num and line_num
        parts = ro.split("-", 1)
        ro_num = parts[0]
        line_num = parts[1] if len(parts) > 1 else ""
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        writer.writerow([ts, ro_num, line_num, status])
    log(f"Result logged to {log_file}: {ro} - {status}")
# ── Frame helpers ─────────────────────────────────────────────────────────────
def all_frames(page: Page) -> list[Frame]:
    return [page.main_frame] + list(page.frames)
def find_csr_frame(page: Page) -> Frame:
    """
    Find the Claim Status Report frame.
    Strategy 1: URL contains 'ClaimStatusReport'
    Strategy 2: Any frame that has the RO input visible
    """
    # log("Locating Claim Status Report frame …")
    deadline = time.time() + DEFAULT_TIMEOUT_MS / 1000
    while time.time() < deadline:
        for fr in all_frames(page):
            url = (fr.url or "").lower()
            if "claimstatusreport" in url:
                # log(f"CSR frame found by URL: {fr.url}")
                return fr
        for fr in all_frames(page):
            try:
                if fr.locator(RO_INPUT_SEL).count() > 0:
                    log(f"CSR frame found by RO input in: {fr.url}")
                    return fr
            except Exception:
                continue
        time.sleep(0.3)
    raise RuntimeError("Could not find Claim Status Report frame.")
# ── Step 1 – Enter RO and click Inquire ──────────────────────────────────────
def enter_ro_and_inquire(csr: Frame, ro: str) -> str:
    """Enter RO number and click Inquire. Returns 'found' or 'already_paid'."""
    # log(f"Entering RO #{ro} …")
    ro_input = csr.locator(RO_INPUT_SEL)
    try:
        ro_input.wait_for(state="visible", timeout=5000)
    except PWTimeoutError:
        log("RO input not visible — closing stale claim tab and retrying …")
        close_claim_tab(csr.page)
        time.sleep(1.0)
        ro_input.wait_for(state="visible", timeout=DEFAULT_TIMEOUT_MS)
    ro_input.click(click_count=3)    # triple-click to select all existing text
    ro_input.fill(ro)
    time.sleep(0.3)
    # log("Clicking Inquire …")
    inquire = csr.locator(INQUIRE_BTN_SEL).first
    inquire.wait_for(state="visible", timeout=DEFAULT_TIMEOUT_MS)
    inquire.click()
    # Wait for at least one result row containing the RO number
    # log("Waiting for result rows …")
    result_row = csr.locator(
        f"{CLAIM_ROW_SEL} td:has-text('{ro}')"
    ).first
    try:
        result_row.wait_for(state="visible", timeout=15_000)
        # log("Results loaded.")
        return "found"
    except PWTimeoutError:
        # No Work In Process rows — check if RO is already paid
        # log("No Work In Process rows. Checking for already-paid status …")
        time.sleep(1.0)
        all_rows = csr.locator("tr").all()
        for row in all_rows:
            try:
                row_text = row.inner_text().strip()
                if ro in row_text and "paid" in row_text.lower():
                    log(f"RO {ro} is already Paid.")
                    return "already_paid"
            except Exception:
                continue
        # No paid row found either — raise the original timeout
        raise PWTimeoutError(f"No claim rows found for RO {ro} (not in WIP or Paid)")
# ── Step 2 – Single-click the claim row (opens claim via OpenWO) ──────────────
def click_claim_row(page: Page, csr: Frame, ro: str, skip_lines: set[str] = None, target_line: str = None) -> str:
    """
    Click the first claim row for this RO with 'Dealer Action Required' status.
    Skips any line numbers in skip_lines.
    If target_line is set, only click that specific line.
    Returns the repair line number that was clicked.
    """
    if skip_lines is None:
        skip_lines = set()
    # log(f"Clicking claim row for RO {ro} with 'Dealer Action Required' status …")
    # Only click rows with "Dealer Action Required" status
    rows = csr.locator(f"{CLAIM_ROW_SEL}").all()
    row = None
    line_num = "??"
    for r in rows:
        try:
            row_text = r.inner_text().strip().lower()
            if ro in row_text and "dealer action required" in row_text:
                # Extract line number (2nd column)
                cells = r.locator("td").all()
                if len(cells) >= 2:
                    line_num = cells[1].inner_text().strip()
                    if line_num in skip_lines:
                        log(f"Skipping already-processed line {line_num}")
                        continue
                    if target_line and line_num.lstrip("0") != target_line.lstrip("0"):
                        log(f"Skipping line {line_num} (targeting line {target_line})")
                        continue
                row = r
                break
        except:
            continue
    if not row:
        # If targeting a specific line, check what status it has
        if target_line:
            for r in rows:
                try:
                    cells = r.locator("td").all()
                    if len(cells) >= 2:
                        ln = cells[1].inner_text().strip()
                        if ln.lstrip("0") == target_line.lstrip("0"):
                            row_text = r.inner_text().strip()
                            # Status is column index 8
                            status = cells[8].inner_text().strip() if len(cells) >= 9 else "unknown"
                            log(f"Line {ln} found with status: '{status}' — skipping.")
                            log_result(f"{ro}-{ln}", f"Skipped: {status}")
                            break
                except:
                    continue
        return None  # No more claims to process
    # log(f"Found repair line {line_num}")
    row.wait_for(state="visible", timeout=DEFAULT_TIMEOUT_MS)
    # Snapshot current frames before clicking so we can detect new ones
    before_frames = {fr.url for fr in all_frames(page) if fr.url}
    # log(f"Triple-clicking row …")
    row.click(click_count=3)

    # Poll until a new frame/tab appears or claim content is detected
    deadline = time.time() + 30
    while time.time() < deadline:
        time.sleep(0.5)
        # Check for a new frame (Pega opens claim in a new TABTHREAD)
        for fr in all_frames(page):
            try:
                if fr.url and fr.url not in before_frames and "TABTHREAD" in fr.url:
                    # log("New claim tab detected.")
                    return line_num
            except Exception:
                continue
        # Also check for claim content appearing anywhere
        for fr in all_frames(page):
            try:
                if fr.locator("button:has-text('Open Claim')").count() > 0:
                    # log("Claim opened (Open Claim button visible).")
                    return line_num
                if fr.locator("text=Claim Entry Screen").count() > 0:
                    # log("Claim opened (Claim Entry Screen visible).")
                    return line_num
                if fr.locator("text=/CU-\\d+/").count() > 0:
                    # log("Claim opened (CU- header visible).")
                    return line_num
            except Exception:
                continue
    log("WARNING: Claim may not have opened after 30 seconds.")
    return line_num
# ── Step 3 – Find the newly opened claim frame ────────────────────────────────
def wait_for_claim_frame(page: Page, known_urls: set[str]) -> Frame:
    """
    After clicking the row, Pega opens the claim in a new frame/tab section.
    We detect it by scoring every frame and picking the best match.

    Scoring (higher = better):
      +4  Submit button present (SubmitClaimUnit onclick)
      +4  PreValidate button present
      +3  "Claim Entry Screen" text present
      +3  Pre-validation message present
      +3  Parts grid present (tr[id*="pParts"])
      +1  CU-NNNN header present  (lowest weight — appears in nav frames too)

    We wait until any frame scores >= 4, then return the highest-scored frame.
    This prevents the nav/tab-strip frame (which shows CU-NNNN in a breadcrumb)
    from being mistakenly selected over the actual claim editing frame.
    """
    # log("Waiting for claim frame to load …")
    deadline = time.time() + DEFAULT_TIMEOUT_MS / 1000
    while time.time() < deadline:
        best_score = 0
        best_frame = None
        for fr in all_frames(page):
            try:
                score = 0
                if fr.locator("button[onclick*='SubmitClaimUnit']").count() > 0:
                    score += 4
                if fr.locator("button[onclick*='PreValidate']").count() > 0:
                    score += 4
                if fr.locator("text=Claim Entry Screen").count() > 0:
                    score += 3
                if fr.locator(f"text={PREVALIDATION_TEXT}").count() > 0:
                    score += 3
                if fr.locator('tr[id*="pRepairDetails$pParts$l"]').count() > 0:
                    score += 3
                if fr.locator("button:has-text('Open Claim')").count() > 0:
                    score += 3
                if fr.locator("text=/CU-\\d+/").count() > 0:
                    score += 1
                if score > best_score:
                    best_score = score
                    best_frame = fr
            except Exception:
                continue
        if best_score >= 4 and best_frame is not None:
            # log(f"Claim frame found (score={best_score}): {best_frame.url[:80]}...")
            return best_frame
        time.sleep(0.3)
    # Fallback: return any frame with TABTHREAD1 or higher (new tab)
    for fr in all_frames(page):
        if fr.url and "TABTHREAD" in fr.url and "TABTHREAD0" not in fr.url:
            log(f"Claim frame (fallback): {fr.url[:80]}...")
            return fr
    raise RuntimeError("Timed out waiting for claim frame after clicking row.")
# ── Step 3b – Click "Open Claim" button to enter claim edit mode ─────────────
def click_open_claim(page: Page) -> None:
    """
    After double-clicking the row, we land on a claim summary page with an
    'Open Claim' button. Click it to enter the full claim entry screen.
    """
    # log("Looking for 'Open Claim' button …")
    # Search all frames for the Open Claim button
    for fr in all_frames(page):
        try:
            open_btn = fr.locator("button:has-text('Open Claim')").first
            if open_btn.count() > 0 and open_btn.is_visible():
                # log("Clicking 'Open Claim' …")
                open_btn.click()
                time.sleep(2.0)  # Wait for claim entry screen to load
                # log("Claim entry screen opened.")
                return
        except Exception:
            continue
    # log("'Open Claim' button not found — may already be in edit mode.")

# ── Step 3c – Click PreValidate button ────────────────────────────────────────
def click_prevalidate(page: Page) -> bool:
    """Click the PreValidate button to re-run pre-validation after fixing errors."""
    # log("Clicking PreValidate …")
    for fr in all_frames(page):
        try:
            btn = fr.locator(PREVALIDATE_BTN_SEL).first
            if btn.count() > 0 and btn.is_visible():
                btn.scroll_into_view_if_needed()
                btn.click()
                # log("PreValidate button clicked. Waiting for validation …")
                time.sleep(2.0)  # Brief wait; caller polls for results
                return True
        except Exception:
            continue
    # log("PreValidate button not found.")
    return False

# ── Step 4 – Check errors, check pre-validation, submit ──────────────────────
def scrape_claim_errors(page: Page) -> dict:
    """
    Scrape all visible errors/messages from the currently open claim.
    Captures DEC codes, SFRU codes, and any other message codes in the
    message area where DEC0007 normally appears.
    Returns a dict with error details for diagnosis.
    """
    html = ""
    for fr in all_frames(page):
        try:
            html += fr.content()
        except:
            pass

    # Capture all message codes: DEC####, SFRU###, or any UPPER+DIGITS pattern
    # that appears with a " - " separator (standard OWS message format)
    all_messages = re.findall(r"([A-Z]{2,6}\d{2,5}\s*-\s*[^<\"]{0,300})", html)
    all_codes = list(set(re.findall(r"[A-Z]{2,6}\d{2,5}", " ".join(all_messages))))

    # Check lock status
    is_locked = "is currently being modified" in html

    # Part numbers / non-error codes that the regex picks up as false positives
    FALSE_POSITIVE_CODES = {"MHT7000", "CPR0126", "REH52"}

    # Separate DEC0007 (success) from actual errors
    has_dec0007 = any(c.upper() == "DEC0007" for c in all_codes)
    error_codes = [c for c in all_codes if c.upper() != "DEC0007" and c.upper() not in FALSE_POSITIVE_CODES and not c.upper().startswith("ESPA")]

    return {
        "all_codes": all_codes,
        "all_messages": [m.strip() for m in all_messages],
        "error_codes": error_codes,
        "is_locked": is_locked,
        "has_dec0007": has_dec0007,
    }


def check_and_submit(page: Page, claim: Frame) -> tuple[str, dict]:
    """
    1. Check if claim is locked by Ford's automated system
    2. Scan HTML for error messages
    3. Confirm DEC0007 pre-validation success text is present.
    4. Click Submit.
    5. Confirm "The repair line has been submitted" is shown.
    Returns: (status, error_details) where status is "submitted", "locked",
             "error" (pre-submit), or "submit_error" (post-submit, need Open Claim)
    """
    # log("Scanning claim HTML …")
    time.sleep(1.5)

    # Scrape all error details from the claim
    details = scrape_claim_errors(page)

    # Check if locked by Ford's automated system
    if details["is_locked"]:
        log("Claim is locked by Ford's automated system.")
        return "locked", details

    # Look for error indicators (DEC####, SFRU###, etc.)
    if details["error_codes"]:
        log(f"ERROR CODES FOUND: {set(details['error_codes'])}")
        for msg in details["all_messages"]:
            log(f"  → {msg}")
        return "error", details
    log("No error codes detected.")

    # Check for pre-validation success — if missing, click PreValidate and re-scrape
    if not details["has_dec0007"]:
        log("Pre-validation message NOT found — clicking PreValidate …")
        from ows_fixes import click_prevalidate, scrape_errors_quick
        click_prevalidate(page)
        # Poll for validation result (DEC0007 or new errors) up to 20s
        deadline = time.time() + 20
        while time.time() < deadline:
            details = scrape_claim_errors(page)
            if details["has_dec0007"] or details["error_codes"]:
                break
            time.sleep(1.0)
        if details["error_codes"]:
            log(f"ERROR CODES FOUND after PreValidate: {set(details['error_codes'])}")
            for msg in details["all_messages"]:
                log(f"  → {msg}")
            return "error", details
        if not details["has_dec0007"]:
            log("Pre-validation message still NOT found after PreValidate.")
            return "error", details
    log("Pre-validation success confirmed. Clicking Submit …")
    # Find Submit button in any frame
    for fr in all_frames(page):
        try:
            submit_btn = fr.locator(SUBMIT_BTN_SEL).first
            if submit_btn.count() > 0 and submit_btn.is_visible():
                submit_btn.scroll_into_view_if_needed()
                submit_btn.click()
                # log("Submit button clicked.")
                break
        except:
            continue

    # Wait for submission confirmation page to load
    # log("Waiting for submission confirmation …")
    deadline = time.time() + DEFAULT_TIMEOUT_MS / 1000
    submitted = False
    while time.time() < deadline:
        post_html = ""
        for fr in all_frames(page):
            try:
                post_html += fr.content()
            except:
                pass
        if SUBMITTED_TEXT in post_html:
            log(f"SUCCESS: '{SUBMITTED_TEXT}' confirmed.")
            return "submitted", details
        # Check for post-submit errors (e.g. BES0027)
        post_details = scrape_claim_errors(page)
        if post_details["error_codes"]:
            log(f"POST-SUBMIT ERRORS detected: {set(post_details['error_codes'])}")
            for msg in post_details["all_messages"]:
                log(f"  → {msg}")
            return "submit_error", post_details
        time.sleep(0.5)

    log(f"WARNING: '{SUBMITTED_TEXT}' not found after waiting.")
    return "error", details
# ── Step 5 – Close the claim tab ──────────────────────────────────────────────
def close_claim_tab(page: Page) -> None:
    """
    Click the close (✕) button on the claim tab in the OWS tab strip.
    The close button is next to the CU- number in the tab.
    """
    # log("Closing claim tab …")
    try:
        # Find the tab with CU- or CF- in it and click its close button
        tabs = page.locator("li").all()
        for tab in tabs:
            try:
                text = tab.inner_text(timeout=1000)
                if "CU-" in text or "CF-" in text:
                    close_icon = tab.locator(".close, [class*=close]").first
                    if close_icon.count() > 0:
                        close_icon.click()
                        time.sleep(0.8)
                        # log("Claim tab closed.")
                        return
            except Exception:
                continue
        # Fallback: try any visible close button
        close_btn = page.locator(
            "button.container-close:visible, "
            "button[title*='close']:visible"
        ).first
        close_btn.wait_for(state="visible", timeout=SHORT_TIMEOUT_MS)
        close_btn.click()
        time.sleep(0.8)
        # log("Claim tab closed (fallback).")
    except Exception as e:
        log(f"Close button not found — {e}")
# ── Helper – Check if more claims exist for this RO ──────────────────────────
def has_more_claims_for_ro(csr: Frame, ro: str, skip_lines: set[str] = None) -> bool:
    """Check if there are more claims for this RO with 'Dealer Action Required' status."""
    if skip_lines is None:
        skip_lines = set()
    try:
        # Look for rows in Work In Process that contain the RO and "Dealer Action Required"
        rows = csr.locator(f"{CLAIM_ROW_SEL}").all()
        for row in rows:
            row_text = row.inner_text().strip().lower()
            if ro in row_text and "dealer action required" in row_text:
                # Check line number
                cells = row.locator("td").all()
                if len(cells) >= 2:
                    line_num = cells[1].inner_text().strip()
                    if line_num in skip_lines:
                        continue  # Already processed this one
                log(f"  Found additional claim for RO {ro} with 'Dealer Action Required' status")
                return True
    except Exception:
        pass
    return False

# ── Step 6 – Re-inquire and poll for 'Paid' status ───────────────────────────
def poll_for_paid_status(csr: Frame, ro: str, line_num: str = None) -> str:
    """
    Click Inquire again and check if status shows 'Paid'.
    If line_num is given, only match rows for that specific repair line.
    Returns: "paid", "stuck", "manual_review", or "processing"
    Polls for max POLL_TIMEOUT_S seconds before giving up.
    """
    label = f"{ro}-{line_num}" if line_num else ro
    log(f"Re-inquiring for RO {label} to check Paid status (max {POLL_TIMEOUT_S}s) …")
    deadline = time.time() + POLL_TIMEOUT_S
    attempt = 0
    while time.time() < deadline:
        attempt += 1
        # log(f"Poll attempt {attempt} …")
        inquire = csr.locator(INQUIRE_BTN_SEL).first
        inquire.wait_for(state="visible", timeout=DEFAULT_TIMEOUT_MS)
        inquire.click()
        time.sleep(1.5)  # Wait for results to reload

        # Check ALL table rows (both Work In Process and Dispositioned sections)
        all_rows = csr.locator("tr").all()
        for row in all_rows:
            try:
                row_text = row.inner_text().strip()
                if ro not in row_text:
                    continue
                # If targeting a specific line, check that the line number matches
                if line_num:
                    cells = row.locator("td").all()
                    if len(cells) >= 2:
                        line_cell = cells[1].inner_text().strip().lstrip("0")
                        if line_cell != line_num.lstrip("0"):
                            continue
                row_lower = row_text.lower()
                if "paid" in row_lower:
                    log(f"  Found {label} with PAID status!")
                    return "paid"
                if "manual review" in row_lower:
                    log(f"  Found {label} with MANUAL REVIEW status.")
                    return "manual_review"
            except Exception:
                continue
        time.sleep(POLL_INTERVAL_S)
    log(f"Timed out after {POLL_TIMEOUT_S}s — status still not 'Paid'.")
    return "stuck"
# ── Main ──────────────────────────────────────────────────────────────────────
def process_ro(ro: str, target_line: str = None) -> None:
    log(f"\\n{'='*60}")
    log(f"Processing RO: {ro}")
    log(f"{'='*60}")
    pw = sync_playwright().start()
    try:
        browser = pw.chromium.connect_over_cdp(CDP_URL)
        if not browser.contexts:
            die("No browser contexts found. Start Chromium with --remote-debugging-port=9222.")
        context = browser.contexts[0]
        # Pick best OWS tab
        page = None
        best_score = -1
        for p in context.pages:
            u = (p.url or "").lower()
            t = ""
            try: t = p.title().lower()
            except Exception: pass
            score = sum(
                3 * (tok in u) + 2 * (tok in t)
                for tok in ["warrantyprocessing", "dealerconnection", "prweb", "ows"]
            ) + (1 if u and u != "about:blank" else 0)
            if score > best_score:
                best_score, page = score, p
        if not page:
            die("No OWS tab found.")
        page.set_default_timeout(DEFAULT_TIMEOUT_MS)
        # log(f"Using tab: '{page.title()}' — {page.url}")
        # ── Step 1 ───────────────────────────────────────────────────────────
        csr = find_csr_frame(page)
        inquire_status = enter_ro_and_inquire(csr, ro)

        if inquire_status == "already_paid":
            log(f"RO {ro} is already Paid — skipping.")
            log_result(ro, "Already Paid")
            return

        # Loop to process all repair lines for this RO
        processed_lines = set()  # Track lines we've already tried
        while True:
            # ── Step 2 ───────────────────────────────────────────────────────
            time.sleep(2.0)  # Wait after inquire before clicking row
            before_urls = {fr.url for fr in all_frames(page) if fr.url}
            line_num = click_claim_row(page, csr, ro, skip_lines=processed_lines, target_line=target_line)
            if line_num is None:
                log(f"No more claims to process for RO {ro}.")
                break
            processed_lines.add(line_num)
            claim_id = f"{ro}-{line_num}"
            log(f"Processing claim {claim_id} …")
            # ── Step 3 ───────────────────────────────────────────────────────
            claim_frame = wait_for_claim_frame(page, before_urls)
            time.sleep(2.0)  # Wait for claim to fully load
            # ── Step 3b ──────────────────────────────────────────────────────
            click_open_claim(page)
            time.sleep(2.0)  # Wait after opening claim before submit
            # ── Step 4 ───────────────────────────────────────────────────────
            result, details = check_and_submit(page, claim_frame)
            if result == "locked":
                # Ford's system is processing - close and retry up to 3 times
                for lock_attempt in range(1, 4):
                    log(f"Claim locked by Ford system. Closing and retrying ({lock_attempt}/3) in 5s…")
                    close_claim_tab(page)
                    time.sleep(5.0)
                    try:
                        before_urls = {fr.url for fr in all_frames(page) if fr.url}
                        click_claim_row(page, csr, ro, target_line=line_num)
                        claim_frame = wait_for_claim_frame(page, before_urls)
                        time.sleep(2.0)
                        click_open_claim(page)
                        time.sleep(2.0)
                        result, details = check_and_submit(page, claim_frame)
                        if result != "locked":
                            break
                    except Exception as e:
                        log(f"Retry {lock_attempt} failed: {e}")
                        if lock_attempt == 3:
                            result, details = "locked", {"error_codes": [], "all_messages": [], "is_locked": True, "has_dec0007": False}
                if result != "submitted":
                    if details.get("is_locked"):
                        # Still locked after all retries — skip
                        log("Claim still locked after retries — skipping.")
                        save_debug(page, f"locked_{claim_id}")
                        log_result(claim_id, "User review: still locked")
                        close_claim_tab(page)
                        continue
                    # Claim unlocked but has errors — fall through to normal error handling
                    result = "error"
            elif result == "submit_error":
                # Post-submit error — claim reverted, need to re-open before fixing
                log("Post-submit error detected. Re-opening claim for error handling …")
                click_open_claim(page)
                time.sleep(2.0)
                result = "error"  # fall through to normal error handling

            if result == "error":
                error_summary = ", ".join(details["error_codes"]) if details["error_codes"] else "no DEC0007"
                log(f"Claim has error ({error_summary}). Details:")
                for msg in details["all_messages"]:
                    log(f"  → {msg}")
                # Attempt auto-fixes for known errors
                if details["error_codes"]:
                    fixed, post_details = try_fix_errors(page, claim_frame, details["error_codes"])
                    if fixed and post_details and post_details["has_dec0007"] and not post_details["error_codes"]:
                        # Fix worked, PreValidate passed — now submit
                        log(f"Auto-fixed: {fixed}. PreValidate passed. Submitting …")
                        result, details = check_and_submit(page, claim_frame)
                        if result == "submitted":
                            log(f"Claim {claim_id} submitted after auto-fix!")
                            close_claim_tab(page)
                            status = poll_for_paid_status(csr, ro, line_num)
                            if status == "paid":
                                log_result(claim_id, f"Submitted & Paid (auto-fixed: {', '.join(fixed)})")
                                subprocess.Popen(["notify-send", "A claim has paid!"])
                                subprocess.Popen(["mpv", "--no-video", os.path.join(SCRIPT_DIR, "assets", "paid.mp3")], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                            elif status == "manual_review":
                                log_result(claim_id, f"Submitted → Manual Review (auto-fixed: {', '.join(fixed)})")
                            else:
                                log_result(claim_id, f"Submitted (auto-fixed: {', '.join(fixed)}), polling: {status}")
                            if has_more_claims_for_ro(csr, ro, processed_lines):
                                continue
                            else:
                                break
                    # Update details to post-fix state so remaining-error check uses current codes
                    if fixed and post_details:
                        details = post_details
                        # If fix cleared errors but DEC0007 missing, retry PreValidate
                        if not details["error_codes"] and not details["has_dec0007"]:
                            log("Errors cleared but DEC0007 missing — retrying PreValidate …")
                            from ows_fixes import click_prevalidate, scrape_errors_quick
                            time.sleep(3.0)
                            click_prevalidate(page)
                            # Poll for validation result up to 20s
                            deadline = time.time() + 20
                            while time.time() < deadline:
                                details = scrape_claim_errors(page)
                                if details["has_dec0007"] or details["error_codes"]:
                                    break
                                time.sleep(1.0)
                            if details["has_dec0007"] and not details["error_codes"]:
                                log("DEC0007 confirmed on retry. Submitting …")
                                result, details = check_and_submit(page, claim_frame)
                                if result == "submitted":
                                    log(f"Claim {claim_id} submitted after retry!")
                                    close_claim_tab(page)
                                    status = poll_for_paid_status(csr, ro, line_num)
                                    if status == "paid":
                                        log_result(claim_id, f"Submitted & Paid (auto-fixed: {', '.join(fixed)})")
                                        subprocess.Popen(["notify-send", "A claim has paid!"])
                                        subprocess.Popen(["mpv", "--no-video", os.path.join(SCRIPT_DIR, "assets", "paid.mp3")], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                                    elif status == "manual_review":
                                        log_result(claim_id, f"Submitted → Manual Review (auto-fixed: {', '.join(fixed)})")
                                    else:
                                        log_result(claim_id, f"Submitted (auto-fixed: {', '.join(fixed)}), polling: {status}")
                                    if has_more_claims_for_ro(csr, ro, processed_lines):
                                        continue
                                    else:
                                        break
                        error_summary = ", ".join(details["error_codes"]) if details["error_codes"] else "no DEC0007"
                # Check if all errors are user-review-only
                unresolved = set(details["error_codes"]) - USER_REVIEW_ERRORS
                if not unresolved:
                    notes = [USER_REVIEW_NOTES[c] for c in details["error_codes"] if c in USER_REVIEW_NOTES]
                    note_str = f" ({'; '.join(notes)})" if notes else ""
                    log(f"User review needed for {claim_id}: {error_summary}{note_str} — skipping.")
                    save_debug(page, f"error_{claim_id}")
                    log_result(claim_id, f"User review: {error_summary}{note_str}")
                    close_claim_tab(page)
                    continue
                # Unknown error — stop batch so user can decide how to handle
                save_debug(page, f"error_{claim_id}")
                log(f"\n{'='*60}")
                log(f"UNKNOWN ERROR on {claim_id}: {error_summary}")
                for msg in details["all_messages"]:
                    log(f"  → {msg}")
                log(f"{'='*60}")
                log_result(claim_id, f"Unknown error: {error_summary}")
                close_claim_tab(page)
                die(f"UNKNOWN ERROR on {claim_id}: {error_summary} — stopping for user input.")
            # ── Step 5 ───────────────────────────────────────────────────────
            close_claim_tab(page)
            # ── Step 6 ───────────────────────────────────────────────────────
            status = poll_for_paid_status(csr, ro, line_num)
            if status == "paid":
                log_result(claim_id, "Submitted & Paid")
                subprocess.Popen(["notify-send", "A claim has paid!"])
                subprocess.Popen(["mpv", "--no-video", os.path.join(SCRIPT_DIR, "assets", "paid.mp3")], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            elif status == "manual_review":
                log_result(claim_id, "Submitted → Manual Review")
            else:
                log_result(claim_id, "Stuck in Processing")
                # Check if there are more repair lines for this RO
                if has_more_claims_for_ro(csr, ro, processed_lines):
                    log(f"More repair lines found for RO {ro}. Processing next line…")
                    continue
                else:
                    break  # Move on to next RO
    except PWTimeoutError as e:
        try: save_debug(page, "timeout")
        except Exception: pass
        die(f"Timeout: {e}")
    except Exception as e:
        try: save_debug(page, "error")
        except Exception: pass
        die(f"Error: {e}")
    finally:
        try: pw.stop()
        except Exception: pass
def load_ro_list(filepath: str) -> list[str]:
    """Load RO numbers from a text file, deduplicate, preserve order."""
    seen = set()
    ros = []
    with open(filepath, "r", encoding="utf-8") as f:
        for line in f:
            ro = line.strip()
            if ro.isdigit() and ro not in seen:
                seen.add(ro)
                ros.append(ro)
    return ros


def remove_ro_from_file(filepath: str, ro: str) -> None:
    """Remove all occurrences of an RO number from the file."""
    with open(filepath, "r", encoding="utf-8") as f:
        lines = f.readlines()
    with open(filepath, "w", encoding="utf-8") as f:
        for line in lines:
            if line.strip() != ro:
                f.write(line)


def main() -> None:
    arg = (sys.argv[1].strip() if len(sys.argv) >= 2 else "").strip()

    # Check if argument is a file path
    if arg and os.path.isfile(arg):
        ros = load_ro_list(arg)
        if not ros:
            die(f"No valid RO numbers found in {arg}.")
        # Save original list to today's log directory, work off a copy
        log_dir = daily_log_dir()
        original_dest = os.path.join(log_dir, os.path.basename(arg))
        if not os.path.exists(original_dest):
            shutil.copy2(arg, original_dest)
        working_copy = os.path.join(log_dir, "remaining.txt")
        if os.path.abspath(arg) != os.path.abspath(working_copy):
            shutil.copy2(arg, working_copy)
        log(f"Loaded {len(ros)} unique RO numbers from {arg}")
        for i, ro in enumerate(ros, 1):
            log(f"\n>>> RO {i}/{len(ros)}: {ro}")
            try:
                process_ro(ro)
            except SystemExit:
                # Unknown error hit — script is stopping for user input
                remove_ro_from_file(working_copy, ro)
                raise
            except Exception as e:
                log(f"RO {ro} failed with error: {e}. Continuing …")
            # Remove this RO from the working copy (original stays intact)
            remove_ro_from_file(working_copy, ro)
        log(f"\nAll done. Processed {len(ros)} ROs. Check {log_dir} for results.")
    else:
        # Single RO mode — supports "510689" or "510689-05" (target specific line)
        ro = arg
        if not ro:
            ro = input("Enter RO number: ").strip()
        target_line = None
        if "-" in ro:
            ro, target_line = ro.rsplit("-", 1)
        if not ro.isdigit():
            die("RO must be numeric (e.g. 513271 or 513271-05).")
        process_ro(ro, target_line=target_line)


if __name__ == "__main__":
    main()
