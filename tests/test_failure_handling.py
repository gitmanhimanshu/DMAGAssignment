import json
import pytest
import httpx
from unittest.mock import patch, MagicMock

from src.loader import load_catalog
from src.planner import Planner
from src.jev import JevRanker
from src.graph_state import ParsedRequest, AgentState
from src.validator import validate_itinerary
from src.pricing import calculate_price
from src.graph import TravelGraph

@pytest.fixture
def catalog():
    return load_catalog()

@pytest.fixture
def sample_state():
    return {
        "request_id": "TEST-FAIL",
        "request_text": "5 days in Kerala for a family of 4 with a relaxed pace, nature and local food.",
        "parsed_request": ParsedRequest(
            destination="Kerala",
            num_days=5,
            adults=2,
            children=2,
            party_adults=2,
            party_children=2,
            child_ages=[8, 11],
            budget=60000,
            budget_level="mid-range",
            interests=["nature", "local food"],
            pace="relaxed"
        ),
        "traveler_profile": {
            "preferences": ["nature", "family-friendly", "local food"],
            "past_trips": [{"feedback": "Loved unhurried mornings and local homestays"}]
        },
        "candidates": [
            {
                "id": "HOT-001",
                "type": "hotel",
                "name": "Backwater Breeze Homestay",
                "location": "Alleppey",
                "price_per_night": 3200,
                "capacity": 4,
                "tags": ["budget", "family-friendly", "backwaters"],
                "rating": 4.5
            },
            {
                "id": "ACT-001",
                "type": "activity",
                "name": "Alleppey Houseboat Day Cruise",
                "location": "Alleppey",
                "price_per_person": 2200,
                "duration_hours": 6,
                "tags": ["nature", "family-friendly", "relaxed"],
                "rating": 4.8
            },
            {
                "id": "ACT-004",
                "type": "activity",
                "name": "Fort Kochi Food Walk",
                "location": "Kochi",
                "price_per_person": 1200,
                "duration_hours": 3,
                "tags": ["food", "culture", "relaxed"],
                "rating": 4.6
            },
            {
                "id": "TRN-001",
                "type": "transport",
                "name": "Private Cab (Sedan, per day)",
                "location": "Kerala",
                "price_per_day": 3000,
                "capacity": 4,
                "tags": ["private", "driver"],
                "rating": 4.7
            }
        ]
    }

def test_llm_timeout_triggers_grounded_fallback(catalog, sample_state):
    """
    Test 1: LLM timeout raises httpx.TimeoutException -> triggers deterministic
    grounded fallback with planner_fallback_reason='llm_timeout'.
    """
    planner = Planner()
    with patch.object(planner.llm, "generate_structured", side_effect=httpx.TimeoutException("API call timed out after 15s")):
        plan = planner.generate_itinerary(sample_state)

    assert plan is not None
    assert plan.status == "success"
    assert plan.planner_fallback_used is True
    assert plan.planner_fallback_reason == "llm_timeout"

    # Verify all generated items exist in the catalog candidates
    candidate_ids = {c["id"] for c in sample_state["candidates"]}
    for day in plan.days:
        for item in day.items:
            assert item.catalog_id in candidate_ids

    # Pricing must be calculated deterministically before validation
    priced_plan = calculate_price(plan, catalog, sample_state["parsed_request"])
    is_valid, reason = validate_itinerary(priced_plan, catalog)
    assert is_valid is True, f"Fallback plan failed validation: {reason}"

def test_llm_rate_limit_triggers_grounded_fallback(catalog, sample_state):
    """
    Test 2: LLM rate limit (HTTP 429 / ResourceExhausted) triggers deterministic
    grounded fallback with planner_fallback_reason='llm_rate_limited'.
    """
    planner = Planner()
    with patch.object(planner.llm, "generate_structured", side_effect=ValueError("HTTP 429 rate limit exceeded: quota exhausted")):
        plan = planner.generate_itinerary(sample_state)

    assert plan is not None
    assert plan.status == "success"
    assert plan.planner_fallback_used is True
    assert plan.planner_fallback_reason == "llm_rate_limited"

    priced_plan = calculate_price(plan, catalog, sample_state["parsed_request"])
    is_valid, reason = validate_itinerary(priced_plan, catalog)
    assert is_valid is True, f"Fallback plan failed validation: {reason}"

def test_llm_malformed_response_triggers_grounded_fallback(catalog, sample_state):
    """
    Test 3: LLM returning invalid JSON or schema error triggers deterministic
    grounded fallback with planner_fallback_reason='llm_invalid_response'.
    """
    planner = Planner()
    with patch.object(planner.llm, "generate_structured", side_effect=json.JSONDecodeError("Expecting value", "{malformed json", 1)):
        plan = planner.generate_itinerary(sample_state)

    assert plan is not None
    assert plan.status == "success"
    assert plan.planner_fallback_used is True
    assert plan.planner_fallback_reason == "llm_invalid_response"

    priced_plan = calculate_price(plan, catalog, sample_state["parsed_request"])
    is_valid, reason = validate_itinerary(priced_plan, catalog)
    assert is_valid is True, f"Fallback plan failed validation: {reason}"

def test_llm_service_error_triggers_grounded_fallback(catalog, sample_state):
    """
    Test 4: LLM 503 / network connection error triggers deterministic
    grounded fallback with planner_fallback_reason='llm_service_error'.
    """
    planner = Planner()
    with patch.object(planner.llm, "generate_structured", side_effect=httpx.ConnectError("503 Service Unavailable: connection refused")):
        plan = planner.generate_itinerary(sample_state)

    assert plan is not None
    assert plan.status == "success"
    assert plan.planner_fallback_used is True
    assert plan.planner_fallback_reason == "llm_service_error"

    priced_plan = calculate_price(plan, catalog, sample_state["parsed_request"])
    is_valid, reason = validate_itinerary(priced_plan, catalog)
    assert is_valid is True, f"Fallback plan failed validation: {reason}"

def test_jev_timeout_and_rate_limit_diagnostics(sample_state):
    """
    Test 5: JEV timeout and rate limiting are classified into crisp failure diagnostics
    and safely trigger deterministic fallback.
    """
    jev = JevRanker()
    jev.api_key = "test-key"

    # 1. Test Timeout
    with patch("httpx.post", side_effect=httpx.TimeoutException("JEV API timed out after 15s")):
        ranked = jev.rank_candidates(sample_state["parsed_request"], sample_state["traveler_profile"], sample_state["candidates"])

    assert len(ranked) == len(sample_state["candidates"])
    assert jev.last_decision is not None
    diag = jev.last_decision.diagnostics
    assert diag.fallback_used is True
    assert diag.ranking_source == "deterministic_fallback"
    assert "jev_timeout" in diag.jev_failure_reason

    # 2. Test Rate Limit (429)
    mock_resp = MagicMock()
    mock_resp.status_code = 429
    mock_resp.text = "Too Many Requests - Rate Limit Exceeded"
    with patch("httpx.post", return_value=mock_resp):
        ranked = jev.rank_candidates(sample_state["parsed_request"], sample_state["traveler_profile"], sample_state["candidates"])

    assert len(ranked) == len(sample_state["candidates"])
    assert jev.last_decision is not None
    diag = jev.last_decision.diagnostics
    assert diag.fallback_used is True
    assert "jev_rate_limited" in diag.jev_failure_reason

def test_end_to_end_graph_with_llm_failure(catalog):
    """
    Test 6: End-to-end execution of the TravelGraph when external LLM fails:
    verifies complete itinerary, deterministic pricing calculation, and 100% grounding validity.
    """
    app = TravelGraph(catalog).build_graph()
    initial_state = {
        "request_id": "REQ-1",
        "request_text": "5 days in Kerala for a family of 4 with a relaxed pace, nature and local food.",
        "traveler_profile": catalog.get("traveler_profile", {}),
        "candidates": [],
        "parsed_request": None,
        "is_unfulfillable": False,
        "unfulfillable_reason": "",
        "raw_plan": None,
        "itinerary_validation_notes": [],
        "validated_plan": None
    }
    # Force LLM calls to fail with timeout
    with patch("src.llm.LLMClient.generate_structured", side_effect=httpx.TimeoutException("Gateway Timeout")):
        result = app.invoke(initial_state)

    plan = result["validated_plan"]
    assert plan is not None
    assert plan.status == "success"
    assert plan.grounding_status == "valid"
    assert plan.planner_fallback_used is True
    assert plan.planner_fallback_reason == "llm_timeout"

    # Price was computed deterministically
    assert plan.total_price > 0
    assert plan.total_price == plan.budget_summary.planned_total

    # Grounding check passes
    is_valid, reason = validate_itinerary(plan, catalog)
    assert is_valid is True, f"End-to-end plan failed validation: {reason}"
