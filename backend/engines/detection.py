import datetime
import json
import uuid
from sqlalchemy.orm import Session
from sqlalchemy import func
from ..models import AgentIdentity, Mandate, TransactionAttempt, Flag, RiskTierHistory

# Detection parameters (tuned for quick and visible demo results)
VELOCITY_WINDOW_SECONDS = 30
VELOCITY_COUNT_THRESHOLD = 4
VELOCITY_SUM_THRESHOLD = 12000.0

COLLUSION_WINDOW_SECONDS = 30
COLLUSION_AGENT_COUNT_THRESHOLD = 3
COLLUSION_SUM_THRESHOLD = 24000.0
COLLUSION_INDIVIDUAL_MAX_AMOUNT = 12000.0  # Per-transaction threshold check: collusion agents buy small items under this limit

def detect_velocity(db: Session, agent_id: str, merchant_id: str = None) -> Flag:
    """
    Velocity Detector (Single-agent pattern):
    Scans the sliding window for an agent. If the count of recent transactions 
    exceeds threshold and their sum exceeds threshold, flags the behavior.
    Filters by merchant_id if provided.
    Includes cluster-overlap deduplication so the same ongoing burst doesn't emit multiple alerts.
    """
    now = datetime.datetime.utcnow()
    window_start = now - datetime.timedelta(seconds=VELOCITY_WINDOW_SECONDS)

    # Get approved attempts in sliding window
    query = db.query(TransactionAttempt).filter(
        TransactionAttempt.agent_id == agent_id,
        TransactionAttempt.decision == "approved",
        TransactionAttempt.timestamp >= window_start
    )
    
    # Filter by merchant if merchant_id is provided (using join with Mandate)
    if merchant_id:
        query = query.join(Mandate).filter(Mandate.merchant_id == merchant_id)
        
    recent_txs = query.all()

    if len(recent_txs) >= VELOCITY_COUNT_THRESHOLD:
        total_spent = sum(tx.amount for tx in recent_txs)
        if total_spent >= VELOCITY_SUM_THRESHOLD:
            current_tx_ids = set(tx.id for tx in recent_txs)

            # Retrieve all existing velocity flags for this agent in the sliding window
            recent_flags = db.query(Flag).filter(
                Flag.agent_id == agent_id,
                Flag.type == "velocity",
                Flag.timestamp >= window_start
            ).all()

            # Collect all previously flagged transaction IDs
            flagged_tx_ids = set()
            for f in recent_flags:
                if f.related_transaction_ids:
                    try:
                        ids = json.loads(f.related_transaction_ids)
                        flagged_tx_ids.update(ids)
                    except Exception:
                        pass

            # Count genuinely unflagged transactions in current cluster
            unflagged_txs = [tx for tx in recent_txs if tx.id not in flagged_tx_ids]

            # DEDUP RULE: If this cluster is just a continuation of an already-flagged burst
            # (i.e. fewer than VELOCITY_COUNT_THRESHOLD new transactions), suppress duplicate flag!
            if recent_flags and len(unflagged_txs) < VELOCITY_COUNT_THRESHOLD:
                return None

            # Check if an exact or subset match already exists
            tx_ids = sorted(list(current_tx_ids))
            tx_ids_json = json.dumps(tx_ids)

            # Trigger flag & upgrade violations
            agent = db.query(AgentIdentity).filter(AgentIdentity.id == agent_id).first()
            if agent:
                old_tier = agent.risk_tier
                agent.violation_count += 1
                agent.risk_tier = "flagged"
                
                # Risk-tier history logging
                if old_tier != "flagged":
                    history = RiskTierHistory(
                        agent_id=agent_id,
                        old_tier=old_tier,
                        new_tier="flagged",
                        reason=(
                            f"Velocity limit exceeded: {len(recent_txs)} transactions "
                            f"within {VELOCITY_WINDOW_SECONDS}s totaling ₹{total_spent:.2f} "
                            f"at merchant '{merchant_id or 'global'}'."
                        )
                    )
                    db.add(history)

            flag_id = f"flag_{uuid.uuid4().hex[:8]}"
            detail = (
                f"Velocity Alert: Agent '{agent_id}' executed {len(recent_txs)} transactions "
                f"within {VELOCITY_WINDOW_SECONDS} seconds, totaling ₹{total_spent:.2f} "
                f"(merchant: '{merchant_id or 'global'}')."
            )
            
            flag = Flag(
                id=flag_id,
                agent_id=agent_id,
                type="velocity",
                related_transaction_ids=tx_ids_json,
                detail=detail,
                timestamp=now
            )
            db.add(flag)
            db.commit()
            db.refresh(flag)
            return flag
                
    return None

def detect_collusion(db: Session, merchant_id: str) -> Flag:
    """
    Collusion Detector (Cross-agent pattern):
    Finds if multiple distinct agents are executing transactions within a tight
    time window at the same merchant, summing to a suspicious total,
    where each transaction is individually below the flag threshold.
    Includes cluster-overlap deduplication.
    """
    now = datetime.datetime.utcnow()
    window_start = now - datetime.timedelta(seconds=COLLUSION_WINDOW_SECONDS)

    # Get approved transactions in the window at this merchant (joined with Mandate)
    recent_txs = db.query(TransactionAttempt).join(Mandate).filter(
        TransactionAttempt.decision == "approved",
        TransactionAttempt.timestamp >= window_start,
        Mandate.merchant_id == merchant_id
    ).all()

    # Per-transaction threshold check: Filter for collusion pattern
    # Each transaction must be individually under the COLLUSION_INDIVIDUAL_MAX_AMOUNT threshold
    recent_txs = [tx for tx in recent_txs if tx.amount < COLLUSION_INDIVIDUAL_MAX_AMOUNT]

    # Group by agent
    agent_txs = {}
    for tx in recent_txs:
        agent_txs.setdefault(tx.agent_id, []).append(tx)

    distinct_agents = list(agent_txs.keys())
    
    if len(distinct_agents) >= COLLUSION_AGENT_COUNT_THRESHOLD:
        total_sum = sum(tx.amount for tx in recent_txs)
        if total_sum >= COLLUSION_SUM_THRESHOLD:
            current_tx_ids = set(tx.id for tx in recent_txs)

            # Retrieve existing collusion flags in this sliding window
            recent_flags = db.query(Flag).filter(
                Flag.type == "collusion",
                Flag.timestamp >= window_start
            ).all()

            flagged_collusion_tx_ids = set()
            for f in recent_flags:
                if f.related_transaction_ids:
                    try:
                        ids = json.loads(f.related_transaction_ids)
                        flagged_collusion_tx_ids.update(ids)
                    except Exception:
                        pass

            # Filter unflagged transactions
            unflagged_txs = [tx for tx in recent_txs if tx.id not in flagged_collusion_tx_ids]
            unflagged_agents = set(tx.agent_id for tx in unflagged_txs)
            unflagged_sum = sum(tx.amount for tx in unflagged_txs)

            # DEDUP RULE: If we already have a collusion alert covering this cluster and
            # the unflagged transactions do NOT form a brand-new qualifying group (3+ agents & >= ₹24,000),
            # suppress duplicate flag!
            if recent_flags and (len(unflagged_agents) < COLLUSION_AGENT_COUNT_THRESHOLD or unflagged_sum < COLLUSION_SUM_THRESHOLD):
                return None

            tx_ids = sorted(list(current_tx_ids))
            tx_ids_json = json.dumps(tx_ids)

            # Flag all involved agents & update risk tier
            for agent_id in distinct_agents:
                agent = db.query(AgentIdentity).filter(AgentIdentity.id == agent_id).first()
                if agent:
                    old_tier = agent.risk_tier
                    agent.violation_count += 1
                    agent.risk_tier = "flagged"
                    
                    # Risk-tier history logging
                    if old_tier != "flagged":
                        history = RiskTierHistory(
                            agent_id=agent_id,
                            old_tier=old_tier,
                            new_tier="flagged",
                            reason=(
                                f"Collusion trigger: coordinated multi-agent purchase pattern "
                                f"at merchant '{merchant_id}' totaling ₹{total_sum:.2f} in window."
                            )
                        )
                        db.add(history)

            flag_id = f"flag_{uuid.uuid4().hex[:8]}"
            detail = (
                f"Collusion Alert: Coordinated behavior detected at merchant '{merchant_id}'. "
                f"{len(distinct_agents)} distinct agents ({', '.join(distinct_agents)}) transacted "
                f"within {COLLUSION_WINDOW_SECONDS} seconds, totaling ₹{total_sum:.2f}. "
                f"All individual transactions were under the ₹{COLLUSION_INDIVIDUAL_MAX_AMOUNT:.2f} threshold."
            )

            # Assign the flag to the agent who completed the trigger transaction
            trigger_agent_id = recent_txs[-1].agent_id
            
            flag = Flag(
                id=flag_id,
                agent_id=trigger_agent_id,
                type="collusion",
                related_transaction_ids=tx_ids_json,
                detail=detail,
                timestamp=now
            )
            db.add(flag)
            db.commit()
            db.refresh(flag)
            return flag

    return None
