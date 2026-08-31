import os
import json
from fastapi import FastAPI, Depends, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import List, Optional

from .database import get_db, Base, engine, SessionLocal
from .models import AgentIdentity, Mandate, TransactionAttempt, Flag, MerchantPolicy, RiskTierHistory
from .simulator import SafetyLayerSimulator

# Initialize SQLite tables
Base.metadata.create_all(bind=engine)

app = FastAPI(title="Mandate Layer — Agentic Commerce Safety Layer API")

# Allow CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# WebSocket Connection Manager
class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)
        print(f"WS client connected. Total connections: {len(self.active_connections)}")

    def disconnect(self, websocket: WebSocket):
        self.active_connections.remove(websocket)
        print(f"WS client disconnected. Remaining connections: {len(self.active_connections)}")

    async def broadcast(self, message: dict):
        for connection in self.active_connections:
            try:
                await connection.send_json(message)
            except Exception:
                # Handle stale connections gracefully
                pass

manager = ConnectionManager()

# Broadcast callback for simulator
async def broadcast_simulator_event(event: dict):
    await manager.broadcast(event)

# Initialize simulator
simulator = SafetyLayerSimulator(broadcast_callback=broadcast_simulator_event)

@app.on_event("startup")
async def startup_event():
    # Start the simulator automatically on server boot
    await simulator.start()

@app.on_event("shutdown")
async def shutdown_event():
    await simulator.stop()

# Pydantic Schemas for Requests
class ResolveEscalationRequest(BaseModel):
    approved: Optional[bool] = None
    resolution: Optional[str] = None

    def is_approved(self) -> bool:
        if self.approved is not None:
            return self.approved
        if self.resolution is not None:
            return self.resolution.lower() == "approved"
        return False

class ResetAgentRequest(BaseModel):
    target_tier: Optional[str] = "established"

# --- REST Endpoints ---

@app.get("/api/status")
def get_status():
    return {
        "simulator_running": simulator.running,
        "merchant_id": simulator.merchant_id,
        "active_connections": len(manager.active_connections)
    }

@app.get("/api/transactions")
def get_transactions(db: Session = Depends(get_db)):
    txs = db.query(TransactionAttempt).order_by(TransactionAttempt.timestamp.desc()).limit(100).all()
    return [tx.to_dict() for tx in txs]

@app.get("/api/mandates")
def get_mandates(db: Session = Depends(get_db)):
    mandates = db.query(Mandate).order_by(Mandate.valid_from.desc()).limit(50).all()
    return [m.to_dict() for m in mandates]

@app.get("/api/flags")
def get_flags(db: Session = Depends(get_db)):
    flags = db.query(Flag).order_by(Flag.timestamp.desc()).limit(50).all()
    return [f.to_dict() for f in flags]

@app.get("/api/agents")
def get_agents(db: Session = Depends(get_db)):
    agents = db.query(AgentIdentity).all()
    return [a.to_dict() for a in agents]

@app.post("/api/simulator/toggle")
async def toggle_simulator():
    if simulator.running:
        await simulator.stop()
    else:
        await simulator.start()
    return {"simulator_running": simulator.running}

@app.post("/api/escalation/{tx_id}/resolve")
@app.post("/api/escalations/{tx_id}/resolve")
async def resolve_escalation(tx_id: str, payload: ResolveEscalationRequest, db: Session = Depends(get_db)):
    tx = db.query(TransactionAttempt).filter(TransactionAttempt.id == tx_id).first()
    if not tx:
        raise HTTPException(status_code=404, detail="Transaction not found")
    
    if tx.decision != "escalated":
        raise HTTPException(status_code=400, detail="Transaction is not in escalated status")

    if payload.is_approved():
        tx.decision = "approved"
        tx.reason = f"Merchant Approved: Transaction was manually reviewed and approved. original reasoning: {tx.reason}"
    else:
        tx.decision = "blocked"
        tx.reason = f"Merchant Denied: Transaction was manually reviewed and denied. original reasoning: {tx.reason}"
        # Increment agent violations on manual decline
        agent = db.query(AgentIdentity).filter(AgentIdentity.id == tx.agent_id).first()
        if agent:
            old_tier = agent.risk_tier
            agent.violation_count += 1
            agent.risk_tier = "flagged"
            
            # Risk-tier history logging on merchant deny
            if old_tier != "flagged":
                history = RiskTierHistory(
                    agent_id=agent.id,
                    old_tier=old_tier,
                    new_tier="flagged",
                    reason="Manual review: merchant denied escalated transaction"
                )
                db.add(history)

    db.commit()
    db.refresh(tx)
    
    # Notify WebSocket clients about the state update
    agent = db.query(AgentIdentity).filter(AgentIdentity.id == tx.agent_id).first()
    mandate = db.query(Mandate).filter(Mandate.id == tx.mandate_id).first() if tx.mandate_id else None
    
    update_event = {
        "type": "transaction_update",
        "data": tx.to_dict(),
        "agent": agent.to_dict() if agent else {},
        "mandate": mandate.to_dict() if mandate else {}
    }
    await manager.broadcast(update_event)

    return tx.to_dict()

@app.post("/api/agents/{agent_id}/reset")
async def reset_agent(agent_id: str, payload: Optional[ResetAgentRequest] = None, db: Session = Depends(get_db)):
    agent = db.query(AgentIdentity).filter(AgentIdentity.id == agent_id).first()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    target_tier = payload.target_tier if payload and payload.target_tier else "established"
    old_tier = agent.risk_tier
    agent.risk_tier = target_tier
    agent.violation_count = 0
    
    # Risk-tier history logging on reset
    history = RiskTierHistory(
        agent_id=agent.id,
        old_tier=old_tier,
        new_tier=target_tier,
        reason=f"Merchant reset: risk tier manually updated to '{target_tier}'"
    )
    db.add(history)
    db.commit()
    db.refresh(agent)

    # Broadcast reset to update frontend Directory immediately
    await manager.broadcast({
        "type": "agent_reset",
        "agent": agent.to_dict()
    })
    
    return agent.to_dict()

@app.get("/api/agents/{agent_id}/history")
def get_agent_history(agent_id: str, db: Session = Depends(get_db)):
    history = db.query(RiskTierHistory).filter(RiskTierHistory.agent_id == agent_id).order_by(RiskTierHistory.timestamp.desc()).all()
    return [h.to_dict() for h in history]

# --- WebSocket Hub ---

@app.websocket("/ws/live")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            # Keep connection open and listen for heartbeat or client requests
            data = await websocket.receive_text()
            # Send simple ping back
            await websocket.send_json({"type": "pong"})
    except WebSocketDisconnect:
        manager.disconnect(websocket)

# --- Serve Static Frontend Files ---

# Determine frontend static files directory
FRONTEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "frontend"))

if os.path.exists(FRONTEND_DIR):
    app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
else:
    print(f"Warning: Static frontend directory not found at: {FRONTEND_DIR}")
