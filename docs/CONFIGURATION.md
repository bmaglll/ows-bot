# Configuration Reference

All tunable settings live in **`config.toml`** at the repo root. You should
never need to edit Python code to add a technician, a recall, an approval
code, or a new user-review error.

## File precedence

Values are merged in this order (later wins):

1. **Built-in defaults** — hardcoded in `ows_config.py`, mirror the shipped
   `config.toml`, so the bot still runs if the file is missing.
2. **`config.toml`** — committed to git; shared team configuration.
3. **`config.local.toml`** — optional, gitignored; personal overrides.
   Same format — only include the keys you want to change.
4. **Environment / `.env`** — `STARS_ID`, `CDP_URL`, `CLAUDE_MODEL`,
   `ANTHROPIC_API_KEY`.

Check what the merged result looks like at any time:

```bash
python3 ows_config.py
```

## Sections

### `[technicians]` — who you are

```toml
[technicians]
default = "jsmith"          # key of the technician used by the LAB0019 fix

[[technicians.list]]
key = "jsmith"              # short handle referenced by `default`
name = "J. Smith"           # display name (informational only)
stars_id = "002498102"      # 9-digit STARS technician ID
```

Add one `[[technicians.list]]` block per technician. The **default**
technician's `stars_id` is what the `LAB0019` auto-fix types into empty
*ServiceTechnicianID* fields on labor lines. To switch technicians, change
`default`. The `STARS_ID` environment variable overrides all of this if set.

If you don't want STARS IDs committed to git, put the `[technicians]`
section in `config.local.toml` instead.

### `[connection]`

```toml
[connection]
cdp_url = "http://127.0.0.1:9222"   # where the debugging-enabled Chromium listens
```

### `[timeouts]`

```toml
[timeouts]
default_ms = 30000       # general Playwright wait timeout
short_ms = 5000          # short waits
poll_interval_s = 2.0    # seconds between "Paid" status polls
poll_timeout_s = 30      # give up polling for Paid after this many seconds
```

### `[logging]`

```toml
[logging]
base_dir = "logs"        # daily logs land in <base_dir>/YYYY-MM-DD/
```

### `[ai]` — provider for AI-assisted fixes

```toml
[ai]
provider = "anthropic"                     # anthropic | openai | gemini | ollama | none
model    = "claude-haiku-4-5-20251001"     # a model id for the chosen provider
# base_url = "http://localhost:11434/v1"   # OpenAI-compatible endpoints only
```

Two fixes fall back to an LLM when regex can't read a value from the technician
comments: the condition code (`ROV0068`) and the causal part (`SUB0003`). This
is the **only** place an LLM runs *inside* the bot — everything else is
deterministic. Any provider works; Claude is just the default.

| Provider | `provider` | Example `model` | API key (env / `.env`) | Package |
|---|---|---|---|---|
| Anthropic | `anthropic` | `claude-haiku-4-5-20251001` | `ANTHROPIC_API_KEY` | `anthropic` |
| OpenAI | `openai` | `gpt-4o-mini` | `OPENAI_API_KEY` | `openai` |
| OpenAI-compatible | `openai` + `base_url` | (endpoint's id) | endpoint's key (or none) | `openai` |
| Gemini | `gemini` | `gemini-2.0-flash` | `GEMINI_API_KEY` / `GOOGLE_API_KEY` | `google-generativeai` |
| Ollama (local) | `ollama` | `llama3` | none | none (stdlib HTTP) |
| Disabled | `none` | — | — | — |

Only the package for the provider you pick needs to be installed; if it's
missing, the key is unset, or `provider = "none"`, the AI fallback is silently
skipped and those fixes rely on regex alone. Env vars `AI_PROVIDER`, `AI_MODEL`,
and `AI_BASE_URL` override this section for quick switching. (The legacy
`[claude] model` / `CLAUDE_MODEL` still work as the Anthropic model when
`[ai].model` is unset.)

### `[approval_codes]` — codes typed into the Approval Code field

```toml
[approval_codes]
SFRU473 = "DDDO"    # daily rate exceeds amount
ODM0001 = "DDR4"    # distance not equal to same-day paid visit
ODM0002 = "DDR4"    # distance less than earlier paid visit
RRP0001 = "DDR1"    # potential repeat repair
BES0027 = "DDET"    # date too old
```

If Ford changes an approval code, update it here.

### `[subcodes]`

```toml
[subcodes]
cc_82 = ["PRENT", "QCM", "P11"]        # subcodes that force condition code 82
test_drive_ops = ["7001D1", "1006DXQ"] # labor ops deleted by the SFRUC09 fix
sfru448_subcode = "LTIS"               # subcode entered for SFRU448/SFRU430
```

When the `SFRUC09` fix logs *"Add the test drive op code…"*, add the op it
printed to `test_drive_ops`.

### `[rental]` — rental claim (type 13) handling

```toml
[rental]
default_daily_rate = 45     # $/day for regular vehicles
premium_daily_rate = 60     # $/day for premium models
premium_models = ["F-150", "F-250", "F-350", "F-450", "F-550", "F-600",
                  "F150", "F250", "F350", "F450", "F550", "F600", "TRANSIT"]
prent_max_days = 10         # PRENT allowed up to this many days, else P11
```

Rental days are computed as `expense amount ÷ daily rate`, and the daily
rate is picked by matching the claim's vehicle description against
`premium_models`.

### `[recalls."XXXXX"]` — recall bulletin lookup

```toml
[recalls."26P02"]
cc = "04"                # condition code
ccc = "G07"              # customer concern code
causal_part = "14B291"
causal_qty = "0"
```

Used by the `SUB0003` / `SUB0006` / `SCCK032` / `ROV0068` fixes on type-31
recall claims: when the claim's subcode matches a recall key, these fields
are filled in automatically. **When a new recall shows up, add a block here**
(values come from the recall bulletin) — that's usually all a new recall needs.

### `[user_review]` — errors that skip instead of stopping

```toml
[user_review]
errors = ["RDC0002", "FSA0001", …]

[user_review.notes]
ATT0001 = "Requires document attachment"
```

Claims whose remaining errors are all in `errors` are logged as
*"User review: …"* and skipped, and the batch keeps going. Any error **not**
in this list (and without an auto-fix) stops the batch. When you decide an
error is "just skip it and let a human look", add it to `errors`; add a
`notes` entry if a short hint helps whoever reviews the log.

### `[scrape]`

```toml
[scrape]
false_positive_codes = ["MHT7000", "CPR0126", "REH52"]
```

Strings (usually part numbers) that look like OWS message codes to the
scraper regex but aren't errors. If the bot reports a bogus "error" that's
actually a part number on the page, add it here.
