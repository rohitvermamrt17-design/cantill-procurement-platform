# Cantill Procure — Corvo Engineering

Policy-driven purchase requisition intake, decisioning and buyer workspace built for the Cantill.ai Full-stack Intern challenge.

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

PDF contract and policy text extraction uses `pdftotext` (installed in the Docker image; install Poppler locally if using PDF upload locally).

## What is implemented

- Chat-first PR intake with item-master matching, clarification for missing item/quantity/plant/need-by date, and explicit confirmation before creating a PR.
- Sample-format PR CSV batch upload and `POST /api/process-batch` decision API.
- Item matching, no-split requirement clubbing, valuation, active supplier/contract checks, contract ceilings, emergency/spot/RFQ/RFP routing and 24-hour PO consolidation.
- Versioned policy JSON with effective dates. Amendment preview, buyer approval and policy history; processing selects the policy version effective on each requirement's creation date.
- Preferred supplier ranking and CSV export.
- Contract register, signed-PDF schedule extraction and add-contract flow.
- Buyer approve/override audit actions; overrides require a written reason.
- Audit trail and downloadable decision CSV.

## API

| Method | Endpoint | Purpose |
|---|---|---|
| GET | `/api/summary` | Master-data counts and active policy |
| POST | `/api/process-batch` | Upload CSV and return JSON decisions |
| POST | `/api/process-batch/export` | Upload CSV and download decision CSV |
| GET | `/api/export/latest` | Download latest processed decisions |
| GET | `/api/preferred-suppliers?as_of=2026-11-01` | Ranked suppliers with evidence |
| GET | `/api/preferred-suppliers/export?as_of=2026-11-01` | Required four-column supplier CSV |
| GET/POST | `/api/contracts` | List/add signed contracts |
| POST | `/api/policy/preview` | Preview policy amendment |
| POST | `/api/policy/approve/{id}` | Approve and activate version |
| GET | `/api/policy` | Active policy data |
| POST | `/api/chat` | Chat intake clarification/match |
| POST | `/api/chat/confirm` | Create confirmed PR |
| POST | `/api/action` | Record buyer approval/override |
| GET | `/api/audit` | Audit events |

Swagger docs are at `/docs`.

## Policy and contract data

The original task kit is retained under `data/`. `data/policy_v5.json` is the current policy data. Approved versions are archived in `data/policies/`. PDF-only contracts are discovered from their signed PDFs at load time; uploaded signed contracts are extracted and appended to the register and line data.

## Deployment

The app is Docker-ready for Render, Railway or any container host. Configure the service to run `docker compose` or build from the included Dockerfile and expose port `10000`. Attach a persistent disk at `/app/data`. A public hosted URL and public GitHub repository require the candidate's own hosting/GitHub account credentials; they are not created by this local project package.

