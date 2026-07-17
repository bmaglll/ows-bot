#!/usr/bin/env python3
"""
Diagnostic script: inspect the parts grid DOM for RO 512125-01.
Connects to Chromium via CDP on port 9222 and dumps every piece of
information that might explain why BOM0002 fix can't find part rows.

Run:
  /nix/store/ycby1aq8jnhk7cgj3js0cqwvycqmqbkb-python3-3.12.12-env/bin/python3 \
    /home/bmag/projects/work/ows/diagnose_parts_grid.py
"""
from __future__ import annotations
import os
import re
import sys
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright

load_dotenv()
CDP_URL = os.getenv("CDP_URL", "http://127.0.0.1:9222")
TARGET_PART = "W520214S440"

DIVIDER = "-" * 72

def p(msg: str) -> None:
    print(msg, flush=True)

def divider(title: str = "") -> None:
    if title:
        p(f"\n{DIVIDER}\n  {title}\n{DIVIDER}")
    else:
        p(DIVIDER)

# ── JS helpers injected into each frame ──────────────────────────────────────

JS_ALL_TR_WITH_PARTS = """
() => {
    // Find every <tr> whose id contains "pParts" or "Parts" (case-sensitive)
    const results = [];
    document.querySelectorAll('tr').forEach(tr => {
        const id = tr.id || '';
        if (id.includes('pParts') || id.includes('Parts')) {
            results.push({
                id: id,
                className: tr.className,
                childCount: tr.children.length,
            });
        }
    });
    return results;
}
"""

JS_ALL_TR_WITH_PART_INPUTS = """
() => {
    // Find every <tr> that contains an input whose name includes "Part"
    const results = [];
    document.querySelectorAll('tr').forEach(tr => {
        const inputs = tr.querySelectorAll('input');
        for (const inp of inputs) {
            const name = inp.name || '';
            if (name.toLowerCase().includes('part')) {
                results.push({
                    trId: tr.id,
                    trClass: tr.className,
                    inputName: inp.name,
                    inputType: inp.type,
                    inputValue: inp.value,
                });
                break;  // one per row is enough
            }
        }
    });
    return results;
}
"""

JS_ALL_PART_INPUTS = """
() => {
    // Find every input/span/td that mentions the duplicate part number
    const TARGET = arguments[0];
    const results = [];

    // Inputs
    document.querySelectorAll('input').forEach(el => {
        const name = el.name || '';
        const val  = el.value || '';
        if (name.toLowerCase().includes('part') || val.toUpperCase() === TARGET) {
            results.push({
                tag: 'INPUT',
                name: el.name,
                type: el.type,
                value: el.value,
                id: el.id,
            });
        }
    });

    // Spans / tds that contain the part number text
    document.querySelectorAll('span, td').forEach(el => {
        if ((el.textContent || '').trim().toUpperCase() === TARGET) {
            results.push({
                tag: el.tagName,
                name: el.getAttribute('name') || '',
                id: el.id || '',
                className: el.className || '',
                text: el.textContent.trim(),
                parentTagId: el.parentElement ? (el.parentElement.id || '(no id)') : '(none)',
            });
        }
    });

    return results;
}
"""

JS_ALL_INPUTS_WITH_NAME = """
() => {
    // Dump ALL inputs that have a name attribute, grouped by name pattern
    const results = [];
    document.querySelectorAll('input[name]').forEach(el => {
        results.push({
            name: el.name,
            type: el.type,
            value: el.value,
            id: el.id,
        });
    });
    return results;
}
"""

JS_SAMPLE_TR_IDS = """
() => {
    // First 60 <tr> ids (non-empty) so we can see the naming pattern
    const ids = [];
    for (const tr of document.querySelectorAll('tr')) {
        if (tr.id) ids.push(tr.id);
        if (ids.length >= 60) break;
    }
    return ids;
}
"""

JS_FIND_TARGET_TEXT = """
(target) => {
    // Walk ALL text nodes looking for the part number string
    const walker = document.createTreeWalker(
        document.body, NodeFilter.SHOW_TEXT, null);
    const results = [];
    let node;
    while ((node = walker.nextNode())) {
        if (node.nodeValue && node.nodeValue.includes(target)) {
            const parent = node.parentElement;
            results.push({
                text: node.nodeValue.trim().substring(0, 120),
                parentTag: parent ? parent.tagName : '?',
                parentId: parent ? (parent.id || '') : '',
                parentName: parent ? (parent.getAttribute('name') || '') : '',
                parentClass: parent ? (parent.className || '') : '',
                grandparentTag: (parent && parent.parentElement) ? parent.parentElement.tagName : '',
                grandparentId: (parent && parent.parentElement) ? (parent.parentElement.id || '') : '',
            });
        }
    }
    return results;
}
"""

JS_PARTS_SECTION_HTML = """
() => {
    // Find any element whose id contains "Parts" and dump its outer HTML (trimmed)
    const found = [];
    document.querySelectorAll('[id*="Parts"]').forEach(el => {
        found.push({
            tag: el.tagName,
            id: el.id,
            html: el.outerHTML.substring(0, 800),
        });
    });
    return found;
}
"""

JS_PEGA_DATA_ATTRS = """
() => {
    // Find elements with Pega data-* attributes that relate to parts
    const found = [];
    document.querySelectorAll('[data-click], [data-name]').forEach(el => {
        const dc = el.getAttribute('data-click') || '';
        const dn = el.getAttribute('data-name') || '';
        if (dc.toLowerCase().includes('part') || dn.toLowerCase().includes('part')) {
            found.push({
                tag: el.tagName,
                id: el.id || '',
                dataClick: dc,
                dataName: dn,
                text: (el.textContent || '').trim().substring(0, 60),
            });
        }
    });
    return found;
}
"""

def run_in_frame(frame, js: str, *args) -> object:
    try:
        if args:
            return frame.evaluate(js, *args)
        return frame.evaluate(js)
    except Exception as e:
        return f"ERROR: {e}"


def main() -> None:
    with sync_playwright() as pw:
        p(f"Connecting to CDP at {CDP_URL} …")
        browser = pw.chromium.connect_over_cdp(CDP_URL)
        ctx = browser.contexts[0]
        pages = ctx.pages
        p(f"Open pages: {len(pages)}")
        for i, pg in enumerate(pages):
            p(f"  [{i}] {pg.url[:100]}")

        # Pick the page most likely to be the claim — warrantyprocessing
        # We want the page that has the actual Pega prweb session, not the OWS shell
        target_page = None
        for pg in pages:
            if "warrantyprocessing" in pg.url.lower():
                target_page = pg
                break
        # Fallback: pick page with most frames (most likely to have Pega gadgets)
        if target_page is None:
            target_page = max(pages, key=lambda pg: len(pg.frames))
        p(f"\nUsing page: {target_page.url[:120]}")

        # Enumerate ALL frames
        all_frames = list(target_page.frames)
        p(f"\nTotal frames: {len(all_frames)}")
        for i, fr in enumerate(all_frames):
            p(f"  [{i}] url={fr.url[:100]}")

        # ── Pass 1: scan every frame for parts-related <tr> elements ─────────
        divider("PASS 1: Scan every frame for <tr id*='pParts'> or <tr id*='Parts'>")
        found_parts_frame_idx = None
        for i, fr in enumerate(all_frames):
            rows = run_in_frame(fr, JS_ALL_TR_WITH_PARTS)
            if isinstance(rows, list) and rows:
                p(f"\n  [Frame {i}] {fr.url[:80]}")
                p(f"  Found {len(rows)} matching <tr> elements:")
                for r in rows[:20]:
                    p(f"    id={r['id']!r:70s}  class={r['className'][:40]!r}")
                if found_parts_frame_idx is None:
                    found_parts_frame_idx = i

        if found_parts_frame_idx is None:
            p("  *** NONE found — parts grid not in any frame via id pattern ***")

        # ── Pass 2: scan for <tr> elements that CONTAIN a part-name input ────
        divider("PASS 2: Scan every frame for <tr> containing input[name*='part' i]")
        found_via_input = False
        for i, fr in enumerate(all_frames):
            rows = run_in_frame(fr, JS_ALL_TR_WITH_PART_INPUTS)
            if isinstance(rows, list) and rows:
                found_via_input = True
                p(f"\n  [Frame {i}] {fr.url[:80]}")
                p(f"  Found {len(rows)} <tr> elements with part inputs:")
                for r in rows[:20]:
                    p(f"    trId={r['trId']!r}")
                    p(f"    inputName={r['inputName']!r}  val={r['inputValue']!r}")
        if not found_via_input:
            p("  *** NONE found — no <tr> contains an input with 'part' in name ***")

        # ── Pass 3: search for the target part number specifically ───────────
        divider(f"PASS 3: Search every frame for '{TARGET_PART}' in inputs/spans/tds")
        found_target = False
        for i, fr in enumerate(all_frames):
            hits = run_in_frame(fr, "([target]) => { " + JS_ALL_PART_INPUTS[3:], TARGET_PART)
            # The above won't work cleanly — use a proper arg approach
            # Re-do with direct evaluate
            try:
                hits = fr.evaluate(
                    """(target) => {
                        const results = [];
                        document.querySelectorAll('input').forEach(el => {
                            const name = el.name || '';
                            const val  = el.value || '';
                            if (name.toLowerCase().includes('part') || val.toUpperCase().includes(target)) {
                                results.push({tag:'INPUT', name:el.name, type:el.type, value:el.value, id:el.id});
                            }
                        });
                        document.querySelectorAll('span, td').forEach(el => {
                            if ((el.textContent||'').trim().toUpperCase().includes(target)) {
                                results.push({
                                    tag: el.tagName,
                                    name: el.getAttribute('name')||'',
                                    id: el.id||'',
                                    className: el.className||'',
                                    text: el.textContent.trim().substring(0,80),
                                    parentId: el.parentElement ? (el.parentElement.id||'') : '',
                                });
                            }
                        });
                        return results;
                    }""",
                    TARGET_PART
                )
            except Exception as e:
                hits = f"ERROR: {e}"

            if isinstance(hits, list) and hits:
                found_target = True
                p(f"\n  [Frame {i}] {fr.url[:80]}")
                for h in hits[:20]:
                    p(f"    {h}")
        if not found_target:
            p(f"  *** '{TARGET_PART}' not found in any frame ***")

        # ── Pass 4: full text-node walk for target part number ───────────────
        divider(f"PASS 4: Text-node walk for '{TARGET_PART}'")
        found_text = False
        for i, fr in enumerate(all_frames):
            try:
                hits = fr.evaluate(
                    """(target) => {
                        const walker = document.createTreeWalker(
                            document.body, NodeFilter.SHOW_TEXT, null);
                        const results = [];
                        let node;
                        while ((node = walker.nextNode())) {
                            if (node.nodeValue && node.nodeValue.toUpperCase().includes(target)) {
                                const parent = node.parentElement;
                                const gp = parent && parent.parentElement;
                                results.push({
                                    text: node.nodeValue.trim().substring(0,120),
                                    parentTag: parent ? parent.tagName : '?',
                                    parentId: parent ? (parent.id||'') : '',
                                    parentName: parent ? (parent.getAttribute('name')||'') : '',
                                    parentClass: parent ? (parent.className||'').substring(0,60) : '',
                                    gpTag: gp ? gp.tagName : '',
                                    gpId: gp ? (gp.id||'') : '',
                                });
                            }
                        }
                        return results;
                    }""",
                    TARGET_PART
                )
            except Exception as e:
                hits = []

            if isinstance(hits, list) and hits:
                found_text = True
                p(f"\n  [Frame {i}] {fr.url[:80]}")
                for h in hits[:20]:
                    p(f"    text={h['text']!r}")
                    p(f"    parent: <{h['parentTag']} id={h['parentId']!r} name={h['parentName']!r} class={h['parentClass']!r}>")
                    p(f"    grandparent: <{h['gpTag']} id={h['gpId']!r}>")
        if not found_text:
            p(f"  *** '{TARGET_PART}' not found via text-node walk in any frame ***")

        # ── Pass 5: sample ALL <tr> ids in every frame ───────────────────────
        divider("PASS 5: Sample <tr id=...> values from ALL frames (first 40 each)")
        for i, fr in enumerate(all_frames):
            try:
                ids = fr.evaluate(JS_SAMPLE_TR_IDS)
            except Exception as e:
                ids = [f"ERROR: {e}"]
            if ids:
                p(f"\n  [Frame {i}] {fr.url[:80]}")
                for tid in ids[:40]:
                    p(f"    {tid}")

        # ── Pass 6: ALL inputs with name attr in promising frames ────────────
        divider("PASS 6: ALL input[name] attributes (every frame, filtered to 'Repair'/'Parts'/'Part')")
        for i, fr in enumerate(all_frames):
            try:
                inputs = fr.evaluate(JS_ALL_INPUTS_WITH_NAME)
            except Exception as e:
                inputs = []
            if isinstance(inputs, list):
                # Filter to plausible part/repair names
                relevant = [
                    x for x in inputs
                    if any(k in (x.get('name','') or '') for k in
                           ['epair', 'art', 'Labor', 'abor', 'Part', 'pCu', 'Claim'])
                ]
                if relevant:
                    p(f"\n  [Frame {i}] {fr.url[:80]}  ({len(inputs)} total inputs, {len(relevant)} relevant)")
                    for x in relevant[:60]:
                        p(f"    name={x['name']!r:80s}  val={x['value']!r}")

        # ── Pass 7: Elements with id containing 'Parts' ──────────────────────
        divider("PASS 7: Elements with id containing 'Parts' — outer HTML sample")
        for i, fr in enumerate(all_frames):
            try:
                found = fr.evaluate(JS_PARTS_SECTION_HTML)
            except Exception as e:
                found = []
            if isinstance(found, list) and found:
                p(f"\n  [Frame {i}] {fr.url[:80]}")
                for x in found[:10]:
                    p(f"  <{x['tag']} id={x['id']!r}>")
                    p(f"    HTML: {x['html'][:400]}")
                    p("")

        # ── Pass 8: Pega data attributes mentioning 'part' ───────────────────
        divider("PASS 8: Pega data-click/data-name attributes mentioning 'part'")
        for i, fr in enumerate(all_frames):
            try:
                found = fr.evaluate(JS_PEGA_DATA_ATTRS)
            except Exception as e:
                found = []
            if isinstance(found, list) and found:
                p(f"\n  [Frame {i}] {fr.url[:80]}")
                for x in found[:20]:
                    p(f"    <{x['tag']} id={x['id']!r} data-click={x['dataClick']!r} data-name={x['dataName']!r}>")
                    p(f"      text: {x['text']!r}")

        # ── Pass 9: check if parts are in a nested sub-frame ─────────────────
        divider("PASS 9: Check child frames of every frame (detect nested iframes)")
        for i, fr in enumerate(all_frames):
            try:
                child_frame_count = fr.evaluate(
                    "() => document.querySelectorAll('iframe, frame').length"
                )
                if child_frame_count > 0:
                    child_srcs = fr.evaluate(
                        """() => {
                            const srcs = [];
                            document.querySelectorAll('iframe, frame').forEach(f => {
                                srcs.push({src: f.src, id: f.id, name: f.name});
                            });
                            return srcs;
                        }"""
                    )
                    p(f"\n  [Frame {i}] contains {child_frame_count} iframe/frame elements: {fr.url[:80]}")
                    for s in child_srcs[:10]:
                        p(f"    src={s['src'][:100]!r}  id={s['id']!r}  name={s['name']!r}")
            except Exception:
                pass

        # ── Pass 10: look for the repair details table by any heuristic ──────
        divider("PASS 10: Locate repair details table by any heuristic (table/grid presence)")
        for i, fr in enumerate(all_frames):
            try:
                table_info = fr.evaluate(
                    """() => {
                        // How many tables/divs reference 'Repair' in their id?
                        const results = [];
                        document.querySelectorAll('[id*="Repair"], [id*="repair"]').forEach(el => {
                            if (['TABLE','TBODY','TR','DIV','SECTION'].includes(el.tagName)) {
                                results.push({
                                    tag: el.tagName,
                                    id: el.id,
                                    childCount: el.children.length,
                                    htmlSnippet: el.outerHTML.substring(0, 300),
                                });
                            }
                        });
                        return results;
                    }"""
                )
            except Exception as e:
                table_info = []
            if isinstance(table_info, list) and table_info:
                p(f"\n  [Frame {i}] {fr.url[:80]}")
                for x in table_info[:10]:
                    p(f"  <{x['tag']} id={x['id']!r}> (children: {x['childCount']})")
                    p(f"    {x['htmlSnippet'][:300]}")
                    p("")

        divider("DIAGNOSIS COMPLETE")
        p("Review output above to identify the correct selectors for the parts grid.")
        p("Key questions to answer from the output:")
        p("  1. Which frame index contains the parts grid?")
        p("  2. What are the actual <tr id=...> patterns?")
        p("  3. Are part numbers in <input> or <span>/<td> (read-only)?")
        p("  4. What name attributes do part-related inputs have?")
        p(f"  5. Was '{TARGET_PART}' found at all?")


if __name__ == "__main__":
    main()
