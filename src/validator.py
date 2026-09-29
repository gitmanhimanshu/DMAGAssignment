from enum import Enum
from dataclasses import dataclass, field
import re
from typing import Dict, Any, Tuple, List, Optional, Set
from src.models import TravelPlan
from src.graph_state import ParsedRequest

# Structured error codes for validation layer
class ErrorCode(str, Enum):
    UNKNOWN_CATALOG_ID = "UNKNOWN_CATALOG_ID"
    INVALID_CATALOG_TYPE = "INVALID_CATALOG_TYPE"
    INSUFFICIENT_CAPACITY = "INSUFFICIENT_CAPACITY"
    LOCATION_INCOMPATIBLE = "LOCATION_INCOMPATIBLE"
    MISSING_PRICE_FIELD = "MISSING_PRICE_FIELD"
    PRICE_MISMATCH = "PRICE_MISMATCH"
    LINE_TOTAL_MISMATCH = "LINE_TOTAL_MISMATCH"
    TOTAL_PRICE_MISMATCH = "TOTAL_PRICE_MISMATCH"
    CANDIDATE_NOT_IN_POOL = "CANDIDATE_NOT_IN_POOL"
    UNSUPPORTED_FACTUAL_CLAIM = "UNSUPPORTED_FACTUAL_CLAIM"

VALID_CATALOG_TYPES: Set[str] = {"hotel", "activity", "transport"}

@dataclass
class ValidationError:
    code: ErrorCode
    message: str
    item_id: Optional[str] = None
    details: Dict[str, Any] = field(default_factory=dict)

@dataclass
class ValidationResult:
    is_valid: bool
    errors: List[ValidationError] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)

    @property
    def reason(self) -> str:
        if self.is_valid or not self.errors:
            return "Valid"
        return "; ".join(e.message for e in self.errors)

# Phrases forbidden by grounding and location coherence rules (unverified distance/time claims)
DISALLOWED_TRAVEL_TIME_PHRASES = [
    "30 minutes away",
    "2 hours drive",
    "short drive",
    "long drive",
    "far away",
    "minimizes travel time",
    "without public transit fatigue",
    "zero long-distance travel"
]

def is_compatible_location(
    location: str,
    destination: str,
    catalog: Dict[str, Any],
    item_type: Optional[str] = None
) -> bool:
    """
    Data-driven location compatibility derived purely from catalog contents and destination.
    Supports:
    - Direct matching for local hubs (e.g. location == destination)
    - Regional matching when destination encompasses catalog items
    - Regional transport matching for local hubs
    - Zero matches for unrepresented destinations
    """
    if not destination or not location:
        return False

    dest = destination.lower().strip()
    loc = location.lower().strip()

    suppliers = catalog.get("suppliers", [])
    if not suppliers:
        return loc == dest

    all_locations = {s.get("location", "").lower().strip() for s in suppliers if s.get("location")}
    hotel_locations = {s.get("location", "").lower().strip() for s in suppliers if s.get("type") == "hotel" and s.get("location")}

    # Combine catalog text to find if destination is mentioned anywhere in catalog
    catalog_corpus = " ".join([
        f"{s.get('name', '')} {s.get('location', '')} {s.get('description', '')} {' '.join(s.get('tags', []))}"
        for s in suppliers
    ]).lower()

    # If destination does not appear anywhere in catalog locations or descriptions, it is unrepresented
    if dest not in all_locations and dest not in catalog_corpus:
        return False

    # Check if requested destination is a regional destination encompassing multiple hubs:
    # A regional destination is in the catalog locations/corpus, but is not a single hotel hub.
    is_regional_dest = (dest in all_locations and dest not in hotel_locations) or (dest in catalog_corpus and dest not in hotel_locations)

    if is_regional_dest:
        # Regional destination encompasses items represented in this catalog
        return True

    # Destination is a specific local hub:
    if loc == dest:
        return True

    # Transport can cover local hubs if transport location is regional (not restricted to a different hotel hub)
    if item_type == "transport" and loc not in hotel_locations:
        return True

    return False

def validate_candidate(
    candidate: Dict[str, Any],
    catalog: Dict[str, Any],
    party_size: int = 1,
    destination: Optional[str] = None
) -> ValidationResult:
    """
    Validates a single candidate against catalog schemas and hard constraints:
    1. Catalog ID exists in catalog
    2. Valid catalog type (hotel, activity, transport)
    3. Required price fields exist according to type
    4. Capacity >= party_size (if capacity specified)
    5. Location compatibility with destination (if destination specified)
    """
    errors: List[ValidationError] = []
    suppliers = catalog.get("suppliers", [])
    suppliers_map = {s["id"]: s for s in suppliers}

    cid = candidate.get("id")
    if not cid or cid not in suppliers_map:
        errors.append(ValidationError(
            code=ErrorCode.UNKNOWN_CATALOG_ID,
            message=f"Candidate ID '{cid}' does not exist in catalog.",
            item_id=cid
        ))
        return ValidationResult(is_valid=False, errors=errors)

    supplier = suppliers_map[cid]
    ctype = candidate.get("type") or supplier.get("type")

    # 2. Type validation
    if ctype not in VALID_CATALOG_TYPES or ctype != supplier.get("type"):
        errors.append(ValidationError(
            code=ErrorCode.INVALID_CATALOG_TYPE,
            message=f"Candidate {cid} has invalid type '{ctype}'. Expected one of {sorted(VALID_CATALOG_TYPES)}.",
            item_id=cid
        ))

    # 3. Schema price validation
    if ctype == "hotel":
        if "price_per_night" not in supplier or supplier["price_per_night"] is None:
            errors.append(ValidationError(
                code=ErrorCode.MISSING_PRICE_FIELD,
                message=f"Hotel candidate {cid} is missing required 'price_per_night' field.",
                item_id=cid
            ))
    elif ctype == "activity":
        if "price_per_person" not in supplier or supplier["price_per_person"] is None:
            errors.append(ValidationError(
                code=ErrorCode.MISSING_PRICE_FIELD,
                message=f"Activity candidate {cid} is missing required 'price_per_person' field.",
                item_id=cid
            ))
    elif ctype == "transport":
        if ("price_per_day" not in supplier or supplier["price_per_day"] is None) and \
           ("price_flat" not in supplier or supplier["price_flat"] is None):
            errors.append(ValidationError(
                code=ErrorCode.MISSING_PRICE_FIELD,
                message=f"Transport candidate {cid} is missing required 'price_per_day' or 'price_flat' field.",
                item_id=cid
            ))

    # 4. Capacity validation (if capacity is present)
    cap = supplier.get("capacity")
    if cap is not None and cap < party_size:
        errors.append(ValidationError(
            code=ErrorCode.INSUFFICIENT_CAPACITY,
            message=f"Candidate {cid} capacity ({cap}) is less than required party size ({party_size}).",
            item_id=cid,
            details={"capacity": cap, "party_size": party_size}
        ))

    # 5. Destination/location compatibility
    if destination:
        loc = supplier.get("location", "")
        if not is_compatible_location(loc, destination, catalog, ctype):
            errors.append(ValidationError(
                code=ErrorCode.LOCATION_INCOMPATIBLE,
                message=f"Candidate {cid} location '{loc}' is incompatible with requested destination '{destination}'.",
                item_id=cid,
                details={"location": loc, "destination": destination}
            ))

    return ValidationResult(is_valid=len(errors) == 0, errors=errors)

def validate_candidate_pool(
    candidates: List[Dict[str, Any]],
    catalog: Dict[str, Any],
    party_size: int = 1,
    destination: Optional[str] = None
) -> Tuple[List[Dict[str, Any]], ValidationResult]:
    """
    Validates a candidate pool, filtering out candidates that violate hard constraints.
    Returns (valid_candidates, validation_result).
    """
    valid_candidates = []
    all_errors: List[ValidationError] = []

    for c in candidates:
        res = validate_candidate(c, catalog, party_size, destination)
        if res.is_valid:
            valid_candidates.append(c)
        else:
            all_errors.extend(res.errors)

    is_valid = len(all_errors) == 0
    return valid_candidates, ValidationResult(is_valid=is_valid, errors=all_errors)

def validate_pricing(plan: TravelPlan, catalog: Dict[str, Any]) -> ValidationResult:
    """
    Validates line totals and grand total deterministically:
    1. Schema price matches catalog unit price
    2. expected_line_total = unit_price * quantity
    3. expected_total = sum(item.line_total for all items)
    """
    if plan.status != "success":
        return ValidationResult(is_valid=True)

    errors: List[ValidationError] = []
    suppliers_map = {s["id"]: s for s in catalog.get("suppliers", [])}
    calculated_total = 0.0

    for day in plan.days:
        for item in day.items:
            cid = item.catalog_id
            if cid not in suppliers_map:
                continue

            supplier = suppliers_map[cid]
            ctype = supplier.get("type")
            if ctype == "hotel":
                expected_unit = supplier.get("price_per_night", 0)
            elif ctype == "activity":
                expected_unit = supplier.get("price_per_person", 0)
            elif ctype == "transport":
                expected_unit = supplier.get("price_per_day", supplier.get("price_flat", 0))
            else:
                expected_unit = 0

            # Unit price check
            if item.unit_price != expected_unit:
                errors.append(ValidationError(
                    code=ErrorCode.PRICE_MISMATCH,
                    message=f"Price mismatch for {cid}: expected {expected_unit}, got {item.unit_price}",
                    item_id=cid,
                    details={"expected": expected_unit, "actual": item.unit_price}
                ))

            # Line total check
            expected_line = item.unit_price * item.quantity
            if item.line_total != expected_line:
                errors.append(ValidationError(
                    code=ErrorCode.LINE_TOTAL_MISMATCH,
                    message=f"Line total mismatch for {cid}: expected {expected_line}, got {item.line_total}",
                    item_id=cid,
                    details={"expected": expected_line, "actual": item.line_total}
                ))

            calculated_total += item.line_total

    # Grand total check
    if round(plan.total_price, 2) != round(calculated_total, 2):
        errors.append(ValidationError(
            code=ErrorCode.TOTAL_PRICE_MISMATCH,
            message=f"Total price mismatch: expected {calculated_total}, got {plan.total_price}",
            details={"expected": calculated_total, "actual": plan.total_price}
        ))

    return ValidationResult(is_valid=len(errors) == 0, errors=errors)

def validate_grounding(
    plan: TravelPlan,
    catalog: Dict[str, Any],
    planner_candidate_ids: Optional[List[str]] = None
) -> ValidationResult:
    """
    Validates catalog grounding, candidate pool membership, and factual claims:
    1. Every catalog ID exists in suppliers
    2. Item type matches supplier type
    3. Every item belongs to planner_candidate_ids (if provided)
    4. Reasons do not contain unverified factual claims (disallowed phrases)
    """
    if plan.status != "success":
        return ValidationResult(is_valid=True)

    errors: List[ValidationError] = []
    suppliers_map = {s["id"]: s for s in catalog.get("suppliers", [])}
    allowed_pool = set(planner_candidate_ids) if planner_candidate_ids is not None else None

    for day in plan.days:
        for item in day.items:
            cid = item.catalog_id

            # 1. Catalog ID exists
            if cid not in suppliers_map:
                errors.append(ValidationError(
                    code=ErrorCode.UNKNOWN_CATALOG_ID,
                    message=f"Fabricated catalog ID used: {cid}",
                    item_id=cid
                ))
                continue

            supplier = suppliers_map[cid]

            # 2. Type matches
            if supplier.get("type") != item.item_type:
                errors.append(ValidationError(
                    code=ErrorCode.INVALID_CATALOG_TYPE,
                    message=f"Type mismatch for {cid}: expected {supplier.get('type')}, got {item.item_type}",
                    item_id=cid
                ))

            # 3. Candidate pool membership
            if allowed_pool is not None and cid not in allowed_pool:
                errors.append(ValidationError(
                    code=ErrorCode.CANDIDATE_NOT_IN_POOL,
                    message=f"Candidate {cid} used in itinerary is not in candidate pool",
                    item_id=cid
                ))

            # 4. Factual claim check
            reason_lower = (item.reason or "").lower()
            for phrase in DISALLOWED_TRAVEL_TIME_PHRASES:
                if phrase in reason_lower:
                    errors.append(ValidationError(
                        code=ErrorCode.UNSUPPORTED_FACTUAL_CLAIM,
                        message=f"Grounding violation in {cid}: Contains unverified travel claim '{phrase}'.",
                        item_id=cid
                    ))

    return ValidationResult(is_valid=len(errors) == 0, errors=errors)

def validate_itinerary_coherence(
    plan: TravelPlan,
    parsed_req: ParsedRequest,
    profile: Dict[str, Any],
    catalog: Dict[str, Any],
    candidate_pool_ids: Optional[List[str]] = None
) -> Tuple[bool, List[str]]:
    """
    Deterministic itinerary-level validation checking:
    1. Grounding: All catalog IDs belong to verified inventory.
    2. Hotel consistency: Nights match trip duration (N-1) and capacity matches party.
    3. Destination coherence: Activities outside hotel base require transport; no ungrounded claims.
    4. Relaxed pace: Fewer packed days, daily duration <= 7 hours, max 1-2 activities/day.
    5. Past feedback compliance: Respect drive aversion and pace preferences.
    6. Preference coverage: Nature, local food, hiking, etc. represented when feasible.
    7. Transport necessity: Transport only included when useful for movement or requested.
    8. Candidate utilization: Avoid excessive unexplained empty days when strong items exist.
    """
    if plan.status != "success" or not plan.days:
        return True, ["Unfulfillable or empty plan, coherence check skipped."]

    suppliers_map = {s["id"]: s for s in catalog.get("suppliers", [])}
    notes = []
    is_valid = True

    total_pax = parsed_req.party_adults + parsed_req.party_children
    is_relaxed = (parsed_req.pace == "relaxed" or "relaxed" in [p.lower() for p in profile.get("preferences", [])])
    past_feedback_text = " ".join([t.get("feedback", "").lower() for t in profile.get("past_trips", [])])
    dislikes_long_drives = ("long drives" in past_feedback_text or "bored on" in past_feedback_text)

    # Collect hotel information
    hotel_locations = set()
    total_hotel_nights = 0
    booked_hotels = []
    for day in plan.days:
        for item in day.items:
            if item.item_type == "hotel":
                hotel_locations.add(item.location.lower().strip())
                total_hotel_nights += item.quantity
                booked_hotels.append(item)

    # 1. Hotel consistency & Capacity
    expected_nights = max(1, parsed_req.num_days - 1)
    if total_hotel_nights > 0 and total_hotel_nights < expected_nights:
        is_valid = False
        notes.append(f"Hotel nights ({total_hotel_nights}) less than expected duration ({expected_nights} nights).")

    for h_item in booked_hotels:
        s_data = suppliers_map.get(h_item.catalog_id, {})
        cap = s_data.get("capacity")
        if cap is not None and cap < total_pax:
            is_valid = False
            notes.append(f"Hotel {h_item.catalog_id} capacity ({cap}) is insufficient for party of {total_pax}.")

    # Collect transport information
    has_trip_transport = False
    transport_days = set()
    booked_transports = []
    for day in plan.days:
        for item in day.items:
            if item.item_type == "transport":
                has_trip_transport = True
                transport_days.add(day.day_number)
                booked_transports.append(item)
                s_data = suppliers_map.get(item.catalog_id, {})
                cap = s_data.get("capacity")
                if cap is not None and cap < total_pax:
                    is_valid = False
                    notes.append(f"Transport {item.catalog_id} capacity ({cap}) is insufficient for party of {total_pax}.")

    # 2. Destination coherence (Generic, no hardcoded cities)
    # If an activity is in a different location than the accommodation, dedicated transport is required.
    for day in plan.days:
        day_has_transport = (day.day_number in transport_days) or has_trip_transport
        acts = [item for item in day.items if item.item_type == "activity"]
        for act in acts:
            act_loc = (act.location or "").lower().strip()
            # If hotel is established and activity location differs from hotel location
            if hotel_locations and act_loc not in hotel_locations and not day_has_transport:
                is_valid = False
                notes.append(f"Destination incoherence: Activity in {act.location} scheduled while based in {list(hotel_locations)[0].capitalize()} without dedicated transport.")
        # If day has activities across multiple different non-hotel locations, flag split-destination incoherence
        non_hotel_locs = {(a.location or "").lower().strip() for a in acts if (a.location or "").lower().strip() not in hotel_locations}
        if len(non_hotel_locs) > 1:
            is_valid = False
            notes.append(f"Day {day.day_number}: Destination incoherence with activities across multiple distant locations.")

    # 3. Relaxed pace and daily duration check
    for day in plan.days:
        acts = [item for item in day.items if item.item_type == "activity"]
        daily_duration = 0
        for act in acts:
            s_data = suppliers_map.get(act.catalog_id, {})
            daily_duration += s_data.get("duration_hours", 3)

        if daily_duration > 7:
            is_valid = False
            notes.append(f"Day {day.day_number}: Activity duration ({daily_duration}h) exceeds maximum daily limit of 7 hours.")

        if is_relaxed and len(acts) > 2:
            is_valid = False
            notes.append(f"Day {day.day_number}: Too many activities ({len(acts)}) scheduled for requested relaxed pace.")

    # 4. Past feedback check (avoid long drives & excessive switching)
    if "long drives" in past_feedback_text:
        day_locs = []
        for day in plan.days:
            for item in day.items:
                if item.item_type == "activity" and item.location:
                    day_locs.append(item.location.lower().strip())
        switches = sum(1 for i in range(len(day_locs) - 1) if day_locs[i] != day_locs[i+1])
        if switches > 2:
            is_valid = False
            notes.append("Itinerary violates past feedback: excessive city switches conflict with 'kids got bored on long drives'.")

    # 5. Preference satisfaction (generic tag and keyword matching)
    all_catalog_ids = [item.catalog_id for day in plan.days for item in day.items]
    all_tags = []
    for cid in all_catalog_ids:
        s_data = suppliers_map.get(cid, {})
        all_tags.extend([t.lower().replace("-", " ") for t in s_data.get("tags", [])])

    interests_normalized = [i.lower().replace("-", " ").strip() for i in parsed_req.interests]
    for interest in interests_normalized:
        tokens = interest.split()
        if not any(any(tok in tag for tok in tokens) for tag in all_tags):
            notes.append(f"Preference issue: Requested interest '{interest}' has no matching representation in booked items.")

    # 6. Check for disallowed unverified travel time claims in reasons
    for day in plan.days:
        for item in day.items:
            reason_lower = item.reason.lower()
            for phrase in DISALLOWED_TRAVEL_TIME_PHRASES:
                if phrase in reason_lower:
                    is_valid = False
                    notes.append(f"Grounding violation in {item.catalog_id}: Contains unverified travel claim '{phrase}'.")

    # 7. Candidate under-utilization & unexplained empty days check
    all_booked_acts = [item for day in plan.days for item in day.items if item.item_type == "activity"]
    if parsed_req.num_days >= 4 and len(all_booked_acts) <= 1:
        # Check if candidate pool had multiple strong activities
        notes.append("Under-utilization issue: Only 1 activity scheduled across multi-day trip when additional candidate activities were available.")

    # Check for consecutive empty days without intentional explanation
    empty_day_count = 0
    for day in plan.days:
        if not day.items:
            empty_day_count += 1
            summary_lower = day.summary.lower()
            if len(summary_lower) < 15 or "day" == summary_lower.strip():
                notes.append(f"Day {day.day_number}: Empty leisure day lacks clear intentional explanation.")
        else:
            empty_day_count = 0
        if empty_day_count >= 3:
            notes.append("Excessive empty days: 3 consecutive days have no scheduled items.")

    # 8. Transport necessity check
    # If transport is included for all days when all activities are in the exact same town as the hotel
    if booked_transports and hotel_locations:
        all_act_locs = {item.location.lower().strip() for day in plan.days for item in day.items if item.item_type == "activity" and item.location}
        is_budget_conscious = (parsed_req.budget_level == "budget" or "budget" in [p.lower() for p in profile.get("preferences", [])])
        # If all activities are co-located with hotel and request didn't specifically ask for private cab
        if all_act_locs and all_act_locs.issubset(hotel_locations) and is_budget_conscious:
            notes.append("Transport necessity warning: Dedicated private cab included when all activities and stay are co-located in a budget-conscious request.")

    # 9. Duplicate activity usage
    seen_activities = set()
    for day in plan.days:
        for item in day.items:
            if item.item_type == "activity":
                if item.catalog_id in seen_activities:
                    is_valid = False
                    notes.append(f"Duplicate activity: {item.catalog_id} is scheduled multiple times.")
                seen_activities.add(item.catalog_id)

    # 10. Budget check (if numeric budget provided)
    if parsed_req.budget is not None and plan.total_price > parsed_req.budget:
        is_valid = False
        notes.append(f"Budget exceeded: Itinerary total (Rs. {plan.total_price}) exceeds requested budget (Rs. {parsed_req.budget}).")

    if is_valid and not notes:
        notes.append("Itinerary coherence, relaxed pace, and preference validations passed.")

    return is_valid, notes

def validate_itinerary(
    plan: TravelPlan,
    catalog: Dict[str, Any],
    planner_candidate_ids: Optional[List[str]] = None
) -> Tuple[bool, str]:
    """
    Strict catalog grounding and mathematical verification:
    1. Every catalog ID exists in suppliers
    2. Item types match catalog
    3. Unit prices match catalog exactly
    4. Line totals match unit_price * quantity
    5. Grand total equals exact sum of line totals
    6. All items belong to planner_candidate_ids (if provided)
    """
    if plan.status != "success":
        return True, "No itinerary to validate"

    # 1. Grounding validation (IDs, types, candidate pool membership, factual claims)
    grounding_res = validate_grounding(plan, catalog, planner_candidate_ids=planner_candidate_ids)
    if not grounding_res.is_valid:
        return False, grounding_res.reason

    # 2. Deterministic pricing validation (unit prices, line totals, total sum)
    pricing_res = validate_pricing(plan, catalog)
    if not pricing_res.is_valid:
        return False, pricing_res.reason

    return True, "Valid"

def generate_human_review_reasons(
    plan: TravelPlan,
    catalog: Dict[str, Any],
    profile: Dict[str, Any],
    parsed_req: Optional[ParsedRequest] = None
) -> List[str]:
    """
    Generates data-driven reasons why human review is required.
    Strictly factual based on catalog metadata, traveler profile, and request constraints.
    """
    reasons: List[str] = []
    suppliers_map = {s["id"]: s for s in catalog.get("suppliers", [])}

    # 1. Unfulfillable check
    if plan.status == "unfulfillable":
        reasons.append("This request cannot be fulfilled from the provided supplier catalog because no matching inventory is available for the requested destination.")
        return reasons

    # 2. Check booked activities for physical difficulty / trekking suitability
    for day in plan.days:
        for item in day.items:
            if item.item_type == "activity":
                s_data = suppliers_map.get(item.catalog_id, {})
                desc = s_data.get("description", "")
                desc_lower = desc.lower()

                # If activity is moderate difficulty trek without age/fitness info in catalog
                if "moderate difficulty" in desc_lower:
                    reasons.append(f"{item.catalog_id} is a moderate-difficulty trek, but the catalog does not specify age or fitness suitability; confirm suitability before booking.")
                elif "difficulty" in desc_lower:
                    reasons.append(f"{item.catalog_id} includes physical difficulty considerations, but the catalog does not specify age or fitness suitability; confirm suitability before booking.")

    # 3. Inter-location travel logistics check when itinerary spans multiple locations
    booked_locations = {item.location.strip().title() for day in plan.days for item in day.items if item.location and item.item_type != "transport"}
    if len(booked_locations) > 1:
        locs_str = ", ".join(sorted(booked_locations))
        reasons.append(f"The itinerary spans multiple locations ({locs_str}) and the supplier catalog does not provide inter-location travel times or transit schedules; confirm travel logistics before booking.")

    # 4. Budget threshold check
    if plan.budget_summary and plan.budget_summary.budget_status == "over_budget":
        reasons.append(f"Itinerary total exceeds the requested budget by Rs. {abs(plan.budget_summary.remaining_budget)}.")

    # 5. Generic availability confirmation (applies to all itineraries since catalog lacks live availability)
    reasons.append("Final availability and booking details are not provided by the supplier catalog and require human confirmation.")

    return reasons

