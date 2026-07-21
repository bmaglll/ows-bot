# OWS Bot — Warranty Claim Automation

Automated warranty claim submission tool built with Playwright. It attaches to a Chromium browser you're already logged into (via CDP — Chrome DevTools Protocol), navigates the OWS (Online Warranty System) Claim Status Report, and processes claims end-to-end: submitting clean claims, auto-fixing 35+ known error codes, and triaging unresolvable claims with categorized logging.

**Documentation:**

| Doc | What's in it |
|---|---|
| [docs/OVERVIEW.md](docs/OVERVIEW.md) | What the bot is, how it works step-by-step, project layout |
| [docs/WORKFLOW.md](docs/WORKFLOW.md) | The day-to-day loop: run the bot → diagnose unknowns with Claude → bake in fixes |
| [docs/CONFIGURATION.md](docs/CONFIGURATION.md) | Full `config.toml` reference — technicians, recalls, approval codes, etc. |
| [docs/DEBUG_HARNESS.md](docs/DEBUG_HARNESS.md) | Using `harness.py` to debug the bot interactively |
| [docs/MCP_SERVER.md](docs/MCP_SERVER.md) | Driving the harness from an LLM (Claude Code, etc.) via MCP |
| [docs/ADDING_FIXES.md](docs/ADDING_FIXES.md) | Step-by-step guide to adding a new error auto-fix |

## Features

- **Batch processing** — feed a list of repair order numbers and walk away
- **Auto-fix engine** — detects and resolves 35+ known error codes automatically
- **Configurable** — technicians, recall lookups, approval codes, error triage lists, rental rates, and timeouts all live in [`config.toml`](config.toml); no code edits needed
- **Error triage** — categorizes and logs unresolvable claims with specific error codes for faster manual resolution
- **Status polling** — monitors submitted claims until paid, flagging manual review or stuck cases
- **Debug capture** — saves screenshots and HTML dumps on failure for diagnostics
- **Debug harness** — interactive shell (`harness.py`) to inspect claims, test selectors, and develop new fixes against live pages or saved dumps

## Quick Start

### 1. Install

```bash
pip install playwright python-dotenv
pip install anthropic          # optional — AI-assisted condition code/part inference
pip install mcp                # optional — only for the MCP server (docs/MCP_SERVER.md)
```

Requires Python 3.12+.

### 2. Launch Chromium with remote debugging

```bash
chromium --remote-debugging-port=9222
```

Log into OWS and open the **Claim Status Report** page.

### 3. Configure

Edit [`config.toml`](config.toml) and add your technician(s):

```toml
[technicians]
default = "jsmith"

[[technicians.list]]
key = "jsmith"
name = "J. Smith"
stars_id = "002498102"
```

Personal/private values can go in a gitignored `config.local.toml` instead (same format — it overrides `config.toml`). Secrets like `ANTHROPIC_API_KEY` go in `.env` (copy `.env.example`). See [docs/CONFIGURATION.md](docs/CONFIGURATION.md) for everything else you can configure.

### 4. Run

```bash
python3 ows_bot.py 513271          # single RO — all claim lines
python3 ows_bot.py 513271-05       # one specific claim line
python3 ows_bot.py ros.txt         # batch file, one RO number per line
```

## Logging

Daily logs are written to `logs/YYYY-MM-DD/`:

| File | Description |
|------|-------------|
| `ows_log.csv` | Timestamped outcome for every claim line (Submitted & Paid, User Review, Unknown Error, etc.) |
| `remaining.txt` | Unprocessed ROs remaining in the batch |
| `debug/` | Screenshots and HTML dumps from failures |

## Debugging

```bash
python3 harness.py                          # interactive shell attached to the live browser
python3 harness.py --scrape logs/…/dump.html  # what does the error scraper see in a saved dump?
python3 harness.py --list-fixes             # list all registered auto-fixes
```

See [docs/DEBUG_HARNESS.md](docs/DEBUG_HARNESS.md) for the full command reference and [docs/ADDING_FIXES.md](docs/ADDING_FIXES.md) for the workflow to add a fix for a new error code.

An LLM can drive the same harness over the **Model Context Protocol** — attach to the browser, scrape errors, test fixes — via `mcp_server.py`. See [docs/MCP_SERVER.md](docs/MCP_SERVER.md).

## Project Structure

```
ows_bot.py       — main automation script (navigation, submit loop, logging)
ows_fixes.py     — error fix registry with 35+ auto-fix handlers
ows_config.py    — config loader (config.toml → config.local.toml → env)
config.toml      — all tunable settings: technicians, recalls, error lists, …
harness.py       — interactive debug harness
mcp_server.py    — MCP server exposing the harness to an LLM
close_claim.py   — utility to close open claim tabs
docs/            — documentation
```
