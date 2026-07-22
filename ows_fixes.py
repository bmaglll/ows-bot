#!/usr/bin/env python3
"""
OWS Error Fixes — auto-fix functions for known claim errors.
Each fix function takes (page, claim_frame) and returns True if it applied a fix.
After all fixes are applied, try_fix_errors runs PreValidate and re-checks for errors.
Register new fixes in ERROR_FIXES dict at the bottom.
"""
from __future__ import annotations
import os
import re
import time
from datetime import date
from playwright.sync_api import Page, Frame

from ows_config import (
    CONFIG,
    get_approval_code,
    get_stars_id,
)


PREVALIDATE_BTN_SEL = "button[onclick*='PreValidate'], button:has-text('PreValidate')"

# ── Recall bulletin lookup (edit in config.toml → [recalls]) ─────────────────
# Each entry: subcode → {cc, ccc, causal_part, causal_qty}
SUBCODE_LOOKUP = {k.upper(): v for k, v in CONFIG["recalls"].items()}

# Subcodes that always map to condition code 82 (Freight/Postage/Maintenance)
SUBCODE_CC_82 = {c.upper() for c in CONFIG["subcodes"]["cc_82"]}

# Labor ops to delete for SFRUC09 (test drive with unchanged mileage)
# Add new codes in config.toml → [subcodes] test_drive_ops
TEST_DRIVE_OPS = {c.upper() for c in CONFIG["subcodes"]["test_drive_ops"]}

# All valid 2-char condition codes from OWS User Guide V10.16
VALID_CONDITION_CODES = {
    "01", "02", "04", "05", "06", "07", "12", "13", "14", "16", "17",
    "24", "25", "28", "30", "31", "33", "34", "38", "39", "40", "41",
    "42", "43", "46", "49", "51", "53", "55", "61", "63", "68", "69",
    "70", "81", "82", "87", "91", "95",
    "1Q", "A8", "B4", "B5", "C2", "C8", "D1", "D4", "D7", "D8", "D9",
    "P1", "P2", "P3", "P4", "V3", "W6", "X1", "X2", "X4", "X7",
}

# Codes grouped by category — used in the Claude API prompt for inference
CONDITION_CODES_BY_CATEGORY = {
    "BODY": {
        "01": "Broken Cracked", "02": "Bent/Buckled/Kinked",
        "05": "Poor Metal Finishing", "06": "Dents/Dings",
        "07": "Improperly Adjusted", "12": "Improper Assembly",
        "14": "Surface Rough/Uneven", "16": "Incorrect Size",
        "17": "Hole Incomplete, Out of Position or Omitted",
        "24": "Loose Fastener", "25": "Missing Fastener",
        "30": "Chafed, Excessive Wear, Frayed", "31": "Sewing Failure/Split Seams",
        "33": "Loose Part", "34": "Distorted/Wrinkles/Wavy",
        "38": "Wrong Part", "39": "Missing Part",
        "40": "Bonus Stock (Extra fastener or material)",
        "41": "Sticks/Binds/Grabs", "42": "Does Not Operate Properly",
        "61": "Weld Defective/Broken", "68": "Sealer Missing/Skipped",
        "70": "Chipped/Scratched", "81": "Tarnished/Faded",
        "91": "Burrs, Sharp Edges", "A8": "Stone Pecking",
        "B5": "Battery Acid/Fluid Damage", "C2": "Stripped/Cross-Threaded Fastener",
        "C8": "Industrial/Environmental Fallout", "D4": "Flaw in Material",
        "D7": "Corrosion (Perforation)", "1Q": "Corrosion/rust Aluminum Panels only",
        "P1": "Polish Repair (Paint)", "P2": "Spot Repair (Paint)",
        "P3": "Spray Panel Repair (Paint)", "P4": "Thick/Cracked (Paint)",
    },
    "CHASSIS": {
        "01": "Broken/Cracked", "02": "Bent/Buckled/Kinked",
        "04": "Software Revision/Flash Module", "07": "Improperly Adjusted/Fits Poorly",
        "12": "Improper Assembly", "13": "Out of Round",
        "14": "Surface Rough/Uneven", "16": "Incorrect Size",
        "17": "Hole Incomplete, Out of Position or Omitted",
        "24": "Loose Fastener", "25": "Missing Fastener",
        "30": "Chafed, Excessive Wear, Frayed", "33": "Loose Part",
        "38": "Wrong Part", "39": "Missing Part",
        "42": "Does Not Operate Properly", "43": "Improperly Routed",
        "49": "Contaminated/Foreign", "53": "Air in System",
        "55": "Plugged/Restricted", "61": "Weld Defective/Broken",
        "63": "Weak/Soft/Sagged (Insufficient Pressure)",
        "68": "Sealer Missing/Skipped", "69": "Frozen/Seized/Binding",
        "70": "Chipped/Scratched", "87": "Teeth Damaged",
        "91": "Burrs, Sharp Edges", "C2": "Stripped/Cross-Threaded Fastener",
        "D1": "Porosity", "D4": "Flaw in Material",
        "D9": "Out of Balance", "W6": "Wheel Alignment Out of Specification",
    },
    "ELECTRICAL": {
        "01": "Broken/Cracked", "04": "Software Revision/Flash Module",
        "12": "Improper Assembly", "24": "Loose Fastener",
        "25": "Missing Fastener", "28": "Open Circuit",
        "30": "Chafed, Excessive Wear, Frayed", "33": "Loose Part",
        "38": "Wrong Part", "39": "Missing Part",
        "42": "Does Not Operate Properly", "43": "Improperly Routed",
        "46": "Burned Out", "95": "Insulation Damage",
        "B4": "Pinched/Damaged Wire", "B5": "Battery Acid/Fluid Damage",
        "D4": "Flaw in Material", "X1": "Poor Ground",
        "X2": "Connection Poor/Not Made", "X4": "Damaged Terminals",
        "X7": "Crossed Wires (Wire Harness)",
    },
    "POWERTRAIN": {
        "01": "Broken/Cracked", "04": "Software Revision/Flash Module",
        "07": "Improperly Adjusted/Fits Poorly", "12": "Improper Assembly",
        "13": "Out of Round", "14": "Surface Rough/Uneven",
        "17": "Hole Incomplete, Out of Position or Omitted",
        "24": "Loose Fastener", "25": "Missing Fastener",
        "30": "Chafed, Excessive Wear, Frayed", "33": "Loose Part",
        "38": "Wrong Part", "39": "Missing Part",
        "42": "Does Not Operate Properly", "43": "Improperly Routed",
        "49": "Contaminated/Foreign", "53": "Air in System",
        "55": "Plugged/Restricted",
        "63": "Weak/Soft/Sagged (Insufficient Pressure)",
        "69": "Frozen/Seized/Binding", "91": "Burrs, Sharp Edges",
        "D1": "Porosity", "D4": "Flaw in Material",
        "D8": "Failed Gasket/Seal",
        "V3": "Kinked/Cut/Mis-routed Vacuum Line",
    },
    "MISCELLANEOUS": {
        "51": "Insufficient Fluid (Pre-delivery only)",
        "82": "Freight/Postage/Maintenance",
    },
}


def log(msg: str) -> None:
    print(f"[OWS][FIX] {msg}", flush=True)


def all_frames(page: Page) -> list[Frame]:
    # page.frames already contains main_frame — keep it first, don't double it
    return [page.main_frame] + [f for f in page.frames if f is not page.main_frame]


def add_causal_part(page: Page, part_number: str) -> str | None:
    """
    Add a causal part to the parts grid. Tries existing empty row first,
    then clicks 'Add a row' if needed. Fills the part number and clicks
    the causal indicator radio.
    Returns the row index (e.g. '$l2') or None if failed.
    """
    new_row_index = None
    filled = False

    # Try filling an existing empty parts row
    for fr in all_frames(page):
        try:
            pn_inputs = fr.locator("input[name*='pParts'][name*='CompletePartNumber']").all()
            for inp in pn_inputs:
                try:
                    val = inp.input_value(timeout=1000).strip()
                    if not val:
                        inp.click()
                        inp.fill(part_number)
                        time.sleep(1.0)
                        log(f"  Causal Part → {part_number} (existing empty row)")
                        name = inp.get_attribute("name")
                        match = re.search(r'\$pParts(\$l\d+)\$', name)
                        if match:
                            new_row_index = match.group(1)
                        filled = True
                        break
                except Exception:
                    continue
            if filled:
                break
        except Exception:
            continue

    # If no empty row, click "Add a row" in parts grid
    if not filled:
        row_added = False
        for fr in all_frames(page):
            try:
                add_btn = fr.locator("a[aria-label*='Add a row'][name*='PartsInformation']").first
                if add_btn.count() > 0 and add_btn.is_visible():
                    add_btn.click()
                    time.sleep(2.0)
                    log("Clicked 'Add a row' in parts grid.")
                    row_added = True
                    break
            except Exception:
                continue
        if row_added:
            for fr in all_frames(page):
                try:
                    pn_inputs = fr.locator("input[name*='pParts'][name*='CompletePartNumber']").all()
                    for inp in pn_inputs:
                        try:
                            val = inp.input_value(timeout=1000).strip()
                            if not val:
                                inp.click()
                                inp.fill(part_number)
                                time.sleep(1.0)
                                log(f"  Causal Part → {part_number} (new row)")
                                name = inp.get_attribute("name")
                                match = re.search(r'\$pParts(\$l\d+)\$', name)
                                if match:
                                    new_row_index = match.group(1)
                                filled = True
                                break
                        except Exception:
                            continue
                    if filled:
                        break
                except Exception:
                    continue

    if filled:
        click_causal_indicator(page, row_index=new_row_index)
        return new_row_index
    else:
        log(f"WARNING: Could not add causal part '{part_number}'.")
        return None


def click_causal_indicator(page: Page, row_index: str | None = None) -> bool:
    """Click the Causal Part radio button.
    If row_index is given (e.g. '$l2'), target that specific parts row.
    Otherwise click the first visible radio (legacy behavior for fix_sub0006).
    """
    for fr in all_frames(page):
        try:
            if row_index:
                radio = fr.locator(f"input[type='radio'][name*='pParts{row_index}'][name*='CausalPartIndicator'][value='true']")
            else:
                radio = fr.locator("input[type='radio'][name*='CausalPartIndicator'][value='true']").first
            if radio.count() > 0 and radio.is_visible():
                radio.click()
                time.sleep(0.3)
                log(f"Clicked Causal Part indicator radio button{f' (row {row_index})' if row_index else ''}.")
                return True
        except Exception:
            continue
    log("Could not find Causal Part indicator radio button.")
    return False


def click_prevalidate(page: Page, max_wait: int = 45) -> bool:
    """
    Click the PreValidate button and wait until validation completes.
    Uses Pega's aria-busy attribute on #PEGA_HARNESS as the primary signal,
    with error-code diff as fallback.
    """
    log("Clicking PreValidate …")
    for fr in all_frames(page):
        try:
            btn = fr.locator(PREVALIDATE_BTN_SEL).first
            if btn.count() > 0 and btn.is_visible():
                # Snapshot codes BEFORE clicking
                before_codes = set()
                for f in all_frames(page):
                    try:
                        before_codes.update(re.findall(r"[A-Z]{2,6}\d{3,5}", f.content()))
                    except:
                        pass
                log(f"Pre-click codes: {sorted(before_codes)}")

                btn.scroll_into_view_if_needed()
                btn.click()
                log("PreValidate clicked. Waiting for validation …")

                # Poll for code changes (reliable — no networkidle/response guessing)
                deadline = time.time() + max_wait
                while time.time() < deadline:
                    time.sleep(2.0)
                    after_codes = set()
                    for f in all_frames(page):
                        try:
                            after_codes.update(re.findall(r"[A-Z]{2,6}\d{3,5}", f.content()))
                        except:
                            pass
                    new_dec = {c for c in after_codes if c.startswith("DEC")} - before_codes
                    if new_dec:
                        log(f"PreValidate completed — new DEC code(s): {sorted(new_dec)}")
                        return True
                    if after_codes != before_codes:
                        log(f"PreValidate completed — codes changed: {sorted(before_codes)} → {sorted(after_codes)}")
                        return True

                log(f"PreValidate timed out after {max_wait}s — codes unchanged.")
                return True
        except Exception:
            continue
    log("PreValidate button not found.")
    return False


def scrape_errors_quick(page: Page) -> dict:
    """Quick error scrape after PreValidate — returns codes and messages."""
    html = ""
    for fr in all_frames(page):
        try:
            html += fr.content()
        except:
            pass
    all_messages = re.findall(r"([A-Z]{2,6}\d{3,5}\s*-\s*[^<\"]{0,300})", html)
    all_codes = list(set(re.findall(r"[A-Z]{2,6}\d{3,5}", " ".join(all_messages))))
    has_dec0007 = any(c.upper() == "DEC0007" for c in all_codes)
    error_codes = [c for c in all_codes if c.upper() != "DEC0007"]
    return {
        "all_codes": all_codes,
        "all_messages": [m.strip() for m in all_messages],
        "error_codes": error_codes,
        "has_dec0007": has_dec0007,
    }


# ── Fix functions ─────────────────────────────────────────────────────────────

def fill_approval_code(page: Page, code: str) -> bool:
    """Shared helper: enter a value into the first empty Approval Code field."""
    selector = "input[name*='pRepairDetails'][name*='pApprovalCode']"
    for fr in all_frames(page):
        try:
            inputs = fr.locator(selector).all()
            if not inputs:
                continue
            for inp in inputs:
                try:
                    val = inp.input_value(timeout=1000)
                    if not val.strip():
                        inp.click()
                        inp.fill(code)
                        inp.press("Tab")  # blur to commit value
                        time.sleep(0.5)
                        log(f"Entered '{code}' into Approval Code field.")
                        return True
                except Exception:
                    continue
        except Exception:
            continue
    log("Could not find an empty Approval Code field.")
    return False


def _approval_code_fix(error_code: str, fallback: str):
    """Build a fix function that enters a configured approval code
    (config.toml → [approval_codes]) into the Approval Code field."""
    def fix(page: Page, claim_frame: Frame) -> bool:
        code = get_approval_code(error_code, fallback)
        log(f"Applying {error_code} fix: entering '{code}' into Approval Code field …")
        return fill_approval_code(page, code)
    fix.__name__ = f"fix_{error_code.lower()}"
    fix.__doc__ = (f"{error_code} — enters approval code "
                   f"'{get_approval_code(error_code, fallback)}' (config.toml → [approval_codes]).")
    return fix


# SFRU473 — Daily rate exceeds amount
fix_sfru473 = _approval_code_fix("SFRU473", "DDDO")
# ODM0001 — Distance not equal to same-day paid visit
fix_odm0001 = _approval_code_fix("ODM0001", "DDR4")
# ODM0002 — Distance less than earlier paid visit
fix_odm0002 = _approval_code_fix("ODM0002", "DDR4")
# RRP0001 — Potential repeat repair
fix_rrp0001 = _approval_code_fix("RRP0001", "DDR1")


def get_vehicle_description(page: Page) -> str:
    """Read the Vehicle field text from the claim header."""
    for fr in all_frames(page):
        try:
            # XPath: find label containing "Vehicle:" then grab following sibling span
            span = fr.locator("xpath=//label[contains(text(),'Vehicle:')]/following-sibling::span[1]").first
            if span.count() > 0:
                text = span.inner_text(timeout=2000).strip()
                if text:
                    return text
        except Exception:
            continue
    # Fallback: parent td approach
    for fr in all_frames(page):
        try:
            span = fr.locator("xpath=//label[contains(text(),'Vehicle:')]/..//span").first
            if span.count() > 0:
                text = span.inner_text(timeout=2000).strip()
                if text:
                    return text
        except Exception:
            continue
    return ""


def get_rental_daily_rate(page: Page) -> int:
    """Return daily rental rate based on vehicle (config.toml → [rental])."""
    rental = CONFIG["rental"]
    vehicle = get_vehicle_description(page).upper()
    log(f"Vehicle description: '{vehicle}'")
    for model in rental["premium_models"]:
        if model.upper() in vehicle:
            log(f"Vehicle matches '{model}' → ${rental['premium_daily_rate']}/day rate")
            return rental["premium_daily_rate"]
    log(f"Vehicle does not match premium models → ${rental['default_daily_rate']}/day rate")
    return rental["default_daily_rate"]


def _read_expense_amount(page: Page) -> float | None:
    """Read the ExpenseAmount from the misc expense row."""
    for fr in all_frames(page):
        try:
            amt_inputs = fr.locator("input[name*='ExpenseAmount'][name*='theCurrencyAmount']").all()
            for inp in amt_inputs:
                try:
                    val = inp.input_value(timeout=1000).strip()
                    if val:
                        return float(val.replace(",", ""))
                except Exception:
                    continue
        except Exception:
            continue
    return None


def _calculate_and_fill_days(page: Page) -> int | None:
    """Calculate rental days from amount/rate and fill the ExpenseDays field. Returns days or None."""
    amount = _read_expense_amount(page)
    if amount is None or amount <= 0:
        log("Could not read ExpenseAmount or amount is 0 — cannot calculate days.")
        return None
    rate = get_rental_daily_rate(page)
    days = round(amount / rate)
    if days < 1:
        days = 1
    log(f"Rental days calculation: Amount=${amount}, rate=${rate}/day, days={days}")

    # Fill the ExpenseDays field
    for fr in all_frames(page):
        try:
            days_inputs = fr.locator("input[name*='ExpenseDays']").all()
            for inp in days_inputs:
                try:
                    if inp.is_visible():
                        inp.click()
                        inp.fill(str(days))
                        time.sleep(0.3)
                        log(f"  ExpenseDays → {days}")
                        return days
                except Exception:
                    continue
        except Exception:
            continue
    log("WARNING: Could not find ExpenseDays field to fill.")
    return days


def fix_sfrng45(page: Page, claim_frame: Frame) -> bool:
    """SFRNG45 — Rental days missing/wrong. Calculate from Amount / daily rate and fill Days."""
    log("Applying SFRNG45 fix: calculating rental days from amount …")
    days = _calculate_and_fill_days(page)
    if days is not None:
        log(f"SFRNG45 fix applied: days={days}")
        return True
    log("SFRNG45 fix failed — could not calculate days.")
    return False


def fix_rental_claim(page: Page, claim_frame: Frame) -> bool:
    """
    Rental claim helper for type 13 claims.
    Detects rental by claim type + expense code or comments containing 'rental'/'FTCP'.
    Fills: CC=82, CCC=A99, VIN→Special Use Vehicle, Causal Part=RENTAL,
    Misc Code=RENTAL, Subcode=PRENT/P11 based on days.
    """
    log("Checking for rental claim (type 13) …")

    # ── Detect: claim type must be 13 ──────────────────────────────────────
    claim_type_val = None
    for fr in all_frames(page):
        try:
            sel = fr.locator("select#ClaimTypeDesc")
            if sel.count() > 0:
                claim_type_val = sel.input_value(timeout=2000)
                break
        except Exception:
            continue
    if not claim_type_val or not claim_type_val.startswith("13"):
        log(f"Not a type 13 claim (got '{claim_type_val}') — rental fix skipped.")
        return False

    # ── Detect: expense code = RENTAL or FTCP ──────────────────────────────
    rental_expense_val = None
    for fr in all_frames(page):
        try:
            expense_inputs = fr.locator("input[name*='ExpenseCode']").all()
            for inp in expense_inputs:
                try:
                    val = inp.input_value(timeout=1000)
                    if val.strip().upper() in ("RENTAL", "FTCP"):
                        rental_expense_val = val.strip().upper()
                        break
                except Exception:
                    continue
            if rental_expense_val:
                break
        except Exception:
            continue

    # ── Detect: fallback — check comments for 'rental' or 'FTCP' ──────────
    comments = ""
    for fr in all_frames(page):
        try:
            ta = fr.locator("textarea#TechnicianComments")
            if ta.count() > 0:
                comments = ta.input_value(timeout=2000)
                break
        except Exception:
            continue

    if not rental_expense_val:
        comments_upper = comments.upper()
        if "RENTAL" in comments_upper or "FTCP" in comments_upper:
            log("Rental indicator found in Technician Comments.")
        else:
            log("No rental indicator found in expense code or comments — rental fix skipped.")
            return False

    log(f"Confirmed rental claim: type={claim_type_val}, expense={rental_expense_val or 'from comments'}")

    # ── Fill CC = 82 ───────────────────────────────────────────────────────
    for fr in all_frames(page):
        try:
            cc_inp = fr.locator("input[name*='ConditionCode']").first
            if cc_inp.count() > 0 and cc_inp.is_visible():
                cc_inp.click()
                cc_inp.fill("82")
                time.sleep(0.3)
                log("  CC → 82")
                break
        except Exception:
            continue

    # ── Fill CCC = A99 ─────────────────────────────────────────────────────
    for fr in all_frames(page):
        try:
            ccc_inp = fr.locator("input[name*='CustomerConcernCode']").first
            if ccc_inp.count() > 0 and ccc_inp.is_visible():
                ccc_inp.click()
                ccc_inp.fill("A99")
                time.sleep(0.3)
                log("  CCC → A99")
                break
        except Exception:
            continue

    # ── Extract VIN from comments ──────────────────────────────────────────
    vin = None
    vins = re.findall(r"VIN[\s:]+([A-HJ-NPR-Z0-9]{17})", comments, re.IGNORECASE)
    if vins:
        vin = vins[-1].upper()
        log(f"Extracted VIN from Technician Comments: {vin}")

    # ── Enter VIN into Special Use Vehicle ─────────────────────────────────
    if vin:
        already_editable = False
        for fr in all_frames(page):
            try:
                ta = fr.locator("textarea[name*='SpecialUse']").first
                if ta.count() > 0 and ta.is_visible() and ta.is_enabled():
                    already_editable = True
                    log("Special Use Vehicle textarea already editable.")
                    break
            except Exception:
                continue

        if not already_editable:
            update_clicked = False
            for fr in all_frames(page):
                try:
                    icon = fr.locator("a.iconUpdate[title*='Update Repair Order']").first
                    if icon.count() > 0 and icon.is_visible():
                        icon.scroll_into_view_if_needed()
                        icon.click()
                        update_clicked = True
                        log("Clicked Update RO icon.")
                        break
                except Exception:
                    continue
            if not update_clicked:
                log("Update RO icon not found — cannot fill Special Use Vehicle.")

        # Poll for textarea to become editable
        suv_textarea = None
        deadline = time.time() + 15
        while time.time() < deadline:
            time.sleep(1.0)
            for fr in all_frames(page):
                try:
                    ta = fr.locator("textarea[name*='SpecialUse']").first
                    if ta.count() > 0 and ta.is_visible() and ta.is_enabled():
                        suv_textarea = ta
                        break
                except Exception:
                    continue
            if suv_textarea:
                break

        if suv_textarea:
            suv_textarea.click()
            suv_textarea.fill(vin)
            time.sleep(0.5)
            log(f"Entered VIN '{vin}' into Special Use Vehicle.")

            # Save
            for fr in all_frames(page):
                try:
                    save_btn = fr.locator("a[title='Save this work item']").first
                    if save_btn.count() > 0 and save_btn.is_visible():
                        save_btn.scroll_into_view_if_needed()
                        save_btn.click()
                        log("Clicked Save button.")
                        time.sleep(3.0)
                        break
                except Exception:
                    continue
        else:
            log("Special Use Vehicle textarea not editable — VIN not entered.")
    else:
        log("No VIN found in comments — Special Use Vehicle not filled.")

    # ── Fill Causal Part = RENTAL (qty 0, causal indicator selected) ─────────
    # Check if RENTAL part row already exists; if not, add it
    rental_row_index = None
    for fr in all_frames(page):
        try:
            pn_inputs = fr.locator("input[name*='pParts'][name*='CompletePartNumber']").all()
            for inp in pn_inputs:
                try:
                    val = inp.input_value(timeout=1000).strip().upper()
                    if val == "RENTAL":
                        name = inp.get_attribute("name")
                        match = re.search(r'\$pParts(\$l\d+)\$', name)
                        if match:
                            rental_row_index = match.group(1)
                        log("RENTAL part row already exists — reusing.")
                        break
                except Exception:
                    continue
            if rental_row_index is not None:
                break
        except Exception:
            continue

    if rental_row_index is None:
        rental_row_index = add_causal_part(page, "RENTAL")

    # Set quantity to 0 on the RENTAL part row
    if rental_row_index:
        for fr in all_frames(page):
            try:
                qty_inp = fr.locator(f"input[name*='pParts{rental_row_index}'][name*='pItemQuantity']").first
                if qty_inp.count() > 0 and qty_inp.is_visible():
                    qty_inp.click()
                    qty_inp.fill("0")
                    time.sleep(0.3)
                    log(f"  RENTAL part qty → 0 (row {rental_row_index})")
                    break
            except Exception:
                continue
        # Ensure causal indicator is selected
        click_causal_indicator(page, row_index=rental_row_index)

    # ── Set Misc Code = RENTAL ─────────────────────────────────────────────
    misc_filled = False
    for fr in all_frames(page):
        try:
            expense_inputs = fr.locator("input[name*='ExpenseCode']").all()
            for inp in expense_inputs:
                try:
                    val = inp.input_value(timeout=1000).strip().upper()
                    if val and val == "RENTAL":
                        continue  # already correct
                    if val in ("FTCP", "") or val != "RENTAL":
                        inp.click()
                        inp.fill("RENTAL")
                        time.sleep(0.3)
                        log(f"  Misc Code: '{val}' → RENTAL")
                        misc_filled = True
                        break
                except Exception:
                    continue
            if misc_filled:
                break
        except Exception:
            continue
    if not misc_filled:
        log("WARNING: Could not find ExpenseCode field to set to RENTAL.")

    # ── Calculate and fill rental days from amount ─────────────────────────
    days = _calculate_and_fill_days(page)

    prent_max_days = CONFIG["rental"]["prent_max_days"]
    if days is None:
        log("Could not calculate rental days — defaulting to PRENT.")
        subcode = "PRENT"
    elif days <= prent_max_days:
        subcode = "PRENT"
    else:
        subcode = "P11"

    log(f"Rental days: {days}, setting subcode to '{subcode}'")

    subcode_filled = False
    for fr in all_frames(page):
        try:
            prog_inputs = fr.locator("input[name*='ProgramCode']").all()
            for inp in prog_inputs:
                try:
                    if inp.is_visible():
                        inp.click()
                        inp.fill(subcode)
                        time.sleep(0.5)
                        subcode_filled = True
                        log(f"  Subcode → {subcode}")
                        break
                except Exception:
                    continue
            if subcode_filled:
                break
        except Exception:
            continue

    if not subcode_filled:
        log("Could not find ProgramCode field to set subcode.")
        return False

    log("Rental claim fix applied successfully.")
    return True


def fix_sub0006(page: Page, claim_frame: Frame) -> bool:
    """
    SUB0006 — Sub code not correct for claim type.
    Handles two cases:
    1. Recall number (5-char like 26P02) → change claim type to 31
    2. Rental claim (type 13) → VIN + Special Use Vehicle + subcode
    """
    log("Applying SUB0006 fix …")

    # ── Check for recall number in subcode ────────────────────────────────────
    subcode_val = None
    for fr in all_frames(page):
        try:
            prog_inputs = fr.locator("input[name*='ProgramCode']").all()
            for inp in prog_inputs:
                try:
                    val = inp.input_value(timeout=1000).strip()
                    if val:
                        subcode_val = val
                        break
                except Exception:
                    continue
            if subcode_val:
                break
        except Exception:
            continue

    # 5-char recall number pattern: 2 digits + letter + 2 digits (e.g. 26P02)
    if subcode_val and re.match(r"^\d{2}[A-Z]\d{2}$", subcode_val.upper()):
        recall_key = subcode_val.upper()
        log(f"Recall number '{recall_key}' detected — changing claim type to 31 …")

        # Change claim type to 31
        type_changed = False
        for fr in all_frames(page):
            try:
                sel = fr.locator("select#ClaimTypeDesc")
                if sel.count() > 0:
                    options = sel.locator("option").all()
                    for opt in options:
                        opt_val = opt.get_attribute("value") or ""
                        if opt_val.startswith("31"):
                            sel.select_option(value=opt_val)
                            log(f"Changed claim type to '{opt_val}'.")
                            time.sleep(0.5)
                            type_changed = True
                            break
                    break
            except Exception:
                continue
        if not type_changed:
            log("Could not find claim type dropdown to change to 31.")
            return False

        # Fill additional fields from recall lookup
        recall_info = SUBCODE_LOOKUP.get(recall_key)
        if not recall_info:
            log(f"No recall lookup entry for '{recall_key}' — claim type changed but fields not filled.")
            return True

        log(f"Filling recall fields from lookup: {recall_info}")

        for fr in all_frames(page):
            try:
                # Condition Code
                cc_inp = fr.locator("input[name*='ConditionCode']").first
                if cc_inp.count() > 0 and cc_inp.is_visible():
                    cc_inp.click()
                    cc_inp.fill(recall_info["cc"])
                    time.sleep(0.3)
                    log(f"  CC → {recall_info['cc']}")
            except Exception:
                pass
            try:
                # Customer Concern Code
                ccc_inp = fr.locator("input[name*='CustomerConcernCode']").first
                if ccc_inp.count() > 0 and ccc_inp.is_visible():
                    ccc_inp.click()
                    ccc_inp.fill(recall_info["ccc"])
                    time.sleep(0.3)
                    log(f"  CCC → {recall_info['ccc']}")
            except Exception:
                pass
            try:
                # Causal Part Number
                cp_inp = fr.locator("input[name*='CausalPartNumber'], input[name*='causalPart']").first
                if cp_inp.count() > 0 and cp_inp.is_visible():
                    cp_inp.click()
                    cp_inp.fill(recall_info["causal_part"])
                    time.sleep(0.3)
                    log(f"  Causal Part → {recall_info['causal_part']}")
            except Exception:
                pass
            try:
                # Causal Part Quantity
                cq_inp = fr.locator("input[name*='CausalPartQuantity'], input[name*='causalPartQty']").first
                if cq_inp.count() > 0 and cq_inp.is_visible():
                    cq_inp.click()
                    cq_inp.fill(recall_info["causal_qty"])
                    time.sleep(0.3)
                    log(f"  Causal Qty → {recall_info['causal_qty']}")
            except Exception:
                pass

        log("Recall fields filled.")
        click_causal_indicator(page)
        return True

    # ── Rental claim (type 13) path ───────────────────────────────────────────
    if fix_rental_claim(page, claim_frame):
        return True

    # ── Rental subcode on wrong claim type → switch to 13 and retry ──────────
    if subcode_val and subcode_val.upper() in ("P11", "PRENT"):
        log(f"Rental subcode '{subcode_val}' on non-13 claim — switching to type 13 …")
        for fr in all_frames(page):
            try:
                sel = fr.locator("select#ClaimTypeDesc")
                if sel.count() > 0:
                    options = sel.locator("option").all()
                    for opt in options:
                        opt_val = opt.get_attribute("value") or ""
                        if opt_val.startswith("13"):
                            sel.select_option(value=opt_val)
                            log(f"Changed claim type to '{opt_val}'.")
                            time.sleep(0.5)
                            break
                    break
            except Exception:
                continue
        if fix_rental_claim(page, claim_frame):
            return True
        log("Switched to type 13 but rental fix still failed.")
        return False

    # ── SPW subcode → switch claim type to 21 ───────────────────────────────
    if subcode_val and subcode_val.upper() == "SPW":
        log("Subcode 'SPW' detected — switching claim type to 21 …")
        for fr in all_frames(page):
            try:
                sel = fr.locator("select#ClaimTypeDesc")
                if sel.count() > 0:
                    options = sel.locator("option").all()
                    for opt in options:
                        opt_val = opt.get_attribute("value") or ""
                        if opt_val.startswith("21"):
                            sel.select_option(value=opt_val)
                            log(f"Changed claim type to '{opt_val}'.")
                            time.sleep(0.5)
                            return True
                    log("No type 21 option found in dropdown.")
                    return False
            except Exception:
                continue
        log("Could not find claim type dropdown.")
        return False

    # ── Fallback: no rental indicators → switch claim type to 11 ─────────────
    log("No rental indicators found — switching claim type to 11 (regular warranty) …")
    for fr in all_frames(page):
        try:
            sel = fr.locator("select#ClaimTypeDesc")
            if sel.count() > 0:
                current = sel.input_value(timeout=2000)
                if current.startswith("11"):
                    log(f"Already type 11 ('{current}') — cannot fix SUB0006 by switching type.")
                    return False
                options = sel.locator("option").all()
                for opt in options:
                    opt_val = opt.get_attribute("value") or ""
                    if opt_val.startswith("11"):
                        sel.select_option(value=opt_val)
                        log(f"Changed claim type to '{opt_val}'.")
                        time.sleep(0.5)
                        return True
                break
        except Exception:
            continue
    log("Could not find claim type dropdown to change to 11.")
    return False


def fix_scck602(page: Page, claim_frame: Frame) -> bool:
    """
    SCCK602 — Approval code invalid/missing because Repair Line Number has a
    leading zero (e.g. '03') but the approval code was obtained for '3'.
    Fix: strip the leading zero from the Repair Line Number field so the
    approval code auto-populates on next PreValidate.
    """
    log("Applying SCCK602 fix: stripping leading zero from Repair Line Number …")
    for fr in all_frames(page):
        try:
            inputs = fr.locator("input").all()
            for inp in inputs:
                try:
                    name = inp.get_attribute("name") or ""
                    inp_id = inp.get_attribute("id") or ""
                    ident = (name + " " + inp_id).lower()
                    if "repairlinenumber" not in ident.replace(" ", "").replace("_", "").replace("-", ""):
                        continue
                    val = inp.input_value(timeout=1000)
                    stripped = val.lstrip("0") or "0"
                    if stripped == val:
                        log(f"Repair Line Number '{val}' has no leading zero — skipping.")
                        return False
                    log(f"Found Repair Line Number: name={name} id={inp_id} val='{val}' → '{stripped}'")
                    inp.click()
                    inp.fill(stripped)
                    time.sleep(0.5)
                    log(f"Stripped leading zero: '{val}' → '{stripped}'")
                    return True
                except Exception:
                    continue
        except Exception:
            continue

    # Fallback: try finding by label text proximity
    for fr in all_frames(page):
        try:
            labels = fr.locator("label:has-text('Repair Line'), td:has-text('Repair Line')").all()
            for label in labels:
                try:
                    parent = label.locator("..").first
                    inputs = parent.locator("input").all()
                    for inp in inputs:
                        val = inp.input_value(timeout=1000)
                        stripped = val.lstrip("0") or "0"
                        if stripped != val:
                            inp.click()
                            inp.fill(stripped)
                            time.sleep(0.5)
                            log(f"Stripped leading zero via label proximity: '{val}' → '{stripped}'")
                            return True
                except Exception:
                    continue
        except Exception:
            continue

    log("Could not find Repair Line Number field or no leading zero to strip.")
    return False


def fix_scck032(page: Page, claim_frame: Frame) -> bool:
    """
    SCCK032 — Condition code invalid for sub code.
    If claim type is 31 and the subcode matches a recall in SUBCODE_LOOKUP,
    overwrite the recall fields (CC, CCC, Causal Part, Qty).
    """
    log("Applying SCCK032 fix: checking for recall subcode …")

    # Read claim type
    claim_type_val = None
    for fr in all_frames(page):
        try:
            sel = fr.locator("select#ClaimTypeDesc")
            if sel.count() > 0:
                claim_type_val = sel.input_value(timeout=2000)
                break
        except Exception:
            continue
    if not claim_type_val or not claim_type_val.startswith("31"):
        log(f"Not a type 31 claim (got '{claim_type_val}') — SCCK032 fix skipped.")
        return False

    # Read subcode (ProgramCode)
    subcode_val = None
    for fr in all_frames(page):
        try:
            prog_inputs = fr.locator("input[name*='ProgramCode']").all()
            for inp in prog_inputs:
                try:
                    val = inp.input_value(timeout=1000).strip()
                    if val:
                        subcode_val = val
                        break
                except Exception:
                    continue
            if subcode_val:
                break
        except Exception:
            continue

    if not subcode_val:
        log("Could not read subcode — SCCK032 fix skipped.")
        return False

    recall_key = subcode_val.upper()
    recall_info = SUBCODE_LOOKUP.get(recall_key)
    if not recall_info:
        log(f"Subcode '{recall_key}' not in SUBCODE_LOOKUP — SCCK032 fix skipped.")
        return False

    log(f"Recall '{recall_key}' found — filling fields: {recall_info}")

    for fr in all_frames(page):
        try:
            cc_inp = fr.locator("input[name*='ConditionCode']").first
            if cc_inp.count() > 0 and cc_inp.is_visible():
                cc_inp.click()
                cc_inp.fill(recall_info["cc"])
                time.sleep(0.3)
                log(f"  CC → {recall_info['cc']}")
        except Exception:
            pass
        try:
            ccc_inp = fr.locator("input[name*='CustomerConcernCode']").first
            if ccc_inp.count() > 0 and ccc_inp.is_visible():
                ccc_inp.click()
                ccc_inp.fill(recall_info["ccc"])
                time.sleep(0.3)
                log(f"  CCC → {recall_info['ccc']}")
        except Exception:
            pass
        try:
            cp_inp = fr.locator("input[name*='CausalPartNumber'], input[name*='causalPart']").first
            if cp_inp.count() > 0 and cp_inp.is_visible():
                cp_inp.click()
                cp_inp.fill(recall_info["causal_part"])
                time.sleep(0.3)
                log(f"  Causal Part → {recall_info['causal_part']}")
        except Exception:
            pass
        try:
            cq_inp = fr.locator("input[name*='CausalPartQuantity'], input[name*='causalPartQty']").first
            if cq_inp.count() > 0 and cq_inp.is_visible():
                cq_inp.click()
                cq_inp.fill(recall_info["causal_qty"])
                time.sleep(0.3)
                log(f"  Causal Qty → {recall_info['causal_qty']}")
        except Exception:
            pass

    log("SCCK032 recall fields filled.")
    return True


def extract_cc_from_comments(comments: str) -> str | None:
    """
    Extract a condition code from technician comments text.
    Handles patterns like: CC 33, CC:33, CC-33, CC33, CC=33, CONDITION CODE D4
    Skips CC ** (means "unknown").
    Returns a validated 2-char code or None.
    """
    if not comments:
        return None

    text = comments.upper()

    # Detect CC ** pattern (means unknown/placeholder) — won't match the regex below
    if re.search(r"(?:CONDITION\s*CODE|CC)\s*[:=\-]?\s*\*{2}", text):
        log("Found CC ** (unknown placeholder) in comments.")

    # Try to extract: CC 33, CC:33, CC-33, CC=33, CC33, CONDITION CODE D4
    m = re.search(r"(?:CONDITION\s*CODE|CC)\s*[:=\-]?\s*([A-Z0-9]{1,2})\b", text)
    if m:
        raw = m.group(1)
        # Pad single digit: "1" → "01", "5" → "05"
        if len(raw) == 1 and raw.isdigit():
            raw = raw.zfill(2)
        if raw in VALID_CONDITION_CODES:
            log(f"Regex extracted condition code: '{raw}' from comments.")
            return raw
        else:
            log(f"Regex matched '{raw}' but it's not a valid condition code.")

    return None


def infer_cc_with_llm(comments: str) -> str | None:
    """
    Use the configured LLM (config.toml → [ai]) to infer a condition code from
    technician comments. Returns a validated 2-char code or None.
    Works with any provider (Anthropic, OpenAI, Gemini, Ollama); if none is
    configured/available the call returns None and the caller carries on.
    """
    from ows_ai import infer

    # Build the code list for the prompt
    code_lines = []
    for category, codes in CONDITION_CODES_BY_CATEGORY.items():
        code_lines.append(f"\n{category}:")
        for code, desc in codes.items():
            code_lines.append(f"  {code} — {desc}")
    code_list_str = "\n".join(code_lines)

    prompt = (
        "You are a Ford warranty claim assistant. Based on the technician comments below, "
        "determine the most appropriate Condition Code (CC). Respond with ONLY the 2-character "
        "code (e.g. '42' or 'D4'). If you cannot determine it, respond with 'UNKNOWN'.\n\n"
        f"Valid codes:\n{code_list_str}\n\n"
        f"Technician comments:\n{comments}\n\n"
        "Condition Code:"
    )

    raw = infer(prompt, max_tokens=10)
    if not raw:
        return None
    result = raw.strip().upper()
    if len(result) == 1 and result.isdigit():
        result = result.zfill(2)
    if result in VALID_CONDITION_CODES:
        log(f"LLM inferred condition code: '{result}'")
        return result
    # Model may have added prose — pull out the first valid code token
    for tok in re.findall(r"\b[A-Z0-9]{1,2}\b", result):
        cand = tok.zfill(2) if (len(tok) == 1 and tok.isdigit()) else tok
        if cand in VALID_CONDITION_CODES:
            log(f"LLM inferred condition code: '{cand}'")
            return cand
    log(f"LLM returned '{result}' — not a valid condition code.")
    return None


# Backward-compatible alias
infer_cc_with_claude = infer_cc_with_llm


def fix_rov0068_condition_code(page: Page, claim_frame: Frame) -> bool:
    """
    ROV0068 — CC is required for the Claim.
    Fix: Determine the correct condition code and fill it in.
    Priority: subcode check → regex from comments → Claude API fallback.
    """
    log("Applying ROV0068 fix: determining condition code …")

    # ── Try rental claim first ─────────────────────────────────────────────
    if fix_rental_claim(page, claim_frame):
        return True

    cc = None

    # ── Step A: Check subcode ────────────────────────────────────────────
    subcode_val = None
    for fr in all_frames(page):
        try:
            prog_inputs = fr.locator("input[name*='ProgramCode']").all()
            for inp in prog_inputs:
                try:
                    val = inp.input_value(timeout=1000).strip().upper()
                    if val:
                        subcode_val = val
                        if val in SUBCODE_CC_82:
                            cc = "82"
                            log(f"Subcode '{val}' → condition code 82 (Freight/Postage/Maintenance).")
                        break
                except Exception:
                    continue
            if subcode_val:
                break
        except Exception:
            continue

    # Check recall lookup — also fill CCC if present
    recall_ccc = None
    if not cc and subcode_val and subcode_val in SUBCODE_LOOKUP:
        recall_info = SUBCODE_LOOKUP[subcode_val]
        cc = recall_info["cc"]
        recall_ccc = recall_info.get("ccc")
        log(f"Recall lookup for '{subcode_val}' → CC='{cc}', CCC='{recall_ccc}'.")

    # ── Step B: Read technician comments ──────────────────────────────────
    comments = ""
    if not cc:
        for fr in all_frames(page):
            try:
                # Primary: editable textarea with known ID
                ta = fr.locator("textarea#TechnicianComments")
                if ta.count() > 0:
                    comments = ta.input_value(timeout=2000)
                    break
                # Fallback: read-only textarea next to "Technician Comments:" label
                label = fr.locator("label:has-text('Technician Comments:')")
                if label.count() > 0:
                    ta = label.locator("xpath=ancestor::td/following-sibling::td//textarea").first
                    if ta.count() > 0:
                        comments = ta.input_value(timeout=2000)
                        break
            except Exception:
                continue

        # ── Step C: Try regex, then Claude fallback ───────────────────────
        cc = extract_cc_from_comments(comments)
        if not cc:
            log("Regex did not find a condition code — trying Claude API fallback …")
            cc = infer_cc_with_llm(comments)
        if not cc:
            log("Could not determine condition code — ROV0068 fix failed.")
            return False

    # ── Step D: Fill the ConditionCode field (and CCC if recall) ────────────
    cc_filled = False
    for fr in all_frames(page):
        try:
            cc_inputs = fr.locator("input[name*='ConditionCode']").all()
            for inp in cc_inputs:
                try:
                    if inp.is_visible():
                        inp.click()
                        inp.fill(cc)
                        time.sleep(0.5)
                        log(f"Entered condition code '{cc}' into ConditionCode field.")
                        cc_filled = True
                        break
                except Exception:
                    continue
            if cc_filled:
                break
        except Exception:
            continue

    if not cc_filled:
        log("Could not find ConditionCode field to fill.")
        return False

    # Fill CCC if we got it from recall lookup
    if recall_ccc:
        for fr in all_frames(page):
            try:
                ccc_inp = fr.locator("input[name*='CustomerConcernCode']").first
                if ccc_inp.count() > 0 and ccc_inp.is_visible():
                    ccc_inp.click()
                    ccc_inp.fill(recall_ccc)
                    time.sleep(0.5)
                    log(f"Entered CCC '{recall_ccc}' into CustomerConcernCode field.")
                    break
            except Exception:
                continue

    return True


def fix_rvc0011_validation_code(page: Page, claim_frame: Frame) -> bool:
    """
    RVC0011 — Missing or incomplete Repair Validation Code.
    Fix: Extract the R-prefixed 13-char validation code from Technician Comments
    and enter it into the Approval Code field.
    """
    log("Applying RVC0011 fix: extracting validation code from Technician Comments …")

    # Read Technician Comments
    comments = ""
    for fr in all_frames(page):
        try:
            ta = fr.locator("textarea#TechnicianComments")
            if ta.count() > 0:
                comments = ta.input_value(timeout=2000)
                break
        except Exception:
            continue

    if not comments:
        log("Unable to scrape RVC from technician comments")
        return False

    # Extract R-prefixed 13-char alphanumeric validation code
    m = re.search(r"\b(R[A-Z0-9]{12})\b", comments.upper())
    if not m:
        log(f"Unable to scrape RVC from technician comments: {comments[:200]}")
        return False

    code = m.group(1)
    log(f"Extracted validation code: {code}")

    return fill_approval_code(page, code)


def fix_sfru448_subcode(page: Page, claim_frame: Frame) -> bool:
    """
    SFRU448 — PLEASE USE SUB CODE.
    Fix: Enter the configured subcode (config.toml → [subcodes] sfru448_subcode)
    into the ProgramCode (subcode) field.
    """
    subcode = CONFIG["subcodes"]["sfru448_subcode"]
    log(f"Applying SFRU448 fix: entering '{subcode}' into subcode field …")
    for fr in all_frames(page):
        try:
            prog_inputs = fr.locator("input[name*='ProgramCode']").all()
            for inp in prog_inputs:
                try:
                    if inp.is_visible():
                        inp.click()
                        inp.fill(subcode)
                        time.sleep(0.5)
                        log(f"Entered '{subcode}' into ProgramCode field.")
                        return True
                except Exception:
                    continue
        except Exception:
            continue
    log("Could not find ProgramCode field.")
    return False


def fix_lab0019_stars_id(page: Page, claim_frame: Frame) -> bool:
    """
    LAB0019 — Technician Identification is required on all Ford standard labor
    operations.  Fix: fill every empty ServiceTechnicianID input with the
    default technician's STARS ID (config.toml → [technicians], or STARS_ID env).
    """
    STARS_ID = get_stars_id()
    if not STARS_ID:
        log("LAB0019 fix skipped: no STARS ID configured. Set one in config.toml "
            "([technicians] section) or as STARS_ID in your .env file.")
        return False
    log(f"Applying LAB0019 fix: entering STARS ID '{STARS_ID}' into ServiceTechnicianID fields …")
    filled = 0
    for fr in all_frames(page):
        try:
            inputs = fr.locator("input[name*='ServiceTechnicianID']").all()
            for inp in inputs:
                try:
                    if inp.is_visible() and not inp.input_value().strip():
                        inp.click()
                        inp.fill(STARS_ID)
                        time.sleep(0.3)
                        filled += 1
                except Exception:
                    continue
        except Exception:
            continue
    if filled:
        log(f"Filled {filled} ServiceTechnicianID field(s) with '{STARS_ID}'.")
        return True
    log("Could not find any empty ServiceTechnicianID fields.")
    return False


# BES0027 — Date too old
fix_bes0027 = _approval_code_fix("BES0027", "DDET")


def fix_bom0002_duplicate_part(page: Page, claim_frame: Frame) -> bool:
    """
    BOM0002 — Duplicate part number on the same repair line.
    Fix: Find rows with the duplicate part number, sum their quantities,
    delete all but the first row, and set the first row's qty to the total.
    """
    log("Applying BOM0002 fix: merging duplicate part rows …")

    # ── Step A: Extract duplicate part number from error message ──────────
    part_number = None
    for fr in all_frames(page):
        try:
            html = fr.content()
            m = re.search(r"BOM0002.*?Part\s+(\S+)\s+is duplicated", html)
            if m:
                part_number = m.group(1).strip()
                break
        except Exception:
            continue
    if not part_number:
        log("Could not extract duplicate part number from BOM0002 message.")
        return False
    log(f"Duplicate part number: {part_number}")

    # ── Step B: Find all parts rows with this part number ─────────────────
    parts_frame = None
    for fr in all_frames(page):
        try:
            rows = fr.locator('tr[id*="pRepairDetails$pParts$l"]')
            if rows.count() > 0:
                parts_frame = fr
                break
        except Exception:
            continue
    if not parts_frame:
        log("Could not find parts grid.")
        return False

    rows = parts_frame.locator('tr[id*="pRepairDetails$pParts$l"]')
    row_count = rows.count()
    log(f"Found {row_count} parts rows. Scanning for '{part_number}' …")

    matching_indices = []  # 0-based indices into the rows locator
    quantities = []
    for i in range(row_count):
        row = rows.nth(i)
        try:
            pn_input = row.locator('input[name*="pCompletePartNumber"]').first
            if pn_input.count() == 0:
                continue
            val = pn_input.input_value(timeout=2000).strip()
            if val.upper() == part_number.upper():
                qty_input = row.locator('input[name*="pItemQuantity"]').first
                qty_val = qty_input.input_value(timeout=2000).strip() if qty_input.count() > 0 else "1"
                try:
                    qty = float(qty_val)
                except ValueError:
                    qty = 1.0
                matching_indices.append(i)
                quantities.append(qty)
                log(f"  Row {i+1}: part={val}, qty={qty}")
        except Exception:
            continue

    if len(matching_indices) < 2:
        log(f"Found {len(matching_indices)} matching row(s) — expected at least 2. Cannot merge.")
        return False

    total_qty = sum(quantities)
    # Format as int if whole number, else keep decimal
    if total_qty == int(total_qty):
        qty_str = f"{int(total_qty):.2f}"
    else:
        qty_str = f"{total_qty:.2f}"
    log(f"Total quantity: {' + '.join(str(q) for q in quantities)} = {qty_str}")

    # ── Step C: Delete duplicate rows (all except the first match) ────────
    # Delete from bottom to top so indices don't shift
    for idx in reversed(matching_indices[1:]):
        # Re-fetch rows each time since DOM changes after delete
        rows = parts_frame.locator('tr[id*="pRepairDetails$pParts$l"]')
        row = rows.nth(idx)
        delete_btn = row.locator('a[title*="Delete this rule"]').first
        if delete_btn.count() > 0:
            delete_btn.scroll_into_view_if_needed()
            delete_btn.click()
            log(f"  Deleted duplicate row {idx+1}.")
            time.sleep(2.0)  # Wait for Pega to refresh the grid
        else:
            log(f"  No delete button found on row {idx+1}.")

    # ── Step D: Update quantity on the remaining row ──────────────────────
    # Re-fetch rows after deletions
    time.sleep(1.0)
    rows = parts_frame.locator('tr[id*="pRepairDetails$pParts$l"]')
    # Find the remaining row with this part number
    for i in range(rows.count()):
        row = rows.nth(i)
        try:
            pn_input = row.locator('input[name*="pCompletePartNumber"]').first
            if pn_input.count() == 0:
                continue
            val = pn_input.input_value(timeout=2000).strip()
            if val.upper() == part_number.upper():
                qty_input = row.locator('input[name*="pItemQuantity"]').first
                if qty_input.count() > 0:
                    qty_input.click()
                    qty_input.fill(qty_str)
                    time.sleep(0.5)
                    log(f"  Set quantity to {qty_str} on remaining row.")
                    break
        except Exception:
            continue

    log("BOM0002 fix applied — duplicate rows merged.")
    return True


def read_technician_comments(page: Page) -> str | None:
    """Read the TechnicianComments textarea and return text, or None."""
    for fr in all_frames(page):
        try:
            ta = fr.locator("textarea#TechnicianComments")
            if ta.count() and ta.first.is_visible(timeout=500):
                text = ta.first.input_value(timeout=2000).strip()
                if text:
                    return text
        except Exception:
            continue
    return None


def extract_part_from_comments(comments: str) -> str | None:
    """
    Extract a Ford part number from technician comments using regex.
    Tries explicit markers first, then general Ford part number patterns.
    """
    upper = comments.upper()

    # Explicit markers: "CAUSAL PART", "PART#", "P/N", "REPLACED"
    marker_patterns = [
        r'CAUSAL\s*PART\s*[#:=\-]?\s*(\S+)',
        r'PART\s*#\s*[#:=\-]?\s*(\S+)',
        r'P/?N\s*[#:=\-]?\s*(\S+)',
        r'REPLACED\s+(\S+)',
    ]
    for pat in marker_patterns:
        m = re.search(pat, upper)
        if m:
            candidate = m.group(1).strip('.,;:')
            if (re.match(r'^[A-Z0-9]{4,10}$', candidate)
                    and re.search(r'\d', candidate)
                    and re.search(r'[A-Z]', candidate)):
                log(f"  Extracted part from comments (marker): {candidate}")
                return candidate

    # General Ford part patterns
    general_patterns = [
        r'\b(\d{1,3}[A-Z][A-Z0-9]{2,7})\b',          # 14B291, 7S004, 18D890
        r'\b([A-Z]{2}\d[A-Z][0-9A-Z]{3,6}[A-Z]?)\b',  # PL3Z5C226A
    ]
    for pat in general_patterns:
        m = re.search(pat, upper)
        if m:
            candidate = m.group(1)
            log(f"  Extracted part from comments (pattern): {candidate}")
            return candidate

    return None


def infer_part_with_llm(comments: str) -> str | None:
    """
    Use the configured LLM (config.toml → [ai]) to identify the causal/replaced
    part number from comments. Returns a validated part number string or None.
    """
    from ows_ai import infer

    prompt = (
        "You are a Ford warranty claim assistant. Based on the technician comments below, "
        "identify the primary part number that was replaced or repaired. "
        "Return ONLY the Ford part number (e.g. '14B291', '19703', 'PL3Z5C226A'). "
        "If you cannot determine a specific part number, respond with 'UNKNOWN'.\n\n"
        f"Technician comments:\n{comments}\n\n"
        "Part number:"
    )

    raw = infer(prompt, max_tokens=30)
    if not raw:
        return None
    # Model may add prose — check the whole reply, then each token
    for cand in [raw.strip().upper()] + re.findall(r"[A-Z0-9]{4,10}", raw.upper()):
        if (cand != "UNKNOWN"
                and re.match(r'^[A-Z0-9]{4,10}$', cand)
                and re.search(r'\d', cand)
                and re.search(r'[A-Z]', cand)):
            log(f"  LLM inferred part number: '{cand}'")
            return cand
    log(f"  LLM returned '{raw.strip()}' — no valid part number.")
    return None


# Backward-compatible alias
infer_part_with_claude = infer_part_with_llm


def fix_sub0003(page: Page, claim_frame: Frame) -> bool:
    """
    SUB0003 — Claim does not have Causal Part Number.
    Paths: (1) rental claim via fix_rental_claim, (2) type 31 recall via
    SUBCODE_LOOKUP, (3) non-recall: extract causal part from Technician
    Comments (regex then Claude fallback) and add via add_causal_part.
    """
    log("Applying SUB0003 fix …")

    # ── Try rental claim first ─────────────────────────────────────────────
    if fix_rental_claim(page, claim_frame):
        return True

    # ── Check claim type is 31 (recall) ───────────────────────────────────────
    claim_type_val = None
    for fr in all_frames(page):
        try:
            sel = fr.locator("select#ClaimTypeDesc")
            if sel.count() > 0:
                claim_type_val = sel.input_value(timeout=2000)
                break
        except Exception:
            continue

    if not claim_type_val or not claim_type_val.startswith("31"):
        # Non-recall: try extracting causal part from Technician Comments
        log(f"Not a type 31 recall (got '{claim_type_val}') — trying comments extraction …")
        comments = read_technician_comments(page)
        if comments:
            part = extract_part_from_comments(comments)
            if not part:
                part = infer_part_with_llm(comments)
            if part:
                row_idx = add_causal_part(page, part)
                if row_idx:
                    log(f"SUB0003 fix applied — causal part '{part}' from technician comments.")
                    return True
        log("Could not determine causal part — SUB0003 fix skipped.")
        return False

    # ── Read subcode to look up recall info ───────────────────────────────────
    subcode_val = None
    for fr in all_frames(page):
        try:
            prog_inputs = fr.locator("input[name*='ProgramCode']").all()
            for inp in prog_inputs:
                try:
                    val = inp.input_value(timeout=1000).strip()
                    if val:
                        subcode_val = val
                        break
                except Exception:
                    continue
            if subcode_val:
                break
        except Exception:
            continue

    if not subcode_val:
        log("Could not read subcode — SUB0003 fix skipped.")
        return False

    recall_key = subcode_val.upper()
    recall_info = SUBCODE_LOOKUP.get(recall_key)
    if not recall_info:
        log(f"No recall lookup entry for '{recall_key}' — SUB0003 fix skipped.")
        return False

    # ── Fill CC and CCC in repair details ────────────────────────────────────
    log(f"Filling recall fields from lookup ({recall_key}): {recall_info}")
    for fr in all_frames(page):
        try:
            cc_inp = fr.locator("input[name*='ConditionCode']").first
            if cc_inp.count() > 0 and cc_inp.is_visible():
                cc_inp.click()
                cc_inp.fill(recall_info["cc"])
                time.sleep(0.5)
                log(f"  CC → {recall_info['cc']}")
        except Exception:
            pass
        try:
            ccc_inp = fr.locator("input[name*='CustomerConcernCode']").first
            if ccc_inp.count() > 0 and ccc_inp.is_visible():
                ccc_inp.click()
                ccc_inp.fill(recall_info["ccc"])
                time.sleep(0.5)
                log(f"  CCC → {recall_info['ccc']}")
        except Exception:
            pass

    # ── Click "Add a row" in parts grid to create a parts row ─────────────
    row_added = False
    for fr in all_frames(page):
        try:
            add_btn = fr.locator("a[aria-label*='Add a row'][name*='PartsInformation']").first
            if add_btn.count() > 0 and add_btn.is_visible():
                add_btn.click()
                time.sleep(2.0)
                log("Clicked 'Add a row' in parts grid.")
                row_added = True
                break
        except Exception:
            continue
    if not row_added:
        log("Could not find 'Add a row' button in parts grid — SUB0003 fix skipped.")
        return False

    # ── Fill part number in the new (empty) grid row ────────────────────────
    new_row_index = None
    for fr in all_frames(page):
        try:
            pn_inputs = fr.locator("input[name*='pParts'][name*='CompletePartNumber']").all()
            for inp in pn_inputs:
                try:
                    val = inp.input_value(timeout=1000).strip()
                    if not val:  # empty = newly added row
                        inp.click()
                        inp.fill(recall_info["causal_part"])
                        time.sleep(0.5)
                        log(f"  Part Number → {recall_info['causal_part']}")
                        # Extract row index from name for causal indicator
                        name = inp.get_attribute("name")
                        match = re.search(r'\$pParts(\$l\d+)\$', name)
                        if match:
                            new_row_index = match.group(1)  # e.g. "$l2"
                        break
                except Exception:
                    continue
            if new_row_index:
                break
        except Exception:
            pass

    # ── Click causal indicator radio on the same row ──────────────────────
    click_causal_indicator(page, row_index=new_row_index)
    log("SUB0003 fix applied — recall fields filled.")
    return True


def fix_sfrne16(page: Page, claim_frame: Frame) -> bool:
    """
    SFRNE16 — Repair eligible as ESP contract repair.
    Fix: Delete misc code 'W1' row and set subcode to 'ESP'.
    """
    log("Applying SFRNE16 fix: deleting W1 misc row and setting subcode to ESP …")

    # ── Delete W1 misc expense row ─────────────────────────────────────────
    w1_deleted = False
    log(f"  Total frames on page: {len(page.frames)}")
    for fi, fr in enumerate(page.frames):
        try:
            expense_inputs = fr.locator("input[name*='ExpenseCode']").all()
            if not expense_inputs:
                continue
            log(f"  Frame[{fi}] ({fr.url[:80]}): {len(expense_inputs)} ExpenseCode inputs")
            for i, inp in enumerate(expense_inputs):
                try:
                    val = inp.input_value(timeout=2000).strip().upper()
                    log(f"    ExpenseCode[{i}] = '{val}'")
                    if val == "W1":
                        # Extract row index from input name, e.g. $pMisc$l1$ → Misc(1)
                        inp_name = inp.get_attribute("name") or ""
                        log(f"    W1 input name: {inp_name}")
                        import re as _re
                        m = _re.search(r'\$pMisc\$l(\d+)\$', inp_name)
                        if m:
                            row_idx = m.group(1)
                            # Delete button name contains Misc(N)
                            delete_sel = f'a[name*="Misc({row_idx})"][title*="Delete"]'
                            delete_btn = fr.locator(delete_sel).first
                            log(f"    Trying delete selector: {delete_sel} → count={delete_btn.count()}")
                            if delete_btn.count() > 0:
                                delete_btn.scroll_into_view_if_needed()
                                time.sleep(1.0)
                                delete_btn.click()
                                time.sleep(2.0)
                                w1_deleted = True
                                log("Deleted W1 misc expense row.")
                        if not w1_deleted:
                            # Fallback: try ancestor row approach
                            row = inp.locator("xpath=ancestor::tr[1]")
                            for selector in [
                                'a[title*="Delete"]',
                                'a[data-click*="removeFromRepeatSource"]',
                            ]:
                                delete_btn = row.locator(selector).first
                                if delete_btn.count() > 0:
                                    delete_btn.scroll_into_view_if_needed()
                                    time.sleep(1.0)
                                    delete_btn.click()
                                    time.sleep(2.0)
                                    w1_deleted = True
                                    log(f"Deleted W1 misc expense row (fallback: {selector}).")
                                    break
                        if not w1_deleted:
                            log("  Could not find delete button for W1 row.")
                        break
                except Exception as e:
                    log(f"    ExpenseCode[{i}] error: {e}")
                    continue
            if w1_deleted:
                break
        except Exception:
            continue

    if not w1_deleted:
        log("Could not find or delete W1 misc expense row.")

    # ── Set subcode to ESP ─────────────────────────────────────────────────
    subcode_filled = False
    for fr in page.frames:
        try:
            prog_inputs = fr.locator("input[name*='ProgramCode']").all()
            for inp in prog_inputs:
                try:
                    if inp.is_visible():
                        inp.click()
                        inp.fill("ESP")
                        time.sleep(0.5)
                        subcode_filled = True
                        log("Subcode → ESP")
                        break
                except Exception:
                    continue
            if subcode_filled:
                break
        except Exception:
            continue

    if not subcode_filled:
        log("Could not find ProgramCode field to set ESP.")
        return False

    log("SFRNE16 fix applied.")
    return True


def fix_sfru379(page: Page, claim_frame: Frame) -> bool:
    """SFRU379 — MHT transmission on hybrid: set causal part to MHT7000."""
    log("Applying SFRU379 fix: setting causal part to 'MHT7000' …")
    result = add_causal_part(page, "MHT7000")
    return result is not None


def fix_lov0007(page: Page, claim_frame: Frame) -> bool:
    """LOV0007 — Labor operation overlap: delete the lower-amount labor row."""
    log("Applying LOV0007 fix: deleting lower-amount overlapping labor row …")

    # ── Step A: Extract the two labor op codes from the error message ─────
    op1 = op2 = None
    for fr in all_frames(page):
        try:
            html = fr.content()
            m = re.search(r"LOV0007.*?LABOR OPERATION\s+(\S+)\s+OVERLAPS WITH\s+(\S+)", html)
            if m:
                op1, op2 = m.group(1).strip(), m.group(2).strip()
                break
        except Exception:
            continue
    if not op1 or not op2:
        log("Could not extract labor op codes from LOV0007 message.")
        return False
    log(f"Overlapping ops: {op1} vs {op2}")

    # ── Step B: Find the labor grid frame and matching rows ──────────────
    labor_frame = None
    for fr in all_frames(page):
        try:
            rows = fr.locator('tr[id*="pRepairDetails$pLabor$l"]')
            if rows.count() > 0:
                labor_frame = fr
                break
        except Exception:
            continue
    if not labor_frame:
        log("Could not find labor grid.")
        return False

    rows = labor_frame.locator('tr[id*="pRepairDetails$pLabor$l"]')
    row_count = rows.count()
    log(f"Found {row_count} labor rows. Scanning for ops {op1} and {op2} …")

    # Collect index, op code, and amount for the two matching rows
    matches = {}  # op_code -> (row_index, amount)
    for i in range(row_count):
        row = rows.nth(i)
        try:
            op_input = row.locator('input[name*="pLaborTechnicianID"]').first
            if op_input.count() == 0:
                continue
            op_val = op_input.input_value(timeout=2000).strip()
            if op_val in (op1, op2):
                amt_input = row.locator('input[name*="pLaborAmount$ptheCurrencyAmount"]').first
                amt_str = amt_input.input_value(timeout=2000).strip() if amt_input.count() > 0 else "0"
                try:
                    amt = float(amt_str)
                except ValueError:
                    amt = 0.0
                matches[op_val] = (i, amt)
                log(f"  Row {i+1}: op={op_val}, amount={amt}")
        except Exception:
            continue

    if len(matches) < 2:
        log(f"Found {len(matches)} matching labor row(s) — expected 2. Cannot resolve overlap.")
        return False

    # ── Step C: Delete the row with the lower amount ─────────────────────
    (op_a, (idx_a, amt_a)), (op_b, (idx_b, amt_b)) = list(matches.items())
    if amt_a <= amt_b:
        del_idx, del_op, del_amt = idx_a, op_a, amt_a
        keep_op, keep_amt = op_b, amt_b
    else:
        del_idx, del_op, del_amt = idx_b, op_b, amt_b
        keep_op, keep_amt = op_a, amt_a
    log(f"Keeping {keep_op} (${keep_amt:.2f}), deleting {del_op} (${del_amt:.2f}) …")

    rows = labor_frame.locator('tr[id*="pRepairDetails$pLabor$l"]')
    row = rows.nth(del_idx)
    delete_btn = row.locator('a[title*="Delete this rule"]').first
    if delete_btn.count() == 0:
        log(f"No delete button found on row {del_idx+1}.")
        return False
    delete_btn.scroll_into_view_if_needed()
    delete_btn.click()
    log(f"Deleted labor row {del_idx+1} (op {del_op}).")
    time.sleep(2.0)

    log("LOV0007 fix applied — lower-amount labor row deleted.")
    return True


def extract_dtc_from_comments(page: Page) -> str | None:
    """Read TechnicianComments and extract first DTC code ([PBCU]\\d{4})."""
    for fr in all_frames(page):
        try:
            ta = fr.locator("textarea#TechnicianComments")
            if ta.count() and ta.first.is_visible(timeout=500):
                text = ta.first.input_value(timeout=2000)
                match = re.search(r"\b[PBCU]\d[0-9A-Z]{3}\b", text, re.IGNORECASE)
                if match:
                    dtc = match.group(0).upper()
                    log(f"  Extracted DTC from comments: {dtc}")
                    return dtc
        except Exception:
            continue
    return None


def fix_sfrnf55(page: Page, claim_frame: Frame) -> bool:
    """
    SFRNF55 — PPT labor operations require DTC in Test Results.
    Fix: Extract DTC from Technician Comments, expand Test Results,
    add a diagnostic row, select 'Other DTC' type, enter the code.
    """
    log("Applying SFRNF55 fix: filling DTC in Test Results …")

    # 1. Extract DTC from comments, fall back to DSYMP (diag by symptom)
    dtc = extract_dtc_from_comments(page)
    if not dtc:
        dtc = "DSYMP"
        log(f"  No DTC code found in Technician Comments — using fallback '{dtc}'.")

    # 2. Expand the Test Results section (collapsed by default)
    expanded = False
    for fr in all_frames(page):
        try:
            # Try the collapsed icon first
            header = fr.locator("td.titleBarIconCollapsed[title='Disclose Test Results']")
            if header.count():
                header.first.scroll_into_view_if_needed(timeout=3000)
                time.sleep(0.5)
                header.first.click()
                time.sleep(1.5)
                expanded = True
                log("  Expanded Test Results section.")
                break
            # Also try the table header (works whether collapsed or expanded)
            tbl_header = fr.locator("table[aria-label='Disclose Test Results']")
            if tbl_header.count():
                tbl_header.first.scroll_into_view_if_needed(timeout=3000)
                time.sleep(0.5)
                tbl_header.first.click()
                time.sleep(1.5)
                expanded = True
                log("  Expanded Test Results section (via table header).")
                break
        except Exception:
            continue

    if not expanded:
        # Maybe already expanded — check for diagnostics table
        for fr in all_frames(page):
            try:
                tbl = fr.locator("table[pl_prop='.Diagnostics']")
                if tbl.count():
                    tbl.first.scroll_into_view_if_needed(timeout=3000)
                    expanded = True
                    break
            except Exception:
                continue
        if not expanded:
            log("  Could not find or expand Test Results section.")
            return False

    # 3. Click "Add a row" in the Diagnostics table
    row_added = False
    for fr in all_frames(page):
        try:
            add_btn = fr.locator("a.iconInsert[name^='DiagnosticData']")
            if add_btn.count():
                add_btn.first.scroll_into_view_if_needed(timeout=3000)
                time.sleep(0.5)
                add_btn.first.click()
                time.sleep(2.0)  # wait for Pega to append row and refresh section
                row_added = True
                log("  Clicked 'Add a row' in Diagnostics.")
                break
        except Exception:
            continue

    if not row_added:
        log("  Could not find 'Add a row' button in Diagnostics.")
        return False

    # 4. Select "OTHER DTC" from Type dropdown in the new row
    type_selected = False
    for fr in all_frames(page):
        try:
            sel = fr.locator("select#DiagnosticType")
            if sel.count() and sel.first.is_visible(timeout=2000):
                sel.first.select_option(value="OT")
                time.sleep(2.0)  # Pega fires server event to show Code input
                type_selected = True
                log("  Selected 'OTHER DTC' type.")
                break
        except Exception:
            continue

    if not type_selected:
        log("  Could not find Type dropdown in Diagnostics row.")
        return False

    # 5. Fill DTC code in the Code input (appears after type selection)
    code_filled = False
    for fr in all_frames(page):
        try:
            # Look for text input inside DiagnosticData section
            diag = fr.locator("div[data-node-id='DiagnosticData']")
            if diag.count():
                inputs = diag.first.locator("input[type='text']").all()
                for inp in inputs:
                    try:
                        if inp.is_visible(timeout=2000):
                            inp.click()
                            inp.fill(dtc)
                            inp.press("Tab")
                            time.sleep(0.5)
                            code_filled = True
                            log(f"  Filled DTC code: {dtc}")
                            break
                    except Exception:
                        continue
            if code_filled:
                break
        except Exception:
            continue

    if not code_filled:
        log(f"  Could not fill DTC code {dtc} in Diagnostics input.")
        return False

    log(f"SFRNF55 fix applied — DTC={dtc}, Type=Other DTC")
    return True


def fix_sfru779_prent_to_p11(page: Page, claim_frame: Frame) -> bool:
    """
    SFRU779 — PRENT can only be used for ≤10 days of rental.
    Fix: switch subcode from PRENT to P11.
    """
    log("Applying SFRU779 fix: switching subcode from PRENT to P11 …")
    for fr in all_frames(page):
        try:
            prog_inputs = fr.locator("input[name*='ProgramCode']").all()
            for inp in prog_inputs:
                try:
                    if inp.is_visible():
                        val = inp.input_value(timeout=1000).strip().upper()
                        if val == "PRENT":
                            inp.click()
                            inp.fill("P11")
                            time.sleep(0.5)
                            log("  Subcode: PRENT → P11")
                            return True
                except Exception:
                    continue
        except Exception:
            continue
    log("Could not find ProgramCode field with PRENT value.")
    return False


def fix_sfru685_maint_causal(page: Page, claim_frame: Frame) -> bool:
    """SFRU685 — QCM/ESQ subcode requires causal part. Add 'Maint' as causal part."""
    log("Applying SFRU685 fix: adding 'Maint' causal part …")
    row_idx = add_causal_part(page, "Maint")
    if not row_idx:
        return False
    log("SFRU685 fix applied — 'Maint' causal part added.")
    return True


def fix_sfruc09_test_drive(page: Page, claim_frame: Frame) -> bool:
    """SFRUC09 — In/out mileage same; delete test drive labor op rows."""
    log("Applying SFRUC09 fix: deleting test drive labor rows …")

    # Find labor grid
    labor_frame = None
    for fr in all_frames(page):
        try:
            rows = fr.locator('tr[id*="pRepairDetails$pLabor$l"]')
            if rows.count() > 0:
                labor_frame = fr
                break
        except Exception:
            continue
    if not labor_frame:
        log("Could not find labor grid.")
        return False

    rows = labor_frame.locator('tr[id*="pRepairDetails$pLabor$l"]')
    row_count = rows.count()

    # Scan for test drive ops (go in reverse so deletion indices stay valid)
    to_delete = []
    for i in range(row_count):
        row = rows.nth(i)
        try:
            op_input = row.locator('input[name*="pLaborTechnicianID"]').first
            if op_input.count() == 0:
                continue
            op_val = op_input.input_value(timeout=2000).strip().upper()
            if op_val in TEST_DRIVE_OPS:
                to_delete.append((i, op_val))
        except Exception:
            continue

    if not to_delete:
        # Collect all op codes for the error message
        all_ops = []
        for i in range(row_count):
            try:
                op_input = rows.nth(i).locator('input[name*="pLaborTechnicianID"]').first
                if op_input.count() > 0:
                    all_ops.append(op_input.input_value(timeout=2000).strip())
            except Exception:
                continue
        log(f"No known test drive ops found. Labor ops present: {all_ops}")
        log(f"Add the test drive op code to TEST_DRIVE_OPS in ows_fixes.py.")
        return False

    # Delete in reverse order to preserve indices
    for idx, op_val in reversed(to_delete):
        rows = labor_frame.locator('tr[id*="pRepairDetails$pLabor$l"]')
        row = rows.nth(idx)
        delete_btn = row.locator('a[title*="Delete this rule"]').first
        if delete_btn.count() == 0:
            log(f"No delete button on labor row {idx+1} (op {op_val}).")
            return False
        delete_btn.scroll_into_view_if_needed()
        delete_btn.click()
        log(f"Deleted test drive labor row {idx+1} (op {op_val}).")
        time.sleep(2.0)

    log(f"SFRUC09 fix applied — deleted {len(to_delete)} test drive row(s).")
    return True


# ── Registry ─────────────────────────────────────────────────────────────────
# Map error codes to their fix functions.
# Each function signature: fix_func(page, claim_frame) -> bool
def fix_rov0034_empty_part(page: Page, claim_frame: Frame) -> bool:
    """
    ROV0034 — Part Extended Amount should be numeric.
    Fix: Find part rows with empty or non-numeric quantity and delete them.
    """
    log("Applying ROV0034 fix: deleting part rows with empty quantity …")

    # ── Find parts grid frame ────────────────────────────────────────────
    parts_frame = None
    for fr in all_frames(page):
        try:
            rows = fr.locator('tr[id*="pRepairDetails$pParts$l"]')
            if rows.count() > 0:
                parts_frame = fr
                break
        except Exception:
            continue
    if not parts_frame:
        log("Could not find parts grid.")
        return False

    rows = parts_frame.locator('tr[id*="pRepairDetails$pParts$l"]')
    row_count = rows.count()
    log(f"Found {row_count} parts rows. Scanning for empty quantities …")

    # ── Find rows with empty/non-numeric quantity ────────────────────────
    bad_indices = []
    for i in range(row_count):
        row = rows.nth(i)
        try:
            qty_input = row.locator('input[name*="pItemQuantity"]').first
            if qty_input.count() == 0:
                continue
            val = qty_input.input_value(timeout=2000).strip()
            if not val:
                bad_indices.append(i)
                log(f"  Row {i+1}: quantity is empty.")
            else:
                try:
                    float(val)
                except ValueError:
                    bad_indices.append(i)
                    log(f"  Row {i+1}: quantity '{val}' is not numeric.")
        except Exception:
            continue

    if not bad_indices:
        log("No rows with empty/non-numeric quantity found.")
        return False

    # ── Delete bad rows bottom-up to preserve indices ────────────────────
    for idx in reversed(bad_indices):
        rows = parts_frame.locator('tr[id*="pRepairDetails$pParts$l"]')
        row = rows.nth(idx)
        delete_btn = row.locator('a[title*="Delete this rule"]').first
        if delete_btn.count() > 0:
            delete_btn.scroll_into_view_if_needed()
            delete_btn.click()
            log(f"  Deleted row {idx+1}.")
            time.sleep(2.0)
        else:
            log(f"  No delete button found on row {idx+1}.")

    log("ROV0034 fix applied — empty-quantity rows deleted.")
    return True


def fix_par0001_invalid_part(page: Page, claim_frame: Frame) -> bool:
    """
    PAR0001 / PRI0001 — Invalid Part Number / No Price Available.
    Fix: Find part rows with an empty description cell and delete them.
    Valid parts get a system-populated description; invalid ones don't.
    """
    log("Applying PAR0001 fix: deleting part rows with empty description …")

    # ── Find parts grid frame ────────────────────────────────────────────
    parts_frame = None
    for fr in all_frames(page):
        try:
            rows = fr.locator('tr[id*="pRepairDetails$pParts$l"]')
            if rows.count() > 0:
                parts_frame = fr
                break
        except Exception:
            continue
    if not parts_frame:
        log("Could not find parts grid.")
        return False

    rows = parts_frame.locator('tr[id*="pRepairDetails$pParts$l"]')
    row_count = rows.count()
    log(f"Found {row_count} parts rows. Scanning for empty descriptions …")

    # ── Find rows with price = 0.00 (invalid part) ─────────────────────────
    bad_indices = []
    for i in range(row_count):
        row = rows.nth(i)
        try:
            price_input = row.locator('input[name*="pUnitPriceAmount"]')
            if price_input.count() == 0:
                continue
            price_val = price_input.input_value(timeout=2000).strip()
            if not price_val or float(price_val) == 0:
                bad_indices.append(i)
                log(f"  Row {i+1}: price is 0.00 (invalid part).")
        except Exception:
            continue

    if not bad_indices:
        log("No rows with empty description found.")
        return False

    # ── Delete bad rows bottom-up to preserve indices ────────────────────
    for idx in reversed(bad_indices):
        rows = parts_frame.locator('tr[id*="pRepairDetails$pParts$l"]')
        row = rows.nth(idx)
        delete_btn = row.locator('a[title*="Delete this rule"]').first
        if delete_btn.count() > 0:
            delete_btn.scroll_into_view_if_needed()
            delete_btn.click()
            log(f"  Deleted row {idx+1}.")
            time.sleep(2.0)
        else:
            log(f"  No delete button found on row {idx+1}.")

    log("PAR0001 fix applied — invalid-part rows deleted.")
    return True


def fix_sfru730_subcode(page: Page, claim_frame: Frame) -> bool:
    """
    SFRU730 — Wrong subcode for Ford Protect claims.
    Fix: If current subcode is QCL (Lincoln), switch to QCM (Ford). Otherwise enter QCL.
    """
    for fr in all_frames(page):
        try:
            prog_inputs = fr.locator("input[name*='ProgramCode']").all()
            for inp in prog_inputs:
                try:
                    if inp.is_visible():
                        current = inp.input_value().strip().upper()
                        target = "QCM" if current == "QCL" else "QCL"
                        log(f"Applying SFRU730 fix: current subcode '{current}' → '{target}' …")
                        inp.click()
                        inp.fill(target)
                        time.sleep(0.5)
                        log(f"Entered '{target}' into ProgramCode field.")
                        return True
                except Exception:
                    continue
        except Exception:
            continue
    log("Could not find ProgramCode field.")
    return False


def fix_sfrne17_remove_subcode(page: Page, claim_frame: Frame) -> bool:
    """
    SFRNE17 — Repair is eligible for warranty coverage, remove contract type sub-code.
    Fix: Clear the ProgramCode (subcode) field.
    """
    log("Applying SFRNE17 fix: clearing subcode field …")
    for fr in all_frames(page):
        try:
            prog_inputs = fr.locator("input[name*='ProgramCode']").all()
            for inp in prog_inputs:
                try:
                    if inp.is_visible():
                        inp.click()
                        inp.fill("")
                        time.sleep(0.5)
                        log("Cleared ProgramCode (subcode) field.")
                        return True
                except Exception:
                    continue
        except Exception:
            continue
    log("Could not find ProgramCode field.")
    return False


def fix_res0001_exceeded_quantity(page: Page, claim_frame: Frame) -> bool:
    """
    RES0001 — Part quantity exceeds allowed maximum.
    Fix: Extract part number and allowed quantity from the error message,
    find the matching row in the parts grid, and set quantity to the allowed value.
    """
    log("Applying RES0001 fix: reducing part quantity to allowed maximum …")

    # ── Step A: Extract part number and allowed quantity from error message ──
    part_number = None
    allowed_qty = None
    for fr in all_frames(page):
        try:
            html = fr.content()
            m = re.search(r"RES0001.*?PART NUMBER\s+(\S+)\s+EXCEEDS ALLOWED QUANTITY OF\s+([\d.]+)", html)
            if m:
                part_number = m.group(1).strip()
                allowed_qty = float(m.group(2))
                break
        except Exception:
            continue
    if not part_number or allowed_qty is None:
        log("Could not extract part number / allowed quantity from RES0001 message.")
        return False
    log(f"Part: {part_number}, allowed qty: {allowed_qty}")

    # ── Step B: Find parts grid and matching row ─────────────────────────────
    parts_frame = None
    for fr in all_frames(page):
        try:
            rows = fr.locator('tr[id*="pRepairDetails$pParts$l"]')
            if rows.count() > 0:
                parts_frame = fr
                break
        except Exception:
            continue
    if not parts_frame:
        log("Could not find parts grid.")
        return False

    rows = parts_frame.locator('tr[id*="pRepairDetails$pParts$l"]')
    row_count = rows.count()
    for i in range(row_count):
        row = rows.nth(i)
        try:
            pn_input = row.locator('input[name*="pCompletePartNumber"]').first
            if pn_input.count() == 0:
                continue
            val = pn_input.input_value(timeout=2000).strip()
            if val.upper() == part_number.upper():
                qty_input = row.locator('input[name*="pItemQuantity"]').first
                if qty_input.count() == 0:
                    continue
                qty_str = f"{allowed_qty:.2f}"
                qty_input.fill("")
                qty_input.fill(qty_str)
                time.sleep(0.5)
                log(f"Set qty for {part_number} to {qty_str} (row {i+1}).")
                return True
        except Exception:
            continue

    log(f"Part {part_number} not found in parts grid.")
    return False


def fix_res0005_exceeded_capacity(page: Page, claim_frame: Frame) -> bool:
    """
    RES0005 — Part exceeds allowed maximum capacity for VIN.
    Fix: Extract part number from error message, find it in the parts grid,
    decrement quantity by 1. The fix loop will re-PreValidate and keep
    decrementing until it passes.
    """
    log("Applying RES0005 fix: decrementing part quantity …")

    # ── Step A: Extract part number from error message ──
    part_number = None
    for fr in all_frames(page):
        try:
            html = fr.content()
            m = re.search(r"RES0005.*?PART NUMBER\s+(\S+)\s+EXCEEDS", html)
            if m:
                part_number = m.group(1).strip()
                break
        except Exception:
            continue
    if not part_number:
        log("Could not extract part number from RES0005 message.")
        return False
    log(f"Part: {part_number}")

    # ── Step B: Find parts grid and matching row ──
    parts_frame = None
    for fr in all_frames(page):
        try:
            rows = fr.locator('tr[id*="pRepairDetails$pParts$l"]')
            if rows.count() > 0:
                parts_frame = fr
                break
        except Exception:
            continue
    if not parts_frame:
        log("Could not find parts grid.")
        return False

    rows = parts_frame.locator('tr[id*="pRepairDetails$pParts$l"]')
    row_count = rows.count()
    for i in range(row_count):
        row = rows.nth(i)
        try:
            pn_input = row.locator('input[name*="pCompletePartNumber"]').first
            if pn_input.count() == 0:
                continue
            val = pn_input.input_value(timeout=2000).strip()
            if val.upper() == part_number.upper():
                qty_input = row.locator('input[name*="pItemQuantity"]').first
                if qty_input.count() == 0:
                    continue
                current_qty = float(qty_input.input_value(timeout=2000).strip() or "0")
                new_qty = max(0, current_qty - 1)
                qty_str = f"{new_qty:.2f}"
                qty_input.fill("")
                qty_input.fill(qty_str)
                time.sleep(0.5)
                log(f"Decremented qty for {part_number}: {current_qty:.2f} → {qty_str} (row {i+1}).")
                return True
        except Exception:
            continue

    log(f"Part {part_number} not found in parts grid.")
    return False


def fix_sfrub05_battery_qty_zero(page: Page, claim_frame: Frame) -> bool:
    """
    SFRUB05 — SPW/OTC battery is an exchange with FAD. Set part quantity to zero.
    """
    log("Applying SFRUB05 fix: setting battery part quantity to 0 …")

    parts_frame = None
    for fr in all_frames(page):
        try:
            rows = fr.locator('tr[id*="pRepairDetails$pParts$l"]')
            if rows.count() > 0:
                parts_frame = fr
                break
        except Exception:
            continue
    if not parts_frame:
        log("Could not find parts grid.")
        return False

    rows = parts_frame.locator('tr[id*="pRepairDetails$pParts$l"]')
    row_count = rows.count()
    fixed_any = False
    for i in range(row_count):
        row = rows.nth(i)
        try:
            qty_input = row.locator('input[name*="pItemQuantity"]').first
            if qty_input.count() == 0:
                continue
            val = qty_input.input_value(timeout=2000).strip()
            if val and float(val) != 0:
                qty_input.fill("")
                qty_input.fill("0.00")
                time.sleep(0.5)
                pn_input = row.locator('input[name*="pCompletePartNumber"]').first
                pn = pn_input.input_value(timeout=2000).strip() if pn_input.count() > 0 else f"row {i+1}"
                log(f"Set qty for {pn} to 0.00 (row {i+1}).")
                fixed_any = True
        except Exception:
            continue

    if not fixed_any:
        log("No parts with non-zero quantity found.")
    return fixed_any


def fix_sfru733_approval_code(page: Page, claim_frame: Frame) -> bool:
    """
    SFRU733 — Repair exceeds self-approval level, needs prior approval code.
    Fix: Strip leading zero from Repair Line Number (so approval code matches),
    then check if an approval code is already present. If not, return False
    so it falls through to user review.
    """
    log("Applying SFRU733 fix: stripping leading zero + checking approval code …")

    # Step 1: Strip leading zero from Repair Line Number
    stripped = False
    for fr in all_frames(page):
        try:
            inputs = fr.locator("input").all()
            for inp in inputs:
                try:
                    name = inp.get_attribute("name") or ""
                    inp_id = inp.get_attribute("id") or ""
                    ident = (name + " " + inp_id).lower().replace(" ", "").replace("_", "").replace("-", "")
                    if "repairlinenumber" not in ident:
                        continue
                    val = inp.input_value(timeout=1000)
                    new_val = val.lstrip("0") or "0"
                    if new_val != val:
                        inp.click()
                        inp.fill(new_val)
                        time.sleep(0.5)
                        log(f"Stripped leading zero: '{val}' → '{new_val}'")
                        stripped = True
                    else:
                        log(f"Repair Line Number '{val}' has no leading zero.")
                    break
                except Exception:
                    continue
            if stripped:
                break
        except Exception:
            continue

    # Step 2: Check if approval code is already present
    selector = "input[name*='pRepairDetails'][name*='pApprovalCode']"
    for fr in all_frames(page):
        try:
            inputs = fr.locator(selector).all()
            for inp in inputs:
                try:
                    val = inp.input_value(timeout=1000).strip()
                    if val:
                        log(f"Approval code already present: '{val}'")
                        return stripped  # leading-zero strip may help it match
                except Exception:
                    continue
        except Exception:
            continue

    if stripped:
        log("Leading zero stripped — approval code may auto-populate on PreValidate.")
        return True

    log("No approval code found and no leading zero to strip — needs user review.")
    return False


ERROR_FIXES = {
    "SUB0003": fix_sub0003,
    "SFRU473": fix_sfru473,
    "SCCK602": fix_scck602,
    "SUB0006": fix_sub0006,
    "SUB0012": fix_sub0006,
    "ROV0068": fix_rov0068_condition_code,
    "BOM0002": fix_bom0002_duplicate_part,
    "SFRU448": fix_sfru448_subcode,
    "ODM0001": fix_odm0001,
    "ODM0002": fix_odm0002,
    "RVC0011": fix_rvc0011_validation_code,
    "LAB0019": fix_lab0019_stars_id,
    "BES0027": fix_bes0027,
    "RRP0001": fix_rrp0001,
    "SCCK032": fix_scck032,
    "SFRNE16": fix_sfrne16,
    "ROV0038": fix_sfrne16,
    "SFRU379": fix_sfru379,
    "LOV0007": fix_lov0007,
    "SFRU430": fix_sfru448_subcode,
    "SFPNK21": fix_scck602,
    "SFRU779": fix_sfru779_prent_to_p11,
    "SFRNG45": fix_sfrng45,
    "SFRU330": fix_sfrng45,
    "SFRNF55": fix_sfrnf55,
    "SFRU685": fix_sfru685_maint_causal,
    "SFRUC09": fix_sfruc09_test_drive,
    "ROV0034": fix_rov0034_empty_part,
    "PAR0001": fix_par0001_invalid_part,
    "PAR0002": fix_par0001_invalid_part,
    "PRI0001": fix_par0001_invalid_part,
    "SFRU730": fix_sfru730_subcode,
    "RES0001": fix_res0001_exceeded_quantity,
    "RES0005": fix_res0005_exceeded_capacity,
    "SFRUB05": fix_sfrub05_battery_qty_zero,
    "SFRU733": fix_sfru733_approval_code,
    "SFRNE17": fix_sfrne17_remove_subcode,
}


def try_fix_errors(page: Page, claim_frame: Frame, error_codes: list[str]) -> tuple[list[str], dict]:
    """
    Attempt to auto-fix known errors, then PreValidate and re-check.
    Returns (fixed_codes, post_fix_details).
    - fixed_codes: list of error codes that had fixes applied
    - post_fix_details: error scrape results AFTER PreValidate (or None if no fixes applied)
    """
    fixed = []
    current_codes = list(error_codes)
    max_rounds = 5  # safety limit

    failed_codes = set()  # track codes whose fix didn't work — don't retry
    for round_num in range(1, max_rounds + 1):
        # Work bottom to top — bottom fix often resolves ones above
        fixable = [(i, c) for i, c in enumerate(current_codes)
                   if c.upper() in ERROR_FIXES and c.upper() not in failed_codes]
        if not fixable:
            for code in current_codes:
                if code.upper() not in failed_codes:
                    log(f"No auto-fix available for {code}.")
            break

        # Pick the bottom-most fixable error
        idx, code = fixable[-1]
        fix_func = ERROR_FIXES[code.upper()]
        log(f"Round {round_num}: fixing {code} (bottom-up) …")
        try:
            if fix_func(page, claim_frame):
                fixed.append(code)
                log(f"Fix applied for {code}.")
            else:
                log(f"Fix for {code} did not succeed — skipping.")
                failed_codes.add(code.upper())
                continue  # try next fixable error
        except Exception as e:
            log(f"Fix for {code} failed with error: {e} — skipping.")
            failed_codes.add(code.upper())
            continue

        # PreValidate after each fix — settle first so DOM is stable
        log(f"Running PreValidate after fixing {code} …")
        time.sleep(3.0)
        click_prevalidate(page)
        post_details = scrape_errors_quick(page)

        if not post_details["error_codes"] and post_details["has_dec0007"]:
            log("PreValidate success — DEC0007 confirmed. Ready to submit.")
            return fixed, post_details
        elif not post_details["error_codes"]:
            # No errors but DEC0007 missing — retry PreValidate once
            log("No errors remain but DEC0007 not found — retrying PreValidate …")
            time.sleep(3.0)
            click_prevalidate(page)
            post_details = scrape_errors_quick(page)
            if post_details["has_dec0007"]:
                log("DEC0007 confirmed on retry. Ready to submit.")
            else:
                log("DEC0007 still not found after retry.")
            return fixed, post_details

        current_codes = post_details["error_codes"]
        log(f"Errors after round {round_num}: {current_codes}")
        for msg in post_details["all_messages"]:
            log(f"  → {msg}")

    if not fixed:
        return fixed, None

    # Final scrape if we exhausted rounds
    post_details = scrape_errors_quick(page)
    if post_details["error_codes"]:
        log(f"Errors remain after {len(fixed)} fix(es): {post_details['error_codes']}")
    return fixed, post_details
