#!/usr/bin/env python3
"""
Parse a GitHub Issue form submission and append the entry to the appropriate
data JSON file. Designed to run inside a GitHub Actions workflow.

Environment variables (set by the workflow):
  ISSUE_BODY     - raw body text of the GitHub issue
  ISSUE_NUMBER   - issue number (used in IDs and update notes)
  ISSUE_AUTHOR   - GitHub username of submitter
  ISSUE_URL      - full URL of the issue
  GITHUB_ENV     - path to the GitHub Actions env file (auto-set by Actions)
"""

import datetime
import json
import os
import re
import sys


# ── Helpers ─────────────────────────────────────────────────────────────────

def set_env(key, value):
    """Write a variable to GITHUB_ENV so subsequent workflow steps can read it."""
    env_file = os.environ.get("GITHUB_ENV", "")
    if env_file:
        with open(env_file, "a") as f:
            f.write(f"{key}={value}\n")


def fail(message):
    print(f"ERROR: {message}", file=sys.stderr)
    set_env("SUBMISSION_STATUS", "error")
    set_env("SUBMISSION_ERROR", message)
    sys.exit(1)


def parse_issue_body(body):
    """
    Convert GitHub issue form body into a dict.

    GitHub renders YAML form fields as:
        ### Field Label
        \nValue text\n
    """
    fields = {}
    sections = re.split(r"^### ", body, flags=re.MULTILINE)
    for section in sections:
        if not section.strip():
            continue
        lines = section.split("\n")
        label = lines[0].strip()
        value = "\n".join(lines[1:]).strip()
        # GitHub puts "_No response_" when a field is left blank
        if value and value.lower() not in ("_no response_", "no response", "none", ""):
            fields[label] = value
    return fields


def normalize_date(raw):
    """Return YYYY-MM-DD from whatever the user typed, best-effort."""
    if not raw:
        return datetime.date.today().isoformat()
    raw = raw.strip()
    if re.match(r"^\d{4}-\d{2}-\d{2}$", raw):
        return raw
    for fmt in ("%B %d, %Y", "%b %d, %Y", "%m/%d/%Y", "%d/%m/%Y", "%Y/%m/%d", "%m-%d-%Y"):
        try:
            return datetime.datetime.strptime(raw, fmt).date().isoformat()
        except ValueError:
            pass
    m = re.search(r"\b(19|20)\d{2}\b", raw)
    return f"{m.group()}-01-01" if m else datetime.date.today().isoformat()


def get_field(fields, *keys):
    """Try multiple key names (handles label differences) and return first match."""
    for k in keys:
        if k in fields:
            return fields[k].strip()
    return ""


# ── Entry builder ────────────────────────────────────────────────────────────

def build_entry(fields, author, issue_url, issue_number):
    entry_type = get_field(fields, "Entry Type").lower()
    if entry_type not in ("legislation", "regulatory", "lawsuit", "research"):
        entry_type = "research"

    title       = get_field(fields, "Title")
    summary     = get_field(fields, "Summary")
    identifier  = get_field(fields, "Bill / Case Number / DOI")
    url         = get_field(fields, "Primary Source URL")
    date_str    = normalize_date(get_field(fields, "Date (YYYY-MM-DD)", "Date"))
    status      = get_field(fields, "Current Status") or "Submitted"
    jurisdiction = get_field(fields, "Jurisdiction").lower() or "federal"
    sponsor     = get_field(fields, "Sponsor / Lead Author / Plaintiff")
    description = get_field(fields, "Additional Details (optional)", "Additional Details")

    # Trafficking types — field value is a newline- or comma-separated list
    trafficking_raw = get_field(fields, "Trafficking Type(s)").lower()
    trafficking_types = []
    if "both" in trafficking_raw or ("sex" in trafficking_raw and "labor" in trafficking_raw):
        trafficking_types = ["sex", "labor"]
    else:
        if "sex" in trafficking_raw:
            trafficking_types.append("sex")
        if "labor" in trafficking_raw:
            trafficking_types.append("labor")

    # Stable unique ID: community-<slug>-<year>-<issue#>
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower())[:40].strip("-")
    entry_id = f"community-{slug}-{date_str[:4]}-{issue_number}"

    today = datetime.date.today().isoformat()

    entry = {
        "id": entry_id,
        "type": entry_type,
        "title": title,
        "identifier": identifier,
        "status": status,
        "jurisdiction": jurisdiction,
        "trafficking_types": trafficking_types,
        "summary": summary,
        "url": url,
        "keywords": [],
        "companies": [],
        "source": f"Community Submission (Issue #{issue_number})",
        "submitted_by": author,
        "latest_action_date": date_str,
        "latest_action": f"Submitted by community member via Issue #{issue_number}",
        "updates": [
            {
                "date": today,
                "event": "Entry submitted to tracker by community member",
                "actor": author,
                "notes": f"Submitted via GitHub Issue #{issue_number}",
                "source_url": issue_url,
            }
        ],
        "upcoming": [],
    }

    if description:
        entry["description"] = description

    # Type-specific date + person fields
    if entry_type in ("legislation", "regulatory"):
        entry["introduced"] = date_str
        entry["cosponsors"] = 0
        entry["cosponsor_list"] = []
        if sponsor:
            entry["sponsor"] = sponsor
    elif entry_type == "lawsuit":
        entry["filed"] = date_str
        entry["plaintiffs"] = [s.strip() for s in re.split(r"[,;]", sponsor)] if sponsor else []
        entry["defendants"] = []
    elif entry_type == "research":
        entry["published"] = date_str
        entry["authors"] = [a.strip() for a in re.split(r"[,;]", sponsor)] if sponsor else []
        entry["organizations"] = []

    # Drop empty strings and None
    return {k: v for k, v in entry.items() if v != "" and v is not None}, entry_type


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    body         = os.environ.get("ISSUE_BODY", "")
    author       = os.environ.get("ISSUE_AUTHOR", "anonymous")
    issue_url    = os.environ.get("ISSUE_URL", "")
    issue_number = os.environ.get("ISSUE_NUMBER", "0")

    if not body.strip():
        fail("Issue body is empty")

    fields = parse_issue_body(body)

    title = get_field(fields, "Title")
    if not title:
        fail("Missing required field: Title")

    entry_type_raw = get_field(fields, "Entry Type")
    if not entry_type_raw:
        fail("Missing required field: Entry Type")

    try:
        entry, entry_type = build_entry(fields, author, issue_url, issue_number)
    except Exception as exc:
        fail(f"Could not build entry: {exc}")

    # Choose target file
    file_map = {
        "legislation": "data/legislation.json",
        "regulatory":  "data/legislation.json",
        "lawsuit":     "data/lawsuits.json",
        "research":    "data/research.json",
    }
    filepath = file_map.get(entry_type, "data/research.json")

    # Load, deduplicate ID, append
    with open(filepath, encoding="utf-8") as f:
        data = json.load(f)

    existing_ids = {item["id"] for item in data}
    if entry["id"] in existing_ids:
        entry["id"] += f"-{issue_number}"

    data.append(entry)

    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    # Update meta.json counts and timestamp
    leg_data = json.load(open("data/legislation.json", encoding="utf-8"))
    law_data = json.load(open("data/lawsuits.json", encoding="utf-8"))
    res_data = json.load(open("data/research.json", encoding="utf-8"))

    with open("data/meta.json", encoding="utf-8") as f:
        meta = json.load(f)

    meta["counts"]["legislation"] = sum(1 for i in leg_data if i.get("type") == "legislation")
    meta["counts"]["regulatory"]  = sum(1 for i in leg_data if i.get("type") == "regulatory")
    meta["counts"]["lawsuit"]     = len(law_data)
    meta["counts"]["research"]    = len(res_data)
    meta["last_updated"] = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

    with open("data/meta.json", "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)

    print(f"SUCCESS: Added '{entry['title']}' (id: {entry['id']}) to {filepath}")
    set_env("SUBMISSION_STATUS", "ok")
    set_env("SUBMISSION_TITLE", entry["title"].replace("\n", " "))


if __name__ == "__main__":
    main()
