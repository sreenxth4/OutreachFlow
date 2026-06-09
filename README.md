# 🚀 OutreachFlow

**Fully automated cold outreach pipeline — one domain in, personalized emails out.**

---

## Architecture

```
                         ┌──────────────┐
                         │  CLI Input   │
                         │  --domain    │
                         │  --dry-run   │
                         │  --mock      │
                         └──────┬───────┘
                                │
                         ┌──────▼───────┐
                         │   main.py    │
                         │ Orchestrator │
                         └──────┬───────┘
                                │
               ┌────────────────┼────────────────┐
               │                │                │
        ┌──────▼──────┐  ┌─────▼──────┐  ┌──────▼──────┐
        │ Health Check │  │  Pipeline  │  │   Output    │
        │  All 3 APIs  │  │  3 Stages  │  │  Artifacts  │
        └─────────────┘  └─────┬──────┘  └─────────────┘
                               │
          ┌────────────────────┼───────────────────┐
          │                    │                   │
   ┌──────▼──────┐    ┌───────▼──────┐    ┌───────▼──────┐
   │  Stage 1    │    │   Stage 2    │    │   Stage 3    │
   │  Ocean.io   │───▶│   Prospeo    │───▶│    Brevo     │
   │  Lookalike  │    │  Contacts    │    │  Send Email  │
   │  Companies  │    │  + Emails    │    │              │
   └─────────────┘    └──────┬───────┘    └──────────────┘
                             │
                      ┌──────▼───────┐
                      │   Safety     │
                      │  Checkpoint  │
                      │   [y/N]      │
                      └──────────────┘
```

---

## Project Structure

```
Outreach-Flow/
├── main.py                      # Root entry point (thin wrapper)
└── outreachflow/
    ├── main.py                  # Pipeline orchestrator (CLI logic)
    ├── config.py                # Settings dataclass + .env loader
    ├── models/
    │   ├── __init__.py          # Re-exports: Company, Contact, Lead
    │   ├── company.py           # Company dataclass
    │   ├── contact.py           # Contact dataclass
    │   └── lead.py              # Lead dataclass
    ├── stages/
    │   ├── __init__.py          # Re-exports all stage functions
    │   ├── ocean.py             # Stage 1: Lookalike company search
    │   ├── prospeo.py           # Stage 2: Decision-makers + email enrichment
    │   └── brevo.py             # Stage 3: Email sending
    ├── utils/
    │   ├── __init__.py          # Re-exports utilities
    │   ├── logger.py            # Dual-output logging (console + file)
    │   ├── retry.py             # Shared API call retry logic
    │   └── cleaner.py           # Domain cleaning, dedup, title filter
    ├── mock/
    │   ├── companies.json       # 5 realistic companies
    │   └── contacts.json        # 8 realistic decision-makers with emails
    ├── output/                  # Generated per-run output
    │   └── run_{RUN_ID}/
    │       ├── companies.csv
    │       ├── verified_emails.csv
    │       └── run_report.json
    ├── logs/
    │   └── pipeline.log         # Structured log file
    ├── .env                     # API keys (NOT committed)
    ├── .env.example             # Template for .env
    ├── .gitignore
    ├── requirements.txt
    └── README.md
```

---

## Setup

### 1. Clone & Install

```bash
git clone https://github.com/your-username/outreachflow.git
cd outreachflow

# Create virtual environment (recommended)
python -m venv venv
source venv/bin/activate   # macOS/Linux
venv\Scripts\activate      # Windows

# Install dependencies
pip install -r outreachflow/requirements.txt
```

### 2. Configure Environment

```bash
cp outreachflow/.env.example outreachflow/.env
```

Edit `outreachflow/.env` with your API keys:

### 3. Environment Variables

| Variable | Required | Description | Where to get it |
|----------|----------|-------------|-----------------|
| `OCEAN_API_KEY` | ✅ | Ocean.io API token | [ocean.io](https://ocean.io) → Account Settings → API Tokens |
| `PROSPEO_API_KEY` | ✅ | Prospeo API key (search + enrichment) | [app.prospeo.io](https://app.prospeo.io) → API Management |
| `BREVO_API_KEY` | ✅ | Brevo v3 API key | [brevo.com](https://app.brevo.com) → SMTP & API → API Keys |
| `BREVO_SENDER_EMAIL` | ❌ | Sender email (default: `sreenath@outreachflow.me`) | — |
| `BREVO_SENDER_NAME` | ❌ | Sender name (default: `Sreenath`) | — |
| `MAX_COMPANIES` | ❌ | Max lookalike companies (default: `5`) | — |
| `MAX_CONTACTS_PER_COMPANY` | ❌ | Max contacts per company (default: `2`) | — |
| `MAX_EMAILS_TO_SEND` | ❌ | Max emails to send (default: `10`) | — |

---

## Usage

### Normal Run (Live APIs + Send Emails)
```bash
python main.py --domain stripe.com
```

### Dry Run (Live APIs, NO Email Sending)
```bash
python main.py --domain stripe.com --dry-run
```
All 3 API stages fire for real. Brevo prints what it **would** send but does not actually deliver emails.

### Mock Run (No APIs, Local Data)
```bash
python main.py --domain stripe.com --mock
```
Loads pre-populated data from `mock/` folder. Zero API calls. Perfect for demos, testing, and development.

> **Note:** You can also use `python -m outreachflow.main --domain stripe.com` if you prefer module-style invocation.

---

## Pipeline Stages

| Stage | Service | Input | Output |
|-------|---------|-------|--------|
| 1 | **Ocean.io** | Seed domain | Lookalike company domains |
| 2 | **Prospeo** | Company domains | Decision-maker contacts + verified emails |
| 3 | **Brevo** | Name + email + company | Personalized cold email |

### Stage 1: Ocean.io — Lookalike Companies

Ocean.io searches for companies similar to the seed domain:

1. **Primary**: Uses `lookalikeDomains` filter on `/v3/search/companies`
2. **Category fallback**: If lookalike search fails but industry is resolved, searches by industry category
3. **Seed domain fallback**: If the domain is blocked (e.g., `robots disallowed`) and industry can't be resolved, falls back to using the seed domain itself as the target company

### Between Stages
1. **Dedup + domain cleaning** after Stage 1
2. **Title filtering** in Stage 2 (C-Suite, Founder/Owner, Vice President, Director)
3. **Email enrichment** in Stage 2 via Prospeo `/enrich-person`
4. **Safety checkpoint** before Stage 3 — shows full summary, prompts `[y/N]`
5. **Run summary** after Stage 3 with timings + metrics

---

## Error Handling Strategy

| Scenario | Handling |
|----------|----------|
| API auth failure (401/403) | Clear error message, exit code 1 |
| Rate limit (429) | Exponential backoff: 1s → 2s → 4s, max 3 retries |
| Not found (404) | Skip item, log warning, continue |
| Timeout | Retry once, then skip |
| Robots disallowed | Seed domain fallback — use the seed itself as target |
| Industry lookup fails | Skip generic category search, fall back to seed domain |
| Empty stage result | Print warning, exit gracefully |
| Missing contacts | Skip, log, continue to next |
| Duplicate domains/emails | Deduplicated silently |
| Invalid LinkedIn URLs | Skipped gracefully |
| Invalid input domain | Validate upfront, fail fast |

---

## Free Tier Limits & Safe Defaults

| Service | Free Tier Limit | Safe Default |
|---------|----------------|--------------|
| Ocean.io | 14-day trial, ~60 req/min | `MAX_COMPANIES=5` |
| Prospeo | 100 credits/month, 1 req/sec | `MAX_CONTACTS_PER_COMPANY=2` |
| Brevo | 300 emails/day | `MAX_EMAILS_TO_SEND=10` |

---

## Output Artifacts

Each run saves to `output/run_{RUN_ID}/`:

| File | Contents |
|------|----------|
| `companies.csv` | All lookalike domains found |
| `verified_emails.csv` | All verified leads (contacts + emails) |
| `run_report.json` | Full run metadata: RUN_ID, domain, timestamps, counts, stage timings, success rates |

---

## Demo Instructions

### Live Demo
```bash
# Full pipeline with a real domain
python main.py --domain stripe.com --dry-run
```

### Fallback (if APIs are down)
```bash
# Uses local mock data — zero API dependencies
python main.py --domain stripe.com --mock
```

### What to Show
1. **Run the pipeline** — watch all 3 stages fire
2. **Safety checkpoint** — show the confirmation prompt
3. **Output artifacts** — open CSV and JSON files
4. **Logs** — show `logs/pipeline.log`
5. **Code walkthrough** — each stage is one file, independently testable

---

## Tech Stack

| Library | Purpose |
|---------|---------|
| `requests` | All API calls |
| `rich` | Colored terminal output, tables, panels |
| `python-dotenv` | Load `.env` file |
| `argparse` | CLI argument parsing |
| `logging` | Structured dual-output logs |
| `dataclasses` | Typed models + Settings |
| `csv` / `json` | Output artifact generation |

---

## Author

**Sreenath**
- Domain: [outreachflow.me](https://outreachflow.me)
- Email: sreenath@outreachflow.me
