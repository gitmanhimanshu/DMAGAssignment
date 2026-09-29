from typing import List, Dict, Any
from src.graph_state import ParsedRequest
from src.validator import is_compatible_location

def retrieve_candidates(parsed_req: ParsedRequest, profile: Dict[str, Any], catalog: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Deterministic candidate retrieval using data-driven catalog compatibility:
    - Data-driven destination matching (direct hub or regional matching)
    - Hard capacity constraint: supplier capacity >= total party size (when specified)
    - Soft budget signals preserved for JEV ranking
    """
    suppliers = catalog.get("suppliers", [])
    valid_candidates = []

    dest = (parsed_req.destination or "").lower().strip()
    total_pax = parsed_req.party_adults + parsed_req.party_children

    for s in suppliers:
        loc = (s.get("location") or "").lower().strip()
        ctype = s.get("type")

        # 1. Generic location compatibility derived from catalog data
        if not is_compatible_location(loc, dest, catalog, ctype):
            continue

        # 2. Hard constraint on capacity (if specified on supplier)
        if "capacity" in s and s["capacity"] is not None:
            if s["capacity"] < total_pax:
                continue

        valid_candidates.append(s)

    return valid_candidates

def check_inventory(candidates: List[Dict[str, Any]]) -> bool:
    return len(candidates) > 0
