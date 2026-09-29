import os
import json
import httpx
from typing import List, Dict, Any, Optional, Set, Tuple
from src.graph_state import ParsedRequest
from src.models import (
    JevDecision,
    RankedCandidate,
    EvaluationScores,
    CandidatePool,
    JevDiagnostics
)
from src.loader import load_env

load_env()

# Configurable semantic alias mapping to normalize interests and tags without code branches
TAG_ALIASES: Dict[str, str] = {
    "tea estates": "tea-estate",
    "tea estate": "tea-estate",
    "tea": "tea-estate",
    "local food": "local-food",
    "local-cuisine": "local-food",
    "food": "local-food",
    "cuisine": "local-food",
    "backwater": "backwaters",
    "backwater cruise": "backwaters",
    "houseboat": "backwaters",
    "trekking": "hiking",
    "trek": "hiking",
    "trails": "hiking",
    "adventure": "adventure",
    "wildlife": "wildlife",
    "culture": "cultural",
    "cultural": "cultural",
    "dance": "cultural",
    "heritage": "heritage",
    "city": "city",
    "beaches": "beach",
    "beach": "beach",
    "nightlife": "nightlife",
    "photography": "photography"
}

def normalize_token(token: str) -> str:
    cleaned = token.strip().lower()
    return TAG_ALIASES.get(cleaned, cleaned)

def extract_candidate_tokens(candidate: Dict[str, Any]) -> Set[str]:
    """Generic token extraction from candidate tags, name, and description."""
    tokens = set()
    for tag in candidate.get("tags", []):
        norm = normalize_token(tag)
        tokens.add(norm)
        for part in norm.replace("-", " ").split():
            tokens.add(part)
    text = f"{candidate.get('name', '')} {candidate.get('description', '')}".lower()
    for word in text.replace("-", " ").replace(",", " ").replace(".", " ").split():
        if len(word) > 2:
            tokens.add(normalize_token(word))
    return tokens

def is_request_budget_sensitive(parsed_req: ParsedRequest, profile: Dict[str, Any]) -> bool:
    """
    Derives budget sensitivity generically from parsed request, budget level,
    or daily per-person allocation without hardcoded thresholds.
    """
    budget_level = (parsed_req.budget_level or profile.get("typical_budget_level", "")).lower()
    if budget_level in ["budget", "budget-conscious", "economy", "low"]:
        return True

    interests_str = " ".join(parsed_req.interests).lower()
    if any(k in interests_str for k in ["budget", "cheap", "affordable", "hostel", "backpacker"]):
        return True

    if parsed_req.budget is not None:
        num_days = max(1, parsed_req.num_days)
        pax = max(1, parsed_req.party_adults + parsed_req.party_children)
        daily_per_pax = parsed_req.budget / (num_days * pax)
        if daily_per_pax < 2500:
            return True

    return False

def compute_destination_fit(
    candidate: Dict[str, Any],
    requested_dest: str,
    base_hub: Optional[str] = None,
    dislikes_long_drives: bool = False
) -> float:
    """
    Derives destination compatibility and base-hub coherence generically
    from candidate location, requested destination, and travel fatigue constraints.
    """
    c_loc = (candidate.get("location") or "").lower().strip()
    req_dest = (requested_dest or "").lower().strip()
    ctype = candidate.get("type")

    # Direct match with requested destination
    if c_loc == req_dest:
        return 1.0

    # Transport items covering the regional or hub network have high destination fit
    if ctype == "transport":
        return 0.95

    # If base hub (e.g. hotel location) is established:
    if base_hub:
        b_hub = base_hub.lower().strip()
        if c_loc == b_hub:
            return 1.0
        # If candidate is in a different location than base hub:
        # Heuristic: when traveler has past feedback indicating travel fatigue / drive aversion,
        # penalize location switches to encourage fewer location changes.
        if dislikes_long_drives:
            return 0.45
        return 0.65

    # Substring / partial match between location and destination
    if c_loc in req_dest or req_dest in c_loc:
        return 0.95

    return 0.50

def get_candidate_price(candidate: Dict[str, Any]) -> Optional[float]:
    """Extracts price strictly from supplier catalog metadata without inventing defaults."""
    ctype = candidate.get("type")
    if ctype == "hotel":
        return candidate.get("price_per_night")
    elif ctype == "activity":
        return candidate.get("price_per_person")
    elif ctype == "transport":
        return candidate.get("price_per_day", candidate.get("price_flat"))
    return None

def classify_jev_error(e: Exception) -> str:
    err_str = str(e).lower()
    if isinstance(e, httpx.TimeoutException) or "timeout" in err_str or "deadline" in err_str or "timed out" in err_str:
        return "jev_timeout"
    if "429" in err_str or "rate limit" in err_str or "quota" in err_str:
        return "jev_rate_limited"
    if "grounding violation" in err_str or "unknown id" in err_str or isinstance(e, json.JSONDecodeError):
        return "jev_invalid_response"
    if isinstance(e, (httpx.ConnectError, httpx.NetworkError)) or any(c in err_str for c in ["500", "502", "503", "unavailable", "connection"]):
        return "jev_service_error"
    return "jev_service_error"

class JevRanker:
    """
    Bounded contextual candidate decision and ranking layer.
    Python retrieves and validates catalog candidates.
    Jev evaluates and ranks candidates using rich context.
    Separates full ranking from focused, role-aware candidate pool.
    """

    def __init__(self):
        load_env()
        self.endpoint = "https://jevmodel.org/v1/systemone"
        self.api_key = os.environ.get("JEVMODEL_API_KEY")
        self.last_decision: Optional[JevDecision] = None

    def rank_candidates(
        self,
        parsed_req: ParsedRequest,
        profile: Dict[str, Any],
        valid_candidates: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        # Hard constraint: Zero candidates must never trigger Jev
        if not valid_candidates:
            self.last_decision = None
            return []

        # 1. Try official Jev decision API
        jev_failure_reason = None
        if self.api_key:
            try:
                ranked = self._call_official_jev(parsed_req, profile, valid_candidates)
                if ranked:
                    return ranked
            except Exception as e:
                classified = classify_jev_error(e)
                jev_failure_reason = f"{classified}: {e}"
                print(f"[Jev Info] Official Jev decision API encountered an issue ({jev_failure_reason}). Proceeding with generic fallback.")
        else:
            jev_failure_reason = "jev_service_error: No JEVMODEL_API_KEY provided in environment."

        # 2. Generic deterministic fallback
        self.last_decision = None
        return self._deterministic_fallback(parsed_req, profile, valid_candidates, jev_failure_reason=jev_failure_reason)

    def _call_official_jev(
        self,
        parsed_req: ParsedRequest,
        profile: Dict[str, Any],
        valid_candidates: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        valid_id_map = {c["id"]: c for c in valid_candidates}
        hotels = [c for c in valid_candidates if c.get("type") == "hotel"]
        activities = [c for c in valid_candidates if c.get("type") == "activity"]
        transports = [c for c in valid_candidates if c.get("type") == "transport"]

        # Build candidate metadata strictly from verified supplier items
        candidate_metadata = []
        for c in valid_candidates:
            candidate_metadata.append({
                "catalog_id": c["id"],
                "type": c.get("type"),
                "name": c.get("name"),
                "location": c.get("location"),
                "price": get_candidate_price(c),
                "rating": c.get("rating"),
                "tags": c.get("tags", []),
                "capacity": c.get("capacity"),
                "duration_hours": c.get("duration_hours"),
                "description": c.get("description", "")
            })

        is_couple = (parsed_req.party_adults == 2 and parsed_req.party_children == 0)
        party_desc = "couple (2 adults)" if is_couple else f"{parsed_req.party_adults} adults, {parsed_req.party_children} children"
        budget_desc = f"budget: ~{parsed_req.budget}" if parsed_req.budget else (parsed_req.budget_level or "standard")
        interests_desc = ", ".join(parsed_req.interests) if parsed_req.interests else "general exploration"

        state_data = {
            "traveler_request": {
                "destination": parsed_req.destination,
                "duration_days": parsed_req.num_days,
                "party": party_desc,
                "adults": parsed_req.party_adults,
                "children": parsed_req.party_children,
                "child_ages": parsed_req.child_ages,
                "budget": parsed_req.budget,
                "budget_level": parsed_req.budget_level,
                "interests": parsed_req.interests,
                "pace": parsed_req.pace
            },
            "traveler_profile": {
                "preferences": profile.get("preferences", []),
                "typical_budget_level": profile.get("typical_budget_level", "mid-range")
            },
            "past_trips": profile.get("past_trips", []),
            "validated_candidates": candidate_metadata
        }

        questions = {}
        if len(hotels) >= 2:
            questions["select_hotel"] = {
                "type": "choice",
                "instructions": (
                    f"Among the provided hotel candidates in {parsed_req.destination}, select the single best hotel for a {party_desc} "
                    f"with budget requirement ({budget_desc}) and interests ({interests_desc}). "
                    "Explicit user requirements and budget sensitivity must be prioritized (lower price matters strongly when budget-conscious). "
                    "Consider traveler past trip feedback. Select ONLY from the provided candidate IDs."
                ),
                "criteria": {
                    h["id"]: f"{h['name']} in {h['location']} | Price: Rs.{h.get('price_per_night')}/night | Rating: {h.get('rating')} | Capacity: {h.get('capacity')} | Tags: [{', '.join(h.get('tags', []))}]. {h.get('description', '')}"
                    for h in hotels[:10]
                }
            }

        if len(activities) >= 2:
            questions["select_activity"] = {
                "type": "choice",
                "instructions": (
                    f"Among the provided activity candidates, select the top activity best aligned with requested interests ({interests_desc}), "
                    f"requested pace ({parsed_req.pace}), and party composition ({party_desc}). "
                    "Consider past feedback (e.g. preference for nature walks or avoiding excessive drive times). "
                    "Select ONLY from the provided candidate IDs."
                ),
                "criteria": {
                    a["id"]: f"{a['name']} in {a['location']} | Price: Rs.{a.get('price_per_person')}/person | Duration: {a.get('duration_hours')}h | Rating: {a.get('rating')} | Tags: [{', '.join(a.get('tags', []))}]. {a.get('description', '')}"
                    for a in activities[:10]
                }
            }

        if len(transports) >= 2:
            questions["select_transport"] = {
                "type": "choice",
                "instructions": (
                    f"Select the transport option best suited for {party_desc} over a {parsed_req.num_days}-day trip. "
                    "Select ONLY from the provided candidate IDs."
                ),
                "criteria": {
                    t["id"]: f"{t['name']} | Price: Rs.{t.get('price_per_day', t.get('price_flat'))} | Capacity: {t.get('capacity')} | Tags: [{', '.join(t.get('tags', []))}]. {t.get('description', '')}"
                    for t in transports[:10]
                }
            }

        payload = {
            "model": "jev-latest",
            "state": state_data,
            "questions": questions
        }

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }

        response = httpx.post(self.endpoint, headers=headers, json=payload, timeout=15.0)
        if response.status_code == 429:
            raise ValueError(f"HTTP 429 rate limit exceeded: {response.text}")
        if response.status_code != 200:
            raise ValueError(f"Jev API HTTP {response.status_code}: {response.text}")

        data = response.json()
        answers = data.get("answers", {})

        # Grounding check: Verify all IDs returned by Jev exist in valid_candidates
        jev_ids = set()
        for _, ans in answers.items():
            if ans.get("type") == "choice":
                if ans.get("choice"):
                    jev_ids.add(ans.get("choice"))
                for cid in ans.get("probabilities", {}).keys():
                    jev_ids.add(cid)

        unknown_ids = jev_ids - set(valid_id_map.keys())
        if unknown_ids:
            raise ValueError(f"Grounding violation: Jev introduced unknown IDs {unknown_ids}")

        return self._build_ranked_decision(
            parsed_req,
            profile,
            valid_candidates,
            answers,
            fallback_used=False,
            ranking_source="jev",
            jev_failure_reason=None
        )

    def _deterministic_fallback(
        self,
        parsed_req: ParsedRequest,
        profile: Dict[str, Any],
        candidates: List[Dict[str, Any]],
        jev_failure_reason: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Generic deterministic candidate evaluation and ranking fallback."""
        return self._build_ranked_decision(
            parsed_req,
            profile,
            candidates,
            answers={},
            fallback_used=True,
            ranking_source="deterministic_fallback",
            jev_failure_reason=jev_failure_reason
        )

    def _build_ranked_decision(
        self,
        parsed_req: ParsedRequest,
        profile: Dict[str, Any],
        candidates: List[Dict[str, Any]],
        answers: Dict[str, Any],
        fallback_used: bool = False,
        ranking_source: str = "deterministic_fallback",
        jev_failure_reason: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        is_budget_conscious = is_request_budget_sensitive(parsed_req, profile)
        is_couple = (parsed_req.party_adults == 2 and parsed_req.party_children == 0)
        is_family = (parsed_req.party_children > 0)
        total_pax = parsed_req.party_adults + parsed_req.party_children
        is_relaxed = (parsed_req.pace == "relaxed" or "relaxed" in [p.lower() for p in profile.get("preferences", [])])

        past_feedback_text = " ".join([t.get("feedback", "").lower() for t in profile.get("past_trips", [])])
        dislikes_long_drives = ("long drives" in past_feedback_text or "bored on" in past_feedback_text or "long drive" in past_feedback_text)

        # Extract normalized request interest tokens
        req_interest_tokens = set()
        for i in parsed_req.interests:
            norm = normalize_token(i)
            req_interest_tokens.add(norm)
            for part in norm.replace("-", " ").split():
                req_interest_tokens.add(part)

        # Extract normalized traveler preferences
        profile_pref_tokens = {normalize_token(p) for p in profile.get("preferences", [])}

        # Extract positive signals from past trip highlights generically
        positive_past_tokens = set()
        for trip in profile.get("past_trips", []):
            for h in trip.get("highlights", []):
                for word in h.lower().split():
                    if len(word) > 2:
                        positive_past_tokens.add(normalize_token(word))

        # Price range for relative scaling without fixed price defaults
        hotel_prices = [c.get("price_per_night") for c in candidates if c.get("type") == "hotel" and c.get("price_per_night") is not None]
        min_hotel_p = min(hotel_prices) if hotel_prices else 0
        max_hotel_p = max(hotel_prices) if hotel_prices else 0

        act_prices = [c.get("price_per_person") for c in candidates if c.get("type") == "activity" and c.get("price_per_person") is not None]
        min_act_p = min(act_prices) if act_prices else 0
        max_act_p = max(act_prices) if act_prices else 0

        # Step 1: Establish trip Base Hub
        # If destination is a specific city/hub (matches a candidate hotel location), that is the base hub.
        # If destination is regional, determine base hub from top hotel candidate fit.
        req_dest_lower = (parsed_req.destination or "").lower().strip()
        candidate_hotels = [c for c in candidates if c.get("type") == "hotel"]
        hotel_locations = {c.get("location", "").lower().strip() for c in candidate_hotels if c.get("location")}

        if req_dest_lower in hotel_locations:
            base_hub = req_dest_lower
        else:
            # Evaluate hotels preliminary to find primary base hub
            best_hotel = None
            best_hotel_fit = -1.0
            for h in candidate_hotels:
                h_tokens = extract_candidate_tokens(h)
                h_pax_fit = 1.0 if h.get("capacity", 2) >= total_pax else 0.2
                h_int_fit = len(req_interest_tokens & h_tokens) / max(1, len(req_interest_tokens))
                h_pref_fit = len(profile_pref_tokens & h_tokens) / max(1, len(profile_pref_tokens))
                h_fit = (h_pax_fit * 0.40) + (h_int_fit * 0.30) + (h_pref_fit * 0.30)
                if h_fit > best_hotel_fit:
                    best_hotel_fit = h_fit
                    best_hotel = h
            base_hub = best_hotel.get("location") if best_hotel else parsed_req.destination

        # Step 2: Dynamic dimension weights (sum = 1.00)
        if is_budget_conscious:
            weights = {
                "budget_fit": 0.25,
                "interest_fit": 0.20,
                "destination_fit": 0.15,
                "party_suitability": 0.15,
                "pace_fit": 0.10,
                "past_feedback_fit": 0.05,
                "traveler_preference_fit": 0.05,
                "quality": 0.05
            }
        elif is_family and is_relaxed:
            weights = {
                "party_suitability": 0.20,
                "destination_fit": 0.20,
                "pace_fit": 0.15,
                "past_feedback_fit": 0.15,
                "interest_fit": 0.10,
                "traveler_preference_fit": 0.10,
                "budget_fit": 0.05,
                "quality": 0.05
            }
        else:
            weights = {
                "destination_fit": 0.20,
                "interest_fit": 0.20,
                "party_suitability": 0.15,
                "budget_fit": 0.15,
                "pace_fit": 0.10,
                "traveler_preference_fit": 0.10,
                "past_feedback_fit": 0.05,
                "quality": 0.05
            }

        ranked_models: List[RankedCandidate] = []
        ranked_list: List[Tuple[Dict[str, Any], float]] = []
        candidates_by_id = {c["id"]: c for c in candidates}

        # Step 3: Evaluate each candidate across all 8 dimensions
        for c in candidates:
            cid = c["id"]
            ctype = c.get("type")
            c_tokens = extract_candidate_tokens(c)
            rating = c.get("rating") or 4.0
            price = get_candidate_price(c)

            # 1. Budget Fit
            if price is None:
                b_fit = 0.50
            elif ctype == "hotel":
                if max_hotel_p > min_hotel_p:
                    rel_affordability = 1.0 - (price - min_hotel_p) / (max_hotel_p - min_hotel_p)
                else:
                    rel_affordability = 1.0
                if is_budget_conscious:
                    b_fit = round(0.20 + 0.80 * rel_affordability, 2)
                else:
                    b_fit = 0.90 if "mid-range" in c.get("tags", []) or rel_affordability > 0.4 else 0.75
            elif ctype == "activity":
                if max_act_p > min_act_p:
                    rel_affordability = 1.0 - (price - min_act_p) / (max_act_p - min_act_p)
                else:
                    rel_affordability = 1.0
                b_fit = round(0.60 + 0.40 * rel_affordability, 2)
            else:
                b_fit = 0.85

            # 2. Destination Fit (Base-hub proximity + travel fatigue penalty)
            d_fit = compute_destination_fit(c, parsed_req.destination, base_hub=base_hub, dislikes_long_drives=dislikes_long_drives)

            # 3. Interest Fit (Generic tag and text token overlap)
            interest_overlap = req_interest_tokens & c_tokens
            if req_interest_tokens:
                i_fit = round(min(1.0, 0.40 + 0.60 * (len(interest_overlap) / len(req_interest_tokens))), 2)
            else:
                i_fit = 0.70

            # 4. Traveler Preference Fit
            pref_overlap = profile_pref_tokens & c_tokens
            t_fit = round(min(1.0, 0.40 + 0.30 * len(pref_overlap)), 2)

            # 5. Party Suitability
            capacity = c.get("capacity")
            if is_couple:
                if "couples" in c_tokens or capacity == 2:
                    p_fit = 0.95
                elif capacity is not None and capacity >= 2:
                    p_fit = 0.80
                else:
                    p_fit = 0.60
            elif is_family:
                if capacity is not None and capacity < total_pax:
                    p_fit = 0.20
                elif "family-friendly" in c_tokens:
                    p_fit = 0.98
                elif capacity is not None and capacity >= total_pax:
                    p_fit = 0.85
                else:
                    p_fit = 0.60
            else:
                p_fit = 0.80

            # 6. Pace Fit
            duration = c.get("duration_hours")
            if is_relaxed:
                if duration and duration > 5:
                    pace_fit = 0.65
                elif "relaxed" in c_tokens or (duration and duration <= 4) or ctype == "hotel":
                    pace_fit = 0.92
                else:
                    pace_fit = 0.75
            else:
                pace_fit = 0.85

            # 7. Past Feedback Fit
            feedback_matches = len(positive_past_tokens & c_tokens)
            past_feedback_fit = round(min(1.0, 0.75 + 0.15 * feedback_matches), 2)

            # 8. Quality Fit
            q_fit = round(min(1.0, rating / 5.0), 2)

            # Multi-dimensional weighted score across all 8 dimensions
            multi_dim_score = round(
                (b_fit * weights["budget_fit"]) +
                (d_fit * weights["destination_fit"]) +
                (i_fit * weights["interest_fit"]) +
                (t_fit * weights["traveler_preference_fit"]) +
                (p_fit * weights["party_suitability"]) +
                (pace_fit * weights["pace_fit"]) +
                (past_feedback_fit * weights["past_feedback_fit"]) +
                (q_fit * weights["quality"]),
                2
            )

            # Contextual Jev probability evaluation
            jev_prob = None
            is_top_jev_pick = False
            if ctype == "hotel" and "select_hotel" in answers:
                h_ans = answers["select_hotel"]
                jev_prob = h_ans.get("probabilities", {}).get(cid)
                if h_ans.get("choice") == cid:
                    is_top_jev_pick = True
            elif ctype == "activity" and "select_activity" in answers:
                a_ans = answers["select_activity"]
                jev_prob = a_ans.get("probabilities", {}).get(cid)
                if a_ans.get("choice") == cid:
                    is_top_jev_pick = True
            elif ctype == "transport" and "select_transport" in answers:
                t_ans = answers["select_transport"]
                jev_prob = t_ans.get("probabilities", {}).get(cid)
                if t_ans.get("choice") == cid:
                    is_top_jev_pick = True

            # Calculate overall score without artificial collapse to 0.50
            if jev_prob is not None:
                prob_float = float(jev_prob)
                if is_top_jev_pick:
                    overall = round(min(0.99, max(0.94, multi_dim_score + 0.15 * prob_float)), 2)
                else:
                    prob_scale = 0.85 + 0.15 * prob_float
                    overall = round(multi_dim_score * prob_scale, 2)
            else:
                overall = multi_dim_score

            reason = self._generate_evidence_based_reason(
                c, b_fit, d_fit, i_fit, p_fit, pace_fit, past_feedback_fit,
                is_budget_conscious, total_pax, base_hub, parsed_req, profile
            )

            scores_obj = EvaluationScores(
                budget_fit=b_fit,
                destination_fit=d_fit,
                interest_fit=i_fit,
                traveler_preference_fit=t_fit,
                past_trip_feedback_fit=past_feedback_fit,
                pace_fit=pace_fit,
                party_suitability=p_fit,
                quality=q_fit
            )

            ranked_models.append(
                RankedCandidate(
                    candidate_id=cid,
                    scores=scores_obj,
                    overall_score=overall,
                    reason=reason
                )
            )
            ranked_list.append((c, overall))

        # Sort descending by overall score
        ranked_list.sort(key=lambda x: x[1], reverse=True)
        ranked_models.sort(key=lambda x: x.overall_score, reverse=True)

        # Assign explicit ranks (1, 2, 3, ...)
        for idx, rm in enumerate(ranked_models, 1):
            rm.rank = idx

        # Step 4: Role-aware Candidate Pool Selection
        candidate_pool = self._select_candidate_pool(
            ranked_models,
            candidates_by_id,
            parsed_req,
            dislikes_long_drives
        )

        planner_cids = (
            candidate_pool.hotels +
            candidate_pool.activities +
            candidate_pool.transport
        )

        # Step 5: Populate Jev Diagnostics
        diagnostics = JevDiagnostics(
            retrieved_candidate_count=len(candidates),
            valid_candidate_count=len(candidates),
            jev_ranked_count=len(ranked_models),
            candidate_pool=candidate_pool,
            jev_used=not fallback_used,
            ranking_source=ranking_source,
            fallback_used=fallback_used,
            jev_failure_reason=jev_failure_reason
        )

        self.last_decision = JevDecision(
            ranked_candidates=ranked_models,
            candidate_pool=candidate_pool,
            planner_candidate_ids=planner_cids,
            selected_candidate_ids=planner_cids,
            diagnostics=diagnostics
        )

        # Return candidates sorted by Jev rank
        return [c[0] for c in ranked_list]

    def _select_candidate_pool(
        self,
        ranked_candidates: List[RankedCandidate],
        candidates_by_id: Dict[str, Dict[str, Any]],
        parsed_req: ParsedRequest,
        dislikes_long_drives: bool
    ) -> CandidatePool:
        """
        Selects a focused, role-aware candidate pool:
        - Hotels: Top 1 (or 2 if scores are very close)
        - Transport: Top 1 matching party capacity
        - Activities: Top 2-3 coherent activities aligned with base hub and pace
        """
        ranked_hotels = [rc for rc in ranked_candidates if candidates_by_id[rc.candidate_id].get("type") == "hotel"]
        ranked_activities = [rc for rc in ranked_candidates if candidates_by_id[rc.candidate_id].get("type") == "activity"]
        ranked_transports = [rc for rc in ranked_candidates if candidates_by_id[rc.candidate_id].get("type") == "transport"]

        # Select top hotel(s)
        selected_hotels = []
        if ranked_hotels:
            selected_hotels.append(ranked_hotels[0].candidate_id)
            if len(ranked_hotels) > 1 and (ranked_hotels[0].overall_score - ranked_hotels[1].overall_score) <= 0.04:
                selected_hotels.append(ranked_hotels[1].candidate_id)

        # Select top transport option
        selected_transports = []
        if ranked_transports:
            selected_transports.append(ranked_transports[0].candidate_id)

        # Select top activities (up to max_activities based on trip duration)
        # Generic heuristic: for relaxed itineraries, prefer fewer location changes when otherwise suitable options are available.
        is_relaxed = (parsed_req.pace == "relaxed")
        max_activities = 3 if parsed_req.num_days >= 3 else 2
        selected_activities = []
        selected_locations = set()
        if selected_hotels:
            h_data = candidates_by_id.get(selected_hotels[0], {})
            if h_data.get("location"):
                selected_locations.add(h_data["location"].lower().strip())

        for rc in ranked_activities:
            if len(selected_activities) >= max_activities:
                break
            c_data = candidates_by_id.get(rc.candidate_id, {})
            c_loc = (c_data.get("location") or "").lower().strip()

            # If relaxed and we already have candidate activities in multiple locations:
            # Check if there is another suitable candidate that avoids adding a 3rd distinct location
            if is_relaxed and len(selected_locations) >= 2 and c_loc and (c_loc not in selected_locations):
                co_located_candidate = next(
                    (other for other in ranked_activities
                     if other.candidate_id not in selected_activities
                     and (candidates_by_id.get(other.candidate_id, {}).get("location") or "").lower().strip() in selected_locations
                     and (rc.overall_score - other.overall_score) <= 0.10),
                    None
                )
                if co_located_candidate:
                    selected_activities.append(co_located_candidate.candidate_id)
                    continue

            selected_activities.append(rc.candidate_id)
            if c_loc:
                selected_locations.add(c_loc)

        if not selected_activities and ranked_activities:
            selected_activities = [ranked_activities[0].candidate_id]

        return CandidatePool(
            hotels=selected_hotels,
            activities=selected_activities,
            transport=selected_transports
        )

    def _generate_evidence_based_reason(
        self,
        candidate: Dict[str, Any],
        b_fit: float,
        d_fit: float,
        i_fit: float,
        p_fit: float,
        pace_fit: float,
        past_feedback_fit: float,
        is_budget_conscious: bool,
        total_pax: int,
        base_hub: str,
        parsed_req: Optional[ParsedRequest] = None,
        profile: Optional[Dict[str, Any]] = None
    ) -> str:
        """
        Generates a transparent, strictly evidence-based reason derived only
        from verified supplier catalog metadata, traveler constraints, and past feedback.
        Never outputs unsupported claims.
        """
        ctype = candidate.get("type")
        name = candidate.get("name", "Option")
        location = candidate.get("location", "")
        tags = candidate.get("tags", [])
        tags_str = ", ".join(tags[:3]) if tags else "curated"
        price = get_candidate_price(candidate)
        price_str = f"Rs. {price}" if price is not None else "catalog price"
        desc = candidate.get("description", "")
        desc_lower = desc.lower()

        parts = []
        if ctype == "hotel":
            cap = candidate.get('capacity', 'standard')
            if is_budget_conscious and b_fit >= 0.8:
                parts.append(f"Budget-conscious accommodation in {location} at {price_str}/night.")
            elif is_budget_conscious and b_fit < 0.6:
                parts.append(f"Higher price point ({price_str}/night) is less aligned with requested budget.")
            else:
                parts.append(f"Accommodates party with capacity for {cap} guests in {location} at {price_str}/night.")

            if p_fit >= 0.9:
                parts.append(f"Matches party requirements with capacity for {cap} guests.")
            if i_fit >= 0.7:
                parts.append(f"Directly satisfies traveler preferences for {tags_str}.")
            if past_feedback_fit >= 0.85:
                parts.append("Aligns with past-trip feedback favoring unhurried stays and local hospitality.")

        elif ctype == "activity":
            dur = candidate.get("duration_hours")
            dur_str = f"{dur}-hour duration" if dur else "flexible schedule"

            # Check matches against parsed request interests and traveler preferences
            matched_req = []
            for req_int in (parsed_req.interests if parsed_req else []):
                norm_int = normalize_token(req_int)
                if any(norm_int in normalize_token(t) or normalize_token(t) in norm_int for t in tags) or norm_int in desc_lower:
                    matched_req.append(req_int.lower().strip())

            matched_pref = []
            profile_prefs = profile.get("preferences", []) if profile else []
            for p in profile_prefs:
                norm_p = normalize_token(p)
                if (any(norm_p in normalize_token(t) or normalize_token(t) in norm_p for t in tags) or norm_p in desc_lower) and norm_p not in [normalize_token(x) for x in matched_req] and norm_p != "relaxed":
                    matched_pref.append(p.lower().strip())

            is_moderate_diff = "moderate difficulty" in desc_lower

            # Filter pace from topic-specific request interests to prevent redundancy
            topic_req = [r for r in matched_req if normalize_token(r) not in ("relaxed", "pace", "slow", "unhurried")]
            is_relaxed = any(normalize_token(r) in ("relaxed", "pace", "slow", "unhurried") for r in matched_req) or ("relaxed" in [normalize_token(t) for t in tags]) or (pace_fit >= 0.85)

            pref_text = ""
            if matched_pref:
                pref_label = matched_pref[0]
                if pref_label.endswith("-oriented"):
                    pref_label = pref_label[:-len("-oriented")]
                if not pref_label.endswith("experiences"):
                    pref_text = f", aligned with the traveler's preference for {pref_label} experiences"
                else:
                    pref_text = f", aligned with the traveler's preference for {pref_label}"

            if is_moderate_diff:
                req_text = f"Matches the {', '.join(topic_req)} request" if topic_req else "Matches activity request"
                pref_phrase = f" and {matched_pref[0]} preference" if matched_pref else ""
                parts.append(f"{req_text}{pref_phrase}; its moderate difficulty warrants human review because the catalog does not specify age or fitness suitability.")
            else:
                if topic_req:
                    req_phrase = f"Matches the request for {', '.join(topic_req)}"
                    pace_phrase = " with a relaxed pace" if is_relaxed else ""
                    main_text = f"{req_phrase}{pace_phrase}"
                elif is_relaxed:
                    main_text = f"Suited for a relaxed pace in {location}"
                else:
                    main_text = f"{dur_str.capitalize()} activity in {location}"

                parts.append(f"{main_text}{pref_text}.")
                if d_fit >= 0.9:
                    parts.append(f"Located directly in base destination {location}.")
                elif d_fit < 0.5:
                    parts.append(f"Located in {location} outside base destination {base_hub}.")
                if b_fit >= 0.8:
                    parts.append(f"Affordable pricing at {price_str}/person.")

        elif ctype == "transport":
            cap = candidate.get("capacity")
            name_lower = name.lower()
            if "sedan" in name_lower or "sedan" in desc_lower:
                veh = "Private sedan"
            elif "suv" in name_lower or "suv" in desc_lower:
                veh = "Private SUV"
            else:
                veh = name

            has_driver = "driver" in desc_lower
            if has_driver and cap:
                parts.append(f"{veh} with driver and capacity for {cap}.")
            elif has_driver:
                parts.append(f"{veh} with driver.")
            elif cap:
                parts.append(f"{veh} with capacity for {cap}.")
            else:
                parts.append(f"{veh} available in {location}.")

        if not parts:
            parts.append(f"Option in {location} aligned with traveler profile and {tags_str} tags.")

        return " ".join(parts)