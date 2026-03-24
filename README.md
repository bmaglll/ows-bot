# OWS Bot — Warranty Claim Automation

Automated warranty claim submission tool built with Playwright. Connects to a running Chromium browser via CDP (Chrome DevTools Protocol), navigates the OWS (Online Warranty System), and processes claims end-to-end — submitting clean claims, auto-fixing common errors, and triaging unresolvable issues with categorized logging.

## Features

- **Batch processing** — feed a list of repair order numbers and walk away
- **Auto-fix engine** — detects and resolves 20+ known error codes automatically
- **Error triage** — categorizes and logs unresolvable claims with specific error codes for faster manual resolution
- **Status polling** — monitors submitted claims until paid, flagging manual review or stuck cases
- **Debug capture** — saves screenshots and HTML dumps on failure for diagnostics

## Prerequisites

- Python 3.12+ with `playwright`
- Chromium launched with remote debugging:
  ```bash
  chromium --remote-debugging-port=9222
  ```
- Logged into OWS with the Claim Status Report page open

### Install Dependencies

```bash
pip install playwright
```

#### Auto Condition Code / Customer Code (optional)

The ROV0068 error fix uses the Anthropic API as a fallback to determine the correct Condition Code when lookup tables don't have a match. To enable this:

```bash
pip install anthropic
export ANTHROPIC_API_KEY=your_key_here
```

## Usage

```bash
# Single RO
python3 ows_bot.py 513271

# Target a specific claim line
python3 ows_bot.py 510689-05

# Interactive prompt
python3 ows_bot.py

# Batch — reads from ro_number.txt
python3 ows_bot.py --batch
```

## Logging

Daily logs are written to `logs/YYYY-MM-DD/`:

| File | Description |
|------|-------------|
| `ows_log.csv` | Timestamped outcome for every claim line (Submitted & Paid, User Review, Unknown Error, etc.) |
| `remaining.txt` | Unprocessed ROs remaining in the batch |
| `debug/` | Screenshots and HTML dumps from failures |

## Project Structure

```
ows_bot.py       — main automation script
ows_fixes.py     — error fix registry with 20+ auto-fix handlers
close_claim.py   — utility to close open claim tabs
error-handling.md — error code reference
```
