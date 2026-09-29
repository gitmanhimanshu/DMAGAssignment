import json
import os
from typing import Dict, Any

def load_env():
    candidates = [
        ".env",
        os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env")
    ]
    for env_path in candidates:
        if os.path.exists(env_path):
            with open(env_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        k = k.strip()
                        v = v.strip().strip("'\"")
                        if k not in os.environ or not os.environ[k]:
                            os.environ[k] = v
            break

load_env()

def load_catalog(filepath: str = "data/sample_data.json") -> Dict[str, Any]:
    # Ensure the path is absolute or relative to the script correctly
    if not os.path.exists(filepath):
        filepath = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "sample_data.json")
    with open(filepath, "r", encoding="utf-8") as f:
        return json.load(f)

def get_test_request(request_id: str, catalog: Dict[str, Any]) -> Dict[str, Any]:
    for req in catalog.get("test_requests", []):
        if req["request_id"] == request_id:
            return req
    raise ValueError(f"Request {request_id} not found")
