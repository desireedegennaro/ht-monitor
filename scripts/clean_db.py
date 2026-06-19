#!/usr/bin/env python3
"""
clean_db.py — Audit and remove non-HT entries from the database.

Runs the same two-layer accuracy filter used by the daily fetch against
every entry already in research.json and legislation.json. Prints what
it finds. Use --apply to actually save the cleaned files.

Usage:
    python scripts/clean_db.py             # dry run — just report
    python scripts/clean_db.py --apply     # remove flagged entries and save
    python scripts/clean_db.py --verbose   # show title + reason for every flag
"""

import argparse
import json
import re
from pathlib import Path

ROOT     = Path(__file__).parent.parent
DATA_DIR = ROOT / "data"

# ── Same filter logic as fetch_data.py ──────────────────────────────────────

# If any of these appear → confirmed human trafficking content, keep it
_HT_CONFIRM = frozenset([
    "human trafficking", "sex trafficking", "labor trafficking",
    "trafficking in persons", "trafficking victims", "trafficking victim",
    "commercial sexual exploitation", "anti-trafficking", "modern slavery",
    "forced labor", "forced labour", "sex tourism", "debt bondage",
    "uyghur forced labor", "survivor of trafficking",
    "tvpa", "tvpra", "fosta", "sesta", "uflpa",
    "§1591", "§1595", "1591 ", "1595 ",
    "dmst", "csec",
])

# If any of these appear WITHOUT a confirming phrase → not HT, remove it
_NOT_HT = frozenset([
    "drug trafficking", "narcotics trafficking", "weapons trafficking",
    "arms trafficking", "fentanyl trafficking", "cocaine trafficking",
    "heroin trafficking", "opioid trafficking", "methamphetamine trafficking",
    "trafficking in drugs", "trafficking in narcotics",
    "trafficking in controlled substances", "trafficking in firearms",
    "trafficking in weapons", "wildlife trafficking",
    "trafficking in counterfeit", "trafficking in stolen",
    "rhinoceros", "pangolin", "ivory trafficking", "elephant",
    "timber trafficking", "fish trafficking",
])


def is_human_trafficking_content(text: str) -> tuple:
    """
    Returns (is_ht: bool, reason: str).
    reason explains why it passed or failed.
    """
    t = text.lower()

    for phrase in _HT_CONFIRM:
        if phrase in t:
            return True, f"confirmed by '{phrase}'"

    for phrase in _NOT_HT:
        if phrase in t:
            return False, f"disqualified by '{phrase}'"

    # Has some HT keyword (got into the DB somehow) but no confirming phrase
    # and no explicit disqualifier — flag for review
    return None, "no confirming phrase found — review manually"


def get_search_text(entry: dict) -> str:
    """Combine all text fields for checking."""
    parts = [
        entry.get("title", ""),
        entry.get("summary", ""),
        entry.get("description", ""),
        entry.get("latest_action", ""),
        " ".join(entry.get("keywords", [])),
    ]
    return " ".join(p for p in parts if p)


def audit_file(path: Path, verbose: bool) -> tuple:
    """
    Returns (clean_entries, flagged_entries, uncertain_entries).
    flagged   = definitely not HT (will be removed with --apply)
    uncertain = no confirming phrase but no disqualifier either (shown for review)
    """
    if not path.exists():
        print(f"  {path.name} not found, skipping.")
        return [], [], []

    with open(path, encoding="utf-8") as f:
        entries = json.load(f)

    clean, flagged, uncertain = [], [], []

    for entry in entries:
        text   = get_search_text(entry)
        result, reason = is_human_trafficking_content(text)
        source = entry.get("source", "")

        # Never remove manually-entered entries (PACER, Community Submission, manual)
        # The filter is only for auto-fetched content
        is_manual = any(s in source for s in [
            "PACER", "manual", "Community Submission", "DOJ (manual)",
            "State Legislature",
        ])
        if is_manual:
            clean.append(entry)
            continue

        if result is True:
            clean.append(entry)
            if verbose:
                print(f"  ✓  {entry.get('title','')[:70]} — {reason}")
        elif result is False:
            flagged.append(entry)
            print(f"  ✗  REMOVE  [{entry.get('id','')}]")
            print(f"       {entry.get('title','')[:80]}")
            print(f"       source: {source}  |  reason: {reason}")
        else:
            uncertain.append(entry)
            print(f"  ?  REVIEW  [{entry.get('id','')}]")
            print(f"       {entry.get('title','')[:80]}")
            print(f"       source: {source}  |  reason: {reason}")

    return clean, flagged, uncertain


def main():
    parser = argparse.ArgumentParser(description="Audit and clean the HT database")
    parser.add_argument("--apply",   action="store_true",
                        help="Remove flagged entries and save the cleaned files")
    parser.add_argument("--verbose", action="store_true",
                        help="Print every entry, not just flagged ones")
    parser.add_argument("--research-only", action="store_true",
                        help="Only check research.json (faster)")
    args = parser.parse_args()

    files = [DATA_DIR / "research.json"]
    if not args.research_only:
        files.append(DATA_DIR / "legislation.json")

    total_flagged   = 0
    total_uncertain = 0
    total_clean     = 0

    for path in files:
        print(f"\n{'─' * 60}")
        print(f"Checking {path.name} ({path.stat().st_size // 1024} KB)")
        print(f"{'─' * 60}")

        clean, flagged, uncertain = audit_file(path, args.verbose)

        total_clean     += len(clean)
        total_flagged   += len(flagged)
        total_uncertain += len(uncertain)

        print(f"\n  {path.name} summary:")
        print(f"    Clean     : {len(clean)}")
        print(f"    Remove    : {len(flagged)}")
        print(f"    Review    : {len(uncertain)}")

        if args.apply and flagged:
            kept = clean + uncertain   # keep uncertain — don't auto-remove ambiguous
            with open(path, "w", encoding="utf-8") as f:
                json.dump(kept, f, indent=2, ensure_ascii=False)
            print(f"\n  ✓ Saved {path.name} — removed {len(flagged)} entries")

    print(f"\n{'═' * 60}")
    print(f"TOTAL  clean: {total_clean}  |  remove: {total_flagged}  |  review: {total_uncertain}")
    if not args.apply:
        print("\nThis was a dry run. Run with --apply to save changes.")
    print(f"{'═' * 60}\n")


if __name__ == "__main__":
    main()
