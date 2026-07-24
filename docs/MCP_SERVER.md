# MCP Server

`mcp_server.py` exposes the [debug harness](DEBUG_HARNESS.md) over the **Model
Context Protocol (MCP)** so an LLM — Claude Code, Claude Desktop, or any MCP
client — can drive it directly: attach to the live OWS browser, scrape claim
errors, hunt for selectors, test individual fixes, and capture debug state,
all as tool calls instead of shell commands.

The one irreversible action — **submit** — is gated: `submit_claim` is a dry
run unless you pass `confirm=true`, and it refuses a claim with outstanding
errors unless you also pass `force=true`. So Claude can drive a claim
end-to-end while you approve each submit.

## Install

```bash
pip install mcp playwright python-dotenv
```

Requires Python 3.12+ and the same setup as the bot (Chromium on
`--remote-debugging-port=9222`, logged into OWS with a claim open).

## Register the server

### Claude Code

Already wired up — [`.mcp.json`](../.mcp.json) in the repo root registers it:

```json
{
  "mcpServers": {
    "ows-harness": { "command": "python3", "args": ["mcp_server.py"] }
  }
}
```

Open Claude Code in the repo and the `ows-harness` tools appear
automatically (approve the server when prompted).

### Claude Desktop / other clients

Add to the client's MCP config (use an absolute path):

```json
{
  "mcpServers": {
    "ows-harness": {
      "command": "python3",
      "args": ["/absolute/path/to/ows-bot/mcp_server.py"]
    }
  }
}
```

The server speaks stdio, the transport MCP clients expect.

## Tools

**Connection**

| Tool | What it does |
|---|---|
| `attach(cdp_url="")` | Attach to the live OWS browser over CDP (picks the OWS tab). Omit `cdp_url` to use the configured one. |
| `load_dump(html_path)` | Load a saved debug HTML dump into a local headless browser for offline inspection. |
| `detach()` | Close this server's browser connection (the browser keeps running). |
| `status()` | Connection mode, page title/URL, frame count. |

**Inspection**

| Tool | What it does |
|---|---|
| `scrape_errors()` | Message codes on the open claim: errors, DEC0007, lock status, which have auto-fixes. |
| `query_selector(selector, limit=10)` | Query a CSS selector in every frame; returns tag/name/id/visibility/value. |
| `find_inputs(pattern)` | Every input/select/textarea whose name or id contains `pattern`. |
| `read_comments()` | The Technician Comments textarea (fixes read data from here). |
| `list_frames()` / `list_pages()` | Enumerate frames / browser tabs. |
| `screenshot(full_page=false)` | PNG of the current page, returned inline. |
| `save_debug(label="mcp")` | Screenshot + all-frames HTML dump to `logs/…/debug/`. |

**Interaction (dictated, click-by-click)** — raw actions for walking through a
fix by hand. They scan every frame and act on the first visible match, exactly
like the fix functions do, so steps that work here translate 1:1 into a baked
fix. They mutate the form but never submit.

| Tool | What it does |
|---|---|
| `fill_field(selector, value, press_tab=false)` | Type into the first visible input/textarea matching the selector. `press_tab` commits the value in some Pega fields. |
| `click_element(selector)` | Click the first visible element matching the selector (buttons, radios, 'Add a row' links, tab icons). |
| `select_option(selector, value="", label="")` | Choose an option in the first visible `<select>` (e.g. the Claim Type dropdown). |

**Fixes**

| Tool | What it does |
|---|---|
| `list_fixes()` | Every registered auto-fix: code, function, summary. (No browser needed.) |
| `run_fix(error_code)` | Run the single fix for one error code against the open claim. |
| `run_fix_loop(error_codes)` | The bot's full fix → PreValidate → re-scrape loop (can take minutes). |
| `prevalidate()` | Click PreValidate, wait, re-scrape the result. |
| `submit_claim(confirm=false, force=false)` | Submit the open claim. Dry run unless `confirm=true`; refuses a claim with errors/lock/no-DEC0007 unless `force=true`. The only irreversible tool. |
| `reload_fixes()` | Re-import `ows_fixes.py` after editing it — no server restart. |

**Config / offline**

| Tool | What it does |
|---|---|
| `scrape_dump(html_path)` | Run the error scraper over a saved dump with no browser at all. |
| `list_debug_dumps(date="")` | List saved dumps (paths for `scrape_dump`/`load_dump`). |
| `get_config()` | Effective config summary (technician, recalls, error lists, rates). |

## Typical LLM session

A model debugging a stuck claim would call, roughly:

1. `attach()` — connect to the browser with the claim open.
2. `scrape_errors()` — see the codes and which lack a fix.
3. For an unknown code: `read_comments()`, `find_inputs("ProgramCode")`,
   `query_selector("input[name*='ConditionCode']")` to locate fields.
4. `run_fix("ROV0068")` then `prevalidate()` then `scrape_errors()` to test.
5. Once clean (`has_dec0007: true`, no errors), `submit_claim()` shows the
   dry-run state; after you approve, `submit_claim(confirm=true)` submits.
6. `save_debug("stuck_513271")` to capture state for a human, or
   `screenshot()` to look at the page.

To develop a *new* fix, the model (or you) edits `ows_fixes.py`, then calls
`reload_fixes()` and `run_fix(...)` again — no restart. See
[ADDING_FIXES.md](ADDING_FIXES.md) for the fix-writing conventions.

## Dictating a fix click-by-click, then baking it in

The interaction tools let you drive the repair by hand and have Claude both
execute and record it. You describe each step; Claude finds the field and acts;
you watch it work; then Claude writes the exact steps into a fix function.

A session for a new error `ZZZ9999` looks like:

> **You:** Attach and scrape the open claim.
> **Claude:** `attach()` → `scrape_errors()` → *"ZZZ9999, no fix registered."*
>
> **You:** Click the Approval Code field and type DDDO.
> **Claude:** `find_inputs("Approval")` → *finds `input[name*='pApprovalCode']`* →
> `fill_field("input[name*='pApprovalCode']", "DDDO", press_tab=true)` → *"filled
> in frame 4."*
>
> **You:** Now click PreValidate.
> **Claude:** `prevalidate()` → `scrape_errors()` → *"DEC0007 present, no errors —
> it cleared."*
>
> **You:** That worked — bake it into the bot.
> **Claude:** writes `fix_zzz9999()` in `ows_fixes.py` using the same selector and
> value, registers it in `ERROR_FIXES`, `reload_fixes()`, then `run_fix("ZZZ9999")`
> on a fresh claim to confirm the baked version behaves identically.

Because `fill_field` / `click_element` / `select_option` use the same
"scan all frames, first visible match" pattern as the fix functions, the
selector Claude just used by hand is exactly what goes into the fix — no
translation gap. Then it's a normal commit (`ows_fixes.py`, or `config.toml`
if the fix turned out to be pure data). See [ADDING_FIXES.md](ADDING_FIXES.md).

## Notes

- **Single browser thread.** Playwright's sync API isn't async-safe, so all
  browser state lives on one dedicated worker thread inside the server; tools
  marshal their work onto it. This is invisible to the client.
- **Offline dumps flatten frames.** `load_dump()` / `scrape_dump()` work on
  saved HTML, where all frames are concatenated into one document — selector
  queries and scraping work, but clicking Pega buttons does nothing (no server
  behind the page). Use `attach()` for anything that clicks.
- **Config-driven.** The server reads the same `config.toml` /
  `config.local.toml` / env as the bot — `get_config()` shows the effective
  values.
