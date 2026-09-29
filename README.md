# Travel AI — Grounded Itinerary Planning System

An AI-assisted travel-planning system that converts a traveler's free-text request into a grounded, priced, day-by-day itinerary using **only** the provided supplier catalog and traveler profile.

The system enforces a fundamental separation of concerns:

> **Generative AI is used for language understanding and contextual reasoning, while deterministic Python logic remains responsible for inventory boundaries, hard constraints, pricing, validation, and grounding.**

The system is designed as an **agent-assist quoting workflow**: it produces mathematically verified quotes, candidate rankings, and human-review flags for human travel operators, rather than executing autonomous, unverified bookings.

---

# 1. Assignment Overview

This implementation solves the end-to-end travel itinerary planning problem under strict grounding constraints:

* **Inventory Retrieval:** Filters relevant supplier inventory from an in-memory catalog based on destination compatibility and party capacity.
* **Traveler Personalization:** Applies traveler profile preferences (e.g., family-friendly, budget, relaxed tempo) and historical past-trip feedback (e.g., avoiding long drives and rushed transfers).
* **Day-by-Day Itinerary Composition:** Synthesizes structured daily schedules respecting pacing constraints, meal/activity balance, and necessary recovery days.
* **Deterministic Pricing:** Recalculates unit prices, line totals, and grand totals directly from verified catalog fields in Python, eliminating financial hallucinations.
* **Catalog ID Grounding:** Cites explicit supplier catalog IDs (`HOT-xxx`, `ACT-xxx`, `TRN-xxx`) for all scheduled items.
* **Unfulfillable Requests:** Detects out-of-scope destinations before invoking external models, exiting gracefully with zero fabricated inventory and Rs. 0 total expenditure.
* **Production Failure Resilience:** Bounded 15-second timeouts, granular error classification, and automatic degradation to deterministic fallbacks for both ranking (JEV) and planning (LLM).
* **Human-in-the-Loop Review:** Flags operational items requiring operator verification (untracked transit logistics, missing physical suitability info, live room availability).
* **Rigorous Automated Evaluation:** 38 unit and integration tests verifying catalog grounding, mathematical integrity, pacing coherence, and API failure modes.

### Supplied Test Requests

1. **REQ-1:** 5-day relaxed family trip in Kerala for a party of 4 (2 adults, 2 children) with a ₹60,000 requested budget, emphasizing nature, local food, and family-friendly pacing.
2. **REQ-2:** 2-day budget-conscious weekend trip in Munnar for a couple (2 adults) interested in hiking and tea estates, with no numeric budget specified.
3. **REQ-3:** 3-day trip in Goa for a solo traveler; functions as a grounding trap because the catalog contains zero Goa inventory.

---

# 2. Key Design Principles

## 2.1 Catalog is the Source of Truth

The in-memory catalog (`data/sample_data.json`) is the sole inventory source. The generative model is strictly prohibited from inventing:
* Accommodations or homestays
* Activities or sightseeing tours
* Transport options or private cab services
* Pricing, discounts, or fee structures
* Supplier catalog IDs

## 2.2 Deterministic Logic Owns Hard Constraints

Language models are probabilistic and struggle with exact arithmetic, combinatorial constraints, and negative boundaries. Python deterministic code owns:
* Catalog ID membership and schema validation
* Room and vehicle capacity verification (`capacity >= party_size`)
* Candidate pool bounding
* Unit price extraction and line total calculations (`unit_price * quantity`)
* Total price summation and budget utilization metrics
* Negative checks (disallowed unverified travel claims, duplicate activities)
* Final post-generation grounding audits

## 2.3 LLMs are Bounded

Generative models never receive raw, unbounded catalog dumps. They operate exclusively on a pre-filtered, role-aware **Candidate Pool** assembled after deterministic retrieval and multi-dimensional ranking. Any item generated outside this bounded pool is rejected by programmatic validators.

## 2.4 Human Approval

Static supplier catalogs lack real-time room availability, live transit timetables, and physical trail conditions. The system outputs an agent-assist itinerary quote marked `human_review_required: true` with context-specific reasons, enabling operators to verify logistics before charging travelers.

---

# 3. Architecture

```text
Traveler Request (Free Text)
       │
       ▼
Request Parsing (LLM extraction with deterministic regex fallback)
       │
       ▼
Deterministic Candidate Retrieval (Data-driven location & capacity filtering)
       │
       ├──── 0 candidates ────► Unfulfillable Response (REQ-3: Early Exit, Rs. 0)
       │
       ▼
Hard Validation (Catalog ID, type schema, capacity, and price field checks)
       │
       ▼
JEV Contextual Ranking (8-dimensional scoring: budget, pace, feedback, etc.)
       │
       ▼
Role-Aware Candidate Pool (Top hotels, activities, and transport bounded for planner)
       │
       ▼
Itinerary Generation (LLM daily scheduling with bounded candidate pool)
       │
       ▼
Deterministic Pricing (Catalog price lookup, line totals, and grand total)
       │
       ▼
Itinerary Validation (Coherence, pacing, duration, and claim checks)
       │
       ├──── validation failure (max 1 retry) ────► Regeneration / Fallback
       │
       ▼
Final Grounding Validation (Strict ID, pool membership, and price math verification)
       │
       ▼
Human Review Required (Dynamic operational review justifications)
```

### Stage-by-Stage Breakdown

#### 1. Request Parsing (`src/planner.py:parse_request`)
* **What it does:** Extracts structured intent (`destination`, `num_days`, `budget`, `budget_level`, `party_adults`, `party_children`, `interests`, `pace`) from free-text traveler input using xAI Grok (primary) or Google Gemini (fallback).
* **Why it exists:** Provides strongly typed input to downstream retrieval and validation filters.
* **Responsibility:** Normalizing traveler intent. If LLM calls time out or fail across both providers, falls back to deterministic regex pattern matching.
* **What it does NOT do:** Does not select inventory or make pricing assumptions.

#### 2. Deterministic Candidate Retrieval (`src/retriever.py:retrieve_candidates`)
* **What it does:** Filters catalog suppliers matching destination compatibility and party capacity (`capacity >= party_size`).
* **Why it exists:** Prunes unusable candidates early so LLM context and ranking models are not wasted on irrelevant inventory.
* **Responsibility:** Data-driven location compatibility (local hubs, encompassing regions) and hard capacity boundaries.
* **What it does NOT do:** Does not rank candidates or make value judgments about relevance.

#### 3. Hard Validation (`src/validator.py:validate_candidate_pool`)
* **What it does:** Validates candidate IDs against the catalog, confirms valid types (`hotel`, `activity`, `transport`), and ensures required price fields exist.
* **Why it exists:** Guarantees that corrupted or incomplete catalog records never enter the ranking or generation layers.
* **Responsibility:** Schema and data integrity enforcement.
* **What it does NOT do:** Does not modify supplier records.

#### 4. JEV Contextual Ranking (`src/jev.py:JevRanker`)
* **What it does:** Evaluates valid candidates across 8 scoring dimensions (budget fit, destination fit, interest fit, traveler preference fit, past-trip feedback fit, pace fit, party suitability, quality).
* **Why it exists:** Solves multi-criteria candidate ranking using structured scoring.
* **Responsibility:** Multi-dimensional relevance scoring. If the external JEV API times out, rate limits, or fails, gracefully executes `_deterministic_fallback`.
* **What it does NOT do:** Does not build itineraries or purchase items.

#### 5. Role-Aware Candidate Pool (`src/models.py:CandidatePool`)
* **What it does:** Partitions top-ranked candidates by role (top hotels, top activities, top transport) and exposes explicit `planner_candidate_ids`.
* **Why it exists:** Restricts the planner's action space to the highest-scoring relevant items, preventing arbitrary catalog selection.
* **Responsibility:** Context bounding.
* **What it does NOT do:** Does not force every candidate in the pool to be scheduled.

#### 6. Itinerary Generation (`src/planner.py:generate_itinerary`)
* **What it does:** Schedules pool candidates across trip days, balancing daily pace, meal times, and recovery days using xAI Grok (primary) or Google Gemini (fallback).
* **Why it exists:** Leverages generative AI for contextual sequencing, narrative summaries, and sensible day-to-day composition.
* **Responsibility:** Daily item arrangement and narrative justification. If both providers fail, invokes `_deterministic_generate_itinerary`.
* **What it does NOT do:** Does not calculate final prices and cannot introduce candidate IDs outside the pool.

#### 7. Deterministic Pricing Engine (`src/pricing.py:calculate_price`)
* **What it does:** Overwrites unit prices from verified catalog supplier records, calculates line totals (`unit_price * quantity`), sums grand totals, and computes budget utilization metrics.
* **Why it exists:** LLMs are unreliable financial calculators. Pricing must be 100% reproducible and audit-compliant.
* **Responsibility:** Exact financial arithmetic and budget summary formatting.
* **What it does NOT do:** Does not invent discounts or change item quantities.

#### 8. Itinerary Validation (`src/validator.py:validate_itinerary_coherence`)
* **What it does:** Checks multi-day hotel continuity (N-1 nights), party capacity, daily activity limits (max 7 hours, max 1-2 activities for relaxed pace), destination coherence, past-feedback compliance, and unsupported claim phrasing.
* **Why it exists:** Catches logical conflicts before quotes reach human operators. Allows at most one regeneration attempt with specific error feedback before falling back to deterministic synthesis.
* **Responsibility:** Logical consistency and itinerary coherence.
* **What it does NOT do:** Does not alter itinerary data directly.

#### 9. Final Grounding Validation (`src/validator.py:validate_itinerary`)
* **What it does:** Programmatically verifies that all item IDs exist in the catalog, match candidate pool IDs, have matching types, and equal exact line item arithmetic.
* **Why it exists:** Final safeguard ensuring zero ungrounded items or math discrepancies reach production outputs.
* **Responsibility:** Hard grounding certification (`grounding_status: "valid"`).
* **What it does NOT do:** Does not perform subjective style grading.

#### 10. Human Review Layer (`src/validator.py:generate_human_review_reasons`)
* **What it does:** Generates dynamic, context-specific justifications explaining why human operators must review the itinerary before commercial booking.
* **Why it exists:** Explicitly declares unknown operational variables (untracked inter-city travel times, missing physical difficulty guidelines, live room availability).
* **Responsibility:** Operational safety and transparency.
* **What it does NOT do:** Does not block the generation of the initial quote.

---

# 4. Component Responsibilities

| Component | Source File | Core Responsibility | Why It Exists |
| :--- | :--- | :--- | :--- |
| **Request Parser** | `src/planner.py` | Extracts structured travel intent from free text | Normalizes unstructured text into validated typed schemas |
| **Retriever** | `src/retriever.py` | Filters catalog by destination & party capacity | Eliminates incompatible items early before model scoring |
| **Hard Validator** | `src/validator.py` | Validates catalog IDs, schemas, and price fields | Prevents malformed or incomplete inventory from entering pipeline |
| **JEV Ranker** | `src/jev.py` | Computes 8-dimensional candidate relevance scores | Separates multi-attribute candidate ranking from itinerary composition |
| **Candidate Pool** | `src/models.py` | Assembles role-aware candidate subsets for planner | Bounds generative context and prevents arbitrary item selection |
| **Itinerary Planner** | `src/planner.py` | Schedules pool items into coherent daily plans | Uses LLM contextual reasoning for daily flow and balance |
| **Pricing Engine** | `src/pricing.py` | Calculates unit prices, line totals, and budget metrics | Keeps financial mathematics 100% deterministic and grounded |
| **Itinerary Validator**| `src/validator.py` | Checks hotel nights, pace, daily duration, claims | Enforces logical consistency, pace limits, and feedback rules |
| **Final Validator** | `src/validator.py` | Verifies catalog ID existence, pool containment, math | Acts as the final programmatic barrier against hallucinations |
| **Human Review** | `src/validator.py` | Compiles dynamic operational review justifications | Bridges catalog limitations (live availability, transit schedules) |

---

# 5. Retrieval Strategy

The candidate retrieval pipeline executes deterministically before candidate ranking or LLM generation:

```text
Request Text ──► Request Parsing ──► Data-Driven Location Filter ──► Capacity Filter ──► Validated Candidate Pool
```

### 1. Data-Driven Destination Compatibility
Location matching in `src/validator.py:is_compatible_location` is derived dynamically from catalog data:
* **Local Hub Matching:** When a request targets a specific base (e.g., `"Munnar"`), it matches candidates where `location == "Munnar"`.
* **Regional Destination Matching:** When a request targets a broader region (e.g., `"Kerala"`), the system checks catalog descriptions and locations to encompass all hubs situated in that state (e.g., Alleppey, Munnar, Kochi).
* **Regional Transport Ingestion:** Transport options tagged at the regional level (`location: "Kerala"`) remain available to service local hubs.
* **Unrepresented Destinations:** If a destination does not appear anywhere in catalog locations or descriptions (e.g., `"Goa"` in REQ-3), retrieval immediately returns zero candidates.

> **Note on Distances:** The catalog does not provide geographic coordinates or physical travel times. The retriever does not invent travel distances. Inter-city movement is handled via hub coherence rules and flagged for human operator review.

### 2. Party Capacity Filtering
If a supplier defines an explicit `capacity` field (e.g., private cars with capacity 4, homestays with capacity 4), retrieval strictly verifies:
`supplier.capacity >= (party_adults + party_children)`
Under-capacity accommodations or vehicles are dropped immediately.

### 3. Role-Aware Candidate Pool
Retrieved candidates are partitioned into structural roles:
* `hotels`: Top-ranked accommodations matching party size.
* `activities`: Top-ranked activities matching requested interests.
* `transport`: Top-ranked transport options matching party size.

### Why Retrieval Happens Before JEV
Retrieval executes before JEV because evaluating candidates through an external ranking model consumes latency and API quota. Discarding invalid candidates (wrong region, insufficient vehicle capacity) via deterministic Python rules ensures JEV only scores viable inventory.

---

# 6. JEV Decision Layer

The **Jev Model** evaluates pre-filtered candidates across eight standardized scoring dimensions:

1. **Budget Fit:** Evaluates item pricing against request budget signals. In budget-conscious requests without numeric caps, lower-cost items are scored higher.
2. **Destination Fit:** Evaluates proximity and alignment with the primary stay location.
3. **Interest Fit:** Matches requested terms (e.g., hiking, tea estates, local food) against candidate titles, tags, and descriptions.
4. **Traveler Preference Fit:** Incorporates persistent traveler profile preferences (e.g., family-friendly, backwaters).
5. **Past-Trip Feedback Fit:** Rewards items aligning with past satisfaction (e.g., unhurried stays) and penalizes conflicts (e.g., excessive driving).
6. **Pace Fit:** Measures activity duration and intensity against the requested tempo.
7. **Party Suitability:** Contextual fit for traveler composition (e.g., family with children vs. couple).
8. **Quality:** Incorporates catalog review ratings to break ties between otherwise comparable items.

### Architectural Separation: JEV vs. Planner

```text
JEV Decision:
"Which validated candidates are most relevant to this traveler?"

Planner Decision:
"Which of those candidates should actually be scheduled into the daily itinerary?"
```

A high JEV score does **not** make an item mandatory:
* In **REQ-2** (a 2-day budget Munnar trip for a couple), private sedan transport (`TRN-001`) receives a respectable JEV score due to driver amenities and vehicle quality.
* However, the **Planner excludes `TRN-001`** from the final itinerary because both scheduled activities (`ACT-002` Tea Estate Walk and `ACT-003` Eravikulam Trek) are situated directly in Munnar, and the travelers requested a budget-conscious trip.

This separation prevents high relevance scores from driving unnecessary purchases.

---

# 7. Grounding and Hallucination Prevention

```text
Catalog (`data/sample_data.json`)
   ↓
Validated Candidates (Deterministic schema & capacity validation)
   ↓
Bounded Planner Input (Explicit role-aware Candidate Pool)
   ↓
Generated Itinerary (Bounded LLM synthesis)
   ↓
Deterministic Validation (Programmatic ID, math, and claim audit)
   ↓
Final Quote (Verified quote with human review justifications)
```

### Core Protection Mechanisms

* **Catalog Authority:** The system only accepts items present in `sample_data.json`.
* **Catalog ID Validation:** Every line item in the plan must resolve to an exact key in the catalog suppliers table. Fabricated IDs (e.g., `HOT-999`) trigger immediate rejection.
* **Candidate Pool Containment:** The planner is explicitly constrained to `planner_candidate_ids`. Items from other regions or unranked inventory are rejected by `validate_grounding`.
* **Deterministic Pricing:** Model-generated prices are disregarded. The pricing engine overwrites unit prices directly from catalog records.
* **Unsupported Claim Validation:** Reasons are scanned for unverified travel time or logistical claims using strict regex patterns (`DISALLOWED_TRAVEL_TIME_PHRASES` in `src/validator.py`):
  * `"30 minutes away"`
  * `"2 hours drive"`
  * `"short drive"` / `"long drive"`
  * `"far away"`
  * `"minimizes travel time"`
  * `"without public transit fatigue"`
  * `"zero long-distance travel"`
* **Zero-Inventory Early Exit (REQ-3):**

```text
REQ-3 (Goa Request)
   ↓
0 Compatible Catalog Candidates
   ↓
Halt Execution (Do not call JEV or LLM Planner)
   ↓
Return Unfulfillable Response (0 items, Rs. 0 total expenditure)
```

The system never prompts an LLM to "suggest alternatives" when inventory does not exist.

---

# 8. Pricing

Final financial calculations are performed purely in Python by `src/pricing.py:calculate_price`:

### Pricing Formulas

```text
Hotel line total:
price_per_night × number_of_nights

Activity line total:
price_per_person × party_size

Transport line total:
price_per_day × number_of_days  (or price_flat)

Final itinerary total:
sum(all validated line totals)
```

### Budget Status Handling

* **Numeric Budget Provided (e.g., REQ-1 ₹60,000):**
  * `planned_total`: Exact sum of line totals (e.g., ₹31,400).
  * `remaining_budget`: `requested_budget - planned_total` (e.g., ₹28,600).
  * `budget_utilization`: `planned_total / requested_budget` (e.g., 0.523 or 52.3%).
  * `budget_status`: `'within_budget'`, `'under_budget'`, or `'over_budget'`.
  * `budget_note`: Explains unused budget without artificial spending.
* **Non-Numeric Budget Request (e.g., REQ-2 "budget-conscious"):**
  * `requested_budget`: `null`
  * `remaining_budget`: `null`
  * `budget_utilization`: `null`
  * `budget_status`: `'no_numeric_budget'`
  * `budget_note`: Dynamically formatted explanation (e.g., *"The request indicates a budget-conscious preference but does not specify a numeric budget. The planned itinerary costs ₹6,100."*).

The LLM never defines final pricing.

---

# 9. Itinerary Validation

Post-generation validation in `src/validator.py` enforces domain invariants across the full itinerary:

1. **Catalog ID & Pool Grounding:** Every item exists in the catalog and belongs to the JEV candidate pool.
2. **Hotel Continuity & Capacity:** Verifies that hotel nights match trip duration (N - 1 nights for an N-day trip) and room capacity accommodates the full party.
3. **Destination Coherence:** Activities scheduled outside the hotel base require dedicated transport on that day; activities spanning multiple disjoint non-hotel hubs are flagged.
4. **Relaxed Pace & Duration Caps:** Daily activity duration cannot exceed 7 hours. Relaxed pace itineraries permit at most 1–2 activities per day.
5. **Past Feedback Compliance:** Enforces historical traveler preferences (e.g., minimizing excessive city switching when past feedback notes "kids got bored on long drives").
6. **No Duplicate Activities:** Rejects plans that duplicate activity catalog IDs across days.
7. **Preference Coverage Audit:** Verifies that requested traveler interests are represented in booked item tags.
8. **Unsupported Claim Audit:** Rejects unverified travel-time and distance assertions.

### Bounded Regeneration Loop
If validation detects recoverable errors (e.g., pace violation, missing hotel night), the pipeline permits **at most one regeneration attempt** (`regeneration_count < 1`), feeding specific validation errors back into the prompt. If validation fails again, the system immediately invokes `_deterministic_generate_itinerary`, avoiding runaway token loops.

---

# 10. Production Failure Handling

```text
External AI Request (Gemini / Grok / JEV)
        │
        ├──── Success (≤ 15.0s) ────► Normal Pipeline
        │
        └──── Failure / Timeout ────► Grounded Deterministic Fallback
                                                │
                                                ▼
                                    Deterministic Validation & Pricing
                                                │
                                                ▼
                                          Verified Quote
```

### Implemented Failure Protections

* **Bounded Request Timeouts:** External HTTP calls to Gemini, Grok, and JEV enforce an explicit **15.0-second timeout** (`types.HttpOptions(timeout=15000)` and `httpx.post(..., timeout=15.0)`).
* **Granular Error Classification:** Exceptions are caught and mapped into standardized error types:
  * `*_timeout`: Upstream connection or read timeout exceeding 15 seconds.
  * `*_rate_limited`: HTTP 429 or `ResourceExhausted` quota errors.
  * `*_service_error`: HTTP 5xx errors, connection resets, or 402 payment requirements.
  * `*_invalid_response`: Non-JSON, truncated, or schema-violating response payloads.
* **Multi-Provider LLM Failover (`src/llm.py:LLMClient`):** The system implements a resilient tiered model architecture:
  1. **Primary LLM:** Calls xAI Grok (`grok-beta`) or Groq (`llama-3.3-70b-versatile` if a `gsk_` key is supplied) for fast, structured generation.
  2. **Secondary LLM Fallback:** If Grok encounters a timeout, HTTP 429 rate limit, 5xx service outage, or authentication issue, it automatically catches the exception and fails over to Google Gemini (`gemini-2.5-flash`).
  3. **Deterministic Planner Fallback:** If all external model providers are unreachable or return invalid schemas, the pipeline cleanly invokes `_deterministic_generate_itinerary`.
* **Grounded JEV Fallback (`src/jev.py:_deterministic_fallback`):** When JEV fails, candidates are ranked using the identical 8 dimensions via deterministic Python scoring. Diagnostics record:
  ```json
  "jev_used": false,
  "ranking_source": "deterministic_fallback",
  "fallback_used": true,
  "jev_failure_reason": "<error_type>: <details>"
  ```
* **Grounded Planner Fallback (`src/planner.py:_deterministic_generate_itinerary`):** When the LLM planner fails or times out, the system deterministically allocates top-ranked candidates across days, sets `planner_fallback_used: true`, and records `planner_fallback_reason`.
* **Missing Catalog Information:** Items missing mandatory pricing or type fields are excluded during candidate validation rather than assigned guessed values.

---

# 11. Evaluation and Testing

The repository features an automated test harness of **38 tests** across three test modules:

```text
tests/
├── test_failure_handling.py  # 6 tests: API timeouts, rate limits, 5xx errors, schema failures
├── test_jev.py               # 15 tests: JEV ranking logic, fallback, scoring dimensions, diagnostics
└── test_validator.py         # 17 tests: Catalog grounding, math integrity, coherence, unfulfillable
```

### Running the Test Suite

```bash
pytest -q
```

Expected output:
```text
......................................                                   [100%]
38 passed in 34.78s
```

### Key Test Categories

* **API Failure Resilience (`tests/test_failure_handling.py`):**
  * `test_llm_timeout_triggers_grounded_fallback`: Validates 15s timeout classification and deterministic fallback activation.
  * `test_llm_rate_limit_triggers_grounded_fallback`: Simulates HTTP 429 rate limits.
  * `test_llm_malformed_response_triggers_grounded_fallback`: Simulates non-JSON responses.
  * `test_llm_service_error_triggers_grounded_fallback`: Simulates 503 service downtime.
  * `test_jev_timeout_and_rate_limit_diagnostics`: Validates JEV diagnostics tracking.
  * `test_end_to_end_graph_with_llm_failure`: Validates full graph execution under LLM failure.
* **JEV Ranking & Diagnostics (`tests/test_jev.py`):**
  * `test_1_no_catalog_ids_hardcoded_into_ranking_logic`: Confirms zero hardcoded supplier IDs.
  * `test_3_budget_sensitivity_without_fixed_40000_threshold`: Validates dynamic budget scaling.
  * `test_6_unknown_jev_ids_rejected`: Ensures unknown JEV IDs are discarded.
  * `test_7_jev_failure_triggers_generic_fallback`: Validates deterministic fallback execution.
  * `test_10_req3_no_candidates_does_not_call_jev`: Confirms zero-candidate early exit.
  * `test_11_role_aware_candidate_pool_selection`: Confirms role partitioning.
* **Grounding & Validation Invariants (`tests/test_validator.py`):**
  * `test_fabricated_id`: Rejects fabricated catalog IDs (`UNKNOWN_CATALOG_ID`).
  * `test_candidate_not_in_pool_rejection`: Enforces candidate pool boundaries.
  * `test_price_mismatch` & `test_total_mismatch`: Enforces exact catalog pricing.
  * `test_unsupported_factual_claim_rejection`: Rejects unverified travel times and distances.
  * `test_itinerary_relaxed_pace_violation`: Catches over-scheduled itineraries.
  * `test_itinerary_past_feedback_conflict`: Flags excessive travel conflicting with past feedback.
  * `test_req3_halts_before_jev`: Verifies REQ-3 halts with Rs. 0 total.

---

# 12. Test Scenarios

## REQ-1: 5-Day Relaxed Family Trip in Kerala
* **Input:** Party of 4 (2 adults, 2 kids), 5 days, ₹60,000 requested budget, nature, local food, relaxed pace.
* **Result:** Planned total **₹31,400** (under budget by ₹28,600; 52.3% utilization).
* **Structure:**
  * Day 1: Check-in at Backwater Breeze Homestay (`HOT-001`, 4 nights, ₹12,800).
  * Day 2: Alleppey Houseboat Day Cruise (`ACT-001`, ₹8,800).
  * Day 3: Unstructured recovery day (intentional leisure, ₹0).
  * Day 4: Fort Kochi Food Walk (`ACT-004`, ₹4,800) + Kathakali Performance (`ACT-005`, ₹2,000) + Private Cab (`TRN-001`, ₹3,000) for inter-city travel.
  * Day 5: Leisurely breakfast and departure.
* **Human Review:** Flagged for inter-location travel logistics (Alleppey to Kochi) and live room availability.
* **Output File:** [`outputs/REQ-1.json`](outputs/REQ-1.json) | JEV: [`outputs/REQ-1_jev_decision.json`](outputs/REQ-1_jev_decision.json)

## REQ-2: 2-Day Budget Munnar Trip for Couple
* **Input:** Party of 2 adults, 2 days, budget-conscious, hiking, tea estates, no numeric budget specified.
* **Result:** Planned total **₹6,100**; `budget_status: "no_numeric_budget"`.
* **Structure:**
  * Day 1: Munnar Hikers Hostel (`HOT-004`, 1 night, ₹1,500) + Munnar Tea Estate Walk & Tasting (`ACT-002`, ₹1,800).
  * Day 2: Eravikulam National Park Trek (`ACT-003`, ₹2,800).
  * Transport: Private cab excluded because all activities are co-located in Munnar.
* **Human Review:** Flagged for physical suitability confirmation on `ACT-003` (moderate difficulty trek lacking age/fitness details in catalog) and live availability.
* **Output File:** [`outputs/REQ-2.json`](outputs/REQ-2.json) | JEV: [`outputs/REQ-2_jev_decision.json`](outputs/REQ-2_jev_decision.json)

## REQ-3: 3-Day Goa Request (Grounding Trap)
* **Input:** Solo traveler, 3 days, Goa.
* **Result:** `status: "unfulfillable"`, **0 items booked**, **₹0 total expenditure**.
* **Behavior:** Retrieval finds 0 candidates. Graph halts before JEV ranking and LLM planning, preventing hallucinated inventory.
* **Output File:** [`outputs/REQ-3.json`](outputs/REQ-3.json)

---

# 13. Sample Outputs

| Request ID | Scenario | Status | Total Price | Key Outputs |
| :--- | :--- | :--- | :--- | :--- |
| **REQ-1** | 5-Day Kerala Family Trip | `success` | ₹31,400 | [`outputs/REQ-1.json`](outputs/REQ-1.json) / [`REQ-1_jev_decision.json`](outputs/REQ-1_jev_decision.json) |
| **REQ-2** | 2-Day Munnar Couple Trip | `success` | ₹6,100 | [`outputs/REQ-2.json`](outputs/REQ-2.json) / [`REQ-2_jev_decision.json`](outputs/REQ-2_jev_decision.json) |
| **REQ-3** | 3-Day Goa Request | `unfulfillable` | ₹0 | [`outputs/REQ-3.json`](outputs/REQ-3.json) |

---

# 14. Setup

### Requirements
* Python 3.10+ (tested on **Python 3.13.1**)
* Git

### 1. Clone the Repository
```bash
git clone git@github-personal:gitmanhimanshu/DMAGAssignment.git
# or via HTTPS:
git clone https://github.com/gitmanhimanshu/DMAGAssignment.git

cd DMAGAssignment/travel-ai-assignment
```

### 2. Create and Activate Virtual Environment
```bash
# Windows (PowerShell)
python -m venv env
.\env\Scripts\activate

# Linux / macOS
python3 -m venv env
source env/bin/activate
```

### 3. Install Dependencies
```bash
pip install -r requirements.txt
```

### 4. Configure Environment Variables
Copy `.env.example` to `.env`:
```bash
cp .env.example .env
```
Populate `.env` with your API keys:
```env
GROK_API_KEY=your_grok_or_groq_api_key_here
GEMINI_API_KEY=your_gemini_api_key_here
JEVMODEL_API_KEY=your_jev_api_key_here
```

* `GROK_API_KEY`: Primary LLM provider (supports xAI `grok-beta`, or Groq keys starting with `gsk_` for `llama-3.3-70b-versatile`; also accepts `XAI_API_KEY` or `GROK`).
* `GEMINI_API_KEY`: Secondary LLM fallback (uses Google `gemini-2.5-flash`).
* `JEVMODEL_API_KEY`: Bounded candidate ranking decision layer.
* **Note on Free Providers & Model Switching:** The current setup uses a free/low-cost provider configuration (Groq / xAI Grok free tier) to make evaluation zero-cost. Evaluators or production teams can easily change models and API keys to Anthropic Claude (`claude-3-5-sonnet`), OpenAI GPT (`gpt-4o`), or other frontier providers as needed without modifying grounding or validation logic.

> **Offline / Resilient Execution:** If API keys are omitted or quotas are exhausted, the system automatically runs using its built-in grounded deterministic fallbacks (`_deterministic_fallback` and `_deterministic_generate_itinerary`). All grounding, pricing, and validation rules remain 100% active.

---

# 15. Running the Application

### Running Test Requests via CLI
Execute any of the test requests:
```bash
python -m src.main --request REQ-1
python -m src.main --request REQ-2
python -m src.main --request REQ-3
```

### CLI Output Details
The terminal displays:
1. **JEV Contextual Decision Layer:** Evaluated candidate scores across all 8 dimensions, role-aware candidate pool, and diagnostic flags (`jev_used`, `ranking_source`, `fallback_used`).
2. **Final Generated Itinerary Plan:** Status, total price, grounding status, human review reasons, budget optimization summary, preference coverage audit, and day-by-day item breakdowns with catalog IDs and line totals.
3. **Artifact Generation:** Updates output JSON files in the `outputs/` directory.

### Running the Optional REST API
To launch the FastAPI server:
```bash
uvicorn src.api:app --reload
```
API endpoint: `POST http://localhost:8000/plan`
```json
{
  "request_id": "REQ-1",
  "text": "Plan a 5-day relaxed family trip in Kerala for 2 adults and 2 kids..."
}
```

---

# 16. Running the Tests

Execute the full pytest suite:
```bash
pytest -q
```
For verbose output showing individual test names:
```bash
pytest -v
```

All 38 tests run synchronously in ~35–45 seconds with zero external network dependencies required during mocked failure testing.

---

# 17. Project Structure

```text
travel-ai-assignment/
├── data/
│   └── sample_data.json         # Master supplier catalog, traveler profile, and test requests
├── outputs/
│   ├── REQ-1.json               # Generated itinerary quote for REQ-1
│   ├── REQ-1_jev_decision.json  # JEV candidate rankings and diagnostics for REQ-1
│   ├── REQ-2.json               # Generated itinerary quote for REQ-2
│   ├── REQ-2_jev_decision.json  # JEV candidate rankings and diagnostics for REQ-2
│   └── REQ-3.json               # Unfulfillable response for REQ-3
├── src/
│   ├── __init__.py
│   ├── api.py                   # FastAPI service exposing POST /plan
│   ├── graph.py                 # LangGraph state graph & conditional routing
│   ├── graph_state.py           # TypedDict schema for pipeline state
│   ├── jev.py                   # JEV multi-dimensional ranking client & deterministic fallback
│   ├── llm.py                   # Gemini/Grok API client with 15s timeout & error classification
│   ├── loader.py                # Environment loader and catalog JSON ingestion
│   ├── main.py                  # CLI entrypoint for running test requests
│   ├── models.py                # Pydantic models for plans, items, and JEV diagnostics
│   ├── planner.py               # Request parsing, prompt synthesis, and deterministic planner fallback
│   ├── pricing.py               # Deterministic pricing engine, line items, and budget metrics
│   ├── retriever.py             # Data-driven inventory retrieval and capacity filtering
│   └── validator.py             # Catalog ID validation, coherence checks, pricing verification
├── tests/
│   ├── test_failure_handling.py # 6 tests: API timeouts, rate limits, 5xx errors, fallback execution
│   ├── test_jev.py              # 15 tests: JEV ranking logic, fallback, scoring dimensions
│   └── test_validator.py        # 17 tests: Catalog grounding, math integrity, coherence, unfulfillable
├── ui/                          # Lightweight web UI (index.html, app.js, style.css)
├── .env.example                 # Example API key configuration
├── .gitignore                   # Standard Git ignore file for Python environments, caches, secrets
├── generate_mock_outputs.py     # Local mock output generator utility
├── PART_B_WRITEUP.md            # Technical design and architectural decision document
├── requirements.txt             # Python dependencies
└── README.md                    # Project documentation
```

---

# 18. Configuration and API Usage

| External Service | Environment Variable | Usage in Pipeline | Failure Behavior |
| :--- | :--- | :--- | :--- |
| **xAI Grok / Groq** | `GROK_API_KEY` (or `GROK`, `XAI_API_KEY`) | **Primary LLM:** Request parsing & structured itinerary generation | Automatically catches errors and fails over to Google Gemini |
| **Google Gemini** | `GEMINI_API_KEY` | **Secondary LLM Fallback:** Request parsing & itinerary generation | Degrades to regex parser & `_deterministic_generate_itinerary` |
| **Jev Model** | `JEVMODEL_API_KEY` | Multi-dimensional candidate evaluation | Degrades to `_deterministic_fallback` with full diagnostics |

All external dependencies are bounded by 15-second request timeouts and granular error classifiers. The system runs fully offline when API keys are absent or invalid.

---

# 19. Human-in-the-Loop Operations

The system operates as an **agent-assist quoting tool**, deliberately declining autonomous booking authority:
* **Static Catalog Availability:** The catalog does not provide real-time room or seat inventory. Operators must confirm live supplier availability prior to charging travelers.
* **Transit Feasibility:** When an itinerary spans multiple locations (e.g., Alleppey and Kochi in REQ-1), the system flags review because catalog records omit live transit schedules and road conditions.
* **Activity Suitability:** When activities feature physical difficulty without explicit age/fitness guidance (e.g., `ACT-003` Eravikulam Trek in REQ-2), review is flagged to ensure traveler safety.

---

# 20. Known Limitations

* **Static In-Memory Catalog:** Inventory is loaded from a local JSON file (`data/sample_data.json`) rather than an active reservation database.
* **Absence of Real-Time Availability:** The catalog cannot indicate sold-out dates or seasonal closures.
* **No Live Routing Engine:** Transit feasibility relies on destination matching rather than real-time GPS routing or distance matrix APIs.
* **No Direct Booking Execution:** The pipeline outputs priced quotes and recommendations; booking confirmation requires human operator intervention.

---

# 21. Future Improvements

As detailed in [`PART_B_WRITEUP.md`](PART_B_WRITEUP.md), three targeted enhancements would provide the highest leverage for production deployment:

1. **Hybrid Semantic & Attribute Retrieval:**
   * *Current limitation:* In-memory attribute filtering does not scale smoothly as catalog volume expands.
   * *Proposed improvement:* Combine dense vector embeddings with lexical BM25 retrieval and structured metadata filtering.
   * *Practical benefit:* Maintains high retrieval relevance across tens of thousands of dynamic catalog items without overflowing context windows.
2. **Automated Evaluation & Regression Framework:**
   * *Current limitation:* Rule-based tests catch grounding and math violations but do not systematically grade stylistic recommendation quality.
   * *Proposed improvement:* Deploy an offline evaluation pipeline that assesses generated itineraries against simulated traveler personas using rubric-based LLM judges.
   * *Practical benefit:* Detects behavioral regressions in preference alignment and itinerary diversity whenever models, prompts, or scoring weights change.
3. **Live Availability and Routing API Integration:**
   * *Current limitation:* The static catalog lacks real-time inventory and physical transit durations.
   * *Proposed improvement:* Connect the retrieval and validation layers to live Global Distribution Systems (GDS) and map routing APIs.
   * *Practical benefit:* Enables deterministic validation of physical travel times and real-time inventory availability before human review.

---

# 22. Security and Secrets

* **Zero Hardcoded Secrets:** No API keys, credentials, or proprietary tokens are committed in source files.
* **Environment Configuration:** Sensitive credentials are read exclusively via environment variables (`.env`).
* **Git Exclusions:** The `.gitignore` file explicitly ignores `.env`, virtual environment directories (`env/`, `.venv/`), bytecode caches (`__pycache__/`, `*.pyc`), and test caches (`.pytest_cache/`).
* **Safe Placeholders:** `.env.example` contains only generic template values.

---

# 23. Submission Checklist

- [x] Grounded only in supplier catalog (`data/sample_data.json`)
- [x] Traveler profile and past feedback used
- [x] Day-by-day itinerary generation
- [x] Deterministic pricing and exact arithmetic
- [x] Supplier IDs on all recommendations
- [x] REQ-3 graceful unfulfillable handling (0 items, Rs. 0 total)
- [x] JEV contextual ranking (8 scoring dimensions)
- [x] Deterministic JEV fallback on error/timeout/quota exhaustion
- [x] Deterministic LLM/planner fallback on error/timeout/schema failure
- [x] Post-generation validation & bounded regeneration (max 1 retry)
- [x] Human review required with dynamic, context-specific justifications
- [x] 38 automated tests passing across 3 test modules
- [x] Verified output JSON artifacts for all three requests (`outputs/REQ-1.json`, `outputs/REQ-2.json`, `outputs/REQ-3.json`)
- [x] Polished Part B technical write-up (`PART_B_WRITEUP.md`)
