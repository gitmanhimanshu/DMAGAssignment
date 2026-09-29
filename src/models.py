from pydantic import BaseModel, Field
from typing import List, Optional, Dict

class EvaluationScores(BaseModel):
    budget_fit: float
    destination_fit: float
    interest_fit: float
    traveler_preference_fit: float
    past_trip_feedback_fit: float
    pace_fit: float
    party_suitability: float
    quality: float

class RankedCandidate(BaseModel):
    rank: int = 1
    candidate_id: str
    scores: EvaluationScores
    overall_score: float
    reason: str

class CandidatePool(BaseModel):
    hotels: List[str] = Field(default_factory=list, description="Selected top hotels")
    activities: List[str] = Field(default_factory=list, description="Selected top activities")
    transport: List[str] = Field(default_factory=list, description="Selected top transport options")

class JevDiagnostics(BaseModel):
    retrieved_candidate_count: int
    valid_candidate_count: int
    jev_ranked_count: int
    candidate_pool: Optional[CandidatePool] = None
    jev_used: bool = False
    ranking_source: str = "deterministic_fallback"
    fallback_used: bool = False
    jev_failure_reason: Optional[str] = None

class JevDecision(BaseModel):
    ranked_candidates: List[RankedCandidate] = Field(description="Candidates ranked by overall_score")
    candidate_pool: CandidatePool = Field(default_factory=CandidatePool, description="Role-aware candidate pool")
    planner_candidate_ids: List[str] = Field(default_factory=list, description="Candidate IDs passed to planner")
    selected_candidate_ids: List[str] = Field(default_factory=list, description="Candidate IDs actually used by planner")
    diagnostics: Optional[JevDiagnostics] = None

class ItineraryItem(BaseModel):
    catalog_id: str
    item_type: str = Field(description="'hotel', 'activity', or 'transport'")
    name: str
    location: str
    quantity: int = Field(description="Number of items (e.g., number of travelers for activities, number of nights for hotels)")
    unit_price: float = Field(description="Unit price from catalog")
    line_total: float = Field(description="Calculated total for this line item")
    reason: str = Field(description="Why this item was selected")

class TravelDay(BaseModel):
    day_number: int
    summary: str
    items: List[ItineraryItem]

class BudgetSummary(BaseModel):
    requested_budget: Optional[float] = None
    planned_total: float = 0.0
    budget_used: Optional[float] = None
    remaining_budget: Optional[float] = None
    budget_utilization: Optional[float] = None
    budget_status: str = Field(default="within_budget", description="'within_budget', 'under_budget', 'over_budget', or 'no_numeric_budget'")
    budget_note: str = ""

class TravelPlan(BaseModel):
    request_id: str
    status: str = Field(description="'success' or 'unfulfillable'")
    summary: str
    days: List[TravelDay]
    total_price: float
    currency: str = "INR"
    grounding_status: str = "pending"
    human_review_required: bool = True
    human_review_reasons: List[str] = Field(default_factory=list)
    budget_summary: Optional[BudgetSummary] = None
    preference_coverage: Dict[str, bool] = Field(default_factory=dict)
    planner_candidate_ids: List[str] = Field(default_factory=list)
    selected_candidate_ids: List[str] = Field(default_factory=list)
    diagnostics: Optional[JevDiagnostics] = None
    planner_fallback_used: Optional[bool] = None
    planner_fallback_reason: Optional[str] = None
