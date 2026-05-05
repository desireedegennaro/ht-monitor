# Human Trafficking Law & Policy Monitor

A searchable, filterable database tracking federal and state legislation, civil and criminal litigation, and peer-reviewed research related to human trafficking. Hosted on GitHub Pages with no backend, no build step, and daily automated data updates.

---

## What it tracks

| Category | Count | Source |
|---|---|---|
| Legislation + Regulatory | 103 | Congress.gov, Federal Register, FinCEN (daily) |
| Lawsuits | 40 | PACER / manual |
| Research | 114 | Overton API (daily) + manual |

All 257 seed entries were hand-researched and curated by the project lead.

---

## Features

- Full-text search across title, summary, sponsor, keywords, company, and court
- Advanced filters: trafficking type, jurisdiction, outcome frame, evidence strength, case category, policy mechanism
- Per-entry update timeline with full action history
- Digest tab: recent activity + upcoming events in one view
- Community submissions via GitHub Issues (auto-processed, no review queue)
- Stars, notes, and annotations stored locally in your browser -- never synced

---

## Project structure

```
ht-tracker/
├── index.html                   # entire app (HTML + CSS + JS, no build step)
├── about.html                   # technical documentation and user guide
├── data/
│   ├── legislation.json         # bills and regulatory entries
│   ├── lawsuits.json            # civil and criminal cases
│   ├── research.json            # research papers and reports
│   └── meta.json                # entry counts and timestamps
├── scripts/
│   ├── fetch_data.py            # daily auto-fetch (Congress, FR, FinCEN, Overton)
│   ├── process_submission.py    # parses GitHub Issue submissions into JSON
│   └── requirements.txt
└── .github/
    ├── ISSUE_TEMPLATE/
    │   └── submit_entry.yml     # structured submission form
    └── workflows/
        ├── fetch.yml            # daily data fetch (8 AM EST)
        └── process_submission.yml
```

---

## Data sources

| Source | Provides | Key required |
|---|---|---|
| [Congress.gov API](https://api.congress.gov/) | Federal bills and actions | Yes, free |
| [Federal Register API](https://www.federalregister.gov/developers/api/v1) | Agency rules and notices | No |
| [FinCEN RSS](https://www.fincen.gov) | Financial crime advisories | No |
| [Overton](https://www.overton.io/) | Research papers | Yes, institutional |
| PACER / court dockets | Lawsuit filings | No public API -- manual |
| State legislatures | State bills and statutes | No uniform API -- manual |

---

## Adding entries manually

Open the appropriate file in `data/` and append a new JSON object. The `id` field must be unique across all three files. See [about.html](about.html) for the full schema for each entry type.

```bash
# After editing a data file
git add data/
git commit -m "add [entry title]"
git push
```

---

## Community submissions

Anyone with a GitHub account can submit an entry via the `[+] Submit` button in the tracker. A GitHub Actions workflow parses the form, appends the entry to the appropriate data file, commits it, and posts a confirmation comment on the issue. Submissions appear on the live site shortly after. Community entries carry a `[C]` badge and can be reviewed or removed by editing the data file directly.

---

## Local development

```bash
cd ht-tracker
python3 -m http.server 8000
# open http://localhost:8000
```

No install, no build step. The app loads data via `fetch()` so a local server is required (direct `file://` access will not work).

---

## License

Data is sourced from public government databases and published academic work. See individual entry `source` fields for provenance. Code is available for reuse with attribution.
