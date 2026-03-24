# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

OWS Bot - A Playwright automation script that processes warranty claims in the OWS (Online Warranty System) via CDP (Chrome DevTools Protocol). The bot attaches to a running Chromium browser where the user is already logged in.

## Prerequisites

- Python 3.x with Playwright installed (`pip install playwright`)
- Chromium launched with remote debugging: `chromium --remote-debugging-port=9222`
- User must be logged into OWS with Claim Status Report page open

## Running the Bot

```bash
# With RO number as argument
python3 ows_bot.py 513271

# Interactive prompt for RO number
python3 ows_bot.py
```

## Architecture

Single-file script (`ows_bot.py`) with this workflow:

1. **CDP Attach**: Connects to Chromium on `http://127.0.0.1:9222`
2. **Frame Detection**: Finds the Claim Status Report iframe by URL or RO input presence
3. **RO Inquiry**: Enters repair order number and clicks Inquire
4. **Claim Row Click**: Double-clicks the claim row to open it (triggers Pega's `OpenWO()`)
5. **Validation Check**: Scans for error codes (DEC####) and confirms pre-validation success (DEC0007)
6. **Submit**: Clicks Submit if pre-validation passed
7. **Close Tab**: Closes the claim tab in OWS tab strip
8. **Poll for Paid**: Re-inquires and polls until status changes to "Paid"

## Key Selectors (Pega-specific)

- RO Input: `input#RepairOrderNumber`
- Inquire Button: `button:has-text('Inquire')`
- Claim Rows: `tr[id*='ClaimRecords'][id*='ppxResults']`
- Submit Button: `button[onclick*='SubmitClaimUnit']`

## Debugging

On error/timeout, the script saves:
- `ows_debug_<label>_<timestamp>.png` - full page screenshot
- `ows_debug_<label>_<timestamp>.html` - page HTML dump
