from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from src.loader import load_catalog
from src.graph import TravelGraph
from src.models import TravelPlan

app = FastAPI(title="Travel AI API")

class PlanRequest(BaseModel):
    request_id: str
    text: str

@app.post("/plan", response_model=TravelPlan)
def plan_trip(req: PlanRequest):
    catalog = load_catalog()
    profile = catalog.get("traveler_profile", {})
    
    initial_state = {
        "request_id": req.request_id,
        "request_text": req.text,
        "traveler_profile": profile,
        "parsed_request": None,
        "candidates": [],
        "is_unfulfillable": False,
        "unfulfillable_reason": "",
        "raw_plan": None,
        "validated_plan": None
    }
    
    graph = TravelGraph(catalog).build_graph()
    final_state = graph.invoke(initial_state)
    
    plan = final_state.get("validated_plan")
    if not plan:
        raise HTTPException(status_code=500, detail="Failed to generate plan")
        
    return plan
