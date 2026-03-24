#!/usr/bin/env python3
"""
Close the currently open claim tab in OWS.
Usage:
    python3 close_claim.py
"""
from __future__ import annotations
import time
from playwright.sync_api import sync_playwright

CDP_URL = "http://127.0.0.1:9222"


def log(msg: str) -> None:
    print(f"[OWS] {msg}", flush=True)


def main() -> None:
    pw = sync_playwright().start()
    try:
        browser = pw.chromium.connect_over_cdp(CDP_URL)
        context = browser.contexts[0]

        # Find OWS page
        page = None
        best_score = -1
        for p in context.pages:
            u = (p.url or "").lower()
            t = ""
            try: t = p.title().lower()
            except: pass
            score = sum(
                3 * (tok in u) + 2 * (tok in t)
                for tok in ["warrantyprocessing", "dealerconnection", "prweb", "ows"]
            ) + (1 if u and u != "about:blank" else 0)
            if score > best_score:
                best_score, page = score, p

        if not page:
            log("No OWS tab found.")
            return

        # Find claim tab (CU- or CF-) and click its close button
        tabs = page.locator("li").all()
        for tab in tabs:
            try:
                text = tab.inner_text(timeout=1000)
                if "CU-" in text or "CF-" in text:
                    close_icon = tab.locator(".close, [class*=close]").first
                    if close_icon.count() > 0:
                        close_icon.click()
                        time.sleep(0.8)
                        log(f"Closed claim tab: {text.strip()[:40]}")
                        return
            except Exception:
                continue

        # Fallback: try any visible close button
        try:
            close_btn = page.locator(
                "button.container-close:visible, "
                "button[title*='close']:visible"
            ).first
            close_btn.click()
            time.sleep(0.8)
            log("Closed claim tab (fallback).")
        except Exception:
            log("No claim tab found to close.")
    finally:
        pw.stop()


if __name__ == "__main__":
    main()
