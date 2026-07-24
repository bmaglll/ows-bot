# Workflow: run the bot, diagnose with Claude, bake in fixes

This is the day-to-day loop the project is built around: the bot handles
routine claims, parks the ones it doesn't understand, and you bring Claude in
(via the [MCP server](MCP_SERVER.md)) to diagnose those and teach the bot new
fixes — with you watching.

## Where this runs

Everything runs **on the machine that has the OWS browser session** — your own
computer, not a cloud sandbox. The bot and MCP server talk to a Chromium you're
logged into OWS on (`127.0.0.1:9222`), so Claude must be driving from that same
machine. Use **Claude Code or Claude Desktop running locally** in this repo.

One-time setup on that machine:

```bash
git clone <this repo> && cd ows-bot
pip install playwright python-dotenv mcp
cp .env.example .env          # add ANTHROPIC_API_KEY if you use the AI fixes
# edit config.toml → add your technician + STARS ID (or put it in config.local.toml)
```

## The loop

### 1. Start Chromium and log into OWS

```bash
chromium --remote-debugging-port=9222
```

Log into OWS and open the **Claim Status Report** page. Leave it open.

### 2. Run the bot on a batch

```bash
python3 ows_bot.py ros.txt        # a file of RO numbers, one per line
# or a single RO:  python3 ows_bot.py 513271
```

The bot walks each claim, auto-fixes the errors it knows, submits the clean
ones, and polls them to Paid. When it hits an **error it has no fix for**, it
saves a screenshot + HTML dump to `logs/YYYY-MM-DD/debug/`, logs
`Unknown error: CODE`, and stops. Results land in `logs/YYYY-MM-DD/ows_log.csv`.

At this point you have a pile of parked unknowns. Skim the CSV:

```bash
grep -i "unknown error" logs/$(date +%F)/ows_log.csv
```

### 3. Bring in Claude to diagnose (you watching)

Open **Claude Code** in the repo (the `.mcp.json` here auto-registers the
`ows-harness` server) or point **Claude Desktop** at `mcp_server.py`. Re-open
the parked RO in OWS, then tell Claude something like:

> Attach to the browser and diagnose the open claim. If it's an error we can
> fix, find the fields and test a fix; if it needs a human or is just a config
> value, tell me which.

Claude drives through the MCP tools — `attach` → `scrape_errors` →
`find_inputs` / `query_selector` → `run_fix` / `prevalidate` — and you watch the
tool calls in the transcript and the page updating in the browser side by side.
Interrupt or correct it anytime.

For a claim that's no longer open, Claude can work the saved dump instead
(`scrape_dump` / `load_dump` on the file from step 2) to triage and find
selectors — but building and *verifying* a fix needs the live claim (a fix
clicks server-side Pega buttons; you can only confirm it cleared the error
against a live page).

### 4. Bake it in

Depending on what the error turned out to be:

- **Needs a human** (attachments, prior approval) → add the code to
  `[user_review].errors` in `config.toml`. The bot will skip + log it instead
  of stopping. No code.
- **Just data** (new recall, approval code, test-drive op) → a `config.toml`
  edit. No code.
- **Fixable by touching the form** → write a `fix_xxx()` in `ows_fixes.py` and
  register it in `ERROR_FIXES` (see [ADDING_FIXES.md](ADDING_FIXES.md)). Have
  Claude call `reload_fixes()` and `run_fix("XXX")` to test it live without
  restarting anything.

Once a fix passes PreValidate and you're happy, approve the submit:
`submit_claim()` first (dry run — shows the state), then
`submit_claim(confirm=true)`.

### 5. Commit and re-run

```bash
git add config.toml ows_fixes.py
git commit -m "fix: auto-fix XXX0001 (short description)"
python3 ows_bot.py ros.txt        # fewer unknowns this time
```

Each baked fix means the bot handles that error on its own next run, so the
unknown-pile shrinks over time. That's the whole point of the config + harness
+ MCP design.

## Quick reference

| Step | Command / action |
|---|---|
| Start browser | `chromium --remote-debugging-port=9222`, log into OWS, open Claim Status Report |
| Run batch | `python3 ows_bot.py ros.txt` |
| Find unknowns | `grep -i "unknown error" logs/$(date +%F)/ows_log.csv` |
| Diagnose | Claude Code/Desktop → "attach and diagnose the open claim" |
| Test a fix | Claude: `run_fix` → `prevalidate` → `scrape_errors` (`reload_fixes` after edits) |
| Submit | Claude: `submit_claim()` (dry run) → `submit_claim(confirm=true)` after you OK it |
| Bake in | edit `config.toml` and/or `ows_fixes.py`, commit |

## Two ways to divide the work

- **Volume** — let `ows_bot.py` run batches unattended. It's fast and free per
  claim (no LLM), and handles every known error. Monitor via `ows_log.csv`.
- **Hard cases** — use the MCP server + Claude for the unknowns the bot parks.
  This is where you want reasoning and a human watching.

Don't run Claude over every routine claim — that's slower and costs tokens for
work the bot already does deterministically. Reserve it for the unknowns.
