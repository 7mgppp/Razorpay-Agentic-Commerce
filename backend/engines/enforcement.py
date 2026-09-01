import os
import json
import urllib.request
import urllib.error
import datetime
from sqlalchemy.orm import Session
from ..models import Mandate, TransactionAttempt

def evaluate_semantic_intent(
    mandate: Mandate,
    merchant_name: str,
    category: str,
    amount: float,
    item_description: str = None
) -> dict:
    """
    Semantic Intent Matcher:
    Compares the transaction's merchant name, category, amount, and item details
    against the mandate's stated_purpose.
    Returns:
      {
        "mismatch": bool,
        "reason": str
      }
    Fails safely: if anything fails, returns {"mismatch": False, "reason": None}.
    """
    if not mandate or not getattr(mandate, "stated_purpose", None):
        return {"mismatch": False, "reason": None}

    stated_purpose = mandate.stated_purpose.strip()
    if not stated_purpose:
        return {"mismatch": False, "reason": None}

    item_label = item_description or category.replace('_', ' ')

    # 1. Live LLM Semantic Intent Evaluation (if API key is present)
    gemini_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if gemini_key:
        candidate_models = ["gemini-2.5-flash", "gemini-flash-latest", "gemini-1.5-flash"]
        prompt = (
            f"You are a semantic intent verification engine for an autonomous agent commerce safety layer.\n"
            f"Compare the transaction attempt against the mandate's stated purpose.\n\n"
            f"Mandate Stated Purpose: \"{stated_purpose}\"\n"
            f"Mandate Category: \"{mandate.category}\"\n\n"
            f"Transaction Details:\n"
            f"- Merchant: \"{merchant_name}\"\n"
            f"- Category: \"{category}\"\n"
            f"- Amount: INR {amount:,.2f}\n"
            f"- Item/Service: \"{item_label}\"\n\n"
            f"Does this transaction plausibly serve the stated purpose, or does it look like a semantic intent mismatch "
            f"(e.g., purchasing high-end gaming accessories under an office pantry snack mandate, or server hardware under travel booking)?\n\n"
            f"Respond ONLY with a JSON object: {{\"mismatch\": bool, \"reason\": \"one-line explanation\"}}"
        )
        req_data = json.dumps({
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"responseMimeType": "application/json"}
        }).encode("utf-8")

        for model_name in candidate_models:
            try:
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={gemini_key}"
                req = urllib.request.Request(url, data=req_data, headers={"Content-Type": "application/json"})
                with urllib.request.urlopen(req, timeout=4) as response:
                    res_body = json.loads(response.read().decode("utf-8"))
                    text_content = res_body["candidates"][0]["content"]["parts"][0]["text"]
                    parsed = json.loads(text_content)
                    is_mismatch = bool(parsed.get("mismatch", False))
                    if is_mismatch:
                        reason_text = f"Mandate scoped for: {stated_purpose}. Actual charge: {item_label} (₹{amount:,.2f}) — does not match stated intent."
                    else:
                        reason_text = "Transaction aligns plausibly with stated mandate purpose."
                    
                    print(f"[Intent Matcher: Gemini LLM Call Succeeded] model='{model_name}' item='{item_label}' vs purpose='{stated_purpose[:35]}...' -> Mismatch={is_mismatch}")
                    return {
                        "mismatch": is_mismatch,
                        "item": item_label,
                        "reason": reason_text,
                        "source": f"gemini_{model_name}"
                    }
            except Exception:
                continue

    # 2. Resilient Heuristic & Contextual Semantic Matching (Fallback)
    try:
        purpose_lower = stated_purpose.lower()
        desc_lower = (item_description or "").lower()
        merchant_lower = (merchant_name or "").lower()

        # Semantic keywords mapping
        pantry_keywords = ["snack", "coffee", "pantry", "beverage", "tea", "refreshment", "lunch", "catering", "food"]
        gaming_luxury_keywords = ["console", "gaming", "playstation", "vr headset", "luxury", "crypto", "drone", "smart tv"]
        travel_keywords = ["flight", "hotel", "travel", "offsite", "airline", "cab", "transport", "lodging"]
        stationery_keywords = ["stationery", "paper", "pen", "desk", "binder", "ergonomic", "restock"]
        cloud_infra_keywords = ["gpu cluster", "cloud compute", "server cluster", "aws", "gcp", "azure", "instance"]

        item_label = item_description or category.replace('_', ' ')
        is_mismatch = False

        # Case 1: Pantry / Snack mandate used for Gaming, Luxury, or Hardware
        if any(pk in purpose_lower for pk in pantry_keywords):
            if any(gk in desc_lower for gk in gaming_luxury_keywords) or category == "electronics" or "electronics" in merchant_lower:
                is_mismatch = True

        # Case 2: Travel / Offsite mandate used for Office Furniture or Cloud Infrastructure
        elif any(tk in purpose_lower for tk in travel_keywords):
            if category in ["cloud_services"] or any(ck in desc_lower for ck in cloud_infra_keywords) or "furniture" in desc_lower:
                is_mismatch = True

        # Case 3: Office Stationery / Restocking mandate used for Cloud Infrastructure or Crypto
        elif any(sk in purpose_lower for sk in stationery_keywords):
            if category == "cloud_services" or any(ck in desc_lower for ck in cloud_infra_keywords) or any(gk in desc_lower for gk in gaming_luxury_keywords):
                is_mismatch = True

        # Case 4: Explicit item description mismatch flag passed
        elif item_description and ("mismatch" in desc_lower or "unauthorized" in desc_lower or "playstation" in desc_lower or "luxury" in desc_lower or "drone" in desc_lower):
            is_mismatch = True

        if is_mismatch:
            formatted_reason = f"Mandate scoped for: {stated_purpose}. Actual charge: {item_label} (₹{amount:,.2f}) — does not match stated intent."
        else:
            formatted_reason = "Transaction aligns plausibly with stated mandate purpose."

        return {
            "mismatch": is_mismatch,
            "item": item_label,
            "reason": formatted_reason,
            "source": "heuristic_engine"
        }
    except Exception:
        return {"mismatch": False, "reason": None, "source": "none"}

def enforce_transaction(
    db: Session,
    mandate: Mandate,
    amount: float,
    category: str,
    timestamp: datetime.datetime,
    merchant_name: str = "merchant_razorpay_shop",
    item_description: str = None
) -> dict:
    """
    Transaction Enforcement Engine:
    Stateless rule-based evaluation comparing a transaction attempt against a negotiated mandate,
    plus an independent Semantic Intent Matching check.
    Returns: {"decision": "approved" | "blocked" | "escalated", "reason": str, "intent_eval": dict}
    """
    # Run Semantic Intent Evaluation independently (fails safely)
    intent_eval = evaluate_semantic_intent(
        mandate=mandate,
        merchant_name=merchant_name,
        category=category,
        amount=amount,
        item_description=item_description
    )

    # 1. Check if mandate exists
    if not mandate:
        return {
            "decision": "blocked",
            "reason": "Security Alert: No active mandate exists. Agents must negotiate a mandate before executing transactions.",
            "intent_eval": intent_eval
        }

    # 2. Check mandate status
    if mandate.status == "revoked":
        return {
            "decision": "blocked",
            "reason": f"Transaction Blocked: Mandate '{mandate.id}' has been explicitly revoked due to previous security flags.",
            "intent_eval": intent_eval
        }
    
    if mandate.status == "expired":
        return {
            "decision": "blocked",
            "reason": f"Transaction Blocked: Mandate '{mandate.id}' is marked as expired.",
            "intent_eval": intent_eval
        }

    # 3. Check category match
    if category != mandate.category:
        return {
            "decision": "blocked",
            "reason": (
                f"Scope Violation: Mandate is strictly scoped for '{mandate.category}', "
                f"but transaction attempted purchasing in '{category}'."
            ),
            "intent_eval": intent_eval
        }

    # 4. Check time window
    if timestamp < mandate.valid_from:
        return {
            "decision": "blocked",
            "reason": f"Timing Violation: Mandate is not yet active. (Becomes valid at {mandate.valid_from.isoformat()})",
            "intent_eval": intent_eval
        }
        
    if timestamp > mandate.valid_until:
        # Deliberate edge case: mandate expires mid-transaction / just before processing completes
        return {
            "decision": "blocked",
            "reason": (
                f"Timing Violation: Mandate expired mid-transaction. "
                f"Mandate valid until {mandate.valid_until.isoformat()}, attempted at {timestamp.isoformat()}."
            ),
            "intent_eval": intent_eval
        }

    # 5. Check cumulative amount cap
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
            ),
            "intent_eval": intent_eval
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
                ),
                "intent_eval": intent_eval
            }

        # Rule B: New agent consuming > 85% of mandate cap in a single attempt
        if agent.risk_tier == "new" and amount > (0.85 * mandate.amount_cap):
            pct = (amount / mandate.amount_cap) * 100
            return {
                "decision": "escalated",
                "reason": (
                    f"Cap Exhaustion Alert: 'new' tier agent '{agent.id}' attempting transaction of ₹{amount:.2f} "
                    f"consuming {pct:.1f}% of granted mandate cap (₹{mandate.amount_cap:.2f}). Awaiting merchant confirmation."
                ),
                "intent_eval": intent_eval
            }
        
        # Rule C: Flagged agent attempting > 75% of their constrained cap
        if agent.risk_tier == "flagged" and amount > (0.75 * mandate.amount_cap):
            pct = (amount / mandate.amount_cap) * 100
            return {
                "decision": "escalated",
                "reason": (
                    f"Flagged Agent Anomaly: Constrained agent '{agent.id}' attempting high-value transaction of ₹{amount:.2f} "
                    f"({pct:.1f}% of constrained cap ₹{mandate.amount_cap:.2f}). Escalated for manual merchant authorization."
                ),
                "intent_eval": intent_eval
            }

    # 7. Otherwise, approve the transaction
    return {
        "decision": "approved",
        "reason": f"Scope Verification: Transaction of ₹{amount:.2f} in '{category}' is within mandate boundaries.",
        "intent_eval": intent_eval
    }

