# MCP Server

`mcp_server.py` exposes the [debug harness](DEBUG_HARNESS.md) over the **Model
Context Protocol (MCP)** so an LLM — Claude Code, Claude Desktop, or any MCP
client — can drive it directly: attach to the live OWS browser, scrape claim
errors, hunt for selectors, test individual fixes, and capture debug state,
all as tool calls instead of shell commands.

Like the interactive harness, **it never submits a claim.** Fixes mutate the
open claim form (that's what they do), but the actual Submit stays a
human/bot decision, off the MCP surface.

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

**Fixes**

| Tool | What it does |
|---|---|
| `list_fixes()` | Every registered auto-fix: code, function, summary. (No browser needed.) |
| `run_fix(error_code)` | Run the single fix for one error code against the open claim. |
| `run_fix_loop(error_codes)` | The bot's full fix → PreValidate → re-scrape loop (can take minutes). |
| `prevalidate()` | Click PreValidate, wait, re-scrape the result. |
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
5. `save_debug("stuck_513271")` to capture state for a human, or
   `screenshot()` to look at the page.

To develop a *new* fix, the model (or you) edits `ows_fixes.py`, then calls
`reload_fixes()` and `run_fix(...)` again — no restart. See
[ADDING_FIXES.md](ADDING_FIXES.md) for the fix-writing conventions.

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
