import datetime
from sqlalchemy.orm import Session
from ..models import Mandate, TransactionAttempt

def enforce_transaction(
    db: Session,
    mandate: Mandate,
    amount: float,
    category: str,
    timestamp: datetime.datetime
) -> dict:
    """
    Transaction Enforcement Engine:
    Stateless evaluation comparing a transaction attempt against a negotiated mandate.
    Returns: {"decision": "approved" | "blocked" | "escalated", "reason": str}
    """
    # 1. Check if mandate exists
    if not mandate:
        return {
            "decision": "blocked",
            "reason": "Security Alert: No active mandate exists. Agents must negotiate a mandate before executing transactions."
        }

    # 2. Check mandate status
    if mandate.status == "revoked":
        return {
            "decision": "blocked",
            "reason": f"Transaction Blocked: Mandate '{mandate.id}' has been explicitly revoked due to previous security flags."
        }
    
    if mandate.status == "expired":
        return {
            "decision": "blocked",
            "reason": f"Transaction Blocked: Mandate '{mandate.id}' is marked as expired."
        }

    # 3. Check category match
    if category != mandate.category:
        return {
            "decision": "blocked",
            "reason": (
                f"Scope Violation: Mandate is strictly scoped for '{mandate.category}', "
                f"but transaction attempted purchasing in '{category}'."
            )
        }

    # 4. Check time window
    if timestamp < mandate.valid_from:
        return {
            "decision": "blocked",
            "reason": f"Timing Violation: Mandate is not yet active. (Becomes valid at {mandate.valid_from.isoformat()})"
        }
        
    if timestamp > mandate.valid_until:
        # Deliberate edge case: mandate expires mid-transaction / just before processing completes
        return {
            "decision": "blocked",
            "reason": (
                f"Timing Violation: Mandate expired mid-transaction. "
                f"Mandate valid until {mandate.valid_until.isoformat()}, attempted at {timestamp.isoformat()}."
            )
        }

    # 5. Check cumulative amount cap
    # Query previously approved transactions under this mandate
    from sqlalchemy import func
    previous_approved_sum = db.query(func.sum(TransactionAttempt.amount)).filter(
        TransactionAttempt.mandate_id == mandate.id,
        TransactionAttempt.decision == "approved"
    ).scalar() or 0.0

    cumulative_total = previous_approved_sum + amount
    if cumulative_total > mandate.amount_cap:
        return {
            "decision": "blocked",
            "reason": (
                f"Cap Exhausted: Attempted transaction of ₹{amount:.2f} exceeds remaining mandate cap. "
                f"Mandate cap: ₹{mandate.amount_cap:.2f}, already spent: ₹{previous_approved_sum:.2f}."
            )
        }

    # 6. Check for human escalation logic
    agent = mandate.agent
    if agent:
        # Rule A: High-value single transaction exceeding automated clearance threshold
        if amount >= 100000.0:
            return {
                "decision": "escalated",
                "reason": (
                    f"High-Value Single Transaction: Charge of ₹{amount:.2f} in '{category}' "
                    f"exceeds merchant automated clearance limit (₹100,000.00). Awaiting manual sign-off."
                )
            }

        # Rule B: New agent consuming > 85% of mandate cap in a single attempt
        if agent.risk_tier == "new" and amount > (0.85 * mandate.amount_cap):
            pct = (amount / mandate.amount_cap) * 100
            return {
                "decision": "escalated",
                "reason": (
                    f"Cap Exhaustion Alert: 'new' tier agent '{agent.id}' attempting transaction of ₹{amount:.2f} "
                    f"consuming {pct:.1f}% of granted mandate cap (₹{mandate.amount_cap:.2f}). Awaiting merchant confirmation."
                )
            }
        
        # Rule C: Flagged agent attempting > 75% of their constrained cap
        if agent.risk_tier == "flagged" and amount > (0.75 * mandate.amount_cap):
            pct = (amount / mandate.amount_cap) * 100
            return {
                "decision": "escalated",
                "reason": (
                    f"Flagged Agent Anomaly: Constrained agent '{agent.id}' attempting high-value transaction of ₹{amount:.2f} "
                    f"({pct:.1f}% of constrained cap ₹{mandate.amount_cap:.2f}). Escalated for manual merchant authorization."
                )
            }

    # 7. Otherwise, approve the transaction
    return {
        "decision": "approved",
        "reason": f"Scope Verification: Transaction of ₹{amount:.2f} in '{category}' is within mandate boundaries."
    }
