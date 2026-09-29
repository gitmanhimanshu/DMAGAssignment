import pytest
from src.loader import load_catalog
from src.validator import validate_itinerary, validate_itinerary_coherence
from src.models import TravelPlan, TravelDay, ItineraryItem
from src.graph_state import ParsedRequest
from src.graph import TravelGraph

@pytest.fixture
def catalog():
    return load_catalog()

def test_valid_itinerary(catalog):
    plan = TravelPlan(
        request_id="TEST-1",
        status="success",
        summary="Test",
        days=[
            TravelDay(
                day_number=1,
                summary="Arrival",
                items=[
                    ItineraryItem(
                        catalog_id="HOT-001",
                        item_type="hotel",
                        name="Backwater Breeze Homestay",
                        location="Alleppey",
                        quantity=2,
                        unit_price=3200,
                        line_total=6400,
                        reason="Test"
                    )
                ]
            )
        ],
        total_price=6400,
        grounding_status="pending",
        human_review_required=True
    )
    is_valid, reason = validate_itinerary(plan, catalog)
    assert is_valid == True

def test_fabricated_id(catalog):
    plan = TravelPlan(
        request_id="TEST-2",
        status="success",
        summary="Test",
        days=[
            TravelDay(
                day_number=1,
                summary="Arrival",
                items=[
                    ItineraryItem(
                        catalog_id="HOT-999", # Fabricated
                        item_type="hotel",
                        name="Fake Hotel",
                        location="Goa",
                        quantity=1,
                        unit_price=1000,
                        line_total=1000,
                        reason="Test"
                    )
                ]
            )
        ],
        total_price=1000,
        grounding_status="pending",
        human_review_required=True
    )
    is_valid, reason = validate_itinerary(plan, catalog)
    assert is_valid == False
    assert "Fabricated catalog ID" in reason

def test_price_mismatch(catalog):
    plan = TravelPlan(
        request_id="TEST-3",
        status="success",
        summary="Test",
        days=[
            TravelDay(
                day_number=1,
                summary="Arrival",
                items=[
                    ItineraryItem(
                        catalog_id="HOT-001",
                        item_type="hotel",
                        name="Backwater Breeze Homestay",
                        location="Alleppey",
                        quantity=1,
                        unit_price=1000, # Should be 3200
                        line_total=1000,
                        reason="Test"
                    )
                ]
            )
        ],
        total_price=1000,
        grounding_status="pending",
        human_review_required=True
    )
    is_valid, reason = validate_itinerary(plan, catalog)
    assert is_valid == False
    assert "Price mismatch" in reason

def test_total_mismatch(catalog):
    plan = TravelPlan(
        request_id="TEST-4",
        status="success",
        summary="Test",
        days=[
            TravelDay(
                day_number=1,
                summary="Arrival",
                items=[
                    ItineraryItem(
                        catalog_id="HOT-001",
                        item_type="hotel",
                        name="Backwater Breeze Homestay",
                        location="Alleppey",
                        quantity=1,
                        unit_price=3200,
                        line_total=3200,
                        reason="Test"
                    )
                ]
            )
        ],
        total_price=4000, # Should be 3200
        grounding_status="pending",
        human_review_required=True
    )
    is_valid, reason = validate_itinerary(plan, catalog)
    assert is_valid == False
    assert "Total price mismatch" in reason

def test_itinerary_destination_incoherence(catalog):
    """
    Test 4: Itinerary validation flags destination incoherence (Alleppey hotel + Munnar activity)
    """
    plan = TravelPlan(
        request_id="TEST-COHERENCE",
        status="success",
        summary="Test Incoherence",
        days=[
            TravelDay(
                day_number=1,
                summary="Day 1 Incoherence",
                items=[
                    ItineraryItem(
                        catalog_id="HOT-001",
                        item_type="hotel",
                        name="Backwater Breeze Homestay",
                        location="Alleppey",
                        quantity=1,
                        unit_price=3200,
                        line_total=3200,
                        reason="Stay in Alleppey"
                    ),
                    ItineraryItem(
                        catalog_id="ACT-003",
                        item_type="activity",
                        name="Eravikulam National Park Trek",
                        location="Munnar",
                        quantity=2,
                        unit_price=1400,
                        line_total=2800,
                        reason="Trek in distant Munnar while staying in Alleppey"
                    )
                ]
            )
        ],
        total_price=6000,
        grounding_status="grounded",
        human_review_required=False
    )
    req = ParsedRequest(destination="Kerala", num_days=1, adults=2, interests=["nature"])
    profile = {"preferences": ["nature"]}
    
    is_valid, notes = validate_itinerary_coherence(plan, req, profile, catalog)
    assert is_valid == False
    assert any("Destination incoherence" in note for note in notes)

def test_itinerary_relaxed_pace_violation(catalog):
    """
    Test 5: Itinerary validation flags relaxed pace violation (>2 activities in one day)
    """
    plan = TravelPlan(
        request_id="TEST-PACE",
        status="success",
        summary="Test Pace",
        days=[
            TravelDay(
                day_number=1,
                summary="Packed Day",
                items=[
                    ItineraryItem(
                        catalog_id="ACT-001",
                        item_type="activity",
                        name="Alleppey Houseboat Day Cruise",
                        location="Alleppey",
                        quantity=2,
                        unit_price=2200,
                        line_total=4400,
                        reason="Activity 1"
                    ),
                    ItineraryItem(
                        catalog_id="ACT-002",
                        item_type="activity",
                        name="Munnar Tea Estate Walk & Tasting",
                        location="Munnar",
                        quantity=2,
                        unit_price=900,
                        line_total=1800,
                        reason="Activity 2"
                    ),
                    ItineraryItem(
                        catalog_id="ACT-003",
                        item_type="activity",
                        name="Eravikulam National Park Trek",
                        location="Munnar",
                        quantity=2,
                        unit_price=1400,
                        line_total=2800,
                        reason="Activity 3"
                    )
                ]
            )
        ],
        total_price=9000,
        grounding_status="grounded",
        human_review_required=False
    )
    req = ParsedRequest(destination="Kerala", num_days=1, adults=2, interests=["nature"], pace="relaxed")
    profile = {"preferences": ["relaxed"]}
    
    is_valid, notes = validate_itinerary_coherence(plan, req, profile, catalog)
    assert is_valid == False
    assert any("relaxed pace" in note.lower() for note in notes)

def test_itinerary_duration_violation(catalog):
    """
    Test 6: Itinerary validation flags duration violation (>7h in one day)
    """
    # ACT-001 duration is 6h, ACT-002 is 3h -> 9h total > 7h
    plan = TravelPlan(
        request_id="TEST-DURATION",
        status="success",
        summary="Test Duration",
        days=[
            TravelDay(
                day_number=1,
                summary="Exceeds Duration Limit",
                items=[
                    ItineraryItem(
                        catalog_id="ACT-001",
                        item_type="activity",
                        name="Alleppey Houseboat Day Cruise",
                        location="Alleppey",
                        quantity=1,
                        unit_price=2200,
                        line_total=2200,
                        reason="6 hour cruise"
                    ),
                    ItineraryItem(
                        catalog_id="ACT-002",
                        item_type="activity",
                        name="Munnar Tea Estate Walk & Tasting",
                        location="Munnar",
                        quantity=1,
                        unit_price=900,
                        line_total=900,
                        reason="3 hour tour"
                    )
                ]
            )
        ],
        total_price=3100,
        grounding_status="grounded",
        human_review_required=False
    )
    req = ParsedRequest(destination="Kerala", num_days=1, adults=1, interests=["adventure"])
    profile = {}
    
    is_valid, notes = validate_itinerary_coherence(plan, req, profile, catalog)
    assert is_valid == False
    assert any("exceeds maximum daily limit of 7 hours" in note for note in notes)

def test_itinerary_past_feedback_conflict(catalog):
    """
    Test 7: Itinerary validation flags past feedback conflict (excessive city switches when feedback warned against long drives)
    """
    plan = TravelPlan(
        request_id="TEST-FEEDBACK",
        status="success",
        summary="Test Feedback Conflict",
        days=[
            TravelDay(day_number=1, summary="D1", items=[
                ItineraryItem(catalog_id="ACT-001", item_type="activity", name="Cruise", location="Alleppey", quantity=1, unit_price=2200, line_total=2200, reason="r")
            ]),
            TravelDay(day_number=2, summary="D2", items=[
                ItineraryItem(catalog_id="ACT-003", item_type="activity", name="Trek", location="Munnar", quantity=1, unit_price=1400, line_total=1400, reason="r")
            ]),
            TravelDay(day_number=3, summary="D3", items=[
                ItineraryItem(catalog_id="ACT-001", item_type="activity", name="Cruise", location="Alleppey", quantity=1, unit_price=2200, line_total=2200, reason="r")
            ]),
            TravelDay(day_number=4, summary="D4", items=[
                ItineraryItem(catalog_id="ACT-004", item_type="activity", name="Food Walk", location="Kochi", quantity=1, unit_price=1200, line_total=1200, reason="r")
            ])
        ],
        total_price=7000,
        grounding_status="grounded",
        human_review_required=False
    )
    req = ParsedRequest(destination="Kerala", num_days=4, adults=2, interests=["nature"])
    profile = {
        "past_trips": [
            {"destination": "Coorg", "feedback": "kids got bored on the long drives."}
        ]
    }
    
    is_valid, notes = validate_itinerary_coherence(plan, req, profile, catalog)
    assert is_valid == False
    assert any("long drives" in note.lower() for note in notes)

def test_deterministic_calculation_math(catalog):
    """
    Test 9: Mathematical calculation verifies nights * price_per_night and participants * price_per_person
    """
    # HOT-001: 3200, ACT-002: 900, TRN-001: 3000
    plan = TravelPlan(
        request_id="TEST-MATH",
        status="success",
        summary="Math Verification",
        days=[
            TravelDay(
                day_number=1,
                summary="Day 1",
                items=[
                    ItineraryItem(
                        catalog_id="HOT-001",
                        item_type="hotel",
                        name="Backwater Breeze Homestay",
                        location="Alleppey",
                        quantity=4, # 4 nights
                        unit_price=3200,
                        line_total=12800, # 4 * 3200 = 12800
                        reason="Hotel stay"
                    ),
                    ItineraryItem(
                        catalog_id="ACT-002",
                        item_type="activity",
                        name="Munnar Tea Estate Walk & Tasting",
                        location="Munnar",
                        quantity=4, # 4 persons
                        unit_price=900,
                        line_total=3600, # 4 * 900 = 3600
                        reason="Activity"
                    ),
                    ItineraryItem(
                        catalog_id="TRN-001",
                        item_type="transport",
                        name="Private Cab (Sedan, per day)",
                        location="Kerala",
                        quantity=5, # 5 days
                        unit_price=3000,
                        line_total=15000, # 5 * 3000 = 15000
                        reason="Transport"
                    )
                ]
            )
        ],
        total_price=31400, # 12800 + 3600 + 15000 = 31400
        grounding_status="grounded",
        human_review_required=False
    )
    is_valid, reason = validate_itinerary(plan, catalog)
    assert is_valid == True, f"Validation failed: {reason}"

    # Intentionally corrupt line total
    plan.days[0].items[0].line_total = 10000
    is_valid, reason = validate_itinerary(plan, catalog)
    assert is_valid == False
    assert "Line total mismatch" in reason

def test_req3_halts_before_jev(catalog):
    """
    Test 10: REQ-3 halts before Jev and returns 0 items and ₹0 total
    """
    test_req = next(r for r in catalog.get("test_requests", []) if r["request_id"] == "REQ-3")
    initial_state = {
        "request_id": "REQ-3",
        "request_text": test_req["text"],
        "traveler_profile": catalog.get("traveler_profile", {}),
        "parsed_request": None,
        "candidates": [],
        "jev_decision": None,
        "is_unfulfillable": False,
        "unfulfillable_reason": "",
        "raw_plan": None,
        "itinerary_validation_notes": [],
        "validated_plan": None
    }
    
    graph = TravelGraph(catalog).build_graph()
    final_state = graph.invoke(initial_state)
    
    assert final_state["is_unfulfillable"] == True
    assert len(final_state["candidates"]) == 0
    assert final_state["jev_decision"] is None
    
    plan = final_state["validated_plan"]
    assert plan is not None
    assert plan.status == "unfulfillable"
    assert plan.total_price == 0.0
    assert len(plan.days) == 0

def test_regeneration_loop_max_once(catalog):
    """
    Test 11: Regeneration loop runs at most once when issues are detected,
    never entering infinite loops.
    """
    test_req = next(r for r in catalog.get("test_requests", []) if r["request_id"] == "REQ-1")
    initial_state = {
        "request_id": "REQ-1",
        "request_text": test_req["text"],
        "traveler_profile": catalog.get("traveler_profile", {}),
        "parsed_request": None,
        "candidates": [],
        "jev_decision": None,
        "is_unfulfillable": False,
        "unfulfillable_reason": "",
        "raw_plan": None,
        "itinerary_validation_notes": [],
        "validation_feedback": [],
        "regeneration_count": 0,
        "validated_plan": None
    }
    graph = TravelGraph(catalog).build_graph()
    final_state = graph.invoke(initial_state)
    assert final_state["regeneration_count"] <= 1

def test_req1_itinerary_structure_and_coverage(catalog):
    """
    Test 12: REQ-1 produces a grounded 5-day itinerary covering nature and local food,
    with intentional leisure time, no unnecessary transport, valid prices, and budget summary.
    """
    test_req = next(r for r in catalog.get("test_requests", []) if r["request_id"] == "REQ-1")
    initial_state = {
        "request_id": "REQ-1",
        "request_text": test_req["text"],
        "traveler_profile": catalog.get("traveler_profile", {}),
        "parsed_request": None,
        "candidates": [],
        "jev_decision": None,
        "is_unfulfillable": False,
        "unfulfillable_reason": "",
        "raw_plan": None,
        "itinerary_validation_notes": [],
        "validation_feedback": [],
        "regeneration_count": 0,
        "validated_plan": None
    }
    graph = TravelGraph(catalog).build_graph()
    final_state = graph.invoke(initial_state)
    plan = final_state["validated_plan"]

    assert len(plan.days) == 5
    booked_cids = [item.catalog_id for day in plan.days for item in day.items]
    assert "HOT-001" in booked_cids
    assert "ACT-001" in booked_cids
    assert "ACT-004" in booked_cids
    assert "ACT-005" in booked_cids
    transport_items = [item for day in plan.days for item in day.items if item.item_type == "transport"]
    total_transport_days = sum(item.quantity for item in transport_items)
    assert total_transport_days == 1
    assert len(plan.days[2].summary) > 20
    assert plan.total_price == 31400.0
    assert plan.grounding_status == "valid"

    # Budget summary checks
    assert plan.budget_summary is not None
    assert plan.budget_summary.requested_budget == 60000.0
    assert plan.budget_summary.planned_total == 31400.0
    assert plan.budget_summary.remaining_budget == 28600.0
    assert plan.budget_summary.budget_utilization == 0.523
    assert plan.budget_summary.budget_status == "under_budget"
    assert "under the requested budget" in plan.budget_summary.budget_note.lower()

    # Preference coverage checks
    assert plan.preference_coverage.get("nature") is True
    assert plan.preference_coverage.get("local food") is True

    # Candidate IDs checks
    assert "HOT-001" in plan.planner_candidate_ids
    assert "HOT-001" in plan.selected_candidate_ids
    assert plan.human_review_required is True

def test_req2_itinerary_budget_and_no_unnecessary_transport(catalog):
    """
    Test 13: REQ-2 selects budget hostel, hiking and tea estate activities,
    and excludes unnecessary transport, while generating human review reasons.
    """
    test_req = next(r for r in catalog.get("test_requests", []) if r["request_id"] == "REQ-2")
    initial_state = {
        "request_id": "REQ-2",
        "request_text": test_req["text"],
        "traveler_profile": catalog.get("traveler_profile", {}),
        "parsed_request": None,
        "candidates": [],
        "jev_decision": None,
        "is_unfulfillable": False,
        "unfulfillable_reason": "",
        "raw_plan": None,
        "itinerary_validation_notes": [],
        "validation_feedback": [],
        "regeneration_count": 0,
        "validated_plan": None
    }
    graph = TravelGraph(catalog).build_graph()
    final_state = graph.invoke(initial_state)
    plan = final_state["validated_plan"]

    booked_cids = [item.catalog_id for day in plan.days for item in day.items]
    assert "HOT-004" in booked_cids
    assert "HOT-002" not in booked_cids
    assert "ACT-002" in booked_cids
    assert "ACT-003" in booked_cids
    transport_items = [item for day in plan.days for item in day.items if item.item_type == "transport"]
    assert len(transport_items) == 0
    assert plan.total_price == 6100.0

    # Test candidate IDs separation
    assert set(plan.planner_candidate_ids) == {"HOT-004", "ACT-002", "ACT-003", "TRN-001"}
    assert set(plan.selected_candidate_ids) == {"HOT-004", "ACT-002", "ACT-003"}
    assert "TRN-001" not in plan.selected_candidate_ids

    # Human review checks
    assert plan.human_review_required is True
    assert any("ACT-003 is a moderate-difficulty trek" in r and "confirm suitability before booking" in r for r in plan.human_review_reasons)
    assert any("Final availability and booking details are not provided by the supplier catalog and require human confirmation" in r for r in plan.human_review_reasons)

    # Budget semantics check for no numeric budget
    assert plan.budget_summary is not None
    assert plan.budget_summary.budget_status == "no_numeric_budget"
    assert plan.budget_summary.budget_used == 6100.0
    assert plan.budget_summary.requested_budget is None
    assert plan.budget_summary.remaining_budget is None
    assert plan.budget_summary.budget_utilization is None
    assert "budget-conscious preference but does not specify a numeric budget" in plan.budget_summary.budget_note

def test_validate_candidate_checks(catalog):
    """
    Test 14: Data-driven candidate validation correctly checks ID, type, pricing schema, and capacity.
    """
    from src.validator import validate_candidate, ErrorCode

    # Valid candidate
    valid_c = {"id": "HOT-001", "type": "hotel"}
    res = validate_candidate(valid_c, catalog, party_size=2, destination="Kerala")
    assert res.is_valid is True

    # Unknown ID
    fake_c = {"id": "HOT-999", "type": "hotel"}
    res = validate_candidate(fake_c, catalog)
    assert res.is_valid is False
    assert any(e.code == ErrorCode.UNKNOWN_CATALOG_ID for e in res.errors)

    # Invalid type
    inv_type_c = {"id": "HOT-001", "type": "spaceship"}
    res = validate_candidate(inv_type_c, catalog)
    assert res.is_valid is False
    assert any(e.code == ErrorCode.INVALID_CATALOG_TYPE for e in res.errors)

    # Insufficient capacity
    cap_c = {"id": "HOT-004", "type": "hotel"} # capacity is 2
    res = validate_candidate(cap_c, catalog, party_size=4)
    assert res.is_valid is False
    assert any(e.code == ErrorCode.INSUFFICIENT_CAPACITY for e in res.errors)

    # Incompatible location for local hub
    res = validate_candidate(valid_c, catalog, party_size=2, destination="Munnar") # HOT-001 is in Alleppey
    assert res.is_valid is False
    assert any(e.code == ErrorCode.LOCATION_INCOMPATIBLE for e in res.errors)

def test_validate_candidate_pool_filters_invalid(catalog):
    """
    Test 15: validate_candidate_pool cleanly separates valid candidates from invalid ones.
    """
    from src.validator import validate_candidate_pool

    raw = [
        {"id": "HOT-001", "type": "hotel"}, # Alleppey, cap 4
        {"id": "HOT-004", "type": "hotel"}, # Munnar, cap 2
        {"id": "HOT-FAKE", "type": "hotel"} # Fake ID
    ]
    # For Kerala destination, party size 4:
    # HOT-001 is valid (cap 4 >= 4)
    # HOT-004 is excluded (cap 2 < 4)
    # HOT-FAKE is excluded (unknown ID)
    valid, res = validate_candidate_pool(raw, catalog, party_size=4, destination="Kerala")
    valid_ids = [c["id"] for c in valid]
    assert "HOT-001" in valid_ids
    assert "HOT-004" not in valid_ids
    assert "HOT-FAKE" not in valid_ids

def test_candidate_not_in_pool_rejection(catalog):
    """
    Test 16: Itinerary containing an item outside planner_candidate_ids is rejected.
    """
    from src.validator import validate_itinerary, validate_grounding, ErrorCode

    plan = TravelPlan(
        request_id="TEST-POOL",
        status="success",
        summary="Test Pool",
        days=[
            TravelDay(
                day_number=1,
                summary="Stay",
                items=[
                    ItineraryItem(
                        catalog_id="HOT-001",
                        item_type="hotel",
                        name="Backwater Breeze Homestay",
                        location="Alleppey",
                        quantity=1,
                        unit_price=3200,
                        line_total=3200,
                        reason="Stay"
                    )
                ]
            )
        ],
        total_price=3200,
        grounding_status="pending",
        human_review_required=True
    )
    # HOT-001 is NOT in allowed pool
    allowed_pool = ["HOT-002", "ACT-002"]
    res = validate_grounding(plan, catalog, planner_candidate_ids=allowed_pool)
    assert res.is_valid is False
    assert any(e.code == ErrorCode.CANDIDATE_NOT_IN_POOL for e in res.errors)

    is_valid, reason = validate_itinerary(plan, catalog, planner_candidate_ids=allowed_pool)
    assert is_valid is False
    assert "not in candidate pool" in reason

def test_unsupported_factual_claim_rejection(catalog):
    """
    Test 17: Items with ungrounded travel time or fatigue claims are caught by validation.
    """
    from src.validator import validate_grounding, ErrorCode

    plan = TravelPlan(
        request_id="TEST-CLAIM",
        status="success",
        summary="Test Claim",
        days=[
            TravelDay(
                day_number=1,
                summary="Stay",
                items=[
                    ItineraryItem(
                        catalog_id="HOT-001",
                        item_type="hotel",
                        name="Backwater Breeze Homestay",
                        location="Alleppey",
                        quantity=1,
                        unit_price=3200,
                        line_total=3200,
                        reason="Selected because it is only 30 minutes away from beach"
                    )
                ]
            )
        ],
        total_price=3200,
        grounding_status="pending",
        human_review_required=True
    )
    res = validate_grounding(plan, catalog)
    assert res.is_valid is False
    assert any(e.code == ErrorCode.UNSUPPORTED_FACTUAL_CLAIM for e in res.errors)


