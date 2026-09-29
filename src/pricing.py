from src.models import TravelPlan, BudgetSummary
from src.graph_state import ParsedRequest
from typing import Dict, Any, Optional

def calculate_price(
    plan: TravelPlan,
    catalog: Dict[str, Any],
    parsed_req: Optional[ParsedRequest] = None
) -> TravelPlan:
    """
    Deterministic pricing and budget calculation engine:
    1. Resolves catalog supplier items by ID.
    2. Overwrites unit prices from verified catalog facts.
    3. Calculates line totals: unit_price * quantity.
    4. Calculates total_price as exact sum of line totals.
    5. Calculates budget summary (requested, planned, remaining, utilization, status, note).
    6. Calculates preference coverage based on booked items.
    """
    if plan.status != "success":
        plan.total_price = 0.0
        if parsed_req and parsed_req.budget:
            plan.budget_summary = BudgetSummary(
                requested_budget=parsed_req.budget,
                planned_total=0.0,
                remaining_budget=parsed_req.budget,
                budget_utilization=0.0,
                budget_status="under_budget",
                budget_note="Unfulfillable request from catalog inventory; zero expenditure incurred."
            )
        return plan

    suppliers_map = {s["id"]: s for s in catalog.get("suppliers", [])}
    total_price = 0.0

    for day in plan.days:
        for item in day.items:
            cat_id = item.catalog_id
            if cat_id in suppliers_map:
                supplier = suppliers_map[cat_id]

                # Determine unit price deterministically from catalog
                if supplier["type"] == "hotel":
                    unit_price = supplier.get("price_per_night", 0)
                elif supplier["type"] == "activity":
                    unit_price = supplier.get("price_per_person", 0)
                elif supplier["type"] == "transport":
                    if "price_per_day" in supplier:
                        unit_price = supplier["price_per_day"]
                    elif "price_flat" in supplier:
                        unit_price = supplier["price_flat"]
                    else:
                        unit_price = 0
                else:
                    unit_price = 0

                item.unit_price = float(unit_price)
                item.line_total = float(unit_price * item.quantity)
                total_price += item.line_total

    plan.total_price = round(total_price, 2)

    # Calculate Budget Summary
    if parsed_req and parsed_req.budget is not None and parsed_req.budget > 0:
        req_b = float(parsed_req.budget)
        rem_b = round(req_b - plan.total_price, 2)
        utilization = round(plan.total_price / req_b, 3)

        if plan.total_price > req_b:
            b_status = "over_budget"
            b_note = f"The planned total exceeds the requested budget by Rs. {abs(rem_b)}."
        elif rem_b > 0:
            b_status = "under_budget"
            b_note = "The itinerary remains under the requested budget because the available catalog does not contain additional inventory that is necessary to satisfy the request without adding unnecessary purchases."
        else:
            b_status = "within_budget"
            b_note = "The planned total matches the requested budget."

        plan.budget_summary = BudgetSummary(
            requested_budget=req_b,
            planned_total=plan.total_price,
            budget_used=plan.total_price,
            remaining_budget=rem_b,
            budget_utilization=utilization,
            budget_status=b_status,
            budget_note=b_note
        )
    else:
        is_budget_pref = False
        if parsed_req:
            b_level = (parsed_req.budget_level or "").lower()
            if b_level in ["budget", "budget-conscious", "economy", "low"]:
                is_budget_pref = True
            interests_str = " ".join(parsed_req.interests or []).lower()
            if any(k in interests_str for k in ["budget", "cheap", "affordable", "cost-effective"]):
                is_budget_pref = True

        formatted_total = f"₹{plan.total_price:,.0f}" if plan.total_price == int(plan.total_price) else f"₹{plan.total_price:,.2f}"
        if is_budget_pref:
            b_note = f"The request indicates a budget-conscious preference but does not specify a numeric budget. The planned itinerary costs {formatted_total}."
        else:
            b_note = f"No numeric budget constraint specified. The planned itinerary costs {formatted_total}."

        plan.budget_summary = BudgetSummary(
            requested_budget=None,
            planned_total=plan.total_price,
            budget_used=plan.total_price,
            remaining_budget=None,
            budget_utilization=None,
            budget_status="no_numeric_budget",
            budget_note=b_note
        )

    # Calculate Preference Coverage
    if parsed_req and parsed_req.interests:
        all_booked_cids = {item.catalog_id for day in plan.days for item in day.items}
        all_tags = set()
        for cid in all_booked_cids:
            s_data = suppliers_map.get(cid, {})
            for t in s_data.get("tags", []):
                all_tags.add(t.lower().replace("-", " "))
            name_desc = f"{s_data.get('name', '')} {s_data.get('description', '')}".lower()
            for word in name_desc.replace(",", " ").replace(".", " ").split():
                if len(word) > 2:
                    all_tags.add(word)

        coverage = {}
        for interest in parsed_req.interests:
            norm_int = interest.lower().replace("-", " ").strip()
            tokens = norm_int.split()
            is_covered = any(any(tok in tag for tag in all_tags) for tok in tokens)
            coverage[interest] = is_covered
        plan.preference_coverage = coverage

    return plan
