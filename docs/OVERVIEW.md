# OWS Bot — Overview

## What it is

OWS Bot automates the repetitive part of Ford warranty claim administration: opening each "Dealer Action Required" claim in OWS (Ford's Online Warranty System, a Pega web app), checking it for validation errors, fixing the common ones, submitting it, and watching until it pays.

It is **not** a headless scraper. It attaches to a real Chromium browser that *you* have already logged into (via the Chrome DevTools Protocol), and drives the same pages you would click through by hand. That means:

- No credentials are stored or automated — you handle login and 2FA yourself.
- You can watch it work in the browser window, and take over at any time.
- If it hits something it doesn't understand, it stops, saves a screenshot + HTML dump, and tells you.

## How a claim is processed

For each repair order (RO) number you give it:

1. **Inquire** — types the RO into the Claim Status Report and clicks *Inquire*.
   If the RO is already Paid, it's logged and skipped.
2. **Open claim** — finds the first repair line with *Dealer Action Required*
   status, clicks the row, and waits for the claim tab to load. Clicks
   *Open Claim* to enter edit mode.
3. **Scan for errors** — scrapes every frame for OWS message codes
   (`DEC0007` = pre-validation success; anything else like `SUB0006`,
   `LAB0019` is an error). Also detects claims locked by Ford's automated
   system (retried 3× with a delay).
4. **Auto-fix** — for each error code with a registered fix in
   `ows_fixes.py`, the fix runs (fill a field, delete a row, change claim
   type, …), then *PreValidate* is clicked and errors are re-scanned. This
   loops bottom-up through the errors for up to 5 rounds.
5. **Submit** — once `DEC0007` is present with no errors, clicks *Submit*
   and confirms "The repair line has been submitted".
6. **Poll for Paid** — re-inquires and polls the status until it shows
   *Paid* (plays a sound + desktop notification), *Manual Review*, or times
   out ("Stuck in Processing").
7. **Next line / next RO** — repeats for the RO's remaining repair lines,
   then moves to the next RO in the batch.

### Error triage outcomes

| Outcome | What happens |
|---|---|
| Error has a registered auto-fix | Fix applied, PreValidate re-run, submit if clean |
| Error is in the `[user_review]` list (config.toml) | Claim skipped, logged as "User review: CODE" — batch continues |
| Unknown error | Screenshot + HTML dump saved, batch **stops** so you can decide (and [add a fix](ADDING_FIXES.md)) |

Every outcome is appended to `logs/YYYY-MM-DD/ows_log.csv`.

## Project layout

```
ows_bot.py       — main loop: attach → inquire → open → fix → submit → poll
ows_fixes.py     — one function per error code + ERROR_FIXES registry
ows_config.py    — loads config.toml / config.local.toml / env into CONFIG
config.toml      — all tunable data (technicians, recalls, error lists, …)
harness.py       — interactive debug shell (live or against saved dumps)
close_claim.py   — one-shot utility: close whatever claim tab is open
diagnose_parts_grid.py — deep DOM diagnostic for the parts grid
logs/YYYY-MM-DD/ — daily results CSV, remaining-RO list, debug dumps
```

## Key concepts

- **Frames** — OWS is a Pega app made of many nested iframes. Almost every
  helper loops over `all_frames(page)` and tries selectors in each frame,
  because the claim form, message area, and tab strip live in different
  frames whose URLs change between sessions.
- **Message codes** — OWS communicates through codes like `DEC0007 - CLAIM
  PASSED PRE-VALIDATION` or `LAB0019 - TECHNICIAN IDENTIFICATION IS
  REQUIRED…`. The scraper regex-matches `[A-Z]{2,6}\d{2,5}` patterns with a
  ` - ` separator across all frame HTML.
- **PreValidate** — after any fix, the *PreValidate* button re-runs Ford's
  validation server-side. Completion is detected by watching for message-code
  changes in the DOM.
