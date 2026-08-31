import datetime
import json
from sqlalchemy import Column, Integer, String, Float, DateTime, Text, ForeignKey
from sqlalchemy.orm import relationship
from .database import Base

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
            "created_at": self.created_at.isoformat() if self.created_at else None,
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
            "valid_from": self.valid_from.isoformat() if self.valid_from else None,
            "valid_until": self.valid_until.isoformat() if self.valid_until else None,
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
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
            "decision": self.decision,
            "reason": self.reason
        }

class Flag(Base):
    __tablename__ = "flags"

    id = Column(String, primary_key=True, index=True)
    agent_id = Column(String, ForeignKey("agent_identities.id"), nullable=False)
    type = Column(String, nullable=False)  # "velocity", "collusion"
    related_transaction_ids = Column(Text, nullable=False)  # JSON-encoded array of transaction strings
    detail = Column(String, nullable=False)
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
            "timestamp": self.timestamp.isoformat() if self.timestamp else None
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
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
            "reason": self.reason
        }
