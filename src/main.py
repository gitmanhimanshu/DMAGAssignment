import argparse
import json
import os
import sys

# Ensure UTF-8 output encoding on Windows terminals
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

from src.loader import load_catalog, get_test_request
from src.graph import TravelGraph

def main():
    parser = argparse.ArgumentParser(description="Travel AI Planner")
    parser.add_argument("--request", type=str, help="Request ID to run (e.g., REQ-1)")
    args = parser.parse_args()

    if not args.request:
        parser.print_help()
        return

    catalog = load_catalog()
    try:
        test_req = get_test_request(args.request, catalog)
    except ValueError as e:
        print(f"Error: {e}")
        return

    profile = catalog.get("traveler_profile", {})
    
    initial_state = {
        "request_id": args.request,
        "request_text": test_req["text"],
        "traveler_profile": profile,
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
    
    print(f"Running request {args.request}...")
    final_state = graph.invoke(initial_state)
    
    validated_plan = final_state.get("validated_plan")
    
    if not validated_plan:
        print("Error: No plan generated.")
        return
        
    out_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "outputs")
    os.makedirs(out_dir, exist_ok=True)
    out_file = os.path.join(out_dir, f"{args.request}.json")
    
    with open(out_file, "w", encoding="utf-8") as f:
        f.write(validated_plan.model_dump_json(indent=2))
        
    # Save Jev decision if available
    jev_decision = final_state.get("jev_decision")
    if jev_decision:
        jev_file = os.path.join(out_dir, f"{args.request}_jev_decision.json")
        with open(jev_file, "w", encoding="utf-8") as f:
            f.write(jev_decision.model_dump_json(indent=2))

    # Print Jev Decision Section
    if jev_decision:
        print("\n" + "=" * 68)
        print("           JEV CONTEXTUAL DECISION LAYER (RANKINGS & CHOICES)")
        print("=" * 68)
        
        suppliers_map = {s["id"]: s for s in catalog.get("suppliers", [])}
        top_hotel = None
        top_activities = []
        top_transport = None
        
        for rc in jev_decision.ranked_candidates:
            supp = suppliers_map.get(rc.candidate_id, {})
            stype = supp.get("type", "item")
            if stype == "hotel" and not top_hotel:
                top_hotel = (rc, supp)
            elif stype == "activity" and len(top_activities) < 2:
                top_activities.append((rc, supp))
            elif stype == "transport" and not top_transport:
                top_transport = (rc, supp)

        print("\n[JEV'S TOP SELECTIONS FOR ITINERARY]")
        if top_hotel:
            print(f"  * Top Hotel:     [{top_hotel[0].candidate_id}] {top_hotel[1].get('name')} | Overall Score: {top_hotel[0].overall_score}")
            print(f"    Reason: {top_hotel[0].reason}")
        if top_activities:
            print(f"  * Top Activities:")
            for a_rc, a_supp in top_activities:
                print(f"    - [{a_rc.candidate_id}] {a_supp.get('name')} | Overall Score: {a_rc.overall_score}")
                print(f"      Reason: {a_rc.reason}")
        if top_transport:
            print(f"  * Top Transport: [{top_transport[0].candidate_id}] {top_transport[1].get('name')} | Overall Score: {top_transport[0].overall_score}")
            print(f"    Reason: {top_transport[0].reason}")

        if jev_decision.candidate_pool:
            print("\n[JEV ROLE-AWARE CANDIDATE POOL]")
            print(f"  * Selected Hotels:       {jev_decision.candidate_pool.hotels}")
            print(f"  * Selected Activities:   {jev_decision.candidate_pool.activities}")
            print(f"  * Selected Transport:    {jev_decision.candidate_pool.transport}")
            print(f"  * Planner Candidate IDs: {jev_decision.planner_candidate_ids}")
            print(f"  * Selected Candidate IDs:{jev_decision.selected_candidate_ids}")

        if jev_decision.diagnostics:
            diag = jev_decision.diagnostics
            print("\n[JEV DIAGNOSTICS]")
            print(f"  * Retrieved Candidates:  {diag.retrieved_candidate_count}")
            print(f"  * Valid Candidates:      {diag.valid_candidate_count}")
            print(f"  * Ranked by Jev:         {diag.jev_ranked_count}")
            print(f"  * Jev Used:              {diag.jev_used}")
            print(f"  * Ranking Source:        {diag.ranking_source}")
            print(f"  * Fallback Used:         {diag.fallback_used}")
            if diag.jev_failure_reason:
                print(f"  * Jev Failure Reason:    {diag.jev_failure_reason}")

        print("\n[ALL CANDIDATES EVALUATED & RANKED BY JEV]")
        for i, ranked_c in enumerate(jev_decision.ranked_candidates, 1):
            supp = suppliers_map.get(ranked_c.candidate_id, {})
            name = supp.get("name", ranked_c.candidate_id)
            ctype = supp.get("type", "unknown")
            print(f"  {i}. [{ranked_c.candidate_id}] {name} ({ctype}) | Score: {ranked_c.overall_score} (Rank: {ranked_c.rank})")
            print(f"     Dimension Scores: {ranked_c.scores.model_dump()}")
            print(f"     Reason: {ranked_c.reason}")
        print("=" * 68)

    # Print Final Day-by-Day Itinerary Plan Section
    print("\n" + "=" * 68)
    print(f"           FINAL GENERATED ITINERARY PLAN ({args.request})")
    print("=" * 68)
    print(f"Status:           {validated_plan.status.upper()}")
    print(f"Summary:          {validated_plan.summary}")
    print(f"Total Price:      Rs. {validated_plan.total_price} {validated_plan.currency}")
    print(f"Grounding Status: {validated_plan.grounding_status.upper()}")
    print(f"Human Review:     {'REQUIRED' if validated_plan.human_review_required else 'NOT REQUIRED'}")
    if validated_plan.human_review_reasons:
        print("Human Review Reasons:")
        for r in validated_plan.human_review_reasons:
            print(f"  * {r}")
    print(f"Planner Candidate IDs:  {validated_plan.planner_candidate_ids}")
    print(f"Selected Candidate IDs: {validated_plan.selected_candidate_ids}")
    if validated_plan.planner_fallback_used:
        print(f"Planner Fallback:       ACTIVE ({validated_plan.planner_fallback_reason})")

    if validated_plan.budget_summary:
        bs = validated_plan.budget_summary
        print("\n[BUDGET OPTIMIZATION SUMMARY]")
        if bs.requested_budget is not None:
            print(f"  * Requested Budget:   Rs. {bs.requested_budget}")
            print(f"  * Planned Total:      Rs. {bs.planned_total}")
            print(f"  * Remaining Budget:   Rs. {bs.remaining_budget}")
            print(f"  * Budget Utilization: {bs.budget_utilization:.3f} ({bs.budget_utilization * 100:.1f}%)")
            print(f"  * Budget Status:      {bs.budget_status.upper()}")
            print(f"  * Budget Note:        {bs.budget_note}")
        else:
            print(f"  * Planned Total:      Rs. {bs.planned_total}")
            print(f"  * Budget Status:      {bs.budget_status.upper()}")
            print(f"  * Budget Note:        {bs.budget_note}")

    if validated_plan.preference_coverage:
        print("\n[PREFERENCE COVERAGE AUDIT]")
        for pref, covered in validated_plan.preference_coverage.items():
            status_symbol = "[x]" if covered else "[ ]"
            print(f"  {status_symbol} {pref}: {'COVERED' if covered else 'NOT COVERED'}")

    if validated_plan.status == "success" and validated_plan.days:
        print("\n[DAY-BY-DAY ITINERARY]")
        for day in validated_plan.days:
            print(f"\n--- Day {day.day_number}: {day.summary} ---")
            if not day.items:
                print("   (Intentional leisure / free day - no paid items)")
            else:
                for item in day.items:
                    print(f"   * [{item.catalog_id}] {item.name} ({item.item_type})")
                    print(f"     Quantity: {item.quantity} | Unit: Rs. {item.unit_price} | Line Total: Rs. {item.line_total}")
                    print(f"     Why: {item.reason}")
    elif validated_plan.status == "unfulfillable":
        print("\n[UNFULFILLABLE NOTICE]")
        print(f"   Notice: {validated_plan.summary}")
        print("   0 items booked, 0 fabricated catalog IDs, Rs 0 total.")

    notes = final_state.get("itinerary_validation_notes", [])
    if notes:
        print("\n[VALIDATION NOTES]")
        for note in notes:
            print(f"  - {note}")

    print(f"\nSaved itinerary to:    outputs/{args.request}.json")
    if jev_decision:
        print(f"Saved Jev decision to: outputs/{args.request}_jev_decision.json")
    print("=" * 68 + "\n")

if __name__ == "__main__":
    main()
