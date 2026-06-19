#!/usr/bin/env python3
"""
ORSJ Lab — HT Tracker Data Fetcher  (v3 — smart dedup + digest)
================================================================
Sources:
  1. Congress.gov API        → legislation.json  (env: CONGRESS_API_KEY — free)
  2. Federal Register API    → legislation.json  (no key)
  3. DOJ / HHS / DHS / FinCEN / State Dept RSS  → legislation.json  (no key)
  4. PubMed API              → research.json     (no key)
  5. Semantic Scholar API    → research.json     (no key)
  6. Overton API             → research.json     (env: OVERTON_API_KEY — optional)

Merge behaviour:
  - Existing item found     → append new actions to its updates[], update status
  - Same paper from 2 APIs  → deduplicated by DOI / PMID — one entry, not two
  - Genuinely new item      → added as a new entry
  - User fields             → NEVER overwritten (evidence_strength, notes, etc.)

Outputs:
  data/legislation.json   — updated legislation + regulatory entries
  data/research.json      — updated research entries
  data/meta.json          — counts + fetch timestamps
  data/digest.json        — new + updated items for the Digest tab

Usage:
  python scripts/fetch_data.py                  # 90-day lookback (default)
  python scripts/fetch_data.py --days-back 365  # backfill run
  python scripts/fetch_data.py --force-full     # re-index everything
  python scripts/fetch_data.py --dry-run        # print only, don't write

API keys: GitHub repo → Settings → Secrets and variables → Actions
  CONGRESS_API_KEY   free at https://api.congress.gov/sign-up/
  OVERTON_API_KEY    optional Northeastern institutional key
"""

import argparse
import json
import logging
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
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
ROOT     = Path(__file__).parent.parent
DATA_DIR = ROOT / "data"
LEG_FILE    = DATA_DIR / "legislation.json"
LAW_FILE    = DATA_DIR / "lawsuits.json"
RES_FILE    = DATA_DIR / "research.json"
META_FILE   = DATA_DIR / "meta.json"
DIGEST_FILE = DATA_DIR / "digest.json"

# ─────────────────────────────────────────────────────────────────────────────
# KEYWORD LISTS
# ─────────────────────────────────────────────────────────────────────────────

HT_KEYWORDS = [
    "human trafficking", "sex trafficking", "labor trafficking",
    "trafficking victims", "forced labor", "trafficking victims protection",
    "exploitation", "commercial sexual exploitation", "TVPA", "FOSTA",
    "SESTA", "survivor services", "anti-trafficking", "modern slavery",
    "human smuggling", "coercion", "trafficking", "forced labor supply chain",
    "uyghur forced labor", "UFLPA", "TVPRA", "§1591", "§1595", "1591",
    "1595", "sex tourism", "debt bondage", "labor exploitation",
    "victim identification", "T visa", "T nonimmigrant",
]

CONGRESS_SEARCH_TERMS = [
    "human trafficking",
    "trafficking victims protection",
    "sex trafficking",
    "labor trafficking forced",
    "forced labor supply chain",
    "survivor services trafficking",
    "uyghur forced labor",
    "FOSTA SESTA",
    "trafficking T visa",
    "anti-trafficking",
]

CONGRESS_MIN_KEYWORDS = [
    "trafficking", "forced labor", "tvpa", "tvpra", "fosta", "sesta",
    "§1591", "1591", "§1595", "1595", "uflpa", "uyghur forced labor",
    "commercial sex", "sex tourism", "survivor services", "t visa",
    "debt bondage", "labor exploitation",
]

PUBMED_QUERIES = [
    "human trafficking intervention evaluation",
    "sex trafficking survivors outcomes",
    "labor trafficking forced labor evidence",
    "anti-trafficking policy evaluation outcomes",
    "trafficking victim identification",
    "modern slavery survivor services",
    "commercial sexual exploitation youth",
    "DMST domestic minor sex trafficking",
    "trafficking law enforcement prosecution",
    "trafficking mental health trauma",
]

SEMANTIC_SCHOLAR_QUERIES = [
    "human trafficking intervention outcome evaluation",
    "sex trafficking survivors long-term outcomes",
    "labor trafficking forced labor policy",
    "anti-trafficking program evaluation evidence",
    "modern slavery measurement prevalence",
]

OVERTON_SEARCH_QUERIES = [
    "human trafficking intervention evaluation",
    "sex trafficking survivors long-term outcomes",
    "labor trafficking forced labor research",
    "anti-trafficking policy evaluation",
    "trafficking victim identification",
    "modern slavery survivor outcomes",
]

# Stricter keyword set for RSS feeds — compound phrases only.
# Bare words like "trafficking", "exploitation", "coercion" catch
# drug trafficking, economic exploitation, and political science in
# DOJ/DHS/State press releases. Every term here requires two+ words.
HT_RSS_KEYWORDS = [
    "human trafficking", "sex trafficking", "labor trafficking",
    "trafficking victims", "trafficking victim", "trafficking in persons",
    "forced labor", "forced labour", "trafficking victims protection",
    "commercial sexual exploitation", "anti-trafficking", "modern slavery",
    "human smuggling", "labor exploitation", "forced labor supply chain",
    "uyghur forced labor", "UFLPA", "TVPA", "TVPRA", "FOSTA", "SESTA",
    "sex tourism", "debt bondage", "survivor services",
    "T visa trafficking", "trafficking prosecution",
]

GOVERNMENT_RSS_FEEDS = [
    {"url": "https://www.justice.gov/feeds/opa/justice-news.xml",
     "source": "DOJ",   "sponsor": "U.S. Department of Justice"},
    {"url": "https://www.hhs.gov/rss/press-releases.rss",
     "source": "HHS",   "sponsor": "U.S. Department of Health and Human Services"},
    {"url": "https://www.dhs.gov/news/rss/dhs-news.xml",
     "source": "DHS",   "sponsor": "U.S. Department of Homeland Security"},
    {"url": "https://www.fincen.gov/news/news-releases/rss.xml",
     "source": "FinCEN","sponsor": "FinCEN / U.S. Treasury"},
    {"url": "https://www.fincen.gov/resources/advisoriesbulletinsfact-sheets/rss.xml",
     "source": "FinCEN","sponsor": "FinCEN / U.S. Treasury"},
    {"url": "https://www.state.gov/rss-feed/press-releases/feed/",
     "source": "State Dept","sponsor": "U.S. Department of State"},
]

# Fields the user fills in manually — NEVER overwritten by auto-fetch
USER_FIELDS = frozenset({
    "evidence_strength", "follow_up_length", "outcome_frame",
    "funder", "organizations", "companies",
    "notes", "my_position", "my_actions", "tag", "relevance",
})

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
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, list) else []
        except Exception as e:
            log.warning("Could not load %s: %s — starting fresh", path.name, e)
    return []


def save_json(path: Path, data, dry_run: bool = False):
    if dry_run:
        log.info("[DRY RUN] Would write %d entries to %s", len(data), path.name)
        return
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    log.info("Wrote %d entries to %s", len(data), path.name)


def normalize_date(raw) -> str:
    if not raw:
        return ""
    if isinstance(raw, str) and re.match(r"^\d{4}-\d{2}-\d{2}$", raw.strip()):
        return raw.strip()
    try:
        return dateparser.parse(str(raw)).strftime("%Y-%m-%d")
    except Exception:
        return str(raw)[:10] if raw else ""


def matches_any_keyword(text: str, keywords) -> bool:
    t = (text or "").lower()
    return any(kw.lower() in t for kw in keywords)


def strip_html(text: str) -> str:
    return re.sub(r"<[^>]+>", " ", text or "").strip()


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


def infer_trafficking_types(text: str) -> list:
    t = text.lower()
    has_sex = any(k in t for k in [
        "sex trafficking", "prostitution", "csec", "commercial sex",
        "escort", "sexual exploitation", "dmst", "sex tourism",
    ])
    has_labor = any(k in t for k in [
        "labor trafficking", "forced labor", "debt bondage",
        "supply chain", "uyghur", "uflpa",
    ])
    if has_sex and has_labor:
        return ["sex", "labor"]
    if has_sex:
        return ["sex"]
    if has_labor:
        return ["labor"]
    return ["sex", "labor"]


def extract_keywords(text: str) -> list:
    t = text.lower()
    return [kw for kw in HT_KEYWORDS if kw.lower() in t][:8]


def _norm_doi(doi: str) -> str:
    """Normalize a DOI for comparison: strip URL prefix, lowercase."""
    d = (doi or "").strip().lower()
    for pfx in ("https://doi.org/", "http://doi.org/", "doi:"):
        if d.startswith(pfx):
            d = d[len(pfx):]
    return d


# ── Strong confirming phrases — if any are present, it's definitively HT ──
_HT_CONFIRM = frozenset([
    "human trafficking", "sex trafficking", "labor trafficking",
    "trafficking in persons", "trafficking victims", "trafficking victim",
    "commercial sexual exploitation", "anti-trafficking", "modern slavery",
    "forced labor", "forced labour", "sex tourism", "debt bondage",
    "uyghur forced labor", "survivor of trafficking",
])

# ── Non-HT trafficking topics — disqualify if no confirming phrase present ──
_NOT_HT = frozenset([
    "drug trafficking", "narcotics trafficking", "weapons trafficking",
    "arms trafficking", "fentanyl trafficking", "cocaine trafficking",
    "heroin trafficking", "opioid trafficking", "methamphetamine trafficking",
    "trafficking in drugs", "trafficking in narcotics",
    "trafficking in controlled substances", "trafficking in firearms",
    "trafficking in weapons", "wildlife trafficking",
    "trafficking in counterfeit", "trafficking in stolen",
])


def is_human_trafficking_content(text: str) -> bool:
    """
    Second-pass accuracy check for content that already matched HT_RSS_KEYWORDS.

    The problem: DOJ publishes dozens of drug trafficking press releases daily.
    "trafficking" in their feeds usually means fentanyl, not people.
    "forced labor" can appear in wage-theft cases that aren't HT.

    Logic:
      1. If a strong HT-confirming phrase is present → keep. Done.
      2. If a non-HT phrase (drug/weapons/wildlife trafficking) is present
         with no confirming HT phrase → reject.
      3. Otherwise (matched HT_RSS_KEYWORDS on a specific term like TVPA,
         debt bondage, etc. but no disqualifying context) → keep.
    """
    t = text.lower()
    if any(p in t for p in _HT_CONFIRM):
        return True
    if any(p in t for p in _NOT_HT):
        return False
    return True


# ─────────────────────────────────────────────────────────────────────────────
# DEDUPLICATION INDEX
# ─────────────────────────────────────────────────────────────────────────────

class DeduplicationIndex:
    """
    Multi-key lookup so the same real-world item is never added twice,
    even when it arrives from different sources (e.g. PubMed + Semantic Scholar
    returning the same paper, or Congress.gov returning the same bill under
    two different search terms).

    Keys checked in priority order:
      1. item id          (e.g. "hr1234-119", "pubmed-38291044")
      2. DOI              (cross-source dedup for research papers)
      3. PMID             (PubMed ↔ Semantic Scholar dedup)
      4. canonical URL    (exact match)
      5. bill identifier  (e.g. "HR 1234-119th" — catches same bill, different id format)
    """

    def __init__(self, items: list):
        self._items = items          # direct reference — mutations reflected here
        self._by_id:    dict = {}    # id → index
        self._by_doi:   dict = {}    # normalized doi → index
        self._by_pmid:  dict = {}    # pmid string → index
        self._by_url:   dict = {}    # url → index
        self._by_bill:  dict = {}    # "HRXXX-119" → index
        for i, item in enumerate(items):
            self._index(i, item)

    # ── index one item ──────────────────────────────────────────────────────

    def _index(self, idx: int, item: dict):
        item_id = item.get("id", "")
        if item_id:
            self._by_id[item_id] = idx

        doi = item.get("doi", "")
        if not doi:
            ident = item.get("identifier", "")
            if ident and ident.upper().startswith("DOI:"):
                doi = ident[4:].strip()
        if doi and "/" in doi:
            self._by_doi[_norm_doi(doi)] = idx

        pmid = str(item.get("pmid", "")).strip()
        if pmid and pmid != "None":
            self._by_pmid[pmid] = idx

        url = item.get("url", "")
        if url:
            self._by_url[url] = idx

        # Bill identifier: normalise "HR 1234" + congress → "HR1234-119"
        ident = (item.get("identifier") or "").upper().replace(" ", "").replace(".", "")
        cong  = str(item.get("congress") or "").strip()
        bill_key = f"{ident}-{cong}" if cong else ident
        if bill_key and len(bill_key) > 3:
            self._by_bill[bill_key] = idx

    # ── find ────────────────────────────────────────────────────────────────

    def find(self, new_item: dict) -> int:
        """Return index of matching existing item, or -1 if not found."""
        # 1. exact id
        item_id = new_item.get("id", "")
        if item_id and item_id in self._by_id:
            return self._by_id[item_id]

        # 2. DOI
        doi = new_item.get("doi", "")
        if not doi:
            ident = new_item.get("identifier", "")
            if ident and ident.upper().startswith("DOI:"):
                doi = ident[4:].strip()
        if doi and "/" in doi:
            nd = _norm_doi(doi)
            if nd in self._by_doi:
                return self._by_doi[nd]

        # 3. PMID
        pmid = str(new_item.get("pmid", "")).strip()
        if pmid and pmid != "None" and pmid in self._by_pmid:
            return self._by_pmid[pmid]

        # 4. URL
        url = new_item.get("url", "")
        if url and url in self._by_url:
            return self._by_url[url]

        # 5. Bill identifier
        ident = (new_item.get("identifier") or "").upper().replace(" ", "").replace(".", "")
        cong  = str(new_item.get("congress") or "").strip()
        bill_key = f"{ident}-{cong}" if cong else ident
        if bill_key and len(bill_key) > 3 and bill_key in self._by_bill:
            return self._by_bill[bill_key]

        return -1

    # ── add ─────────────────────────────────────────────────────────────────

    def add(self, item: dict):
        """Call after appending a new item to self._items."""
        self._index(len(self._items) - 1, item)


# ─────────────────────────────────────────────────────────────────────────────
# SMART MERGE
# ─────────────────────────────────────────────────────────────────────────────

def smart_merge(
    existing_list: list,
    dedup_index: DeduplicationIndex,
    new_item: dict,
) -> tuple:
    """
    Decide what to do with a freshly-fetched item.

    Returns (action, result_item) where action is one of:
      "new"       – item has never been seen; appended to existing_list
      "updated"   – existing item now has a new status/action; updated in place
                    result_item includes "_update_summary" and "_new_update" keys
                    for the digest
      "duplicate" – item already exists, nothing meaningful changed; skipped

    Rules:
      • User fields (notes, evidence_strength, outcome_frame, etc.) are never
        touched — the user's annotations survive every re-fetch.
      • Auto fields (status, latest_action, latest_action_date, cosponsors)
        are updated only when the incoming value is non-empty and different.
      • The updates[] timeline is append-only: new events are added, nothing
        is removed or overwritten.
      • For cross-source dedup (PubMed + Semantic Scholar same paper):
        the entry that arrived first wins on all static fields; only the
        source name is extended to show both origins.
    """

    idx = dedup_index.find(new_item)

    # ── genuinely new item ──────────────────────────────────────────────────
    if idx == -1:
        existing_list.append(new_item)
        dedup_index.add(new_item)
        return "new", new_item

    # ── existing item found — decide update vs duplicate ────────────────────
    existing = existing_list[idx]
    merged   = dict(existing)   # start from existing, never from new_item
    changed  = False
    status_change_desc = None

    # 1. Status change?
    old_status = existing.get("status", "")
    new_status = new_item.get("status", "")
    if new_status and old_status and new_status != old_status:
        merged["status"] = new_status
        status_change_desc = f"{old_status} → {new_status}"
        changed = True

    # 2. New latest_action?
    old_action = existing.get("latest_action", "")
    new_action = new_item.get("latest_action", "")
    new_action_date = new_item.get("latest_action_date", "")
    if new_action and new_action != old_action:
        merged["latest_action"]      = new_action
        merged["latest_action_date"] = new_action_date
        changed = True

    # 3. Cosponsor count increased?
    old_cos = existing.get("cosponsors", 0) or 0
    new_cos = new_item.get("cosponsors", 0) or 0
    if isinstance(new_cos, int) and new_cos > old_cos:
        merged["cosponsors"] = new_cos
        changed = True

    # 4. Append genuinely new timeline events
    existing_event_keys = {
        (u.get("date", "") + u.get("event", "")[:60])
        for u in existing.get("updates", [])
    }
    new_events = []
    for ev in new_item.get("updates", []):
        key = ev.get("date", "") + ev.get("event", "")[:60]
        if key not in existing_event_keys:
            new_events.append(ev)
            changed = True

    if new_events:
        merged["updates"] = existing.get("updates", []) + new_events

    # 5. Fill in blanks on the existing record (e.g. abstract was empty before)
    #    — only for fields that are empty in existing AND non-empty in new,
    #    AND are NOT user fields.
    for k, v in new_item.items():
        if k in USER_FIELDS:
            continue
        if k in ("id", "type", "source", "updates", "status",
                 "latest_action", "latest_action_date", "cosponsors"):
            continue
        if not existing.get(k) and v:
            merged[k] = v

    # 6. Multi-source label (e.g. "PubMed + Semantic Scholar")
    old_src = existing.get("source", "")
    new_src = new_item.get("source", "")
    if new_src and new_src not in old_src:
        merged["source"] = f"{old_src} + {new_src}" if old_src else new_src

    if not changed:
        return "duplicate", merged

    # Attach digest metadata (stripped before saving to JSON)
    the_new_event = new_events[0] if new_events else None
    merged["_update_summary"] = (
        status_change_desc
        or (f"New action: {new_action[:100]}" if new_action else "Updated")
    )
    merged["_new_update"] = the_new_event

    existing_list[idx] = merged
    return "updated", merged


# ─────────────────────────────────────────────────────────────────────────────
# CONGRESS.GOV FETCHER
# ─────────────────────────────────────────────────────────────────────────────

def fetch_congress(api_key: str, days_back: int) -> list:
    if not api_key:
        log.warning("CONGRESS_API_KEY not set — skipping Congress.gov fetch.")
        log.warning("  → Free key: https://api.congress.gov/sign-up/")
        log.warning("  → Add it: repo Settings → Secrets → CONGRESS_API_KEY")
        return []

    base = "https://api.congress.gov/v3"
    since = since_date(days_back)
    new_items, seen_ids = [], set()
    session = requests.Session()
    session.headers.update({"X-Api-Key": api_key})

    for term in CONGRESS_SEARCH_TERMS:
        params = {"query": term, "sort": "updateDate+desc",
                  "limit": 25, "format": "json", "api_key": api_key}
        try:
            resp = session.get(f"{base}/bill", params=params, timeout=20)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            log.error("Congress.gov error for '%s': %s", term, e)
            continue

        for bill in data.get("bills", []):
            updated = normalize_date(bill.get("updateDate", ""))
            if updated and updated < since:
                continue

            bill_type = (bill.get("type") or "").upper()
            bill_num  = bill.get("number", "")
            congress  = bill.get("congress", "")
            item_id   = f"{bill_type.lower()}{bill_num}-{congress}"
            if item_id in seen_ids:
                continue
            seen_ids.add(item_id)

            title       = bill.get("title") or "Untitled"
            action      = bill.get("latestAction") or {}
            action_text = action.get("text", "")
            combined    = f"{title} {action_text}".lower()

            if not matches_any_keyword(combined, CONGRESS_MIN_KEYWORDS):
                continue

            action_date  = normalize_date(action.get("actionDate", ""))
            sponsor_info = (bill.get("sponsors") or [{}])[0]
            sname = (
                sponsor_info.get("fullName") or
                f"{sponsor_info.get('firstName','')} {sponsor_info.get('lastName','')}".strip()
            )
            sponsor  = f"{sname} ({sponsor_info.get('party','')}-{sponsor_info.get('state','')})" if sname else ""
            intro    = normalize_date(bill.get("introducedDate", ""))
            is_house = bill_type in ("HR", "HRES", "HJRES", "HCONRES")
            cong_url = (
                f"https://www.congress.gov/bill/{congress}th-congress/"
                f"{'house' if is_house else 'senate'}-bill/{bill_num}"
            )

            item = {
                "id": item_id, "type": "legislation",
                "title": title, "identifier": f"{bill_type} {bill_num}",
                "status": map_congress_status(action_text),
                "jurisdiction": "federal",
                "trafficking_types": infer_trafficking_types(combined),
                "introduced": intro, "latest_action_date": action_date,
                "latest_action": action_text, "sponsor": sponsor,
                "cosponsors": (bill.get("cosponsors") or {}).get("count", 0)
                               if isinstance(bill.get("cosponsors"), dict) else 0,
                "cosponsor_list": [], "committee": "",
                "url": cong_url, "summary": title, "description": "",
                "keywords": extract_keywords(combined),
                "companies": [], "source": "Congress.gov", "upcoming": [],
                "updates": [{"date": action_date, "event": action_text,
                              "actor": "", "source_url": cong_url}] if action_text else [],
            }
            if intro and intro != action_date:
                item["updates"].insert(0, {"date": intro,
                    "event": f"{bill_type} {bill_num} introduced",
                    "actor": sponsor, "source_url": cong_url})

            new_items.append(item)
        time.sleep(0.4)

    log.info("Congress.gov: %d HT-relevant bills", len(new_items))
    return new_items


# ─────────────────────────────────────────────────────────────────────────────
# FEDERAL REGISTER FETCHER
# ─────────────────────────────────────────────────────────────────────────────

def fetch_federal_register(days_back: int) -> list:
    base  = "https://www.federalregister.gov/api/v1/documents.json"
    since = since_date(days_back)
    new_items, seen_ids = [], set()

    for query in ["human trafficking", "trafficking victims", "forced labor",
                  "sex trafficking", "labor trafficking", "anti-trafficking"]:
        params = {
            "per_page": 20, "order": "newest",
            "fields[]": ["document_number","title","abstract","publication_date",
                         "agencies","type","html_url","agency_names"],
            "conditions[publication_date][gte]": since,
            "conditions[term]": query,
        }
        try:
            resp = requests.get(base, params=params, timeout=20)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            log.error("Federal Register [%s]: %s", query, e)
            continue

        for doc in data.get("results", []):
            title    = doc.get("title", "")
            abstract = doc.get("abstract", "")
            combined = f"{title} {abstract}"
            doc_num  = doc.get("document_number", "")
            if doc_num in seen_ids or not matches_any_keyword(combined, HT_KEYWORDS):
                continue
            seen_ids.add(doc_num)

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
            new_items.append({
                "id": f"fr-{doc_num.lower().replace(' ','-').replace('/','-')}",
                "type": "regulatory", "title": title, "identifier": doc_num,
                "status": status_map.get(doc_type, "Active — Guidance"),
                "jurisdiction": "federal",
                "trafficking_types": infer_trafficking_types(combined),
                "introduced": pub_date, "latest_action_date": pub_date,
                "latest_action": f"{doc_type} published in Federal Register",
                "sponsor": agencies, "cosponsors": 0, "cosponsor_list": [],
                "committee": "N/A", "url": html_url,
                "summary": abstract[:400] if abstract else title,
                "description": abstract or "",
                "keywords": extract_keywords(combined),
                "companies": [], "source": "Federal Register", "upcoming": [],
                "updates": [{"date": pub_date,
                              "event": f"{doc_type} published: {title[:80]}",
                              "actor": agencies, "source_url": html_url}],
            })
        time.sleep(0.3)

    log.info("Federal Register: %d HT-relevant documents", len(new_items))
    return new_items


# ─────────────────────────────────────────────────────────────────────────────
# GOVERNMENT RSS FEEDS  (DOJ, HHS, DHS, FinCEN, State Dept)
# ─────────────────────────────────────────────────────────────────────────────

def fetch_government_rss(days_back: int) -> list:
    since_dt  = datetime.now(timezone.utc) - timedelta(days=days_back)
    new_items, seen_ids = [], set()

    for cfg in GOVERNMENT_RSS_FEEDS:
        try:
            feed = feedparser.parse(cfg["url"])
        except Exception as e:
            log.warning("RSS error [%s]: %s", cfg["source"], e)
            continue

        found = 0
        for entry in feed.entries:
            title   = entry.get("title", "")
            summary = strip_html(entry.get("summary","") or entry.get("description","") or "")
            link    = entry.get("link", "")
            if not matches_any_keyword(f"{title} {summary}", HT_RSS_KEYWORDS):
                continue
            if not is_human_trafficking_content(f"{title} {summary}"):
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

            uid = f"{cfg['source'].lower()}-{re.sub(r'[^a-z0-9]', '-', title.lower())[:50]}"
            if uid in seen_ids:
                continue
            seen_ids.add(uid)

            tl = (title + " " + link).lower()
            doc_type = (
                "Advisory" if "advisory" in tl
                else "Rule Update" if "rule" in tl or "regulation" in tl
                else "Guidance" if "guidance" in tl or "notice" in tl
                else "Report" if "report" in tl
                else "Press Release"
            )
            new_items.append({
                "id": uid, "type": "regulatory", "title": title,
                "identifier": f"{cfg['source']}-{pub_date or 'UNDATED'}",
                "status": f"Active — {doc_type}",
                "jurisdiction": "federal",
                "trafficking_types": infer_trafficking_types(f"{title} {summary}"),
                "introduced": pub_date, "latest_action_date": pub_date,
                "latest_action": f"{doc_type} published by {cfg['source']}: {title[:60]}",
                "sponsor": cfg["sponsor"], "cosponsors": 0, "cosponsor_list": [],
                "committee": "N/A", "url": link,
                "summary": summary[:400] if summary else title,
                "description": summary or "",
                "keywords": extract_keywords(f"{title} {summary}"),
                "companies": [], "source": cfg["source"], "upcoming": [],
                "updates": [{"date": pub_date,
                              "event": f"{doc_type} published: {title[:70]}",
                              "actor": cfg["sponsor"], "source_url": link}],
            })
            found += 1
        log.info("%s RSS: %d HT-relevant items", cfg["source"], found)

    log.info("Government RSS total: %d items", len(new_items))
    return new_items


# ─────────────────────────────────────────────────────────────────────────────
# PUBMED FETCHER  (free, no key)
# ─────────────────────────────────────────────────────────────────────────────

PUBMED_BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"

def fetch_pubmed(days_back: int) -> list:
    since = since_date(days_back)
    new_items, seen_pmids = [], set()
    session = requests.Session()
    session.headers.update({"User-Agent": "ORSJ-HT-Monitor/3.0 (academic research)"})

    for query in PUBMED_QUERIES:
        try:
            r = session.get(f"{PUBMED_BASE}/esearch.fcgi", timeout=20, params={
                "db": "pubmed", "term": f"{query}[tiab]", "retmax": 8,
                "retmode": "json", "sort": "pub_date",
                "mindate": since.replace("-", "/"), "datetype": "pdat",
            })
            r.raise_for_status()
            ids = r.json().get("esearchresult", {}).get("idlist", [])
        except Exception as e:
            log.error("PubMed search [%s]: %s", query, e)
            continue

        new_ids = [pid for pid in ids if pid not in seen_pmids]
        seen_pmids.update(new_ids)
        if not new_ids:
            continue

        try:
            r = session.get(f"{PUBMED_BASE}/esummary.fcgi", timeout=20, params={
                "db": "pubmed", "id": ",".join(new_ids), "retmode": "json"})
            r.raise_for_status()
            summaries = r.json().get("result", {})
        except Exception as e:
            log.error("PubMed summary: %s", e)
            continue

        abstracts = {}
        try:
            r = session.get(f"{PUBMED_BASE}/efetch.fcgi", timeout=30, params={
                "db": "pubmed", "id": ",".join(new_ids),
                "retmode": "xml", "rettype": "abstract"})
            r.raise_for_status()
            root = ET.fromstring(r.content)
            for art in root.findall(".//PubmedArticle"):
                pmid_el = art.find(".//PMID")
                abs_el  = art.find(".//AbstractText")
                if pmid_el is not None and abs_el is not None:
                    abstracts[pmid_el.text] = (abs_el.text or "").strip()
        except Exception as e:
            log.warning("PubMed XML parse: %s", e)

        for pmid in new_ids:
            paper = summaries.get(pmid, {})
            if not paper or paper.get("error"):
                continue
            title    = paper.get("title", "").rstrip(".")
            pub_date = normalize_date(paper.get("pubdate") or paper.get("epubdate",""))
            abstract = abstracts.get(pmid, "")
            combined = f"{title} {abstract}"
            if not matches_any_keyword(combined, HT_KEYWORDS):
                continue
            if not is_human_trafficking_content(combined):
                continue

            authors = [a.get("name","") for a in paper.get("authors",[])
                       if a.get("authtype") == "Author"]
            journal = paper.get("fulljournalname") or paper.get("source","")
            doi = next((a.get("value","") for a in paper.get("articleids",[])
                        if a.get("idtype") == "doi"), "")
            url    = f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"
            do_url = f"https://doi.org/{doi}" if doi else url

            new_items.append({
                "id": f"pubmed-{pmid}", "type": "research",
                "title": title,
                "identifier": f"PMID: {pmid}" + (f" | DOI: {doi}" if doi else ""),
                "status": "Published", "jurisdiction": "international",
                "trafficking_types": infer_trafficking_types(combined),
                "published": pub_date, "latest_action_date": pub_date,
                "latest_action": f"Published in {journal}" if journal else "Published",
                "authors": authors[:6], "journal": journal,
                "funder": "", "evidence_strength": "",
                "follow_up_length": "", "outcome_frame": "",
                "url": do_url, "pmid": pmid, "doi": doi,
                "summary": (abstract or title)[:500],
                "description": abstract or "",
                "keywords": extract_keywords(combined),
                "organizations": [], "source": "PubMed", "upcoming": [],
                "updates": [{"date": pub_date,
                              "event": f"Published in {journal}" if journal else "Published",
                              "actor": ", ".join(authors[:2]) + (" et al." if len(authors) > 2 else ""),
                              "source_url": do_url}],
            })
        time.sleep(0.35)

    log.info("PubMed: %d HT-relevant papers", len(new_items))
    return new_items


# ─────────────────────────────────────────────────────────────────────────────
# SEMANTIC SCHOLAR FETCHER  (free, no key)
# ─────────────────────────────────────────────────────────────────────────────

SS_BASE = "https://api.semanticscholar.org/graph/v1"

def fetch_semantic_scholar(days_back: int) -> list:
    cutoff_year = (datetime.now(timezone.utc) - timedelta(days=days_back)).year
    new_items, seen_ids = [], set()
    session = requests.Session()
    session.headers.update({"User-Agent": "ORSJ-HT-Monitor/3.0 (academic research)"})
    fields = "title,abstract,authors,year,externalIds,publicationDate,journal,url,openAccessPdf"

    for query in SEMANTIC_SCHOLAR_QUERIES:
        try:
            r = session.get(f"{SS_BASE}/paper/search", timeout=20, params={
                "query": query, "fields": fields, "limit": 8,
                "year": f"{cutoff_year}-",
            })
            if r.status_code == 429:
                log.warning("Semantic Scholar rate limited — waiting 60s")
                time.sleep(60)
                continue
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            log.error("Semantic Scholar [%s]: %s", query, e)
            continue

        for paper in data.get("data", []):
            ss_id = paper.get("paperId", "")
            if not ss_id or ss_id in seen_ids:
                continue
            title    = paper.get("title", "")
            abstract = paper.get("abstract", "")
            combined = f"{title} {abstract}"
            if not matches_any_keyword(combined, HT_KEYWORDS):
                continue
            if not is_human_trafficking_content(combined):
                continue
            seen_ids.add(ss_id)

            year     = paper.get("year")
            pub_date = normalize_date(
                paper.get("publicationDate") or (f"{year}-01-01" if year else ""))
            authors      = [a.get("name","") for a in (paper.get("authors") or [])]
            journal_info = paper.get("journal") or {}
            journal      = journal_info.get("name","") if isinstance(journal_info, dict) else ""
            ext    = paper.get("externalIds") or {}
            doi    = ext.get("DOI","") or ext.get("doi","")
            pmid_s = str(ext.get("PubMed","")).strip()
            oa     = paper.get("openAccessPdf") or {}
            oa_url = oa.get("url","") if isinstance(oa, dict) else ""
            url    = f"https://doi.org/{doi}" if doi else oa_url or paper.get("url", f"https://www.semanticscholar.org/paper/{ss_id}")
            ident  = f"DOI: {doi}" if doi else (f"PMID: {pmid_s}" if pmid_s else ss_id[:20])

            item = {
                "id": f"ss-{ss_id[:20]}", "type": "research",
                "title": title, "identifier": ident,
                "status": "Published", "jurisdiction": "international",
                "trafficking_types": infer_trafficking_types(combined),
                "published": pub_date, "latest_action_date": pub_date,
                "latest_action": f"Published in {journal}" if journal else "Published",
                "authors": authors[:6], "journal": journal,
                "funder": "", "evidence_strength": "",
                "follow_up_length": "", "outcome_frame": "",
                "url": url, "ss_id": ss_id, "doi": doi,
                "pmid": pmid_s if pmid_s and pmid_s != "None" else "",
                "summary": (abstract or title)[:500],
                "description": abstract or "",
                "keywords": extract_keywords(combined),
                "organizations": [], "source": "Semantic Scholar", "upcoming": [],
                "updates": [{"date": pub_date,
                              "event": f"Published in {journal}" if journal else "Published",
                              "actor": ", ".join(authors[:2]) + (" et al." if len(authors) > 2 else ""),
                              "source_url": url}],
            }
            new_items.append(item)
        time.sleep(1.0)

    log.info("Semantic Scholar: %d HT-relevant papers", len(new_items))
    return new_items


# ─────────────────────────────────────────────────────────────────────────────
# OVERTON FETCHER  (optional institutional key)
# ─────────────────────────────────────────────────────────────────────────────

def fetch_overton(api_key: str, days_back: int) -> list:
    if not api_key:
        log.info("OVERTON_API_KEY not set — skipping (PubMed + SS active).")
        return []

    base    = "https://app.overton.io/api/v1"
    since   = since_date(days_back)
    new_items, seen_ids = [], set()
    headers = {"Authorization": f"Bearer {api_key}", "Accept": "application/json"}

    for query in OVERTON_SEARCH_QUERIES:
        try:
            resp = requests.get(f"{base}/search", headers=headers, timeout=15,
                                params={"q": query, "type": "paper",
                                        "from_date": since, "per_page": 20, "sort": "date_desc"})
            if resp.status_code == 401:
                log.error("Overton: invalid API key")
                return []
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            log.error("Overton [%s]: %s", query, e)
            continue

        for paper in data.get("results", []):
            pid = paper.get("id") or paper.get("doi","").replace("/","-")
            if not pid or pid in seen_ids:
                continue
            seen_ids.add(pid)
            title    = paper.get("title","")
            abstract = paper.get("abstract","")
            combined = f"{title} {abstract}"
            if not matches_any_keyword(combined, HT_KEYWORDS):
                continue
            if not is_human_trafficking_content(combined):
                continue

            pub_date = normalize_date(paper.get("date") or paper.get("published_date",""))
            authors  = [a.get("name","") for a in (paper.get("authors") or [])]
            journal  = paper.get("journal") or paper.get("source","")
            doi      = paper.get("doi","")
            do_url   = f"https://doi.org/{doi}" if doi else paper.get("url","")

            new_items.append({
                "id": f"overton-{re.sub(r'[^a-z0-9]','-',str(pid).lower())[:40]}",
                "type": "research", "title": title,
                "identifier": f"DOI: {doi}" if doi else pid,
                "status": "Published", "jurisdiction": "international",
                "trafficking_types": infer_trafficking_types(combined),
                "published": pub_date, "latest_action_date": pub_date,
                "latest_action": f"Published in {journal}" if journal else "Published",
                "authors": authors[:6], "journal": journal,
                "funder": "", "evidence_strength": "",
                "follow_up_length": "", "outcome_frame": "",
                "url": do_url, "doi": doi, "overton_id": str(paper.get("id","")),
                "summary": (abstract or title)[:400], "description": abstract or "",
                "keywords": extract_keywords(combined),
                "organizations": [], "source": "Overton", "upcoming": [],
                "updates": [{"date": pub_date,
                              "event": f"Published in {journal}" if journal else "Published",
                              "actor": ", ".join(authors[:2]) + (" et al." if len(authors) > 2 else ""),
                              "source_url": do_url}],
            })
        time.sleep(0.3)

    log.info("Overton: %d HT-relevant papers", len(new_items))
    return new_items


# ─────────────────────────────────────────────────────────────────────────────
# DIGEST BUILDER
# ─────────────────────────────────────────────────────────────────────────────

def _digest_card(item: dict, action: str) -> dict:
    """Build a compact card for digest.json from a full item dict."""
    card = {
        "id":     item.get("id",""),
        "title":  item.get("title",""),
        "type":   item.get("type",""),
        "source": item.get("source",""),
        "date":   (item.get("latest_action_date")
                   or item.get("published")
                   or item.get("introduced") or ""),
        "status": item.get("status",""),
        "summary": (item.get("summary") or item.get("title",""))[:200],
        "url":    item.get("url",""),
        "identifier": item.get("identifier",""),
        "trafficking_types": item.get("trafficking_types",[]),
        "keywords": item.get("keywords",[]),
    }
    if action == "updated":
        card["update_summary"] = item.get("_update_summary","Updated")
        new_ev = item.get("_new_update")
        if new_ev:
            card["new_update"] = new_ev
    return card


def write_digest(
    new_leg:     list,   # newly added legislation/regulatory items
    updated_leg: list,   # updated legislation/regulatory items
    new_res:     list,   # newly added research items
    updated_res: list,   # updated research items
    days_back:   int,
    dry_run:     bool,
):
    """Write data/digest.json — the Digest tab reads this file."""
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # Sort each bucket newest-first
    def _date_key(c):
        return c.get("date","")
    new_all     = sorted([_digest_card(i,"new")     for i in new_leg + new_res],
                         key=_date_key, reverse=True)
    updated_all = sorted([_digest_card(i,"updated") for i in updated_leg + updated_res],
                         key=_date_key, reverse=True)

    digest = {
        "generated":  now_iso,
        "period_days": days_back,
        "new":     new_all,
        "updated": updated_all,
        "counts": {
            "new_legislation":  len(new_leg),
            "new_research":     len(new_res),
            "updated_legislation": len(updated_leg),
            "updated_research":    len(updated_res),
        },
    }

    if dry_run:
        log.info("[DRY RUN] Digest: %d new, %d updated items",
                 len(new_all), len(updated_all))
        return

    DIGEST_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(DIGEST_FILE, "w", encoding="utf-8") as f:
        json.dump(digest, f, indent=2, ensure_ascii=False)
    log.info("Digest written: %d new + %d updated → %s",
             len(new_all), len(updated_all), DIGEST_FILE.name)


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="ORSJ HT Tracker data fetcher v3")
    parser.add_argument("--days-back", type=int, default=90)
    parser.add_argument("--force-full", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--skip-pubmed", action="store_true")
    parser.add_argument("--skip-semantic-scholar", action="store_true")
    args = parser.parse_args()

    congress_key = os.environ.get("CONGRESS_API_KEY", "")
    overton_key  = os.environ.get("OVERTON_API_KEY", "")

    if not congress_key:
        log.warning("=" * 60)
        log.warning("CONGRESS_API_KEY is not set! Bills won't be fetched.")
        log.warning("Free key → https://api.congress.gov/sign-up/")
        log.warning("Add it  → repo Settings → Secrets → CONGRESS_API_KEY")
        log.warning("=" * 60)

    log.info("HT Tracker fetch — lookback %d days", args.days_back)

    # ── Fetch ────────────────────────────────────────────────────────────────
    leg_fetched  = fetch_congress(congress_key, args.days_back)
    leg_fetched += fetch_federal_register(args.days_back)
    leg_fetched += fetch_government_rss(args.days_back)

    res_fetched  = [] if args.skip_pubmed           else fetch_pubmed(args.days_back)
    res_fetched += [] if args.skip_semantic_scholar else fetch_semantic_scholar(args.days_back)
    res_fetched += fetch_overton(overton_key, args.days_back)

    log.info("Fetched totals — leg/reg: %d, research: %d",
             len(leg_fetched), len(res_fetched))

    # ── Load existing data ───────────────────────────────────────────────────
    existing_leg = [] if args.force_full else load_json(LEG_FILE)
    existing_res = [] if args.force_full else load_json(RES_FILE)

    # Build dedup indexes from existing data
    leg_index = DeduplicationIndex(existing_leg)
    res_index = DeduplicationIndex(existing_res)

    # ── Merge — tracking new / updated / duplicate per category ─────────────
    new_leg, updated_leg = [], []
    for item in leg_fetched:
        action, result = smart_merge(existing_leg, leg_index, item)
        if   action == "new":     new_leg.append(result)
        elif action == "updated": updated_leg.append(result)

    new_res, updated_res = [], []
    for item in res_fetched:
        action, result = smart_merge(existing_res, res_index, item)
        if   action == "new":     new_res.append(result)
        elif action == "updated": updated_res.append(result)

    log.info("Leg   — new: %d, updated: %d, duplicates skipped",
             len(new_leg), len(updated_leg))
    log.info("Res   — new: %d, updated: %d, duplicates skipped",
             len(new_res), len(updated_res))

    # ── Sort newest-first ────────────────────────────────────────────────────
    existing_leg.sort(
        key=lambda x: x.get("latest_action_date") or x.get("introduced") or "",
        reverse=True)
    existing_res.sort(
        key=lambda x: x.get("published") or x.get("latest_action_date") or "",
        reverse=True)

    # ── Strip internal digest keys before saving ──────────────────────────────
    for lst in (existing_leg, existing_res):
        for item in lst:
            item.pop("_update_summary", None)
            item.pop("_new_update", None)

    # ── Update meta.json ─────────────────────────────────────────────────────
    meta = {}
    if META_FILE.exists():
        try:
            with open(META_FILE, encoding="utf-8") as f:
                raw = json.load(f)
            meta = raw if isinstance(raw, dict) else {}
        except Exception:
            pass

    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    meta.update({
        "last_updated":                    now_iso,
        "last_fetched_federal_register":   now_iso,
        "last_fetched_pubmed":             now_iso,
        "last_fetched_semantic_scholar":   now_iso,
        "last_fetched_government_rss":     now_iso,
    })
    if congress_key: meta["last_fetched_congress"] = now_iso
    if overton_key:  meta["last_fetched_overton"]  = now_iso

    meta["counts"] = {
        "legislation": sum(1 for i in existing_leg if i.get("type") == "legislation"),
        "regulatory":  sum(1 for i in existing_leg if i.get("type") == "regulatory"),
        "lawsuit":     len(load_json(LAW_FILE)),
        "research":    len(existing_res),
    }

    # ── Write data files ─────────────────────────────────────────────────────
    any_leg_change = bool(new_leg or updated_leg)
    any_res_change = bool(new_res or updated_res)

    if any_leg_change or args.force_full:
        save_json(LEG_FILE, existing_leg, dry_run=args.dry_run)
    else:
        log.info("No changes to legislation.json")

    if any_res_change or args.force_full:
        save_json(RES_FILE, existing_res, dry_run=args.dry_run)
    else:
        log.info("No changes to research.json")

    save_json(META_FILE, meta, dry_run=args.dry_run)

    # ── Write digest.json ─────────────────────────────────────────────────────
    write_digest(new_leg, updated_leg, new_res, updated_res,
                 args.days_back, args.dry_run)

    log.info(
        "Done — leg/reg: %d total (%d new, %d updated) | "
        "research: %d total (%d new, %d updated)",
        meta["counts"]["legislation"] + meta["counts"]["regulatory"],
        len(new_leg), len(updated_leg),
        meta["counts"]["research"],
        len(new_res), len(updated_res),
    )


if __name__ == "__main__":
    main()
