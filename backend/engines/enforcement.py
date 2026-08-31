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
    # Rule: Escalate if a transaction consumes > 85% of the total mandate cap in a single attempt for a 'new' agent.
    # Also escalate if a 'flagged' agent attempts > 75% of their constrained cap.
    agent = mandate.agent
    if agent:
        if agent.risk_tier == "new" and amount > (0.85 * mandate.amount_cap):
            return {
                "decision": "escalated",
                "reason": (
                    f"Risk Escalation: Single transaction of ₹{amount:.2f} consumes over 85% of the mandate cap "
                    f"(₹{mandate.amount_cap:.2f}) for a 'new' risk-tier agent. Awaiting merchant confirmation."
                )
            }
        
        if agent.risk_tier == "flagged" and amount > (0.75 * mandate.amount_cap):
            return {
                "decision": "escalated",
                "reason": (
                    f"Risk Escalation: Flagged agent '{agent.id}' attempting high-value transaction of ₹{amount:.2f} "
                    f"(>75% of constrained cap ₹{mandate.amount_cap:.2f}). Awaiting merchant manual review."
                )
            }

    # 7. Otherwise, approve the transaction
    return {
        "decision": "approved",
        "reason": f"Scope Verification: Transaction of ₹{amount:.2f} in '{category}' is within mandate boundaries."
    }
