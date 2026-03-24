# OWS Error Handling Guide

Reference for all known errors encountered during claim submission and how to handle them.

## Known Errors

### DEC0007 — Pre-validation Success
- **Type:** Success indicator (not an error)
- **Action:** Proceed with Submit
- **Automated:** Yes

### "is currently being modified"
- **Type:** Ford system lock
- **Seen on:** 2026-03-02, RO 511333 line 05 (Ford paying line 01 locked line 05)
- **Symptoms:** Claim opens but no DEC0007 pre-validation. Message contains "is currently being modified"
- **Root cause:** Ford's automated system is processing another claim on the same RO (e.g., paying a previously submitted line)
- **Resolution:** Close claim tab, wait 5s, retry once
- **Note:** The claim number and "Agent" are rendered in separate DOM elements from the lock message. The full string "currently being modified by Agent" never appears as one text node. Match on "is currently being modified" to reliably detect regardless of Pega element splitting.
- **Automated:** Yes

---

## Error Log

### SFRU473 — Daily Rate Exceeds Amount for Third-Party Rentals
- **Type:** validation
- **Seen on:** 2026-03-02, RO 511333 line 05
- **Symptoms:** Claim opens but no DEC0007. Message: "SFRU473 - DAILY RATE EXCEEDS THE AMOUNT ALLOWED FOR THIRD-PARTY RENTALS. DEALER SELF-AUTHORIZATION CODE "DDDO" REQUIRED IF THIS IS A DEALER-OWNED UNIT"
- **Root cause:** Rental rate exceeds allowed amount, needs dealer self-authorization
- **Resolution:** Enter "DDDO" into the Approval Code field, then re-submit
- **Automated:** Yes

---

### FSA0022 — Campaign number does not exist
- **Type:** validation
- **Seen on:** 2026-03-02, RO 512279 line 01
- **Symptoms:** Claim has wrong claim type or wrong/missing recall number in sub-code field
- **Root cause:** Two possible causes:
  1. Claim submitted as recall but should be normal warranty (B2B/Powertrain) — needs claim type changed to 11
  2. Wrong or missing recall number in the sub-code field
- **Resolution:** Manual review required — skip and add to manual review list
- **Automated:** No (manual review)

---

### SCCK602 — Approval Code Invalid or Missing
- **Type:** validation
- **Seen on:** 2026-03-02, RO 510325 line 03
- **Symptoms:** "THE APPROVAL CODE IS INVALID OR MISSING" — often accompanied by SCCK075 and SCCK050
- **Root cause:** Repair Line Number has a leading zero (e.g. `03`) but the technician obtained the approval code using the unpadded number (`3`). The mismatch causes the approval code lookup to fail.
- **Resolution:** Strip the leading zero from the Repair Line Number field, then PreValidate — the approval code auto-populates and the other SCCK errors resolve as well (bottom-to-top principle)
- **Automated:** Yes

---

### ROV0038 — Misc Expense Amount Should Be Numeric
- **Type:** validation
- **Seen on:** 2026-03-02, RO 510325 line 01
- **Symptoms:** "Misc Expense Amount should be numeric"
- **Root cause:** Non-numeric value in the Misc Expense Amount field. Sometimes fixable (clear/correct the field), sometimes requires manual review depending on the claim's subcode.
- **Resolution:** Manual review — may be auto-fixable in certain cases (TBD)
- **Automated:** No (manual review)

---

### SUB0006 — Sub Code Not Correct for Claim Type 13
- **Type:** validation
- **Seen on:** 2026-03-02, RO 508014 line 03
- **Symptoms:** "SUB CODE NOT CORRECT FOR CLAIM TYPE 13 SUBMITTED"
- **Root cause:** Rental claim (type 13-POLICY) missing subcode and Special Use Vehicle VIN. Rental claims need the VIN from Technician Comments copied into Special Use Vehicle, and the correct subcode based on rental days.
- **Resolution:** Extract VIN from Technician Comments (regex `VIN <17chars>`), click Update RO icon, fill Special Use Vehicle with VIN, set subcode to `PRENT` (<=10 days) or `P11` (>10 days)
- **Automated:** Yes (for type 13 rental claims with RENTAL misc expense)

---

### ROV0068 — CC (Condition Code) Required for Claim
- **Type:** validation
- **Seen on:** 2026-03-02, RO 512279 line 01
- **Symptoms:** "CC is required for the Claim" — the Condition Code field (`input[name*="ConditionCode"]`) is empty
- **Root cause:** Technician did not fill in the condition code, or it was lost during claim creation
- **Resolution:** Three-path auto-fix:
  1. **Subcode check** — if subcode is PRENT, QCM, or P11 → CC is always `82`
  2. **Regex extraction** — parse CC from Technician Comments (e.g. `CC 33`, `CC:D4`)
  3. **Claude API fallback** — if regex fails and `ANTHROPIC_API_KEY` is set, infer from comments
- **Automated:** Yes
- **Reference:** `reference-docs/Condition_Codes.pdf` (full code list)

---

*New errors will be added below. Format:*

### ERROR_CODE — Short Description
- **Type:** (validation / system / timeout / selector / unknown)
- **Seen on:** (date, RO number)
- **Symptoms:** (what happened)
- **Root cause:** (what we figured out)
- **Resolution:** (how to handle it)
- **Automated:** (Yes / No / Planned)
