import re
import json
from typing import Dict, Any, List, Optional
from src.llm import LLMClient, classify_llm_error
from src.models import TravelPlan, TravelDay, ItineraryItem
from src.graph_state import ParsedRequest, AgentState

def extract_requested_destination(text: str) -> str:
    """
    Extracts the destination generically from the user request text
    without hardcoding any specific city, state, or regional names.
    Supports prepositions like 'in <Location>', 'to <Location>', 'visiting <Location>', etc.
    """
    # 1. Match destination prepositions (e.g., 'in <Location>', 'to <Location>', 'visiting <Location>')
    prep_match = re.search(r"\b(?:in|to|visiting|exploring|around)\s+([A-Za-z]+)", text, re.IGNORECASE)
    if prep_match:
        cand = prep_match.group(1).strip()
        stop_words = {"the", "a", "an", "our", "my", "this", "that", "all", "some"}
        if cand.lower() not in stop_words:
            return cand.capitalize()

    # 2. Match capitalized destination words that are not common grammar/domain tokens
    common_tokens = {"plan", "weekend", "family", "days", "day", "couple", "budget", "need", "please", "trip", "tour", "hotel", "resort"}
    for word in text.split():
        clean = re.sub(r"[^\w]", "", word)
        if clean.istitle() and clean.lower() not in common_tokens:
            return clean

    return "Destination"

class Planner:
    def __init__(self):
        self.llm = LLMClient()

    def parse_request(self, request_text: str) -> ParsedRequest:
        prompt = f"""
        Extract the following structured travel parameters from the user request.
        Request: "{request_text}"

        Fields to extract:
        - destination: Primary destination mentioned in the request
        - num_days: Total number of days (e.g. 5, weekend = 2)
        - budget: Numeric budget in INR if mentioned (e.g. 60000), otherwise null
        - budget_level: "budget", "mid-range", or "luxury" based on phrasing (e.g. "budget-conscious" -> "budget")
        - party_adults: Number of adults (assume 2 if "couple" or not specified)
        - party_children: Number of children (e.g. 2 for family of 4)
        - child_ages: Array of child ages if mentioned (e.g. [8, 11])
        - interests: List of key interests (e.g. ["nature", "local-food", "hiking", "tea-estates"])
        - pace: "relaxed", "moderate", or "active" (e.g. "relaxed pace" -> "relaxed", "hiking" -> "active")
        """
        try:
            return self.llm.generate_structured(prompt, ParsedRequest)
        except Exception as e:
            reason = getattr(self.llm, "last_failure_reason", None) or classify_llm_error(e)
            print(f"[LLM Notice] LLM parser unavailable ({reason}: {e}). Using deterministic request parser.")
            return self._deterministic_parse_request(request_text)

    def generate_itinerary(self, state: AgentState) -> TravelPlan:
        candidates_str = json.dumps(state.get('candidates', []), indent=2)
        profile_str = json.dumps(state.get('traveler_profile', {}), indent=2)
        parsed_req = state.get('parsed_request')

        # Check for feedback from previous validation attempt
        validation_feedback = state.get("validation_feedback", [])
        feedback_section = ""
        if validation_feedback:
            feedback_section = f"""
            PREVIOUS VALIDATION FEEDBACK (YOU MUST ADDRESS THESE SPECIFIC ISSUES IN THIS REGENERATION):
            {json.dumps(validation_feedback, indent=2)}
            """

        prompt = f"""
        You are an expert travel planner. Create a cohesive, grounded, day-by-day travel itinerary.

        Request: {state['request_text']}
        Traveler Profile: {profile_str}

        Allowed Candidate Inventory (YOU MUST ONLY USE ITEMS FROM THIS CANDIDATE POOL):
        {candidates_str}
        {feedback_section}

        MANDATORY PLANNING RULES:
        1. GROUNDING: Use ONLY catalog_id from the Allowed Candidate Inventory. Never invent hotels, activities, transport, or prices.
        2. PREFERENCE COVERAGE: Explicitly cover the traveler's stated interests across the itinerary where suitable candidates exist.
        3. NO UNEXPLAINED EMPTY DAYS: A relaxed trip can include intentional leisure time, but do NOT produce unexplained empty days. For each leisure day, provide an intentional explanation (e.g. "slower day after a longer activity", "flexible family time", "unstructured relaxation", "departure/checkout day").
        4. INCREMENTAL VALUE & PACING: For multi-day trips, schedule 2 to 3 meaningful activities across the trip if candidates permit. You may pair a short light activity (e.g. 2 hours) on the same day as another compatible light activity (e.g. 3 hours) in the same location, but keep total daily activity duration <= 7 hours and preserve dedicated recovery days.
        5. TRANSPORT IS OPTIONAL: Do NOT automatically include transport. Include transport ONLY when useful for the itinerary (e.g. inter-location movement between hotel and activity, airport transfer, or explicit private transport preference). If all activities are co-located with the accommodation, do NOT force transport.
        6. LOCATION COHERENCE: Use only explicit location fields from catalog data. Never invent travel times or ungrounded distance claims ("30 minutes away", "2 hours drive", "short drive", "long drive", "minimizes travel time").
        7. QUANTITIES:
           - Hotel: quantity must be N-1 nights for an N-day trip.
           - Activity: quantity must equal party size.
           - Transport: quantity must equal the exact number of days transport is actually used.
        8. STATUS: Set status to "success" and human_review_required to true.
        """
        try:
            plan = self.llm.generate_structured(prompt, TravelPlan)
            plan.planner_fallback_used = False
            return plan
        except Exception as e:
            reason = getattr(self.llm, "last_failure_reason", None) or classify_llm_error(e)
            print(f"[LLM Notice] LLM synthesis unavailable ({reason}: {e}). Using grounded synthesis fallback from Jev-ranked candidates.")
            plan = self._deterministic_generate_itinerary(state)
            plan.planner_fallback_used = True
            plan.planner_fallback_reason = reason
            return plan

    def _deterministic_parse_request(self, text: str) -> ParsedRequest:
        lower = text.lower()

        # 1. Destination (Derived generically without hardcoded location lists)
        dest = extract_requested_destination(text)

        # 2. Number of days
        days = 3
        day_match = re.search(r"(\d+)\s*days?", lower)
        if day_match:
            days = int(day_match.group(1))
        elif "weekend" in lower:
            days = 2

        # 3. Party size & children ages
        adults = 2
        children = 0
        child_ages = []
        if "couple" in lower or "two people" in lower:
            adults = 2
            children = 0
        else:
            adult_match = re.search(r"(\d+)\s*adults?", lower)
            if adult_match:
                adults = int(adult_match.group(1))
            child_match = re.search(r"(\d+)\s*(?:kids?|children)", lower)
            if child_match:
                children = int(child_match.group(1))
            elif "family of 4" in lower:
                adults = 2
                children = 2

            if "kids aged" in lower or "children aged" in lower:
                raw_ages = re.findall(r"(\d+)\s*(?:and|,)?\s*(\d+)?", lower[lower.find("aged"):])
                for pair in raw_ages:
                    for a in pair:
                        if a and 1 <= int(a) <= 17:
                            child_ages.append(int(a))

        # 4. Budget & Budget Level
        budget = None
        budget_match = re.search(r"(\d{1,3}(?:,\d{3})+|\d{4,6})", text)
        if budget_match:
            try:
                budget = float(budget_match.group(1).replace(",", ""))
            except ValueError:
                budget = None

        budget_level = "mid-range"
        if "budget" in lower or "cheap" in lower:
            budget_level = "budget"
        elif "luxury" in lower:
            budget_level = "luxury"
        elif budget is not None:
            num_days_est = max(1, days)
            pax_est = max(1, adults + children)
            daily_per_pax = budget / (num_days_est * pax_est)
            if daily_per_pax < 2500:
                budget_level = "budget"
            elif daily_per_pax > 15000:
                budget_level = "luxury"
            else:
                budget_level = "mid-range"

        # 5. Interests
        interests = []
        for kw in ["nature", "local food", "local-food", "relaxed", "hiking", "tea estates", "tea-estate", "nightlife", "beach", "culture"]:
            if kw in lower:
                interests.append(kw)

        # 6. Pace
        pace = "relaxed" if "relaxed" in lower or "slow" in lower or "unhurried" in lower else ("active" if "hiking" in lower or "trek" in lower else "moderate")

        return ParsedRequest(
            destination=dest,
            num_days=days,
            budget=budget,
            budget_level=budget_level,
            party_adults=adults,
            party_children=children,
            child_ages=child_ages,
            interests=interests,
            pace=pace
        )

    def _deterministic_generate_itinerary(self, state: AgentState) -> TravelPlan:
        """
        Generic, grounded itinerary synthesis from candidate pool:
        - Covers explicit traveler preferences across available candidates
        - Inspects unused candidates and incorporates meaningful incremental value
        - Eliminates unexplained empty days by providing intentional explanations
        - Evaluates transport necessity (includes transport ONLY when useful for movement)
        - Respects hotel nights (N-1) and party capacity
        - Contains NO hardcoded catalog IDs or destination names
        """
        candidates = state.get("candidates", [])
        parsed_req = state.get("parsed_request")
        profile = state.get("traveler_profile", {})
        num_days = parsed_req.num_days if parsed_req else 3
        total_pax = (parsed_req.party_adults + parsed_req.party_children) if parsed_req else 2

        hotels = [c for c in candidates if c.get("type") == "hotel"]
        all_activities = [c for c in candidates if c.get("type") == "activity"]
        transports = [c for c in candidates if c.get("type") == "transport"]

        # 1. Choose accommodation (top ranked hotel matching capacity)
        selected_hotel = None
        for h in hotels:
            if h.get("capacity", 2) >= total_pax:
                selected_hotel = h
                break
        if not selected_hotel and hotels:
            selected_hotel = hotels[0]

        base_location = selected_hotel.get("location") if selected_hotel else parsed_req.destination
        hotel_nights = max(1, num_days - 1)

        # 2. Preference-aware Initial Activity Selection
        req_interest_tokens = set()
        for i in (parsed_req.interests if parsed_req else []):
            norm = i.lower().replace("-", " ")
            req_interest_tokens.add(norm)
            for part in norm.split():
                if len(part) > 2:
                    req_interest_tokens.add(part)

        pref_tokens = set()
        for p in profile.get("preferences", []):
            norm = p.lower().replace("-", " ")
            pref_tokens.add(norm)
            for part in norm.split():
                if len(part) > 2:
                    pref_tokens.add(part)

        # Score and rank available activities by preference coverage and base hub proximity
        scored_activities = []
        for a in all_activities:
            a_tags = {t.lower().replace("-", " ") for t in a.get("tags", [])}
            a_text = f"{a.get('name', '')} {a.get('description', '')}".lower()

            int_matches = sum(1 for tok in req_interest_tokens if tok in a_tags or tok in a_text)
            pref_matches = sum(1 for tok in pref_tokens if tok in a_tags or tok in a_text)
            is_local = (a.get("location", "").lower() == base_location.lower())

            # Combined heuristic score
            score = (int_matches * 3.0) + (pref_matches * 1.5) + (2.0 if is_local else 0.5)
            scored_activities.append((a, score, is_local))

        scored_activities.sort(key=lambda x: x[1], reverse=True)

        # Check traveler pace and past feedback
        is_relaxed = (parsed_req.pace == "relaxed" or "relaxed" in [p.lower() for p in profile.get("preferences", [])])
        past_feedback_text = " ".join([t.get("feedback", "").lower() for t in profile.get("past_trips", [])])
        dislikes_long_drives = ("long drives" in past_feedback_text or "bored on" in past_feedback_text)
        has_major_activity = any(a[0].get("duration_hours", 0) >= 5 for a in scored_activities[:1])

        # Core initial activity count: 2 for weekend/short trips, 2 for relaxed multi-day trips with major activity
        if num_days <= 2:
            target_core_count = min(len(scored_activities), 2)
        elif num_days == 3:
            target_core_count = min(len(scored_activities), 2)
        elif is_relaxed and (has_major_activity or dislikes_long_drives):
            target_core_count = min(len(scored_activities), 2)
        else:
            target_core_count = min(len(scored_activities), 3 if len(scored_activities) >= 3 else 2)

        # Select core activities covering distinct preferences
        chosen_activities: List[Dict[str, Any]] = []
        for a_entry in scored_activities:
            if len(chosen_activities) >= target_core_count:
                break
            chosen_activities.append(a_entry[0])

        # Distribute core activities across days
        activities_by_day: Dict[int, List[Dict[str, Any]]] = {d: [] for d in range(1, num_days + 1)}
        excursion_days = set()

        if num_days == 2:
            if len(chosen_activities) >= 1:
                activities_by_day[1].append(chosen_activities[0])
            if len(chosen_activities) >= 2:
                activities_by_day[2].append(chosen_activities[1])
        elif num_days >= 3:
            # Day 1: Arrival & check-in
            # Day 2: Primary activity (e.g. nature/cruise)
            if len(chosen_activities) >= 1:
                activities_by_day[2].append(chosen_activities[0])
            # Day 3: Intentional unstructured recovery day after major Day 2 activity
            # Day 4: Second activity (e.g. food walk or excursion)
            if len(chosen_activities) >= 2 and num_days >= 4:
                activities_by_day[4].append(chosen_activities[1])
            elif len(chosen_activities) >= 2 and num_days == 3:
                activities_by_day[3].append(chosen_activities[1])

        # Track which days have an excursion outside base accommodation
        for d_num, acts in activities_by_day.items():
            for act in acts:
                if act.get("location", "").lower() != base_location.lower():
                    excursion_days.add(d_num)

        # 3. Post-Planning Evaluation of Unused Candidates (Soft Optimization)
        # Calculate current estimated total
        current_est_total = 0.0
        if selected_hotel:
            current_est_total += selected_hotel.get("price_per_night", 0) * hotel_nights
        for acts in activities_by_day.values():
            for act in acts:
                current_est_total += act.get("price_per_person", 0) * total_pax

        # Transport estimation
        selected_transport = None
        needs_transport = bool(excursion_days)
        req_text_lower = state.get("request_text", "").lower()
        if "cab" in req_text_lower or "private transport" in req_text_lower or "driver" in req_text_lower:
            needs_transport = True

        if needs_transport and transports:
            for t in transports:
                if t.get("capacity", 4) >= total_pax:
                    selected_transport = t
                    break
            if not selected_transport:
                selected_transport = transports[0]
            t_rate = selected_transport.get("price_per_day") or selected_transport.get("price_flat", 0)
            current_est_total += t_rate * max(1, len(excursion_days))

        remaining_budget = (parsed_req.budget - current_est_total) if (parsed_req and parsed_req.budget) else float("inf")

        # Evaluate unused candidate activities for meaningful incremental value
        scheduled_ids = {item["id"] for acts in activities_by_day.values() for item in acts}
        unused_activities = [a for a in all_activities if a["id"] not in scheduled_ids]

        for cand in unused_activities:
            cand_cost = cand.get("price_per_person", 0) * total_pax
            if cand_cost > remaining_budget:
                continue

            cand_loc = (cand.get("location") or "").lower().strip()
            cand_dur = cand.get("duration_hours", 2)

            # Check if this candidate can be paired with an existing excursion day (same location)
            # without exceeding the daily limit of 7 hours or creating an overloaded day
            compatible_day = None
            for d_num in sorted(excursion_days):
                day_acts = activities_by_day[d_num]
                day_dur = sum(a.get("duration_hours", 3) for a in day_acts)
                all_same_loc = all(a.get("location", "").lower().strip() == cand_loc for a in day_acts)

                if all_same_loc and (day_dur + cand_dur <= 7) and cand_dur <= 3 and len(day_acts) < 2:
                    compatible_day = d_num
                    break

            # If co-located in base location on a lighter day (not Day 3 recovery)
            if not compatible_day and cand_loc == base_location.lower():
                for d_num in range(1, num_days):
                    if d_num == 3 and is_relaxed:
                        continue # Preserve dedicated recovery day
                    day_acts = activities_by_day[d_num]
                    day_dur = sum(a.get("duration_hours", 3) for a in day_acts)
                    if day_dur + cand_dur <= 6 and len(day_acts) < 2:
                        compatible_day = d_num
                        break

            if compatible_day is not None:
                # Assess incremental value: adds new experiential tags on this day, introduces new trip tags, or enriches a single-item day
                cand_tags = {t.lower().replace("-", " ") for t in cand.get("tags", [])}
                day_existing_tags = {t.lower().replace("-", " ") for a in activities_by_day[compatible_day] for t in a.get("tags", [])}
                trip_existing_tags = {t.lower().replace("-", " ") for acts in activities_by_day.values() for a in acts for t in a.get("tags", [])}
                adds_value = bool(cand_tags - day_existing_tags) or bool(cand_tags - trip_existing_tags) or (len(activities_by_day[compatible_day]) == 0)

                cap = cand.get("capacity")
                is_cap_ok = (cap is None or cap >= total_pax)

                if is_cap_ok and adds_value:
                    activities_by_day[compatible_day].append(cand)
                    scheduled_ids.add(cand["id"])
                    remaining_budget -= cand_cost

        # Re-verify excursion days after additions
        excursion_days = set()
        for d_num, acts in activities_by_day.items():
            for act in acts:
                if act.get("location", "").lower() != base_location.lower():
                    excursion_days.add(d_num)

        needs_transport = bool(excursion_days)
        if "cab" in req_text_lower or "private transport" in req_text_lower or "driver" in req_text_lower:
            needs_transport = True

        if needs_transport and not selected_transport and transports:
            for t in transports:
                if t.get("capacity", 4) >= total_pax:
                    selected_transport = t
                    break
            if not selected_transport:
                selected_transport = transports[0]

        # Candidate reasons evaluated upstream by Jev
        candidate_reasons = {}
        jev_decision = state.get("jev_decision")
        if jev_decision:
            for rc in jev_decision.ranked_candidates:
                candidate_reasons[rc.candidate_id] = rc.reason

        # 4. Construct Day-by-Day Itinerary
        days: List[TravelDay] = []

        for day_num in range(1, num_days + 1):
            day_items: List[ItineraryItem] = []

            # Book Hotel on Day 1 for N-1 nights
            if day_num == 1 and selected_hotel:
                h_price = selected_hotel.get("price_per_night", 0)
                h_reason = candidate_reasons.get(
                    selected_hotel["id"],
                    f"Selected accommodation ({selected_hotel['name']}) in {selected_hotel['location']} tailored to party size and requested budget."
                )
                day_items.append(
                    ItineraryItem(
                        catalog_id=selected_hotel["id"],
                        item_type="hotel",
                        name=selected_hotel["name"],
                        location=selected_hotel["location"],
                        quantity=hotel_nights,
                        unit_price=h_price,
                        line_total=h_price * hotel_nights,
                        reason=h_reason
                    )
                )

            # Schedule activities assigned to this day
            scheduled_acts = activities_by_day.get(day_num, [])
            for s_act in scheduled_acts:
                a_price = s_act.get("price_per_person", 0)
                a_reason = candidate_reasons.get(
                    s_act["id"],
                    f"Curated activity ({s_act['name']}) in {s_act['location']} satisfying traveler interests."
                )
                day_items.append(
                    ItineraryItem(
                        catalog_id=s_act["id"],
                        item_type="activity",
                        name=s_act["name"],
                        location=s_act["location"],
                        quantity=total_pax,
                        unit_price=a_price,
                        line_total=a_price * total_pax,
                        reason=a_reason
                    )
                )

            # Book Transport if needed for an excursion on this day
            if selected_transport and (day_num in excursion_days or (not excursion_days and needs_transport and day_num == 1)):
                t_price = selected_transport.get("price_per_day") or selected_transport.get("price_flat", 0)
                t_reason = candidate_reasons.get(
                    selected_transport["id"],
                    f"Dedicated private transit ({selected_transport['name']}) booked for Day {day_num} to facilitate travel between locations."
                )
                day_items.append(
                    ItineraryItem(
                        catalog_id=selected_transport["id"],
                        item_type="transport",
                        name=selected_transport["name"],
                        location=selected_transport["location"],
                        quantity=1,
                        unit_price=t_price,
                        line_total=t_price,
                        reason=t_reason
                    )
                )

            # 5. Intentional Day Explanations (Strictly eliminating unexplained empty days)
            if day_num == 1:
                summary = f"Arrival in {base_location}, check-in at {selected_hotel['name'] if selected_hotel else base_location}, and unhurried local relaxation."
            elif scheduled_acts:
                act_names = " and ".join(a['name'] for a in scheduled_acts)
                act_loc = scheduled_acts[0]['location']
                summary = f"{act_names} in {act_loc}."
            elif day_num == num_days:
                summary = f"Leisurely morning in {base_location} for breakfast and packing, followed by check-out and onward departure."
            elif day_num == 3 and activities_by_day.get(2):
                prev_act_name = activities_by_day[2][0].get('name', 'Day 2 activity')
                summary = f"Dedicated unstructured recovery day following {prev_act_name}, allowing flexible family time and self-guided relaxation at the accommodation."
            else:
                summary = f"Planned leisure day in {base_location} for an unhurried pace, local nature walks, and flexible personal exploration."

            days.append(
                TravelDay(
                    day_number=day_num,
                    summary=summary,
                    items=day_items
                )
            )

        return TravelPlan(
            request_id=state.get("request_id", "REQ"),
            status="success",
            summary=f"{num_days}-day grounded, relaxed itinerary based in {base_location}",
            days=days,
            total_price=0.0,
            currency="INR",
            grounding_status="valid",
            human_review_required=True
        )
