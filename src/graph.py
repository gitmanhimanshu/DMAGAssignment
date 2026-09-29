from langgraph.graph import StateGraph, END
from src.graph_state import AgentState
from src.planner import Planner
from src.retriever import retrieve_candidates, check_inventory
from src.jev import JevRanker
from src.pricing import calculate_price
from src.validator import (
    validate_itinerary,
    validate_itinerary_coherence,
    generate_human_review_reasons,
    validate_candidate_pool
)
from src.models import TravelPlan
from typing import Dict, Any

class TravelGraph:
    def __init__(self, catalog: Dict[str, Any]):
        self.catalog = catalog
        self.planner = Planner()
        self.jev = JevRanker()

    def build_graph(self):
        workflow = StateGraph(AgentState)

        # Nodes
        workflow.add_node("parse_request", self.parse_request_node)
        workflow.add_node("retrieve_candidates", self.retrieve_candidates_node)
        workflow.add_node("inventory_check", self.inventory_check_node)
        workflow.add_node("graceful_unfulfillable_response", self.unfulfillable_node)
        workflow.add_node("rank_candidates_with_jev", self.rank_candidates_with_jev_node)
        workflow.add_node("generate_itinerary", self.generate_itinerary_node)
        workflow.add_node("calculate_price", self.calculate_price_node)
        workflow.add_node("validate_itinerary", self.validate_itinerary_node)
        workflow.add_node("regenerate_itinerary", self.regenerate_itinerary_node)
        workflow.add_node("final_validation", self.final_validation_node)

        # Edges
        workflow.set_entry_point("parse_request")
        workflow.add_edge("parse_request", "retrieve_candidates")
        workflow.add_edge("retrieve_candidates", "inventory_check")

        workflow.add_conditional_edges(
            "inventory_check",
            lambda state: "rank_candidates_with_jev" if not state.get("is_unfulfillable") else "graceful_unfulfillable_response"
        )

        workflow.add_edge("graceful_unfulfillable_response", END)
        workflow.add_edge("rank_candidates_with_jev", "generate_itinerary")
        workflow.add_edge("generate_itinerary", "calculate_price")
        workflow.add_edge("calculate_price", "validate_itinerary")

        def check_validation_decision(state: AgentState):
            if state.get("validation_failed") and state.get("regeneration_count", 0) < 1:
                return "regenerate_itinerary"
            return "final_validation"

        workflow.add_conditional_edges(
            "validate_itinerary",
            check_validation_decision,
            {
                "regenerate_itinerary": "regenerate_itinerary",
                "final_validation": "final_validation"
            }
        )

        workflow.add_edge("regenerate_itinerary", "calculate_price")
        workflow.add_edge("final_validation", END)

        return workflow.compile()

    def parse_request_node(self, state: AgentState):
        parsed = self.planner.parse_request(state["request_text"])
        return {"parsed_request": parsed, "regeneration_count": 0, "validation_feedback": []}

    def retrieve_candidates_node(self, state: AgentState):
        raw_candidates = retrieve_candidates(state["parsed_request"], state["traveler_profile"], self.catalog)
        # Perform schema & hard validation on retrieved candidates
        pax = state["parsed_request"].party_adults + state["parsed_request"].party_children
        dest = state["parsed_request"].destination
        valid_candidates, _ = validate_candidate_pool(raw_candidates, self.catalog, party_size=pax, destination=dest)
        return {"candidates": valid_candidates}

    def inventory_check_node(self, state: AgentState):
        has_inventory = check_inventory(state["candidates"])
        is_unfulfillable = not has_inventory
        return {
            "is_unfulfillable": is_unfulfillable,
            "unfulfillable_reason": "This request cannot be fulfilled from the provided supplier catalog because no matching inventory is available for the requested destination." if is_unfulfillable else ""
        }

    def unfulfillable_node(self, state: AgentState):
        plan = TravelPlan(
            request_id=state["request_id"],
            status="unfulfillable",
            summary=state.get("unfulfillable_reason", "This request cannot be fulfilled from the provided supplier catalog because no matching inventory is available for the requested destination."),
            days=[],
            total_price=0.0,
            grounding_status="valid",
            human_review_required=True,
            human_review_reasons=[state.get("unfulfillable_reason", "This request cannot be fulfilled from the provided supplier catalog because no matching inventory is available for the requested destination.")],
            planner_candidate_ids=[],
            selected_candidate_ids=[],
            diagnostics=None
        )
        return {"validated_plan": plan}

    def rank_candidates_with_jev_node(self, state: AgentState):
        ranked = self.jev.rank_candidates(state["parsed_request"], state["traveler_profile"], state["candidates"])
        selected_ids = set()
        if self.jev.last_decision:
            if self.jev.last_decision.planner_candidate_ids:
                selected_ids = set(self.jev.last_decision.planner_candidate_ids)
            else:
                selected_ids = set(self.jev.last_decision.selected_candidate_ids)
        focused_candidates = [c for c in ranked if c["id"] in selected_ids]
        if not focused_candidates:
            focused_candidates = ranked
        return {"candidates": focused_candidates, "jev_decision": self.jev.last_decision}

    def generate_itinerary_node(self, state: AgentState):
        raw_plan = self.planner.generate_itinerary(state)
        raw_plan.request_id = state["request_id"]
        return {"raw_plan": raw_plan}

    def regenerate_itinerary_node(self, state: AgentState):
        new_count = state.get("regeneration_count", 0) + 1
        raw_plan = self.planner.generate_itinerary(state)
        raw_plan.request_id = state["request_id"]
        return {"raw_plan": raw_plan, "regeneration_count": new_count}

    def calculate_price_node(self, state: AgentState):
        priced_plan = calculate_price(state["raw_plan"], self.catalog, state.get("parsed_request"))
        return {"raw_plan": priced_plan}

    def validate_itinerary_node(self, state: AgentState):
        raw_plan = state.get("raw_plan")
        if not raw_plan:
            return {"itinerary_validation_notes": [], "validation_failed": False}

        planner_candidate_ids = None
        jev_decision = state.get("jev_decision")
        if jev_decision and jev_decision.planner_candidate_ids:
            planner_candidate_ids = jev_decision.planner_candidate_ids

        is_coherent, notes = validate_itinerary_coherence(
            raw_plan,
            state["parsed_request"],
            state["traveler_profile"],
            self.catalog,
            candidate_pool_ids=planner_candidate_ids
        )

        is_math_valid, math_reason = validate_itinerary(
            raw_plan,
            self.catalog,
            planner_candidate_ids=planner_candidate_ids
        )

        all_notes = list(notes)
        if not is_math_valid:
            all_notes.append(f"Grounding failure: {math_reason}")

        # Check if validation failed
        critical_issues = [n for n in all_notes if "validations passed" not in n]
        validation_failed = not is_coherent or not is_math_valid or (len(critical_issues) > 0 and state.get("regeneration_count", 0) < 1)

        return {
            "itinerary_validation_notes": all_notes,
            "validation_feedback": critical_issues,
            "validation_failed": validation_failed
        }

    def final_validation_node(self, state: AgentState):
        plan = state["raw_plan"]
        jev_decision = state.get("jev_decision")
        if jev_decision:
            plan.planner_candidate_ids = list(jev_decision.planner_candidate_ids)
            plan.diagnostics = jev_decision.diagnostics

        is_valid, reason = validate_itinerary(
            plan,
            self.catalog,
            planner_candidate_ids=plan.planner_candidate_ids
        )
        if not is_valid:
            plan.grounding_status = f"failed: {reason}"
        else:
            plan.grounding_status = "valid"

        booked_cids = []
        for day in plan.days:
            for item in day.items:
                if item.catalog_id and item.catalog_id not in booked_cids:
                    booked_cids.append(item.catalog_id)
        plan.selected_candidate_ids = booked_cids

        # Update jev_decision with actual selected candidate IDs
        if jev_decision:
            jev_decision.selected_candidate_ids = booked_cids

        # Populate human_review_reasons
        plan.human_review_required = True
        plan.human_review_reasons = generate_human_review_reasons(
            plan,
            self.catalog,
            state.get("traveler_profile", {}),
            state.get("parsed_request")
        )

        return {
            "validated_plan": plan,
            "regeneration_count": state.get("regeneration_count", 0),
            "validation_feedback": state.get("validation_feedback", [])
        }


