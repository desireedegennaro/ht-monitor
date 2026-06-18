# Human Trafficking Law & Policy Monitor

A searchable database tracking federal legislation, civil and criminal litigation, and peer-reviewed research on human trafficking. Built for ORSJ Lab at Northeastern. Live at [desireedegennaro.github.io/ht-monitor](https://desireedegennaro.github.io/ht-monitor).

---

## Database

| Category | Count | Source |
|---|---|---|
| Legislation | 84 | Congress.gov (daily) |
| Regulatory | 22 | Federal Register, FinCEN, DOJ, HHS, DHS, State Dept (daily) |
| Lawsuits | 40 | PACER / manual |
| Research | 114+ | PubMed, Semantic Scholar, Overton (daily) |

Counts update automatically. `data/meta.json` holds current totals and last-fetch timestamps per source.

---

## Features

- Full-text search across title, summary, sponsor, keywords, company, and court
- Filters: trafficking type, jurisdiction, outcome frame, evidence strength, case category, policy mechanism
- Per-entry update timeline — status changes and actions are preserved chronologically, never overwritten
- Digest tab: reads from `data/digest.json` to show new entries and updates to existing ones separately
- Community submissions via GitHub Issues (auto-parsed and merged)
- Stars, notes, and annotations stored locally in the browser

---

## Pipeline

The fetch script runs daily via GitHub Actions and writes to four JSON files. The core problem it solves: the same item arrives from multiple sources, and the same source returns the same item on multiple days. A naive append produces duplicates. A naive overwrite destroys your annotations.

### Sources

| Source | Category | Key | Notes |
|---|---|---|---|
| [Congress.gov API](https://api.congress.gov/) | Legislation | Yes — free | 10 search terms, 25 results each |
| [Federal Register API](https://www.federalregister.gov/developers/api/v1) | Regulatory | No | 6 search queries per run |
| DOJ press releases | Regulatory | No | RSS |
| HHS press releases | Regulatory | No | RSS |
| DHS press releases | Regulatory | No | RSS |
| FinCEN advisories | Regulatory | No | RSS (2 feeds) |
| State Dept (TIP Office) | Regulatory | No | RSS |
| [PubMed E-utilities](https://eutils.ncbi.nlm.nih.gov/entrez/eutils/) | Research | No | 10 queries; full abstracts via XML |
| [Semantic Scholar](https://api.semanticscholar.org/) | Research | No | 5 queries; cross-deduped with PubMed by DOI/PMID |
| [Overton](https://www.overton.io/) | Research | Yes — institutional | Northeastern access; falls back to PubMed + SS if absent |
| PACER / court dockets | Lawsuits | No public API | Manual |

### Accuracy filtering (RSS)

Government RSS feeds are noisy. DOJ publishes drug trafficking prosecutions daily. "Forced labor" appears in wage-theft cases. The RSS fetch runs two filters before accepting an item:

**Layer 1 — compound phrase match.** Items must contain at least one multi-word HT-specific phrase (e.g., "human trafficking," "sex trafficking," "forced labor," "debt bondage," "TVPA"). Bare words like "trafficking" and "exploitation" are not in the filter list, so drug trafficking and economic exploitation don't match.

**Layer 2 — false positive check.** Items that passed layer 1 are checked a second time. If the text contains a confirming phrase ("human trafficking," "sex trafficking," "forced labor," etc.), it passes immediately. If it contains a disqualifying phrase ("drug trafficking," "narcotics trafficking," "fentanyl trafficking," "weapons trafficking," etc.) without any confirming phrase, it's rejected. Everything else passes.

A DOJ press release about a fentanyl conviction that also mentions "TVPA" in passing fails layer 2. A genuine HT prosecution passes both layers on "human trafficking" in layer 2 before the disqualifiers even run.

### Deduplication

Before processing any new items, the script builds a `DeduplicationIndex` from the existing JSON files. The index maintains five lookup maps, checked in this order:

1. **Item ID** — catches the same bill returned by two different search terms in the same run
2. **DOI** (normalized) — catches the same paper from PubMed and Semantic Scholar
3. **PMID** — secondary cross-source dedup for research
4. **URL** — catches RSS entries seen on multiple consecutive days
5. **Bill identifier + Congress** (e.g. `HR1234-119`) — catches the same bill when ID formatting differs

### Merge logic

For each incoming item, `smart_merge()` returns one of three outcomes:

- **New** — not in the index; appended to the JSON and indexed
- **Updated** — found in index; status or action changed; existing record updated in-place, new event appended to its `updates[]` timeline, digest card generated
- **Duplicate** — found in index; nothing changed; silently skipped

User fields — `notes`, `evidence_strength`, `outcome_frame`, `relevance`, `my_position`, `funder`, `organizations` — are never touched. They survive every run.

### Digest

Each run writes `data/digest.json` with two arrays: `new` (items added this run) and `updated` (items that got a new status or action). Each card includes title, source, date, status, summary, url, and — for updates — an `update_summary` string (e.g. `"In Committee → Passed House"`) and the specific new timeline event.

---

## Project Structure

```
ht-monitor/
├── index.html                     # full app — HTML, CSS, JS in one file
├── about.html                     # documentation and user guide
├── data/
│   ├── legislation.json           # legislation + regulatory entries
│   ├── lawsuits.json              # civil and criminal cases (manual)
│   ├── research.json              # peer-reviewed papers
│   ├── meta.json                  # entry counts + per-source fetch timestamps
│   └── digest.json                # new + updated items from most recent run
├── scripts/
│   ├── fetch_data.py              # pipeline: DeduplicationIndex + smart_merge
│   ├── process_submission.py      # parses GitHub Issue into JSON entry
│   └── requirements.txt
└── .github/
    ├── ISSUE_TEMPLATE/
    │   └── submit_entry.yml       # structured submission form
    └── workflows/
        ├── fetch.yml              # runs daily at 8 AM EST
        └── process_submission.yml # triggers on [SUBMISSION] issues
```

---

## Setup

API keys go in GitHub Actions repository secrets. Both are optional — if absent, those sources are skipped and everything else continues.

| Secret | Where to get it |
|---|---|
| `CONGRESS_API_KEY` | [api.congress.gov/sign-up](https://api.congress.gov/sign-up/) — free |
| `OVERTON_API_KEY` | Northeastern institutional access |

**First run (backfill):** Actions → Fetch & Update HT Tracker Data → Run workflow → `days_back` = `365`

**Ongoing:** runs automatically at 8 AM EST.

**Local:**
```bash
pip install -r scripts/requirements.txt
CONGRESS_API_KEY=your_key python scripts/fetch_data.py --days-back 90 --dry-run
```

---

## Data Schema

Fields marked `[user]` are never overwritten by auto-fetch.

```
id                    string    e.g. "hr1234-119", "pubmed-38291044"
type                  string    "legislation" | "regulatory" | "research"
title                 string
identifier            string    bill number, doc number, DOI/PMID
status                string    auto-updated
jurisdiction          string    "federal" | "state" | "international"
trafficking_types     array     ["sex"] | ["labor"] | ["sex", "labor"]
introduced/published  string    YYYY-MM-DD
latest_action_date    string    YYYY-MM-DD — auto-updated
latest_action         string    auto-updated
url                   string
summary               string
description           string    full abstract or bill text excerpt
keywords              array     matched HT keywords
updates               array     append-only event timeline
  └─ date, event, actor, source_url
source                string    may show "PubMed + Semantic Scholar" for cross-source items
upcoming              array     scheduled events (manual)

-- user fields --
notes                 string    [user]
relevance             string    [user]  High | Medium | Low
evidence_strength     string    [user]  Strong | Moderate | Weak | Insufficient
outcome_frame         string    [user]
follow_up_length      string    [user]
funder                string    [user]
organizations         array     [user]
my_position           string    [user]
```

---

## Contributing

Submit via the [GitHub Issues form](https://github.com/desireedegennaro/ht-monitor/issues/new/choose) using the Submit Entry template. Required: Entry Type, Title, Date, Summary, URL. It's automatically parsed, merged, and live within a few minutes. Include the entry ID for corrections to existing entries.
