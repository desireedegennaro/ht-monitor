# Human Trafficking Law & Policy Monitor

A searchable, filterable database tracking federal and state legislation, civil and criminal litigation, and peer-reviewed research related to human trafficking. Visit at https://desireedegennaro.github.io/ht-monitor 

---

## DB Population & Tracking

| Category | Seed Count | Source |
|---|---|---|
| Legislation + Regulatory | 103 | Congress.gov, Federal Register, FinCEN (daily) |
| Lawsuits | 40 | PACER / manual |
| Research | 114 | Overton API (daily) + manual |

---

## Features

- Full-text search across title, summary, sponsor, keywords, company, and court
- Advanced filters: trafficking type, jurisdiction, outcome frame, evidence strength, case category, policy mechanism
- Per-entry update timeline with full action history
- Digest tab: recent activity + upcoming events in one view
- Community submissions via GitHub Issues (auto-processed)
- Stars, notes, and annotations stored locally

---

## Project structure

```
ht-tracker/
├── index.html                   # HTML + CSS + JS
├── about.html                   # documentation and user guide
├── data/                        # seed population and db
│   ├── legislation.json         
│   ├── lawsuits.json            
│   ├── research.json            
│   └── meta.json                # entry counts and timestamps
├── scripts/
│   ├── fetch_data.py            # daily auto-fetch (Congress, FR, FinCEN, Overton)
│   ├── process_submission.py    # parses GitHub Issue into json
│   └── requirements.txt
└── .github/
    ├── ISSUE_TEMPLATE/
    │   └── submit_entry.yml     # structured submission form for Git Issue
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
