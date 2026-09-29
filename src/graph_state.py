from typing import TypedDict, List, Dict, Any, Optional
from pydantic import BaseModel, Field

from src.models import TravelPlan

class ParsedRequest(BaseModel):
    destination: str = Field(description="The primary destination of the trip")
    num_days: int = Field(description="Number of days for the trip")
    budget: Optional[float] = Field(None, description="Total budget in INR, if specified")
    budget_level: Optional[str] = Field(None, description="'budget', 'mid-range', or 'luxury'")
    party_adults: int = Field(default=2)
    party_children: int = Field(default=0)
    child_ages: List[int] = Field(default_factory=list, description="Ages of children if provided")
    interests: List[str] = Field(default_factory=list, description="List of interests or tags from the request")
    pace: str = Field(default="moderate", description="'relaxed', 'moderate', or 'active'")

class AgentState(TypedDict):
    request_id: str
    request_text: str
    traveler_profile: Dict[str, Any]
    parsed_request: Optional[ParsedRequest]
    candidates: List[Dict[str, Any]]
    jev_decision: Optional[Any] # The detailed evaluation from Jev
    is_unfulfillable: bool
    unfulfillable_reason: str
    raw_plan: Optional[TravelPlan]
    itinerary_validation_notes: List[str]
    validation_feedback: List[str]
    regeneration_count: int
    validated_plan: Optional[TravelPlan]
