import uuid
import datetime
import json
from sqlalchemy.orm import Session
from ..models import AgentIdentity, Mandate, MerchantPolicy

# Default Fallback Policies if DB is empty
DEFAULT_POLICIES = {
    "electronics": {"new": 16000.0, "established": 80000.0, "flagged": 8000.0},
    "office_supplies": {"new": 12000.0, "established": 40000.0, "flagged": 4000.0},
    "cloud_services": {"new": 40000.0, "established": 240000.0, "flagged": 12000.0},
    "default": {"new": 12000.0, "established": 40000.0, "flagged": 4000.0}
}

def get_policy_limit(db: Session, category: str, risk_tier: str) -> float:
    """Look up policy limit from database, fallback to defaults if not found."""
    policy = db.query(MerchantPolicy).filter(
        MerchantPolicy.category == category,
        MerchantPolicy.risk_tier == risk_tier
    ).first()
    
    if policy:
        return policy.amount_limit

    # Fallback to in-memory defaults
    cat_policies = DEFAULT_POLICIES.get(category, DEFAULT_POLICIES["default"])
    return cat_policies.get(risk_tier, cat_policies.get("new", 4000.0))

def negotiate_mandate(
    db: Session,
    agent_id: str,
    merchant_id: str,
    category: str,
    requested_cap: float,
    valid_from: datetime.datetime,
    valid_until: datetime.datetime,
    purpose: str
) -> Mandate:
    """
    Mandate Negotiation Engine:
    Fully rule-based mandate scoping based on agent risk tier and merchant policy.
    Returns a Mandate object (not yet committed, but fully populated).
    """
    # 1. Fetch or create agent identity
    agent = db.query(AgentIdentity).filter(AgentIdentity.id == agent_id).first()
    if not agent:
        # Create new agent profile
        agent = AgentIdentity(
            id=agent_id,
            name=f"Agent-{agent_id[:5]}" if len(agent_id) > 5 else f"Agent-{agent_id}",
            risk_tier="new"
        )
        db.add(agent)
        db.commit()
        db.refresh(agent)

    risk_tier = agent.risk_tier

    # 2. Look up the policy limit for this category & tier
    limit = get_policy_limit(db, category, risk_tier)

    # 3. Negotiation evaluation
    granted_cap = requested_cap
    steps = []
    
    steps.append({
        "requested": requested_cap,
        "category": category,
        "agent_tier": risk_tier,
        "policy_limit": limit,
        "purpose": purpose
    })

    if requested_cap <= limit:
        # Request fits within allowed bounds
        granted_cap = requested_cap
        reason = (
            f"Mandate approved as requested. Agent risk tier is '{risk_tier}' which allows "
            f"up to ₹{limit:.2f} for '{category}'. Requested: ₹{requested_cap:.2f}."
        )
        steps.append({
            "granted": granted_cap,
            "reason": reason,
            "status": "approved"
        })
    else:
        # Request exceeds allowed bounds -> Counter-offer at the cap limit
        granted_cap = limit
        reason = (
            f"Requested cap of ₹{requested_cap:.2f} exceeds the merchant policy limit of "
            f"₹{limit:.2f} for '{category}' under risk tier '{risk_tier}'. Counter-offered maximum allowed cap."
        )
        steps.append({
            "granted": granted_cap,
            "reason": reason,
            "status": "countered"
        })

    # 4. Create new Mandate object
    mandate_id = f"mandate_{uuid.uuid4().hex[:8]}"
    mandate = Mandate(
        id=mandate_id,
        agent_id=agent_id,
        merchant_id=merchant_id,
        category=category,
        amount_cap=granted_cap,
        valid_from=valid_from,
        valid_until=valid_until,
        status="active",
        negotiation_log=json.dumps(steps)
    )

    return mandate
