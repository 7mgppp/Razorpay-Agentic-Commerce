import datetime
import json
from typing import Optional
from sqlalchemy import Column, Integer, String, Float, DateTime, Text, ForeignKey
from sqlalchemy.orm import relationship
from .database import Base

def format_utc_iso(dt: Optional[datetime.datetime]) -> Optional[str]:
    """Ensure datetime is serialized as timezone-aware ISO 8601 UTC string (+00:00)."""
    if not dt:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return dt.isoformat()

class AgentIdentity(Base):
    __tablename__ = "agent_identities"

    id = Column(String, primary_key=True, index=True)
    name = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    risk_tier = Column(String, default="new")  # "new", "established", "flagged"
    violation_count = Column(Integer, default=0)

    # Relationships
    mandates = relationship("Mandate", back_populates="agent")
    transactions = relationship("TransactionAttempt", back_populates="agent")
    flags = relationship("Flag", back_populates="agent")

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "created_at": format_utc_iso(self.created_at),
            "risk_tier": self.risk_tier,
            "violation_count": self.violation_count
        }

class Mandate(Base):
    __tablename__ = "mandates"

    id = Column(String, primary_key=True, index=True)
    agent_id = Column(String, ForeignKey("agent_identities.id"), nullable=False)
    merchant_id = Column(String, nullable=False)
    category = Column(String, nullable=False)
    amount_cap = Column(Float, nullable=False)
    valid_from = Column(DateTime, nullable=False)
    valid_until = Column(DateTime, nullable=False)
    status = Column(String, default="active")  # "active", "expired", "revoked"
    stated_purpose = Column(String, nullable=True)  # Plain-language description of mandate scope
    negotiation_log = Column(Text, nullable=True)  # JSON string of negotiation steps

    # Relationships
    agent = relationship("AgentIdentity", back_populates="mandates")
    transactions = relationship("TransactionAttempt", back_populates="mandate")

    def to_dict(self):
        logs = []
        if self.negotiation_log:
            try:
                logs = json.loads(self.negotiation_log)
            except Exception:
                logs = [{"log": self.negotiation_log}]
        return {
            "id": self.id,
            "agent_id": self.agent_id,
            "merchant_id": self.merchant_id,
            "category": self.category,
            "amount_cap": self.amount_cap,
            "stated_purpose": self.stated_purpose,
            "valid_from": format_utc_iso(self.valid_from),
            "valid_until": format_utc_iso(self.valid_until),
            "status": self.status,
            "negotiation_log": logs
        }

class TransactionAttempt(Base):
    __tablename__ = "transaction_attempts"

    id = Column(String, primary_key=True, index=True)
    mandate_id = Column(String, ForeignKey("mandates.id"), nullable=True)  # Null if no mandate was found
    agent_id = Column(String, ForeignKey("agent_identities.id"), nullable=False)
    amount = Column(Float, nullable=False)
    category = Column(String, nullable=False)
    timestamp = Column(DateTime, default=datetime.datetime.utcnow)
    decision = Column(String, nullable=False)  # "approved", "blocked", "escalated"
    reason = Column(String, nullable=False)

    # Relationships
    agent = relationship("AgentIdentity", back_populates="transactions")
    mandate = relationship("Mandate", back_populates="transactions")

    def to_dict(self):
        return {
            "id": self.id,
            "mandate_id": self.mandate_id,
            "agent_id": self.agent_id,
            "amount": self.amount,
            "category": self.category,
            "timestamp": format_utc_iso(self.timestamp),
            "decision": self.decision,
            "reason": self.reason
        }

class Flag(Base):
    __tablename__ = "flags"

    id = Column(String, primary_key=True, index=True)
    agent_id = Column(String, ForeignKey("agent_identities.id"), nullable=False)
    type = Column(String, nullable=False)  # "velocity", "collusion", "intent_mismatch"
    related_transaction_ids = Column(Text, nullable=False)  # JSON-encoded array of transaction strings
    detail = Column(String, nullable=False)
    ai_reasoning = Column(Text, nullable=True)
    confidence = Column(String, nullable=True)  # "high", "medium", "low"
    source = Column(String, nullable=True)  # "ai_llm", "rule_based_fallback"
    timestamp = Column(DateTime, default=datetime.datetime.utcnow)

    # Relationships
    agent = relationship("AgentIdentity", back_populates="flags")

    def to_dict(self):
        txs = []
        if self.related_transaction_ids:
            try:
                txs = json.loads(self.related_transaction_ids)
            except Exception:
                txs = []
        return {
            "id": self.id,
            "agent_id": self.agent_id,
            "type": self.type,
            "related_transaction_ids": txs,
            "detail": self.detail,
            "ai_reasoning": self.ai_reasoning,
            "confidence": self.confidence,
            "source": self.source,
            "timestamp": format_utc_iso(self.timestamp)
        }

class MerchantPolicy(Base):
    __tablename__ = "merchant_policies"

    id = Column(Integer, primary_key=True, autoincrement=True)
    category = Column(String, nullable=False)
    risk_tier = Column(String, nullable=False)  # "new", "established", "flagged"
    amount_limit = Column(Float, nullable=False)

    def to_dict(self):
        return {
            "id": self.id,
            "category": self.category,
            "risk_tier": self.risk_tier,
            "amount_limit": self.amount_limit
        }

class RiskTierHistory(Base):
    __tablename__ = "risk_tier_history"

    id = Column(Integer, primary_key=True, autoincrement=True)
    agent_id = Column(String, ForeignKey("agent_identities.id"), nullable=False)
    old_tier = Column(String, nullable=False)
    new_tier = Column(String, nullable=False)
    timestamp = Column(DateTime, default=datetime.datetime.utcnow)
    reason = Column(String, nullable=False)

    def to_dict(self):
        return {
            "id": self.id,
            "agent_id": self.agent_id,
            "old_tier": self.old_tier,
            "new_tier": self.new_tier,
            "timestamp": format_utc_iso(self.timestamp),
            "reason": self.reason
        }

class RedTeamAttempt(Base):
    __tablename__ = "red_team_attempts"

    id = Column(String, primary_key=True, index=True)
    timestamp = Column(DateTime, default=datetime.datetime.utcnow)
    agent_id = Column(String, default="agent_redteam")
    target_technique = Column(String, nullable=False)
    evasion_strategy = Column(Text, nullable=False)
    mandate_purpose = Column(String, nullable=False)
    category = Column(String, nullable=False)
    merchant_name = Column(String, nullable=False)
    amount = Column(Float, nullable=False)
    item_description = Column(String, nullable=False)
    outcome = Column(String, nullable=False)  # "caught", "evaded"
    detected_by = Column(String, nullable=False)  # "intent_mismatch", "velocity", "collusion", "policy_cap", "timing", "escalation", "none_evaded"
    defense_response = Column(Text, nullable=True)
    source = Column(String, default="ai_llm")  # "ai_llm", "static_fallback"
    is_synthetic = Column(Integer, default=1)
    related_transaction_id = Column(String, nullable=True)

    def to_dict(self):
        return {
            "id": self.id,
            "timestamp": format_utc_iso(self.timestamp),
            "agent_id": self.agent_id,
            "target_technique": self.target_technique,
            "evasion_strategy": self.evasion_strategy,
            "mandate_purpose": self.mandate_purpose,
            "category": self.category,
            "merchant_name": self.merchant_name,
            "amount": self.amount,
            "item_description": self.item_description,
            "outcome": self.outcome,
            "detected_by": self.detected_by,
            "defense_response": self.defense_response,
            "source": self.source,
            "is_synthetic": bool(self.is_synthetic),
            "related_transaction_id": self.related_transaction_id
        }


