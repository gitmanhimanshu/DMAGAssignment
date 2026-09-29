import json
from src.models import TravelPlan, TravelDay, ItineraryItem
from src.graph_state import ParsedRequest

# This is a monkey patch to run locally without a GEMINI_API_KEY
def mock_parse_request(self, request_text: str) -> ParsedRequest:
    if "Kerala" in request_text:
        return ParsedRequest(destination="Kerala", num_days=5, budget=60000, party_adults=2, party_children=2, interests=["nature", "local-food", "relaxed"])
    elif "Munnar" in request_text:
        return ParsedRequest(destination="Munnar", num_days=2, budget=None, party_adults=2, party_children=0, interests=["hiking", "tea estates"])
    else:
        return ParsedRequest(destination="Goa", num_days=3, budget=None, party_adults=1, party_children=0, interests=["nightlife"])

def mock_generate_itinerary(self, state) -> TravelPlan:
    req_id = state['request_id']
    if req_id == "REQ-1":
        return TravelPlan(
            request_id="REQ-1",
            status="success",
            summary="5-day relaxed family trip in Kerala",
            days=[
                TravelDay(
                    day_number=1,
                    summary="Arrival and Backwaters",
                    items=[
                        ItineraryItem(catalog_id="HOT-001", item_type="hotel", name="Backwater Breeze Homestay", location="Alleppey", quantity=2, unit_price=3200, line_total=6400, reason="Family friendly homestay"),
                        ItineraryItem(catalog_id="ACT-001", item_type="activity", name="Alleppey Houseboat Day Cruise", location="Alleppey", quantity=4, unit_price=2200, line_total=8800, reason="Relaxed nature activity"),
                        ItineraryItem(catalog_id="TRN-002", item_type="transport", name="Private Cab (SUV, per day)", location="Kerala", quantity=5, unit_price=4200, line_total=21000, reason="SUV for family of 4")
                    ]
                )
            ],
            total_price=0,
            grounding_status="pending",
            human_review_required=True
        )
    elif req_id == "REQ-2":
        return TravelPlan(
            request_id="REQ-2",
            status="success",
            summary="Budget hiking weekend in Munnar",
            days=[
                TravelDay(
                    day_number=1,
                    summary="Hiking and Tea Estates",
                    items=[
                        ItineraryItem(catalog_id="HOT-004", item_type="hotel", name="Munnar Hikers Hostel", location="Munnar", quantity=2, unit_price=1500, line_total=3000, reason="Budget stay"),
                        ItineraryItem(catalog_id="ACT-003", item_type="activity", name="Eravikulam National Park Trek", location="Munnar", quantity=2, unit_price=1400, line_total=2800, reason="Hiking interest"),
                        ItineraryItem(catalog_id="ACT-002", item_type="activity", name="Munnar Tea Estate Walk", location="Munnar", quantity=2, unit_price=900, line_total=1800, reason="Tea estates interest")
                    ]
                )
            ],
            total_price=0,
            grounding_status="pending",
            human_review_required=True
        )
    else:
        return TravelPlan(
            request_id="REQ-3",
            status="unfulfillable",
            summary="Cannot fulfill request",
            days=[],
            total_price=0,
            grounding_status="pending",
            human_review_required=True
        )

def mock_rank_candidates(self, parsed_req, profile, valid_candidates):
    # Dummy mock returning all valid candidates
    return valid_candidates

from src.planner import Planner
from src.jev import JevRanker
Planner.parse_request = mock_parse_request
Planner.generate_itinerary = mock_generate_itinerary
JevRanker.rank_candidates = mock_rank_candidates

import sys
from src.main import main
sys.argv = ["main.py", "--request", "REQ-1"]
main()
sys.argv = ["main.py", "--request", "REQ-2"]
main()
sys.argv = ["main.py", "--request", "REQ-3"]
main()
