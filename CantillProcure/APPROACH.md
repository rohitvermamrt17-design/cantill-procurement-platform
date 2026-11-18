# APPROACH.md

## Architecture

- **Frontend:** responsive HTML/CSS/vanilla JavaScript buyer and requester workspace.
- **Backend:** Python FastAPI REST API; decision logic is isolated in `app/engine.py`.
- **Persistence:** SQLite audit and pending-policy records; CSV/JSON master data and versioned policy files remain inspectable and portable.
- **Policy:** JSON policy configuration with effective dates and archived versions. Amendments are previewed, approved, versioned and applied by requirement date.
- **Data:** original challenge item, supplier, PO, plant, contract and PR batch files are retained under `data/`.

## AI/tool disclosure

OpenAI ChatGPT was used as a coding assistant to help interpret the task kit, scaffold code, draft tests and documentation, and review implementation logic. No external AI API is called by the running application. Requester chat and item matching are deterministic in this MVP, ensuring reproducible policy decisions and no invented item matches.

## Important modules

- `app/engine.py`: item resolution, requirement clubbing, valuation, contract ceiling checks, preferred supplier ranking, decision routing and PO consolidation.
- `app/main.py`: API, upload/export endpoints, contract extraction, policy preview/approval and audit logging.
- `static/`: single-page procurement workspace.
- `data/policy_v5.json` and `data/policies/`: active and historical policy data.
- `DECISIONS.md`: assumptions, weighting and unresolved policy gaps.
