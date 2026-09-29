import os
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from src.loader import load_catalog
from src.graph import TravelGraph
from src.models import TravelPlan

app = FastAPI(title="Travel AI API")

# Enable CORS for browser access from file:// or local servers
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

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

# Mount static UI files for convenience
ui_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "ui")
if os.path.exists(ui_dir):
    app.mount("/", StaticFiles(directory=ui_dir, html=True), name="ui")

