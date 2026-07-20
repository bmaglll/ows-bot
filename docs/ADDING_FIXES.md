# Adding a New Error Fix

When the bot hits an error code it doesn't know, it saves a screenshot +
HTML dump to `logs/YYYY-MM-DD/debug/`, logs `Unknown error: CODE`, and stops
the batch. This guide is the workflow for teaching it that error.

## 0. Decide: fix, skip, or config?

Not every error needs code:

- **Human-only error** (needs judgment, attachments, prior approval…):
  add the code to `[user_review] errors` in `config.toml`. The bot will
  skip and log it instead of stopping. Optionally add a note under
  `[user_review.notes]`.
- **New recall subcode**: add a `[recalls."XXXXX"]` block in `config.toml` —
  the existing recall fixes will handle it.
- **New approval code / test-drive op / false-positive part number**:
  also just `config.toml`. See [CONFIGURATION.md](CONFIGURATION.md).
- **Fixable by filling/changing something on the claim**: write a fix
  function — continue below.

## 1. Understand what the scraper saw

```bash
python3 harness.py --scrape logs/2026-07-20/debug/ows_debug_error_513271-02_*.html
```

This prints every message code and full message text found in the dump, plus
which codes already have fixes. Read the message — it usually says exactly
what OWS wants (e.g. `SFRU448 - PLEASE USE SUB CODE`).

## 2. Find the fields the fix needs to touch

Open the dump in the harness and hunt for selectors:

```bash
python3 harness.py --html logs/2026-07-20/debug/ows_debug_error_513271-02_*.html
```

```
ows> inputs Approval          # find inputs by name/id fragment
ows> sel "input[name*='pApprovalCode']"
ows> comments                 # technician comments often contain the answer
```

Pega input names look like `$PWorkPage$pClaimUnit$pRepairDetails$pParts$l1$pCompletePartNumber`.
Match on stable fragments with `input[name*='…']`, never on the full name —
the `$l1` row indices and prefixes change.

You can also do this against the **live** claim (open it in OWS, run
`python3 harness.py` with no args) — that's better when the fix needs to
click buttons and observe Pega's server-side reactions.

## 3. Write the fix in `ows_fixes.py`

A fix is a function `(page, claim_frame) -> bool` that returns `True` if it
changed something. Template:

```python
def fix_zzz9999(page: Page, claim_frame: Frame) -> bool:
    """ZZZ9999 — <what the error means>. Fix: <what this does>."""
    log("Applying ZZZ9999 fix: …")
    for fr in all_frames(page):
        try:
            inp = fr.locator("input[name*='SomeField']").first
            if inp.count() > 0 and inp.is_visible():
                inp.click()
                inp.fill("VALUE")
                time.sleep(0.5)
                log("  SomeField → VALUE")
                return True
        except Exception:
            continue
    log("Could not find SomeField.")
    return False
```

Conventions the existing fixes follow:

- **Loop over `all_frames(page)`** and swallow per-frame exceptions — the
  target field's frame varies.
- **Log every field you touch** (`log(f"  Field → {value}")`) so the batch
  log tells the story.
- **Return `False` if you couldn't apply the fix** — the loop marks the code
  as failed and moves on rather than retrying forever.
- **Don't click PreValidate or Submit inside the fix** — `try_fix_errors`
  does that after every fix and re-scrapes the errors.
- **Sleep briefly (`time.sleep(0.3–0.5)`) after filling a field** — Pega
  fires server events on change.
- Put any tunable values (codes, IDs, lookup tables) in `config.toml` and
  read them via `ows_config.CONFIG`, not as literals.
  For fixes that just type an approval code, use the one-liner factory:
  `fix_zzz9999 = _approval_code_fix("ZZZ9999", "DDXX")` plus an entry in
  `[approval_codes]`.

## 4. Register it

At the bottom of `ows_fixes.py`:

```python
ERROR_FIXES = {
    …
    "ZZZ9999": fix_zzz9999,
}
```

Several codes can share one function (see `PAR0001`/`PAR0002`/`PRI0001`).

## 5. Test it

Re-open the failing claim in OWS, then:

```bash
python3 harness.py
```

```
ows> scrape            # confirm the error is present
ows> fix ZZZ9999       # run just your fix
ows> prevalidate
ows> scrape            # did the error clear? did DEC0007 appear?
```

Edit → `reload` → `fix` again until it works. For the full-loop behavior the
bot will use, run `tryfix ZZZ9999`.

Finally, run the real bot on that RO:

```bash
python3 ows_bot.py 513271-02
```

## 6. Ship it

Commit `ows_fixes.py` (and any `config.toml` additions) with a message like
`fix: auto-fix ZZZ9999 (short description)`.
