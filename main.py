from typing import Optional

from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI(title="KisanMate API")


@app.get("/")
def home():
    return {"status": "live"}


class DiagnoseRequest(BaseModel):
    farmer_id: str
    image_note: Optional[str] = None


class ConfirmRequest(BaseModel):
    case_id: str
    officer_verdict: str


class RecommendRequest(BaseModel):
    farmer_id: str
    crop: Optional[str] = None


@app.post("/api/diagnose")
def diagnose(request: DiagnoseRequest):
    return {
        "case_id": "placeholder-case-id",
        "farmer_id": request.farmer_id,
        "status": "pending",
        "vision": None,
        "fusion": None,
        "message": "placeholder response - diagnosis logic not yet implemented",
    }


@app.post("/api/confirm")
def confirm(request: ConfirmRequest):
    return {
        "case_id": request.case_id,
        "status": "confirmed",
        "officer_verdict": request.officer_verdict,
        "message": "placeholder response - confirmation logic not yet implemented",
    }


@app.get("/api/alerts/{farmer_id}")
def get_alerts(farmer_id: str):
    return {
        "farmer_id": farmer_id,
        "alerts": [],
        "message": "placeholder response - alert lookup not yet implemented",
    }


@app.post("/api/recommend")
def recommend(request: RecommendRequest):
    return {
        "farmer_id": request.farmer_id,
        "recommendations": [],
        "message": "placeholder response - recommendation logic not yet implemented",
    }


@app.get("/api/health")
def health():
    return {"status": "ok"}