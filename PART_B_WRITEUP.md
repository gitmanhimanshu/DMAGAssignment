# Travel AI Architecture and System Design

## 1. Architecture

The central design philosophy of this system is that generative models should interpret natural language and synthesize recommendations, while deterministic software must govern commercial truth, inventory boundaries, and financial calculations. In a travel booking environment, hallucinated inventory, incorrect capacities, and arithmetic errors carry direct commercial and operational liabilities.

To enforce these boundaries, the system is organized as a staged LangGraph pipeline where responsibilities are cleanly isolated:

```text
User Request
  │
  ▼
Request Parsing (Extraction & Normalization)
  │
  ▼
Deterministic Candidate Retrieval & Filtering (Location & Capacity)
  │  ──[ 0 Candidates ]──► Early Termination (Unfulfillable Response)
  ▼
Hard Validation (Catalog Schema & Integrity Check)
  │
  ▼
JEV Contextual Decision Layer (Multi-Dimensional Scoring)
  │
  ▼
Candidate Pool Selection (Role-Aware Inventory Subset)
  │
  ▼
Itinerary Generation & Day-by-Day Scheduling
  │
  ▼
Itinerary Validation (Pace, Coherence, Duration, & Feedback)
  │  ──[ Validation Failure ]──► Bounded Regeneration (Max 1 Loop)
  ▼
Deterministic Pricing Calculation (Catalog Math & Budget Status)
  │
  ▼
Final Grounding Validation & Human-Review Flagging
```

Each stage serves a specific purpose with clear operational boundaries:

* **Request Parsing:** Normalizes unstructured traveler requests into structured attributes (destination, party size, target budget, duration, and interests). This extracts semantic intent into a typed contract before any filtering occurs.
* **Deterministic Retrieval & Filtering:** Filters the supplier catalog by location compatibility and party capacity. This eliminates invalid inventory upfront so downstream reasoning layers only evaluate usable candidates.
* **Hard Validation:** Verifies that retrieved candidates adhere strictly to the catalog schema and contain valid identifiers.
* **JEV Contextual Ranking:** Evaluates filtered candidates across multi-dimensional criteria to measure qualitative fit against traveler preferences and profile history.
* **Candidate Pool Selection:** Curates a compact, role-aware subset (hotels, activities, transport) from top-ranked candidates, preventing downstream prompt bloat.
* **Itinerary Generation:** Uses the generative model solely for logical sequencing and day-by-day scheduling, drawing strictly from the selected candidate pool.
* **Itinerary Validation:** Programmatically checks that generated plans respect requested travel pace, avoid conflicting feedback, and remain logistically coherent.
* **Deterministic Pricing:** Recalculates all line items and itinerary totals directly from catalog prices in Python, bypassing LLM-generated numbers entirely.
* **Final Grounding Validation & Human Review:** Verifies that all items exist in the catalog and flags operational areas that require human confirmation prior to booking.

Location matching is data-driven and destination-agnostic: location compatibility is derived dynamically from the request text and supplier catalog metadata rather than hardcoded regional rules. The system operates on the provided supplier catalog schema regardless of geographic region.

## 2. Grounding and Hallucination Prevention

Grounding is structured as an unbroken chain of trust:

```text
Catalog
  ↓
Validated Candidates
  ↓
Bounded Planner Input
  ↓
Generated Itinerary
  ↓
Deterministic Validation
  ↓
Final Quote
```

Each barrier in this chain addresses a distinct failure mode:

* **Catalog Authority:** The supplier catalog (`sample_data.json`) is the sole inventory source. The LLM has no authority to create suppliers, accommodations, activities, or prices.
* **Candidate ID Validation:** Every item in the generated itinerary must resolve to an active identifier in the catalog. An apparently reasonable name or plausible description is insufficient to establish that inventory exists.
* **Candidate Pool Containment:** The planner is restricted to the pre-selected candidate pool. This prevents the generative model from retrieving out-of-context items or pulling arbitrary catalog entries.
* **Deterministic Pricing:** Monetary calculations are strictly excluded from generative prompts. Unit prices are resolved from catalog records, multiplied by nights or travelers, and summed in Python. This guarantees that arithmetic is exact and reproducible.
* **Unsupported Claim Rejection:** Item justifications must not invent unverified operational facts—such as guaranteed transit times, travel distances, exact operating hours, or fitness criteria—that do not exist in catalog descriptions.
* **Zero-Inventory Early Exit:** When a request targets a destination with no compatible catalog items, the pipeline halts immediately after retrieval. For example, in **REQ-3** (a 3-day request for Goa), retrieval yields zero candidates. Rather than invoking JEV or an LLM to fabricate options, the system immediately returns an unfulfillable status with ₹0 total, demonstrating that retrieval functions as a hard trust boundary.

Together, these mechanisms significantly reduce the risk of hallucinated inventory, fabricated pricing, and unsupported operational claims.

## 3. Retrieval and JEV Decision Making

Deterministic retrieval occurs before JEV ranking because there is no reason to spend model context or ranking effort on inventory that is already invalid for the request. Deterministic retrieval first applies hard filters such as destination/location compatibility and party capacity. This prevents obviously incompatible inventory from reaching the ranking and planning stages and keeps the downstream model focused on candidates that are actually usable.

Filtered candidates then enter the JEV contextual decision layer (`jev.py`), which evaluates each candidate across eight normalized dimensions:

1. **Budget Fit:** Evaluates alignment between candidate price and traveler spending intent. A qualitative descriptor like "budget-conscious" is treated as a ranking signal rather than a rigid cap, whereas explicit numeric budgets are passed to deterministic pricing.
2. **Destination Fit:** Measures compatibility with the requested base destination. This helps the planner prioritize candidates rooted in the primary stay location without assuming unmeasured travel times.
3. **Interest Fit:** Matches explicit request terms (e.g., hiking, tea estates, local food) against candidate titles, tags, and descriptions.
4. **Traveler Preference Fit:** Incorporates broader traveler profile preferences (e.g., family-friendly, nature, backwaters) that persist across trips even when omitted from a specific prompt.
5. **Past-Trip Feedback Fit:** Incorporates historical traveler sentiments (e.g., preference for unhurried stays, aversion to long drives) to personalize recommendations beyond basic keyword matching.
6. **Pace Fit:** Evaluates whether activity duration and intensity fit the traveler's preferred tempo, ensuring relaxed travelers are not scheduled with excessive commitments.
7. **Party Suitability:** JEV provides a contextual party-fit signal using the available party and catalog metadata; hard capacity remains deterministic.
8. **Quality:** Uses catalog-provided review ratings to break ties between otherwise comparable candidates.

### Architectural Separation: JEV vs. Planner

An essential architectural boundary separates ranking from scheduling:

* **JEV answers:** *"Which validated candidates are most relevant to this traveler?"*
* **The Planner answers:** *"Which of those candidates should actually be used to compose the daily itinerary?"*

A high JEV score does not make an item mandatory. In **REQ-2** (a 2-day budget trip in Munnar), a private cab (`TRN-001`) receives a high JEV score because of its capacity and driver amenities. However, the planner excludes it from the final itinerary because the scheduled activities are already based in Munnar and do not require cross-region travel. This separation prevents candidate ranking from turning into unnecessary purchasing.

## 4. Cost and Latency Considerations

Production travel systems must maintain predictable latency and cost profiles. The system optimizes resource usage through several architectural boundaries:

* **Bounded Context Windows:** Sending only the relevant candidate pool reduces prompt size and avoids spending model context on inventory that has already been filtered out.
* **Deterministic Offloading:** Freeform parsing defaults to LLM extraction but immediately falls back to deterministic regex parsing if APIs are unreachable. Pricing arithmetic, capacity checks, and score aggregations run natively in Python, eliminating unnecessary model roundtrips.
* **Structured Outputs:** Generation targets rigid Pydantic schemas, reducing malformed outputs and parsing retries.
* **Bounded Regeneration:** If post-generation validation fails, the pipeline permits at most a single regeneration attempt with specific error feedback before falling back to deterministic synthesis, preventing runaway token loops.

In a scaled production deployment, further optimizations would include caching static catalog embeddings and maintaining standardized routing templates for high-frequency destination pairings.

## 5. Failure Handling

External AI and network services can be slow, rate-limited, unavailable, or return malformed data. The pipeline is designed around bounded execution and safe degradation so that external failures never cause indefinite hangs, unhandled exceptions, or ungrounded hallucinations:

* **Bounded Request Timeouts:** All external network calls to Gemini, Grok, and JEV enforce an explicit 15-second timeout (configured via HTTP client options and `httpx` timeouts). The system never waits indefinitely on an upstream model or ranking service.
* **Granular Error Classification:** API failures are caught and categorized into standardized error types:
  * `*_timeout`: Request exceeded the 15-second boundary (connection or read timeout).
  * `*_rate_limited`: Upstream quota or rate limit exceeded (HTTP 429 or `ResourceExhausted`).
  * `*_service_error`: Upstream service unavailable or transport failure (HTTP 5xx, connection dropped).
  * `*_invalid_response`: Non-JSON, incomplete, or schema-violating payload returned by the model.
* **Grounded JEV Fallback:** If JEV encounters a timeout, rate limit, service failure, or malformed response, it gracefully degrades to `_deterministic_fallback`. This fallback scores candidates across the identical eight dimensions using catalog metadata and traveler attributes, recording transparent diagnostics (`jev_used: false`, `fallback_used: true`, `ranking_source: "deterministic_fallback"`, `jev_failure_reason: "<error_type>: <details>"`).
* **Deterministic Planner Synthesis:** If LLM request parsing or itinerary generation fails due to a timeout, rate limit, service error, or unresolvable validation failure, the pipeline invokes `_deterministic_generate_itinerary`. This fallback deterministically allocates top-ranked candidates across the requested days, sets `planner_fallback_used: true` with the classified `planner_fallback_reason`, and passes directly into deterministic pricing and final grounding validation. No items or prices are ever invented.
* **No Matching Inventory Guard:** When retrieval yields zero candidates (e.g., REQ-3), the pipeline stops immediately before calling JEV or the planner. If no valid catalog items exist for the destination, returning an unfulfillable status immediately is safer and cheaper than invoking generative models.
* **Missing Catalog Information:** When catalog items omit optional pricing or duration data, the system flags the item or rejects it rather than guessing. Missing data is treated as unknown rather than assumed.

## 6. Evaluation Framework

System evaluation must assess more than whether an itinerary was successfully returned. The evaluation suite assesses whether the output is grounded, mathematically sound, resilient to external API failures, and safe for human operators to review.

The automated test harness contains **38 tests** across `tests/test_failure_handling.py`, `tests/test_jev.py`, and `tests/test_validator.py`, validating core system invariants:

* **API Failure Resilience & Fallback Integrity:** Validates that LLM timeouts, HTTP 429 rate limits, malformed JSON responses, and 5xx service errors gracefully trigger deterministic itinerary generation, preserve catalog grounding, and populate failure diagnostics without crashing (`test_llm_timeout_triggers_grounded_fallback`, `test_llm_rate_limit_triggers_grounded_fallback`, `test_llm_malformed_response_triggers_grounded_fallback`, `test_llm_service_error_triggers_grounded_fallback`, `test_jev_timeout_and_rate_limit_diagnostics`, `test_end_to_end_graph_with_llm_failure`).
* **Catalog ID Grounding & Pool Membership:** All generated items must exist in the catalog and originate from the selected candidate pool (`test_fabricated_id`, `test_candidate_not_in_pool_rejection`).
* **Pricing & Mathematical Correctness:** Unit prices must match the catalog, line totals must equal `unit_price * quantity`, and itinerary totals must match the exact sum of line items (`test_price_mismatch`, `test_total_mismatch`, `test_deterministic_calculation_math`).
* **Unsupported Factual Claims:** Item reasons are audited to ensure no unverified travel times, distances, or operational claims are introduced (`test_unsupported_factual_claim_rejection`).
* **Itinerary Pacing & Coherence:** Plans must respect requested pace, insert recovery days after heavy excursions, and honor past-trip feedback (`test_itinerary_relaxed_pace_violation`, `test_itinerary_past_feedback_conflict`).

### Benchmark Scenarios

The test suite exercises three distinct production scenarios:

1. **REQ-1 (5-Day Relaxed Family Trip in Kerala):** Validates multi-day accommodation booking (4 nights for a party of 4), insertion of an unstructured recovery day following a houseboat cruise, use of private transport for the Kochi portion of the itinerary, and budget containment (spending ₹31,400 of a ₹60,000 budget without artificial upselling).
2. **REQ-2 (2-Day Budget Munnar Couple):** Validates non-numeric budget semantics (`budget_status: "no_numeric_budget"`), selection of a budget hostel, exclusion of unnecessary transport, and coverage of both hiking and tea-estate preferences.
3. **REQ-3 (3-Day Goa Request):** Functions as a grounding trap. With zero compatible catalog items, the system terminates before JEV or LLM planning, returning an unfulfillable status with 0 items and ₹0 total.

## 7. Human-in-the-Loop Operations

The system functions as an agent-assist quoting tool, not an autonomous booking agent. It produces grounded recommendations and verified pricing, but final commercial commitments require human approval.

Every generated itinerary returns `human_review_required: true` with dynamic, context-specific justifications:

* **Live Inventory Confirmation:** Static catalog data lacks real-time room availability and seat inventory; operators must confirm live supplier availability prior to payment.
* **Transit & Route Logistics:** In **REQ-1**, the itinerary spans both Alleppey and Kochi. Because the catalog does not provide transit schedules, travel times, or road conditions, human operators must verify inter-city travel feasibility.
* **Activity Suitability Confirmation:** In **REQ-2**, the Eravikulam National Park Trek (`ACT-003`) is cataloged as moderate difficulty, but the catalog omits minimum fitness or age guidelines. Because the catalog does not provide enough suitability information for the system to make a final determination, human review is flagged to confirm that the activity is suitable for the travelers before booking.

## 8. Future Improvements

Three targeted enhancements would deliver the highest leverage in a production deployment:

1. **Hybrid Semantic & Attribute Retrieval:**
   * *Current limitation:* In-memory attribute and token filtering does not scale smoothly as catalog volume expands.
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

## 9. Conclusion

The core architectural thesis of this system is that generative models should be applied where language interpretation and contextual reasoning add value, while deterministic software must remain responsible for supplier truth, hard constraints, pricing, and validation. Bounding the generative layer with deterministic filtering, multi-dimensional JEV ranking, and programmatic post-validation significantly reduces the risk of hallucinated inventory, fabricated pricing, and unsupported claims. This separation makes the system easier to test, straightforward to reason about, and safe to integrate into production booking workflows.
