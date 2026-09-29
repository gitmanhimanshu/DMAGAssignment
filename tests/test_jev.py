import pytest
from src.jev import JevRanker, get_candidate_price, is_request_budget_sensitive
from src.graph_state import ParsedRequest

def test_1_no_catalog_ids_hardcoded_into_ranking_logic():
    """
    Test 1: Verify no catalog IDs (HOT-*, ACT-*, TRN-*) or request IDs (REQ-*)
    are hardcoded into src/jev.py ranking logic.
    """
    with open("src/jev.py", "r", encoding="utf-8") as f:
        src = f.read()
    
    assert "HOT-001" not in src
    assert "HOT-002" not in src
    assert "HOT-004" not in src
    assert "ACT-001" not in src
    assert "ACT-002" not in src
    assert "ACT-003" not in src
    assert "REQ-1" not in src
    assert "REQ-2" not in src
    assert "REQ-3" not in src

def test_2_adding_new_candidate_affects_ranking():
    """
    Test 2: Adding a completely new candidate (e.g. HOT-999) with matching tags
    affects ranking naturally based on metadata without code changes.
    """
    jev = JevRanker()
    req = ParsedRequest(
        destination="Munnar",
        num_days=2,
        adults=2,
        budget_level="budget",
        interests=["hiking"]
    )
    profile = {"preferences": ["hiking", "nature"]}
    
    # Introduce a new un-hardcoded candidate
    new_candidate = {
        "id": "HOT-999",
        "type": "hotel",
        "name": "Cloud Peak Hiker Haven",
        "location": "Munnar",
        "price_per_night": 1200,
        "rating": 4.5,
        "tags": ["budget", "hiking", "couples", "nature"],
        "capacity": 2,
        "description": "Trailside budget hostel for mountain hikers."
    }
    candidates = [
        {
            "id": "HOT-002",
            "type": "hotel",
            "name": "Tea County Resort",
            "location": "Munnar",
            "price_per_night": 6800,
            "rating": 4.6,
            "tags": ["luxury", "tea-estate", "nature"],
            "capacity": 3
        },
        new_candidate
    ]
    
    ranked = jev._deterministic_fallback(req, profile, candidates)
    assert len(ranked) == 2
    assert ranked[0]["id"] == "HOT-999"
    assert ranked[0]["name"] == "Cloud Peak Hiker Haven"

def test_3_budget_sensitivity_without_fixed_40000_threshold():
    """
    Test 3: Budget sensitivity works without any fixed 40,000 threshold.
    """
    # 1. Budget sensitivity via explicit budget_level
    req1 = ParsedRequest(destination="Munnar", num_days=2, adults=2, budget=None, budget_level="budget")
    assert is_request_budget_sensitive(req1, {}) is True

    # 2. Budget sensitivity via low daily per-person allocation (e.g. 15000 for 5 days and 4 pax = 750/pax/day)
    req2 = ParsedRequest(destination="Kerala", num_days=5, adults=2, children=2, budget=15000)
    assert is_request_budget_sensitive(req2, {}) is True

    # 3. Mid-range allocation is not budget-constrained (e.g. 60000 for 5 days and 4 pax)
    req3 = ParsedRequest(destination="Kerala", num_days=5, adults=2, children=2, budget=60000, budget_level="mid-range")
    assert is_request_budget_sensitive(req3, {}) is False

def test_4_arbitrary_interests_work_without_python_conditionals():
    """
    Test 4: Brand new arbitrary interests (e.g. wildlife, photography) rank candidates
    based on generic token matching without new Python conditionals.
    """
    jev = JevRanker()
    req = ParsedRequest(
        destination="Wayanad",
        num_days=3,
        adults=2,
        interests=["wildlife", "photography"]
    )
    profile = {"preferences": ["wildlife"]}
    
    candidates = [
        {
            "id": "ACT-888",
            "type": "activity",
            "name": "Night Wildlife Safari & Photography Tour",
            "location": "Wayanad",
            "price_per_person": 1500,
            "duration_hours": 4,
            "rating": 4.8,
            "tags": ["wildlife", "photography", "nature", "night"],
            "description": "Guided forest photography tour focusing on nocturnal wildlife."
        },
        {
            "id": "ACT-777",
            "type": "activity",
            "name": "Town Shopping & Textile Walk",
            "location": "Wayanad",
            "price_per_person": 500,
            "duration_hours": 2,
            "rating": 4.0,
            "tags": ["shopping", "city", "textile"],
            "description": "Walk through town markets."
        }
    ]
    
    ranked = jev._deterministic_fallback(req, profile, candidates)
    assert ranked[0]["id"] == "ACT-888"
    assert jev.last_decision.ranked_candidates[0].scores.interest_fit > 0.8

def test_5_past_feedback_passed_to_jev():
    """
    Test 5: Verify past trip feedback is passed in full to the Jev state payload.
    """
    jev = JevRanker()
    req = ParsedRequest(destination="Kerala", num_days=5, adults=2, children=2)
    past_trips = [
        {"destination": "Coorg", "feedback": "Loved the estate stay; kids got bored on the long drives."},
        {"destination": "Wayanad", "feedback": "Wanted more nature walks and fewer packed days."}
    ]
    profile = {"preferences": ["nature"], "past_trips": past_trips}
    candidates = [{"id": "HOT-001", "type": "hotel", "name": "Homestay", "location": "Alleppey", "price_per_night": 3200}]
    
    captured_payload = {}
    def mock_post(url, headers, json, timeout):
        nonlocal captured_payload
        captured_payload = json
        class MockResp:
            status_code = 200
            def json(self):
                return {"answers": {"select_hotel": {"choice": "HOT-001", "probabilities": {"HOT-001": 1.0}}}}
        return MockResp()
        
    jev.api_key = "test-key"
    import httpx
    orig_post = httpx.post
    httpx.post = mock_post
    try:
        jev.rank_candidates(req, profile, candidates)
    finally:
        httpx.post = orig_post
        
    assert "state" in captured_payload
    assert captured_payload["state"]["past_trips"] == past_trips

def test_6_unknown_jev_ids_rejected():
    """
    Test 6: Jev response containing unknown IDs is rejected and triggers fallback.
    """
    jev = JevRanker()
    jev.api_key = "test-key"
    
    def mock_invalid_post(*args, **kwargs):
        class MockResp:
            status_code = 200
            def json(self):
                return {"answers": {"select_hotel": {"choice": "HOT-FABRICATED", "probabilities": {"HOT-FABRICATED": 1.0}}}}
        return MockResp()
        
    import httpx
    orig_post = httpx.post
    httpx.post = mock_invalid_post
    
    req = ParsedRequest(destination="Munnar", num_days=2, adults=2)
    profile = {}
    candidates = [{"id": "HOT-004", "type": "hotel", "name": "Hostel", "location": "Munnar", "price_per_night": 1500}]
    
    try:
        ranked = jev.rank_candidates(req, profile, candidates)
        # Should reject fabricated ID and return valid candidate
        assert len(ranked) == 1
        assert ranked[0]["id"] == "HOT-004"
    finally:
        httpx.post = orig_post

def test_7_jev_failure_triggers_generic_fallback():
    """
    Test 7: Network failure or error triggers deterministic fallback safely.
    """
    jev = JevRanker()
    jev.api_key = "test-key"
    
    def mock_fail_post(*args, **kwargs):
        raise ConnectionError("Network timeout")
        
    import httpx
    orig_post = httpx.post
    httpx.post = mock_fail_post
    
    req = ParsedRequest(destination="Munnar", num_days=2, adults=2, interests=["hiking"], budget_level="budget")
    profile = {}
    candidates = [
        {"id": "HOT-002", "type": "hotel", "location": "Munnar", "price_per_night": 6800, "rating": 4.6},
        {"id": "HOT-004", "type": "hotel", "location": "Munnar", "price_per_night": 1500, "rating": 4.1, "tags": ["budget", "hiking"]}
    ]
    
    try:
        ranked = jev.rank_candidates(req, profile, candidates)
        assert len(ranked) == 2
        assert ranked[0]["id"] == "HOT-004"
    finally:
        httpx.post = orig_post

def test_8_missing_prices_not_replaced_with_fake_defaults():
    """
    Test 8: Missing prices in catalog return None and are never replaced with fake ₹3000 or ₹1000.
    """
    candidate_no_price = {"id": "HOT-NOPRICE", "type": "hotel", "name": "Mystery Stay"}
    price = get_candidate_price(candidate_no_price)
    assert price is None
    assert price != 3000
    assert price != 1000

def test_9_req2_naturally_prefers_hot004():
    """
    Test 9: REQ-2 scenario naturally prefers HOT-004 without hardcoded ID branches.
    """
    jev = JevRanker()
    req = ParsedRequest(
        destination="Munnar",
        num_days=2,
        adults=2,
        children=0,
        budget=None,
        budget_level="budget",
        interests=["hiking", "tea estates"],
        pace="relaxed"
    )
    profile = {
        "preferences": ["hiking", "budget", "nature"],
        "typical_budget_level": "budget"
    }
    candidates = [
        {
            "id": "HOT-002",
            "type": "hotel",
            "name": "Tea County Resort",
            "location": "Munnar",
            "price_per_night": 6800,
            "rating": 4.6,
            "tags": ["luxury", "spa", "tea-estate", "nature"],
            "capacity": 3
        },
        {
            "id": "HOT-004",
            "type": "hotel",
            "name": "Munnar Hikers Hostel",
            "location": "Munnar",
            "price_per_night": 1500,
            "rating": 4.1,
            "tags": ["budget", "hiking", "backpacker", "couples", "nature"],
            "capacity": 2
        }
    ]
    
    ranked = jev._deterministic_fallback(req, profile, candidates)
    assert len(ranked) == 2
    assert ranked[0]["id"] == "HOT-004"
    decision = jev.last_decision
    assert decision.ranked_candidates[0].candidate_id == "HOT-004"
    assert decision.ranked_candidates[0].scores.budget_fit > decision.ranked_candidates[1].scores.budget_fit
    assert "budget-conscious" in decision.ranked_candidates[0].reason.lower()

def test_10_req3_no_candidates_does_not_call_jev():
    """
    Test 10: Zero candidates (REQ-3 Goa) halts before Jev and Jev is never called.
    """
    jev = JevRanker()
    jev.api_key = "test-key"
    req = ParsedRequest(destination="Goa", num_days=3, interests=["beach"])
    res = jev.rank_candidates(req, {}, [])
    assert res == []
    assert jev.last_decision is None

def test_11_role_aware_candidate_pool_selection():
    """
    Test 11: Candidate pool produces a focused role-aware selection,
    strictly selecting a subset (hotels: 1-2, transport: 1, activities: 1-3),
    NOT the entire catalog.
    """
    from src.loader import load_catalog
    from src.retriever import retrieve_candidates
    catalog = load_catalog()
    profile = catalog.get("traveler_profile", {})
    req = ParsedRequest(
        destination="Kerala",
        num_days=5,
        party_adults=2,
        party_children=2,
        child_ages=[8, 11],
        budget=60000,
        budget_level="mid-range",
        interests=["nature", "local food"],
        pace="relaxed"
    )
    candidates = retrieve_candidates(req, profile, catalog)
    assert len(candidates) == 10

    jev = JevRanker()
    ranked = jev._deterministic_fallback(req, profile, candidates)
    decision = jev.last_decision

    assert decision is not None
    assert len(decision.ranked_candidates) == 10
    # Crucial: selected_candidate_ids must NOT dump all 10 candidates!
    assert len(decision.selected_candidate_ids) < len(decision.ranked_candidates)
    assert len(decision.selected_candidate_ids) <= 5
    assert len(decision.candidate_pool.hotels) in [1, 2]
    assert len(decision.candidate_pool.transport) == 1
    assert len(decision.candidate_pool.activities) in [1, 2, 3]
    # Alleppey hotel and sedan should be in pool
    assert "HOT-001" in decision.candidate_pool.hotels
    assert "TRN-001" in decision.candidate_pool.transport
    assert "ACT-001" in decision.candidate_pool.activities

def test_12_no_overall_score_collapse_to_point_fifty():
    """
    Test 12: Candidates do NOT artificially collapse to 0.50.
    Different activities have distinct, differentiated overall scores.
    """
    from src.loader import load_catalog
    from src.retriever import retrieve_candidates
    catalog = load_catalog()
    profile = catalog.get("traveler_profile", {})
    req = ParsedRequest(
        destination="Kerala",
        num_days=5,
        party_adults=2,
        party_children=2,
        budget=60000,
        interests=["nature", "local food"],
        pace="relaxed"
    )
    candidates = retrieve_candidates(req, profile, catalog)
    jev = JevRanker()
    jev._deterministic_fallback(req, profile, candidates)
    decision = jev.last_decision

    scores = [rc.overall_score for rc in decision.ranked_candidates]
    # Check that scores are diverse, not all 0.50
    fifty_count = scores.count(0.50)
    assert fifty_count <= 1, f"Too many candidates collapsed to 0.50: {scores}"
    # Verify top candidate has a significantly higher score than lower candidates
    assert decision.ranked_candidates[0].overall_score > decision.ranked_candidates[-1].overall_score + 0.20

def test_13_base_hub_coherence_penalizes_distant_activities():
    """
    Test 13: When staying in Alleppey for a relaxed family trip (with drive aversion),
    activities in distant Munnar receive significantly lower destination_fit scores.
    """
    from src.loader import load_catalog
    from src.retriever import retrieve_candidates
    catalog = load_catalog()
    profile = catalog.get("traveler_profile", {})
    req = ParsedRequest(
        destination="Kerala",
        num_days=5,
        party_adults=2,
        party_children=2,
        budget=60000,
        interests=["nature", "local food"],
        pace="relaxed"
    )
    candidates = retrieve_candidates(req, profile, catalog)
    jev = JevRanker()
    jev._deterministic_fallback(req, profile, candidates)
    decision = jev.last_decision

    scores_by_id = {rc.candidate_id: rc.scores for rc in decision.ranked_candidates}
    # ACT-001 is in Alleppey (base hub) -> high destination_fit
    assert scores_by_id["ACT-001"].destination_fit >= 0.95
    # ACT-003 is in Munnar (distant from Alleppey, 5 hrs) -> heavily penalized
    assert scores_by_id["ACT-003"].destination_fit < 0.50
    assert scores_by_id["ACT-001"].destination_fit > scores_by_id["ACT-003"].destination_fit

def test_14_evidence_based_reasons_contain_no_unsupported_claims():
    """
    Test 14: Reasons contain NO unsupported claims like 'without public transit fatigue'
    or 'zero long-distance travel'.
    """
    from src.loader import load_catalog
    from src.retriever import retrieve_candidates
    catalog = load_catalog()
    profile = catalog.get("traveler_profile", {})
    req = ParsedRequest(
        destination="Kerala",
        num_days=5,
        party_adults=2,
        party_children=2,
        budget=60000,
        interests=["nature", "local food"],
        pace="relaxed"
    )
    candidates = retrieve_candidates(req, profile, catalog)
    jev = JevRanker()
    jev._deterministic_fallback(req, profile, candidates)
    decision = jev.last_decision

    for rc in decision.ranked_candidates:
        reason_lower = rc.reason.lower()
        assert "public transit fatigue" not in reason_lower
        assert "zero long-distance travel" not in reason_lower
        # Check rank is assigned
        assert rc.rank >= 1

def test_15_diagnostics_block_structure():
    """
    Test 15: Diagnostics block is properly populated with candidate counts and pool.
    """
    from src.loader import load_catalog
    from src.retriever import retrieve_candidates
    catalog = load_catalog()
    profile = catalog.get("traveler_profile", {})
    req = ParsedRequest(
        destination="Kerala",
        num_days=5,
        party_adults=2,
        party_children=2,
        budget=60000,
        interests=["nature", "local food"],
        pace="relaxed"
    )
    candidates = retrieve_candidates(req, profile, catalog)
    jev = JevRanker()
    jev._deterministic_fallback(req, profile, candidates)
    decision = jev.last_decision

    assert decision.diagnostics is not None
    assert decision.diagnostics.retrieved_candidate_count == 10
    assert decision.diagnostics.valid_candidate_count == 10
    assert decision.diagnostics.jev_ranked_count == 10
    assert decision.diagnostics.candidate_pool == decision.candidate_pool
    assert decision.diagnostics.fallback_used is True
