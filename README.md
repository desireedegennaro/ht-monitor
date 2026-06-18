# Human Trafficking Law & Policy Monitor

A searchable, filterable database tracking federal and state legislation, civil and criminal litigation, and peer-reviewed research related to human trafficking. Built for ORSJ Lab at Northeastern. Live at [desireedegennaro.github.io/ht-monitor](https://desireedegennaro.github.io/ht-monitor).

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
- Per-entry update timeline: each status change, action, or publication event is preserved chronologically
- Digest tab: reads from `data/digest.json` to separate new entries from updates to existing tracked items
- Community submissions via GitHub Issues (auto-parsed and merged)
- Stars, notes, and annotations stored locally in the browser

---

## Pipeline Architecture

The fetch pipeline runs daily via GitHub Actions and writes to four JSON files. The core design problem it solves: the same real-world item arrives from multiple sources, and the same source returns the same item on multiple days. Naive appending produces duplicates. Naive overwriting destroys manually-entered annotations.

### Sources (v3)

| Source | Category | API Key | Notes |
|---|---|---|---|
| [Congress.gov API](https://api.congress.gov/) | Legislation | Yes — free | 10 search terms, 25 results each |
| [Federal Register API](https://www.federalregister.gov/developers/api/v1) | Regulatory | No | 6 search queries per run |
| DOJ press releases | Regulatory | No | RSS |
| HHS press releases | Regulatory | No | RSS |
| DHS press releases | Regulatory | No | RSS |
| FinCEN advisories | Regulatory | No | RSS (2 feeds) |
| State Dept (TIP Office) | Regulatory | No | RSS |
| [PubMed E-utilities](https://eutils.ncbi.nlm.nih.gov/entrez/eutils/) | Research | No | 10 queries; fetches full abstracts via XML |
| [Semantic Scholar](https://api.semanticscholar.org/) | Research | No | 5 queries; cross-deduped against PubMed by DOI/PMID |
| [Overton](https://www.overton.io/) | Research | Yes — institutional | Northeastern access; falls back to PubMed + SS if key absent |
| PACER / court dockets | Lawsuits | No public API | Manual |

### Deduplication

Each fetch run builds a `DeduplicationIndex` from the existing JSON files before processing new items. The index maintains five lookup maps in priority order:

1. **Item ID** — catches the same bill returned by two different search terms in the same run
2. **DOI** (normalized) — catches the same research paper from PubMed and Semantic Scholar
3. **PMID** — secondary cross-source dedup for research
4. **URL** — catches RSS entries seen on multiple consecutive days
5. **Bill identifier + Congress** (e.g. `HR1234-119`) — catches the same bill when ID formatting differs across fetches

### Merge logic

For each incoming item, `smart_merge()` returns one of three outcomes:

- **New** — item not found in index; appended to the JSON and indexed
- **Updated** — item found; status or latest action has changed; the existing record is updated in-place, the new event is appended to its `updates[]` timeline, and a digest card is generated
- **Duplicate** — item found; nothing meaningful changed; silently skipped

User-annotated fields — `notes`, `evidence_strength`, `outcome_frame`, `relevance`, `my_position`, `funder`, `organizations` — are never overwritten by auto-fetch. They survive every run.

### Digest output

Each run writes `data/digest.json` with two arrays: `new` (items added this run) and `updated` (items with new actions or status changes). Each card contains a compact subset of the full entry plus an `update_summary` string (e.g. `"In Committee → Passed House"`) and the specific new timeline event. The Digest tab reads from this file rather than scanning all entries for recent dates.

### Keyword matching

Bills and documents are filtered using a two-tier keyword list. A broader `CONGRESS_MIN_KEYWORDS` list (including statutory citations like `§1591`, `§1595`, `TVPA`, `UFLPA`) matches against title + latest action text combined — not title alone, which was too narrow. A wider `HT_KEYWORDS` list is used for Federal Register, RSS, and abstract matching in research sources.

---

## Project Structure

```
ht-monitor/
├── index.html                     # full app — HTML, CSS, JS in one file
├── about.html                     # data documentation and user guide
├── data/
│   ├── legislation.json           # legislation + regulatory entries
│   ├── lawsuits.json              # civil and criminal cases (manual)
│   ├── research.json              # peer-reviewed papers
│   ├── meta.json                  # entry counts + per-source fetch timestamps
│   └── digest.json                # new + updated items from most recent run
├── scripts/
│   ├── fetch_data.py              # v3 pipeline: DeduplicationIndex + smart_merge
│   ├── process_submission.py      # parses GitHub Issue into JSON entry
│   └── requirements.txt
└── .github/
    ├── ISSUE_TEMPLATE/
    │   └── submit_entry.yml       # structured submission form
    └── workflows/
        ├── fetch.yml              # scheduled daily at 8 AM EST
        └── process_submission.yml # triggers on [SUBMISSION] issues
```

---

## Setup

**API keys** are stored as GitHub Actions repository secrets. Neither is required for the pipeline to run — if absent, those sources are skipped and the rest continue.

| Secret | Where to get it |
|---|---|
| `CONGRESS_API_KEY` | [api.congress.gov/sign-up](https://api.congress.gov/sign-up/) — free, instant |
| `OVERTON_API_KEY` | Northeastern institutional access — contact library |

**First run (backfill):**
Go to Actions → Fetch & Update HT Tracker Data → Run workflow → set `days_back` to `365`.

**Ongoing:** runs automatically at 8 AM EST daily.

**Local dev:**

```bash
pip install -r scripts/requirements.txt
CONGRESS_API_KEY=your_key python scripts/fetch_data.py --days-back 90 --dry-run
```

---

## Data Schema

Each entry in `legislation.json` and `research.json` follows the same base schema. Fields marked `user` are never overwritten by auto-fetch.

```
id                    string    unique identifier (e.g. "hr1234-119", "pubmed-38291044")
type                  string    "legislation" | "regulatory" | "research"
title                 string
identifier            string    bill number, doc number, DOI/PMID
status                string    current status (auto-updated)
jurisdiction          string    "federal" | "state" | "international"
trafficking_types     array     ["sex"] | ["labor"] | ["sex", "labor"]
introduced/published  string    YYYY-MM-DD
latest_action_date    string    YYYY-MM-DD (auto-updated)
latest_action         string    (auto-updated)
url                   string
summary               string
description           string    full abstract or bill text excerpt
keywords              array     matched HT keywords
updates               array     append-only timeline of events
  └─ date, event, actor, source_url
source                string    origin API(s) — may show "PubMed + Semantic Scholar"
upcoming              array     scheduled events (manually added)

-- user fields (never auto-overwritten) --
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

Submit an entry via the [GitHub Issues form](https://github.com/desireedegennaro/ht-monitor/issues/new/choose). Use the **Submit Entry** template. Required fields: Entry Type, Title, Date, Summary, URL. The submission is automatically parsed, merged into the appropriate JSON file, and live on the site within a few minutes.

Corrections and additions to existing entries can be submitted the same way, include the entry ID if known.
