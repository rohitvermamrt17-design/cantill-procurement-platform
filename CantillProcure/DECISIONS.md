# DECISIONS.md — assumptions and judgment log

This document distinguishes explicit policy requirements from implementation choices where the task kit is silent or ambiguous.

## Policy rules implemented as written

- Item code takes precedence; otherwise description matching is used. A weak/ambiguous match is treated as a new item rather than inventing an item identity.
- New items are not clubbed, use the requester's estimated unit price, and receive no supplier suggestions.
- Requirements are built greedily in creation-time order. The first eligible requirement is chosen when more than one could accept a line.
- Clubbing is same item + same plant + within 14 calendar days from the first line + need-by date within ±7 calendar days from the first line. Emergency on any line makes the whole requirement emergency.
- Contract eligibility is based on the requirement's first-line creation date, active supplier status and inclusive contract dates. Contract ceilings are consumed in requirement order. Contracts found only in signed PDFs are loaded with zero consumed value, as required by section 4.2.
- Capex is tested before contracts. Contract PO is tested before emergency and spot. Emergency and spot require an active-supplier purchase in the preceding 365 days.
- A requirement that cannot use a contract is valued at the lowest applicable contract rate even when all applicable contracts have insufficient remaining ceiling; then the normal decision rules apply.
- Supplier suggestions exclude Blocked and Inactive suppliers. RFQ receives three and RFP/Capex receives five, topping up from Active suppliers in the item's category.
- PO consolidation is chronological and greedy: same supplier, plant and route, no more than 24 hours from the first requirement in the PO. Sourcing events are never consolidated.

## Explicit implementation decisions

### 1. Item matching confidence
**Observation:** The policy requires matching free-text descriptions but gives no algorithm or confidence threshold.

**Decision:** Use exact item-code match first, then a deterministic text similarity/token-overlap score. Require a score of at least 0.48; below that, classify as `NEW` and route to `NEW_ITEM_SOURCING`. This favors a safe sourcing review over a false direct PO. The threshold is deterministic and can be tuned without changing the policy's decision rules.

### 2. Preferred supplier ranking
**Observation:** Section 6.2 names frequency, recency, contract history, on-time delivery and quality but leaves weighting to system design. Worked examples also illustrate recency bands of 1.0 (within 90 days), 0.5 (within a year), 0 (older).

**Decision:** Rank by a normalized weighted score:
- Purchase frequency: 35% (capped at 10 POs in the 548-day window)
- Recency: 25% (1.0 ≤90 days; 0.5 ≤365 days; otherwise 0)
- Contract history: 15% (held a contract for the item starting on/before the as-of date)
- On-time delivery: 15%
- Quality rating: 10% (normalized against 5)

Tie-break by PO count descending, then supplier ID ascending. Only Active suppliers qualify. Reference price is the supplier's latest PO price in the 548-day window; if there is no PO, use the most recent qualifying contract rate. The UI exposes the score components/evidence so a buyer can explain a rank. This is a consistent design choice, not a weight formula prescribed by Corvo.

### 3. Valuation for direct spot/emergency POs
**Observation:** Section 3.1 values the requirement using a contract rate, recent PO price, or standard price. Sections 5.3–5.4 separately say to place a PO at the first preferred supplier's reference price.

**Decision:** For a qualifying contract PO, use the contract rate. For spot/emergency PO, use the first preferred supplier's reference price and recompute requirement value using that price, because the direct-PO rule explicitly names that price. For sourcing, retain the section 3.1 valuation hierarchy. This matches the worked examples' rounded prices and totals.

### 4. Supplier fallback for sourcing
**Observation:** Section 6.4 says to add other Active suppliers listing the category but does not define their ordering.

**Decision:** Sort fallback suppliers by an equal-weight average of normalized on-time delivery and quality, then supplier ID. This reproduces the example preference of strong combined delivery/quality over delivery rate alone. Preferred-list suppliers retain their calculated rank and are never duplicated.

### 5. Contract ceiling consumption
**Observation:** Register consumption is a snapshot, and the policy requires requirement-date ordering. A batch may include requirements from different dates.

**Decision:** Initialize each contract's usage from the supplied `consumed_value`, then consume new requirements in chronological processing order. A contract found only in a signed PDF starts at zero. The engine does not persist hypothetical batch consumption back to the source register; a real PO issuance workflow should persist confirmed PO consumption transactionally.

### 6. Policy effective dates and amendments
**Observation:** Policy versions have effective dates and a blind-test amendment must change behavior through the app without code changes.

**Decision:** Store policy rules as JSON, preserve each approved version, and select the latest version whose effective date is on or before the requirement date. The preview step creates a pending version; approval is an explicit buyer action. Threshold amendments in plain language/PDF are extracted into structured fields when recognizable. Structured JSON policy upload supports exact rule data; unrecognized natural-language clauses are retained for buyer review rather than silently guessed. For a new rule type or plant-specific condition, encode it as a structured rule before approval.

### 7. Date and urgency interpretation
**Observation:** The policy does not specify a timezone or handling for malformed dates and missing fields in imported batch data.

**Decision:** Treat CSV timestamps as local plant/business time, as the challenge timeline is IST and the source has no timezone offset. Missing/invalid created timestamps fall back to 1 Nov 2026 only to keep batch processing deterministic; missing need-by dates fall back to creation date. Chat intake asks for these fields before PR creation. Missing quantity becomes zero in batch import and should be flagged by a production validation layer.

### 8. Unit-of-measure and quantity compatibility
**Observation:** The policy says club by same item and values quantity × price but does not define conversion across units.

**Decision:** Item-master UOM is canonical. The current batch engine assumes quantities are already expressed in the item's UOM and does not convert incompatible UOMs. A production-grade release should flag UOM mismatch for buyer clarification rather than aggregate unlike units.

### 9. PO numbering and transactional persistence
**Observation:** The challenge defines consolidation behavior but not numbering, PO lifecycle states, or ERP integration.

**Decision:** Generate deterministic sequential PO IDs per processed batch. The MVP records buyer actions in SQLite audit history; it does not transmit an actual PO to an ERP. Approval/override actions are logged, with written reason mandatory for override.

### 10. Chat assistant scope
**Observation:** Chat intake must cope with vague/unknown items and ask for missing fields. No LLM credentials or external AI endpoint are supplied.

**Decision:** Implement deterministic item-master matching and guided clarification with an explicit confirm-before-create step. Unknown/low-confidence items are not invented. This keeps decisions reproducible and avoids making a hidden model call. AI tools used during development are disclosed in `APPROACH.md`.

### 11. PDF contract extraction
**Observation:** Some binding contracts exist only as signed PDFs and have not been added to the register snapshot.

**Decision:** Parse contract ID, supplier, dates, ceiling and item/rate schedule from signed PDF text. The task kit's RC-2026-031 is extracted and starts at zero consumption. For user-added contracts, the UI asks the buyer to supply the contract metadata and extracts the schedule; buyer review remains necessary before real purchasing.

### 12. Not processed lines
**Observation:** The brief allows `NOT_PROCESSED` where a line should not be processed but does not define examples.

**Decision:** The current MVP does not silently discard valid PR lines. Invalid/missing mandatory fields are surfaced through intake validation; future strict CSV validation should mark invalid lines `NOT_PROCESSED` with a reason rather than infer missing business data.

## Known limitations to explain honestly

- The PDF amendment parser recognizes common numeric threshold language. Arbitrary prose is preserved for review; it is not a general-purpose legal-policy NLP model. Structured policy JSON is the reliable route for new condition types and plant-specific rules.
- The UI's guided chat uses deterministic matching, not a generative LLM.
- Batch simulation does not reserve contract ceilings across separate concurrent batch jobs, and PO approvals do not yet decrement persisted contract balances. Production deployment should wrap evaluation/approval in database transactions.
- A signed contract added through the UI should be checked by a buyer against the source PDF before approval.
