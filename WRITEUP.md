# Travel AI Architecture and Write-up

## Architecture
The system employs a deterministic-first approach combined with LLMs using LangGraph.
- **Retrieval & Constraints**: Handled in Python via deterministic filtering (`retriever.py`). Only items matching basic constraints (location, capacity) are passed to the next stage.
- **Decision Layer (Jev)**: A bounded decision layer that ranks candidates from the filtered set (`jev.py`).
- **LLM**: Uses `google-genai` for structured generation (Pydantic models). It merely formats the selected valid candidates into a readable day-by-day itinerary.
- **Pricing**: The LLM's pricing is entirely ignored. The system recalculates unit prices and totals directly from the `sample_data.json` catalog using deterministic Python logic (`pricing.py`).
- **Validation**: Ensures every generated catalog ID exists, types match, prices match, and totals sum up correctly.

### Decision Layer — Jev
1. **Deterministic Retrieval First**: We retrieve candidates using deterministic location and capacity checks before passing them to Jev. This limits context size and removes invalid options upfront.
2. **Bounded Candidate Selection**: Jev is designed solely to select and rank valid IDs from the provided candidates based on interests and traveler profile feedback.
3. **No Hallucinated Items**: Since Jev can only output IDs from the provided context, and we explicitly validate them against the `valid_candidates` set, it cannot introduce new catalog items.
4. **Fallback Handling**: If Jev fails (times out, malformed output, hallucinated IDs), we gracefully catch the exception and fall back to a simple deterministic Python scoring function.
5. **Deterministic Pricing**: Pricing is entirely deterministic (calculated from the catalog), bypassing Jev and the generative LLM.
6. **LLM for Synthesis**: The generative LLM is reserved for formatting the itinerary logically with natural language, separating decision-making from synthesis.

## Grounding
Hallucinated inventory and prices are prevented through strict barriers:
1. The LLM is only provided a pre-filtered list of valid JSON candidates. It is explicitly prompted to use ONLY those IDs.
2. The pricing module `pricing.py` overrides any LLM-generated unit prices with the factual data from the catalog.
3. The `validator.py` checks that every ID in the final output exists in the original catalog and that no prices or totals have been tampered with or hallucinated.
4. For REQ-3 (Goa), the deterministic retriever yields 0 results, tripping an early exit condition in LangGraph which bypasses the LLM entirely and returns a safe "unfulfillable" response.

## Cost & Latency
- Uses a fast, low-cost model (`gemini-2.5-flash`).
- Minimal LLM calls: Only 2 calls per request (one for extracting parameters, one for generating the itinerary).
- Retrieval happens before generation, limiting the context window to only relevant candidates.
- The REQ-3 case uses 0 LLM calls for itinerary generation due to the early exit.

## Failure Handling
- LLM failures or malformed outputs (caught by Pydantic parsing) will result in a fast failure rather than passing unvalidated data to the user.
- If the retrieval yields no valid options, the graph immediately returns a graceful failure.

## Evaluation
A deterministic evaluation harness (`tests/test_validator.py`) ensures that:
- Fabricated catalog IDs are caught.
- Price mismatches and total miscalculations are caught.
- Grounding accuracy is maintained by ensuring the plan is flagged if validation fails.

## Human-in-the-loop
The generated plan explicitly includes `human_review_required: true`. The AI only provides a recommendation; it never confirms or books inventory autonomously.

## With more time
1. Add more sophisticated vector-based retrieval for larger catalogs using embeddings.
2. Implement retry mechanisms (with exponential backoff) for transient LLM API errors.
3. Implement a proper UI framework (React/Vue) integrated with a robust backend database instead of a static JSON file.
