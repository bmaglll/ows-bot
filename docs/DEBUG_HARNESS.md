# Debug Harness

`harness.py` is an interactive shell for poking at OWS claims without
running the full bot: inspect frames, scrape errors, test selectors, and run
individual fixes on demand. It's the main tool for
[developing new error fixes](ADDING_FIXES.md).

> Want an **LLM** to drive these same capabilities instead of typing commands
> yourself? `mcp_server.py` exposes them over the Model Context Protocol —
> see [MCP_SERVER.md](MCP_SERVER.md).

## Modes

```bash
python3 harness.py                      # LIVE: attach to the running Chromium (CDP)
python3 harness.py --html DUMP.html     # OFFLINE: load a saved debug dump in a headless browser
python3 harness.py --scrape DUMP.html   # scrape a saved dump, print codes/messages (no browser)
python3 harness.py --list-fixes         # print the fix registry and exit
```

**Live mode** attaches to the same browser/tab the bot uses. Open the claim
you want to inspect first (manually or by letting the bot stop on it).

**Offline mode** loads an HTML dump saved by the bot
(`logs/YYYY-MM-DD/debug/ows_debug_*.html`) into a local headless browser.
Dumps concatenate all frames into one document, so everything appears in
frame 0. Selector queries and scraping work; clicking Pega buttons obviously
does nothing (there's no server behind the page).

## Shell commands

| Command | What it does |
|---|---|
| `help` | show command list |
| `frames` | list all frames (index + URL) |
| `pages` | list all open browser tabs |
| `scrape` | run the error scraper: codes, messages, DEC0007, lock status |
| `fixes` | list registered error fixes |
| `fix CODE` | run the fix registered for `CODE`, report True/False + timing |
| `tryfix CODE [CODE …]` | run the full fix loop (fix → PreValidate → re-scrape), like the bot does |
| `prevalidate` | click PreValidate and wait for the validation result |
| `sel SELECTOR` | query a CSS selector in every frame; print tag/name/id/visibility/value |
| `inputs PATTERN` | list every `input`/`select`/`textarea` whose name or id contains PATTERN |
| `comments` | print the Technician Comments textarea |
| `dump [LABEL]` | save screenshot + full HTML dump to `logs/…/debug/` |
| `config` | summary of effective config (technician, STARS ID, recalls, …) |
| `reload` | re-import `ows_fixes.py` after you edit it — no restart needed |
| `quit` | exit |

### Examples

What errors does the currently open claim have, and can we fix them?

```
ows> scrape
error_codes : ['LAB0019', 'SUB0006']
…
ows> fix LAB0019
Running fix_lab0019_stars_id for LAB0019 …
Fix returned True in 3.2s.
ows> prevalidate
ows> scrape
```

Hunt for the right selector for a field:

```
ows> inputs ProgramCode
[frame 4] 1 match(es) — https://…/prweb/…
  <input type='text'> name='$PWorkPage$pClaimUnit$pProgramCode' id='ProgramCode' value='PRENT'
ows> sel "input[name*='ProgramCode']"
```

Iterate on a fix you're writing (edit `ows_fixes.py` in another window):

```
ows> reload
Reloaded ows_fixes.py — 38 fixes registered.
ows> fix ZZZ9999
```

## Notes

- The harness never clicks **Submit** — the `fix`/`tryfix`/`prevalidate`
  commands only do what the named fix does. Submitting stays a bot (or
  human) decision.
- Commands with spaces in arguments (CSS selectors) can be quoted:
  `sel "button:has-text('Open Claim')"`.
- If live attach fails, make sure Chromium was started with
  `--remote-debugging-port=9222` and `cdp_url` in `config.toml` matches.
