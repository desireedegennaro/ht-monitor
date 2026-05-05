#!/usr/bin/env python3
"""
ORSJ Lab — HT Tracker Data Fetcher
====================================
Fetches updates from:
  1. Congress.gov API        → legislation.json  (key: CONGRESS_API_KEY env var)
  2. Federal Register API    → legislation.json  (no key needed)
  3. FinCEN RSS feeds        → legislation.json  (no key needed)
  4. Overton API             → research.json     (key: OVERTON_API_KEY env var)

Merges new entries into data/*.json, preserving all existing
manually-added content (notes, cosponsors, descriptions, etc.).

Usage:
  python scripts/fetch_data.py                  # default 7-day lookback
  python scripts/fetch_data.py --days-back 30   # broader lookback
  python scripts/fetch_data.py --force-full     # ignore existing, re-check all
  python scripts/fetch_data.py --dry-run        # print changes, don't write

API keys are read from environment variables — never hardcoded here.
Set them as GitHub Actions secrets (see README.md).
"""

import argparse
import json
import logging
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    import requests
    import feedparser
    from dateutil import parser as dateparser
except ImportError:
    print("Missing dependencies. Run:  pip install -r scripts/requirements.txt")
    sys.exit(1)

# ─────────────────────────────────────────────────────────────────────────────
# PATHS
# ─────────────────────────────────────────────────────────────────────────────
ROOT = Path(__file__).parent.parent
DATA_DIR = ROOT / "data"
LEG_FILE = DATA_DIR / "legislation.json"
LAW_FILE = DATA_DIR / "lawsuits.json"
RES_FILE = DATA_DIR / "research.json"
META_FILE = DATA_DIR / "meta.json"

# ─────────────────────────────────────────────────────────────────────────────
# HT KEYWORD FILTERS
# ─────────────────────────────────────────────────────────────────────────────
HT_KEYWORDS = [
    "human trafficking", "sex trafficking", "labor trafficking",
    "trafficking victims", "forced labor", "trafficking victims protection",
    "exploitation", "commercial sexual exploitation", "TVPA", "FOSTA",
    "SESTA", "survivor services", "anti-trafficking", "modern slavery",
    "human smuggling", "coercion", "trafficking", "forced labor supply chain",
    "uyghur forced labor", "UFLPA",
]

CONGRESS_SEARCH_TERMS = [
    "human trafficking",
    "trafficking victims protection",
    "sex trafficking",
    "labor trafficking forced",
    "forced labor supply chain",
    "survivor services trafficking",
    "uyghur forced labor",
]

FINCEN_KEYWORD_FILTERS = [
    "trafficking", "forced labor", "exploitation", "human smuggling",
    "AML", "SAR", "modern slavery", "labor trafficking",
]

FEDREGISTER_AGENCIES = [
    "Department of Justice",
    "Department of Homeland Security",
    "Department of Health and Human Services",
    "Department of Labor",
    "Department of State",
    "Department of the Treasury",
]

FEDREGISTER_KEYWORDS = HT_KEYWORDS

OVERTON_SEARCH_QUERIES = [
    "human trafficking intervention evaluation",
    "sex trafficking survivors long-term outcomes",
    "labor trafficking forced labor research",
    "anti-trafficking policy evaluation",
    "trafficking victim identification",
    "modern slavery survivor outcomes",
]

# ─────────────────────────────────────────────────────────────────────────────
# LOGGING
# ─────────────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("ht-fetcher")

# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def load_json(path: Path) -> list:
    if path.exists():
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return []


def save_json(path: Path, data, dry_run: bool = False):
    if dry_run:
        log.info("[DRY RUN] Would write %d entries to %s", len(data), path.name)
        return
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    log.info("Wrote %d entries to %s", len(data), path.name)


def normalize_date(raw) -> str:
    """Return YYYY-MM-DD string from various date formats."""
    if not raw:
        return ""
    if isinstance(raw, str) and re.match(r"^\d{4}-\d{2}-\d{2}$", raw.strip()):
        return raw.strip()
    try:
        return dateparser.parse(str(raw)).strftime("%Y-%m-%d")
    except Exception:
        return str(raw)[:10] if raw else ""


def matches_keywords(text: str, keywords: list) -> bool:
    t = (text or "").lower()
    return any(kw.lower() in t for kw in keywords)


def map_congress_status(action_text: str) -> str:
    t = (action_text or "").lower()
    if "signed by president" in t or "became public law" in t:
        return "Enacted"
    if "passed senate" in t or "passed/agreed to in senate" in t:
        return "Passed Senate"
    if "passed house" in t or "passed/agreed to in house" in t:
        return "Passed House"
    if "committee" in t or "subcommittee" in t or "hearing" in t:
        return "In Committee"
    return "Introduced"


def since_date(days_back: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_back)).strftime("%Y-%m-%d")


def merge_item(existing_list: list, new_item: dict, id_field: str = "id") -> tuple[list, bool]:
    """
    Merge new_item into existing_list by id.
    Returns (updated_list, was_changed).
    Preserves all manually-set fields; only updates auto-fetched fields.
    """
    AUTO_FIELDS = {
        "status", "latest_action", "latest_action_date", "cosponsors",
        "updates",  # we append, not overwrite
    }
    for i, existing in enumerate(existing_list):
        if existing.get(id_field) == new_item.get(id_field):
            changed = False
            merged = dict(existing)
            for k, v in new_item.items():
                if k not in AUTO_FIELDS:
                    continue
                if k == "updates":
                    # Append new updates not already present
                    existing_dates = {u.get("date", "") + u.get("event", "")[:40]
                                      for u in existing.get("updates", [])}
                    for update in v:
                        key = update.get("date", "") + update.get("event", "")[:40]
                        if key not in existing_dates:
                            merged.setdefault("updates", []).append(update)
                            changed = True
                elif existing.get(k) != v and v:
                    merged[k] = v
                    changed = True
            existing_list[i] = merged
            return existing_list, changed

    # New item — add it
    existing_list.append(new_item)
    return existing_list, True


# ─────────────────────────────────────────────────────────────────────────────
# CONGRESS.GOV FETCHER
# ─────────────────────────────────────────────────────────────────────────────

def fetch_congress(api_key: str, days_back: int) -> list:
    if not api_key:
        log.warning("CONGRESS_API_KEY not set — skipping Congress.gov fetch")
        return []

    base = "https://api.congress.gov/v3"
    since = since_date(days_back)
    new_items = []
    seen_ids = set()

    for term in CONGRESS_SEARCH_TERMS:
        url = (
            f"{base}/bill"
            f"?query={requests.utils.quote(term)}"
            f"&sort=updateDate+desc"
            f"&limit=20"
            f"&format=json"
            f"&api_key={api_key}"
        )
        try:
            resp = requests.get(url, timeout=15)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            log.error("Congress.gov error for '%s': %s", term, e)
            continue

        for bill in data.get("bills", []):
            updated = normalize_date(bill.get("updateDate", ""))
            if updated < since:
                continue

            bill_type = (bill.get("type") or "").upper()
            bill_num = bill.get("number", "")
            congress = bill.get("congress", "")
            item_id = f"{bill_type.lower()}{bill_num}-{congress}"

            if item_id in seen_ids:
                continue
            seen_ids.add(item_id)

            title = bill.get("title") or "Untitled"
            if not matches_keywords(title, HT_KEYWORDS):
                continue

            action = bill.get("latestAction") or {}
            action_text = action.get("text", "")
            action_date = normalize_date(action.get("actionDate", ""))
            sponsor_info = (bill.get("sponsors") or [{}])[0]
            sponsor_name = (
                sponsor_info.get("fullName") or
                f"{sponsor_info.get('firstName','')} {sponsor_info.get('lastName','')}".strip()
            )
            sponsor_party = sponsor_info.get("party", "")
            sponsor_state = sponsor_info.get("state", "")
            sponsor = f"{sponsor_name} ({sponsor_party}-{sponsor_state})" if sponsor_name else ""

            introduced = normalize_date(bill.get("introducedDate", ""))
            congress_url = (
                f"https://www.congress.gov/bill/{congress}th-congress/"
                f"{'house' if bill_type in ('HR','HRES','HJRES','HCONRES') else 'senate'}-bill/{bill_num}"
            )

            item = {
                "id": item_id,
                "type": "legislation",
                "title": title,
                "identifier": f"{bill_type} {bill_num}",
                "status": map_congress_status(action_text),
                "jurisdiction": "federal",
                "trafficking_types": infer_trafficking_types(title),
                "introduced": introduced,
                "latest_action_date": action_date,
                "latest_action": action_text,
                "sponsor": sponsor,
                "cosponsors": (bill.get("cosponsors") or {}).get("count", 0),
                "cosponsor_list": [],
                "committee": "",
                "url": congress_url,
                "summary": title,
                "description": "",
                "keywords": extract_keywords(title),
                "companies": [],
                "source": "Congress.gov",
                "updates": [
                    {
                        "date": action_date,
                        "event": action_text,
                        "actor": "",
                        "source_url": congress_url,
                    }
                ] if action_text else [],
                "upcoming": [],
            }
            if introduced and introduced != action_date:
                item["updates"].insert(0, {
                    "date": introduced,
                    "event": f"{bill_type} {bill_num} introduced",
                    "actor": sponsor,
                    "source_url": congress_url,
                })

            new_items.append(item)
        time.sleep(0.4)  # be polite to API

    log.info("Congress.gov: found %d HT-relevant bills", len(new_items))
    return new_items


def infer_trafficking_types(text: str) -> list:
    t = text.lower()
    has_sex = any(k in t for k in ["sex trafficking", "prostitution", "csec", "commercial sex", "escort"])
    has_labor = any(k in t for k in ["labor trafficking", "forced labor", "debt bondage", "supply chain"])
    if has_sex and has_labor:
        return ["sex", "labor"]
    if has_sex:
        return ["sex"]
    if has_labor:
        return ["labor"]
    return ["sex", "labor"]  # default for general trafficking bills


def extract_keywords(text: str) -> list:
    t = text.lower()
    return [kw for kw in HT_KEYWORDS if kw.lower() in t][:8]


# ─────────────────────────────────────────────────────────────────────────────
# FEDERAL REGISTER FETCHER
# ─────────────────────────────────────────────────────────────────────────────

def fetch_federal_register(days_back: int) -> list:
    base = "https://www.federalregister.gov/api/v1/documents.json"
    since = since_date(days_back)
    new_items = []

    params = {
        "per_page": 40,
        "order": "newest",
        "fields[]": ["document_number", "title", "abstract", "publication_date",
                      "agencies", "type", "html_url", "pdf_url", "agency_names"],
        "conditions[publication_date][gte]": since,
        "conditions[term]": "human trafficking OR forced labor OR trafficking victims",
    }

    try:
        resp = requests.get(base, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        log.error("Federal Register fetch error: %s", e)
        return []

    for doc in data.get("results", []):
        title = doc.get("title", "")
        abstract = doc.get("abstract", "")
        combined = f"{title} {abstract}"

        if not matches_keywords(combined, HT_KEYWORDS):
            continue

        doc_num = doc.get("document_number", "")
        pub_date = normalize_date(doc.get("publication_date", ""))
        agencies = ", ".join(doc.get("agency_names", []))
        doc_type = doc.get("type", "Rule")
        html_url = doc.get("html_url", "")

        status_map = {
            "Rule": "Active — Final Rule",
            "Proposed Rule": "Active — Proposed Rule",
            "Notice": "Active — Advisory",
            "Presidential Document": "Active",
        }

        item = {
            "id": f"fr-{doc_num.lower().replace(' ','-')}",
            "type": "regulatory",
            "title": title,
            "identifier": doc_num,
            "status": status_map.get(doc_type, "Active — Guidance"),
            "jurisdiction": "federal",
            "trafficking_types": infer_trafficking_types(combined),
            "introduced": pub_date,
            "latest_action_date": pub_date,
            "latest_action": f"{doc_type} published in Federal Register",
            "sponsor": agencies,
            "cosponsors": 0,
            "cosponsor_list": [],
            "committee": "N/A",
            "url": html_url,
            "summary": abstract[:400] if abstract else title,
            "description": abstract or "",
            "keywords": extract_keywords(combined),
            "companies": [],
            "source": "Federal Register",
            "updates": [
                {
                    "date": pub_date,
                    "event": f"{doc_type} published: {title[:80]}",
                    "actor": agencies,
                    "source_url": html_url,
                }
            ],
            "upcoming": [],
        }
        new_items.append(item)

    log.info("Federal Register: found %d HT-relevant documents", len(new_items))
    return new_items


# ─────────────────────────────────────────────────────────────────────────────
# FINCEN RSS FETCHER
# ─────────────────────────────────────────────────────────────────────────────

FINCEN_FEEDS = [
    "https://www.fincen.gov/news/news-releases/rss.xml",
    "https://www.fincen.gov/resources/advisoriesbulletinsfact-sheets/rss.xml",
]


def fetch_fincen(days_back: int) -> list:
    since_dt = datetime.now(timezone.utc) - timedelta(days=days_back)
    new_items = []

    for feed_url in FINCEN_FEEDS:
        try:
            feed = feedparser.parse(feed_url)
        except Exception as e:
            log.error("FinCEN RSS error (%s): %s", feed_url, e)
            continue

        for entry in feed.entries:
            title = entry.get("title", "")
            summary = entry.get("summary", "")
            combined = f"{title} {summary}"

            if not matches_keywords(combined, FINCEN_KEYWORD_FILTERS):
                continue

            pub = entry.get("published_parsed") or entry.get("updated_parsed")
            pub_date = ""
            if pub:
                try:
                    pub_dt = datetime(*pub[:6], tzinfo=timezone.utc)
                    if pub_dt < since_dt:
                        continue
                    pub_date = pub_dt.strftime("%Y-%m-%d")
                except Exception:
                    pass

            link = entry.get("link", "https://www.fincen.gov")
            entry_id = re.sub(r"[^a-z0-9]", "-", title.lower())[:50]

            item = {
                "id": f"fincen-{pub_date}-{entry_id}",
                "type": "regulatory",
                "title": title,
                "identifier": entry.get("id", "")[:30],
                "status": "Active — Advisory",
                "jurisdiction": "federal",
                "trafficking_types": infer_trafficking_types(combined),
                "introduced": pub_date,
                "latest_action_date": pub_date,
                "latest_action": f"Published by FinCEN: {title[:60]}",
                "sponsor": "FinCEN",
                "cosponsors": 0,
                "cosponsor_list": [],
                "committee": "N/A",
                "url": link,
                "summary": summary[:300] if summary else title,
                "description": summary or "",
                "keywords": extract_keywords(combined),
                "companies": ["FinCEN"],
                "source": "FinCEN",
                "updates": [
                    {
                        "date": pub_date,
                        "event": f"Advisory/release published: {title[:70]}",
                        "actor": "FinCEN",
                        "source_url": link,
                    }
                ],
                "upcoming": [],
            }
            new_items.append(item)

    log.info("FinCEN: found %d HT-relevant advisories", len(new_items))
    return new_items


# ─────────────────────────────────────────────────────────────────────────────
# OVERTON API FETCHER (Research Papers)
# ─────────────────────────────────────────────────────────────────────────────

def fetch_overton(api_key: str, days_back: int) -> list:
    if not api_key:
        log.warning("OVERTON_API_KEY not set — skipping Overton fetch")
        return []

    base = "https://app.overton.io/api/v1"
    since = since_date(days_back)
    new_items = []
    seen_ids = set()

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Accept": "application/json",
    }

    for query in OVERTON_SEARCH_QUERIES:
        params = {
            "q": query,
            "type": "paper",
            "from_date": since,
            "per_page": 20,
            "sort": "date_desc",
        }
        try:
            resp = requests.get(f"{base}/search", params=params, headers=headers, timeout=15)
            if resp.status_code == 401:
                log.error("Overton API: invalid API key")
                return []
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            log.error("Overton error for '%s': %s", query, e)
            continue

        for paper in data.get("results", []):
            paper_id = paper.get("id") or paper.get("doi", "").replace("/", "-")
            if not paper_id or paper_id in seen_ids:
                continue
            seen_ids.add(paper_id)

            title = paper.get("title", "")
            abstract = paper.get("abstract", "")
            combined = f"{title} {abstract}"
            if not matches_keywords(combined, HT_KEYWORDS):
                continue

            pub_date = normalize_date(paper.get("date") or paper.get("published_date", ""))
            authors = [
                a.get("name", "") for a in (paper.get("authors") or [])
            ]
            journal = paper.get("journal") or paper.get("source", "")
            doi = paper.get("doi", "")
            doi_url = f"https://doi.org/{doi}" if doi else paper.get("url", "")

            item = {
                "id": f"overton-{re.sub(r'[^a-z0-9]', '-', paper_id.lower())[:40]}",
                "type": "research",
                "title": title,
                "identifier": f"DOI: {doi}" if doi else paper_id,
                "status": "Published",
                "jurisdiction": "international",
                "trafficking_types": infer_trafficking_types(combined),
                "published": pub_date,
                "latest_action_date": pub_date,
                "latest_action": f"Published in {journal}" if journal else "Published",
                "authors": authors[:6],
                "journal": journal,
                "funder": "",
                "evidence_strength": "",
                "follow_up_length": "",
                "outcome_frame": "",
                "url": doi_url,
                "overton_id": str(paper.get("id", "")),
                "summary": (abstract or title)[:400],
                "description": abstract or "",
                "keywords": extract_keywords(combined),
                "organizations": [],
                "source": "Overton",
                "updates": [
                    {
                        "date": pub_date,
                        "event": f"Published in {journal}" if journal else "Published",
                        "actor": ", ".join(authors[:2]) + (" et al." if len(authors) > 2 else ""),
                        "source_url": doi_url,
                    }
                ],
                "upcoming": [],
            }
            new_items.append(item)

        time.sleep(0.3)

    log.info("Overton: found %d HT-relevant papers", len(new_items))
    return new_items


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="ORSJ HT Tracker data fetcher")
    parser.add_argument("--days-back", type=int, default=90, help="Days to look back")
    parser.add_argument("--force-full", action="store_true", help="Force full re-check")
    parser.add_argument("--dry-run", action="store_true", help="Print changes, don't write")
    args = parser.parse_args()

    congress_key = os.environ.get("CONGRESS_API_KEY", "")
    overton_key = os.environ.get("OVERTON_API_KEY", "")

    if not congress_key:
        log.warning("CONGRESS_API_KEY not set. Get a free key at https://api.congress.gov/sign-up/")
    if not overton_key:
        log.warning("OVERTON_API_KEY not set. Get access at https://www.overton.io/")

    log.info("Fetching data (lookback: %d days)…", args.days_back)

    # Fetch from all sources
    congress_items = fetch_congress(congress_key, args.days_back)
    fed_reg_items = fetch_federal_register(args.days_back)
    fincen_items = fetch_fincen(args.days_back)
    overton_items = fetch_overton(overton_key, args.days_back)

    # Load existing data
    existing_leg = load_json(LEG_FILE)
    existing_res = load_json(RES_FILE)

    # Merge legislation/regulatory
    leg_changed = 0
    for item in congress_items + fed_reg_items + fincen_items:
        existing_leg, changed = merge_item(existing_leg, item)
        if changed:
            leg_changed += 1

    # Merge research
    res_changed = 0
    for item in overton_items:
        existing_res, changed = merge_item(existing_res, item)
        if changed:
            res_changed += 1

    # Update meta
    meta = load_json(META_FILE) if META_FILE.exists() else {}
    if isinstance(meta, list):
        meta = {}
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    meta["last_updated"] = now_iso
    if congress_key:
        meta["last_fetched_congress"] = now_iso
    if overton_key:
        meta["last_fetched_overton"] = now_iso
    meta["last_fetched_federal_register"] = now_iso
    meta["counts"] = {
        "legislation": sum(1 for i in existing_leg if i.get("type") == "legislation"),
        "regulatory": sum(1 for i in existing_leg if i.get("type") == "regulatory"),
        "lawsuit": len(load_json(LAW_FILE)),
        "research": len(existing_res),
    }

    # Write
    if leg_changed or args.force_full:
        save_json(LEG_FILE, existing_leg, dry_run=args.dry_run)
    else:
        log.info("No changes to legislation.json")

    if res_changed or args.force_full:
        save_json(RES_FILE, existing_res, dry_run=args.dry_run)
    else:
        log.info("No changes to research.json")

    save_json(META_FILE, meta, dry_run=args.dry_run)

    log.info("Done. Leg changes: %d, Research changes: %d", leg_changed, res_changed)


if __name__ == "__main__":
    main()
