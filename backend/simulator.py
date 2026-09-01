import asyncio
import datetime
import random
import uuid
import json
from sqlalchemy.orm import Session
from .database import SessionLocal
from .models import AgentIdentity, Mandate, TransactionAttempt, Flag, MerchantPolicy, RiskTierHistory
from .engines.negotiation import negotiate_mandate
from .engines.enforcement import enforce_transaction
from .engines.detection import detect_velocity, detect_collusion

CATEGORY_PURPOSES = {
    "office_supplies": [
        "Restock team stationery, whiteboard markers, and desk notebooks",
        "Order ergonomic lumbar supports and replacement mice",
        "Pantry refreshment stock: premium espresso beans and herbal tea boxes",
        "Bulk departmental paper reams and toner cartridges"
    ],
    "electronics": [
        "Developer 4K external display and USB-C docking station",
        "Hardware security tokens and biometric authentication keys",
        "Noise-cancelling headsets for remote conference calls",
        "Testing mobile device hardware for QA test bench"
    ],
    "cloud_services": [
        "Monthly cloud compute instance reservation and CDN bandwidth",
        "PostgreSQL automated read-replica and backup storage tier",
        "GPU cluster compute allocation for scheduled inference batch",
        "Enterprise API gateway monitoring and telemetry logging tier"
    ],
    "food_delivery": [
        "Executive all-hands quarterly catering buffet",
        "Overtime team dinner delivery for sprint release night",
        "Client briefing meeting refreshments and snack platters"
    ]
}

class SafetyLayerSimulator:
    def __init__(self, broadcast_callback=None):
        self.broadcast_callback = broadcast_callback
        self.running = False
        self.merchant_id = "merchant_razorpay_shop"
        self.active_agents = [
            {"id": "agent_procure_bot", "name": "Procurement Agent", "tier": "established"},
            {"id": "agent_travel_planner", "name": "Travel Booking Agent", "tier": "established"},
            {"id": "agent_office_runner", "name": "Office Restocking Agent", "tier": "new"},
            {"id": "agent_temp_guest", "name": "Guest Ad-hoc Buyer", "tier": "new"},
        ]
        self.escalation_index = 0
        self.mismatch_index = 0
        self.speed = 1.0
        self.base_interval = 2.5

    async def start(self):
        self.running = True
        asyncio.create_task(self.run_simulation_loop())

    async def stop(self):
        self.running = False

    async def run_simulation_loop(self):
        print("Simulation loop started.")
        counter = 0
        while self.running:
            try:
                sleep_duration = max(0.2, self.base_interval / max(0.05, self.speed))
                await asyncio.sleep(sleep_duration)
                counter += 1
                
                # Setup base environment & policies
                self.seed_static_data()

                # Action triggers based on timing loops to keep the dashboard interesting:
                if counter % 30 == 0:
                    # 1. Trigger Collusion Attack Pattern (3 distinct agents, short window, high sum)
                    await self.simulate_collusion_pattern()
                elif counter % 22 == 0:
                    # 2. Trigger Velocity Attack Pattern (1 agent, rapid transactions)
                    await self.simulate_velocity_pattern()
                elif counter % 16 == 0:
                    # 3. Trigger Semantic Intent Mismatch Event (LLM checks actual item vs stated purpose)
                    await self.simulate_intent_mismatch_event()
                elif counter % 12 == 0:
                    # 4. Trigger Expired Mandate Edge Case (Expires mid-transaction)
                    await self.simulate_expired_mandate_edge_case()
                elif counter % 8 == 0:
                    # 5. Trigger Escalation / Human Approval Event (>85% cap consumption)
                    await self.simulate_escalation_event()
                elif counter % 6 == 0:
                    # 6. Trigger Over-Ask Negotiation -> COUNTERED mandate with LLM terms
                    await self.simulate_countered_negotiation()
                elif counter % 4 == 0:
                    # 7. Trigger Single-Transaction Violations (Direct block: wrong category, over cap)
                    await self.simulate_direct_violations()
                else:
                    # 8. Normal compliant transaction (steady stream of APPROVED transactions)
                    await self.simulate_normal_transaction()

            except Exception as e:
                print(f"Error in simulation loop: {e}")
                await asyncio.sleep(5)

    def seed_static_data(self):
        """Seed initial agents and merchant policies into the database if not present."""
        db = SessionLocal()
        try:
            # Seed Merchant Policies
            policies = [
                ("electronics", "new", 16000.0),
                ("electronics", "established", 80000.0),
                ("electronics", "flagged", 8000.0),
                ("office_supplies", "new", 12000.0),
                ("office_supplies", "established", 40000.0),
                ("office_supplies", "flagged", 4000.0),
                ("cloud_services", "new", 40000.0),
                ("cloud_services", "established", 240000.0),
                ("cloud_services", "flagged", 12000.0),
            ]
            
            existing_policies = db.query(MerchantPolicy).count()
            if existing_policies == 0:
                for category, risk_tier, limit in policies:
                    db.add(MerchantPolicy(category=category, risk_tier=risk_tier, amount_limit=limit))
                db.commit()

            # Seed Agents
            for agent_info in self.active_agents:
                exists = db.query(AgentIdentity).filter(AgentIdentity.id == agent_info["id"]).first()
                if not exists:
                    db.add(AgentIdentity(
                        id=agent_info["id"],
                        name=agent_info["name"],
                        risk_tier=agent_info["tier"],
                        violation_count=0
                    ))
            db.commit()
        finally:
            db.close()

    async def log_and_broadcast(self, db: Session, tx: TransactionAttempt, flag: Flag = None, intent_eval: dict = None):
        """Helper to commit transactions and notify websocket listener."""
        db.add(tx)
        db.commit()
        db.refresh(tx)
        
        # If semantic intent mismatch was detected independently, create and attach intent Flag if not already flagged
        if intent_eval and intent_eval.get("mismatch"):
            existing_intent_flag = db.query(Flag).filter(
                Flag.type == "intent_mismatch",
                Flag.related_transaction_ids.like(f"%{tx.id}%")
            ).first()

            if not existing_intent_flag:
                mismatch_detail = intent_eval.get("reason")
                if not mismatch_detail:
                    mandate = db.query(Mandate).filter(Mandate.id == tx.mandate_id).first() if tx.mandate_id else None
                    stated_purpose = mandate.stated_purpose if (mandate and mandate.stated_purpose) else "General procurement"
                    item_name = intent_eval.get("item") or tx.category.replace('_', ' ')
                    mismatch_detail = f"Mandate scoped for: {stated_purpose}. Actual charge: {item_name} (₹{tx.amount:,.2f}) — does not match stated intent."

                intent_flag = Flag(
                    id=f"flag_{uuid.uuid4().hex[:8]}",
                    agent_id=tx.agent_id,
                    type="intent_mismatch",
                    related_transaction_ids=json.dumps([tx.id]),
                    detail=mismatch_detail,
                    timestamp=datetime.datetime.utcnow()
                )
                db.add(intent_flag)
                db.commit()
                db.refresh(intent_flag)
                flag = intent_flag  # Attach for WebSocket broadcast
            else:
                flag = existing_intent_flag

        agent = db.query(AgentIdentity).filter(AgentIdentity.id == tx.agent_id).first()
        agent_dict = agent.to_dict() if agent else {}

        mandate = db.query(Mandate).filter(Mandate.id == tx.mandate_id).first() if tx.mandate_id else None
        mandate_dict = mandate.to_dict() if mandate else {}

        payload = {
            "type": "transaction",
            "data": tx.to_dict(),
            "agent": agent_dict,
            "mandate": mandate_dict
        }
        
        if flag:
            payload["flag"] = flag.to_dict()

        if self.broadcast_callback:
            await self.broadcast_callback(payload)

    async def simulate_normal_transaction(self):
        """Simulate a standard, fully compliant transaction that results in an APPROVED ledger item."""
        db = SessionLocal()
        try:
            # Pick established or clean agents for regular business transactions
            agent_id = random.choice(["agent_procure_bot", "agent_travel_planner", "agent_office_runner"])
            agent = db.query(AgentIdentity).filter(AgentIdentity.id == agent_id).first()
            if not agent:
                return

            # If enterprise procurement agents were flagged in prior attack cycles, rehabilitate them
            if agent_id in ["agent_procure_bot", "agent_travel_planner"] and agent.risk_tier == "flagged":
                agent.risk_tier = "established"
                agent.violation_count = 0
                db.commit()
                db.refresh(agent)

            category = random.choice(["office_supplies", "electronics", "cloud_services"])
            
            # Request a reasonable cap based on agent tier & category
            if agent.risk_tier == "established":
                requested_cap = random.choice([25000.0, 35000.0, 50000.0])
            else:
                requested_cap = random.choice([8000.0, 12000.0, 15000.0])

            valid_from = datetime.datetime.utcnow() - datetime.timedelta(minutes=1)
            valid_until = datetime.datetime.utcnow() + datetime.timedelta(minutes=30)
            
            purpose_list = CATEGORY_PURPOSES.get(category, [f"Standard business expenditure for {category}"])
            purpose_text = random.choice(purpose_list)

            mandate = negotiate_mandate(
                db, 
                agent_id=agent.id,
                merchant_id=self.merchant_id,
                category=category,
                requested_cap=requested_cap,
                valid_from=valid_from,
                valid_until=valid_until,
                purpose=purpose_text
            )
            db.add(mandate)
            db.commit()
            db.refresh(mandate)

            if self.broadcast_callback:
                await self.broadcast_callback({
                    "type": "negotiation",
                    "data": mandate.to_dict(),
                    "agent": mandate.agent.to_dict()
                })

            # Generate a compliant transaction amount strictly within the granted mandate cap (25% to 60% of cap)
            max_spendable = max(500.0, mandate.amount_cap * 0.60)
            min_spendable = max(300.0, mandate.amount_cap * 0.20)
            tx_amount = round(random.uniform(min_spendable, max_spendable), 2)

            decision_data = enforce_transaction(
                db, 
                mandate=mandate,
                amount=tx_amount,
                category=category,
                timestamp=datetime.datetime.utcnow(),
                merchant_name=self.merchant_id,
                item_description=f"Compliant {category.replace('_', ' ')} purchase item"
            )

            tx = TransactionAttempt(
                id=f"tx_{uuid.uuid4().hex[:8]}",
                mandate_id=mandate.id,
                agent_id=agent.id,
                amount=tx_amount,
                category=category,
                timestamp=datetime.datetime.utcnow(),
                decision=decision_data["decision"],
                reason=decision_data["reason"]
            )
            
            await self.log_and_broadcast(db, tx, intent_eval=decision_data.get("intent_eval"))
        finally:
            db.close()

    async def simulate_intent_mismatch_event(self):
        """
        Simulate an independent Semantic Intent Mismatch:
        Transaction is within category and cap, but the item/activity completely
        contradicts the mandate's stated purpose (e.g. buying high-end gaming console
        under a pantry snack restock mandate).
        """
        db = SessionLocal()
        try:
            mismatch_scenarios = [
                {
                    "agent_id": "agent_office_runner",
                    "category": "office_supplies",
                    "cap": 12000.0,
                    "stated_purpose": "Pantry snack restock: premium espresso beans, tea boxes, and breakfast granola",
                    "merchant_name": "Digital Games & Gadgets Hub",
                    "item_description": "Sony PlayStation 5 DualSense Controller & Gaming Headset Bundle",
                    "min_amount": 7490.0,
                    "max_amount": 9200.0
                },
                {
                    "agent_id": "agent_travel_planner",
                    "category": "food_delivery",
                    "cap": 12000.0,
                    "stated_purpose": "Executive working lunch catering for strategic partnership summit",
                    "merchant_name": "Luxury Timepieces & Jewellers",
                    "item_description": "Designer Gold-Plated Smart Wristwatch",
                    "min_amount": 8500.0,
                    "max_amount": 10500.0
                },
                {
                    "agent_id": "agent_temp_guest",
                    "category": "electronics",
                    "cap": 16000.0,
                    "stated_purpose": "Developer monitor and ergonomics equipment procurement",
                    "merchant_name": "Aviation & Drone Emporium",
                    "item_description": "4K High-Speed Drone with VR Flight Goggles",
                    "min_amount": 12200.0,
                    "max_amount": 14500.0
                },
                {
                    "agent_id": "agent_procure_bot",
                    "category": "cloud_services",
                    "cap": 40000.0,
                    "stated_purpose": "Q3 Staging Server & Kubernetes Cluster Hosting",
                    "merchant_name": "Antminer Crypto Rig Mart",
                    "item_description": "ASIC Crypto Mining Hardware Rig Chassis",
                    "min_amount": 29000.0,
                    "max_amount": 35500.0
                },
                {
                    "agent_id": "agent_office_runner",
                    "category": "office_supplies",
                    "cap": 12000.0,
                    "stated_purpose": "Bulk stationery restock: whiteboard pens, notebooks, and folders",
                    "merchant_name": "Luxury Leather & Apparel",
                    "item_description": "Italian Leather Weekend Travel Bag",
                    "min_amount": 6800.0,
                    "max_amount": 8900.0
                },
                {
                    "agent_id": "agent_travel_planner",
                    "category": "food_delivery",
                    "cap": 8000.0,
                    "stated_purpose": "Team daily breakfast muffins and cold brew cans",
                    "merchant_name": "Virtual Reality Tech Arena",
                    "item_description": "Meta Quest 3 VR Gaming Headset & Controllers",
                    "min_amount": 5400.0,
                    "max_amount": 7100.0
                },
                {
                    "agent_id": "agent_temp_guest",
                    "category": "electronics",
                    "cap": 16000.0,
                    "stated_purpose": "USB-C charging docks and mechanical keyboard replacements",
                    "merchant_name": "Pro Camera & Cinema Gear",
                    "item_description": "4K Waterproof Action Camera with Gimbal Stabilizer",
                    "min_amount": 11000.0,
                    "max_amount": 13900.0
                }
            ]

            # Round-robin selection ensures no consecutive duplicates
            scenario = mismatch_scenarios[self.mismatch_index % len(mismatch_scenarios)]
            self.mismatch_index += 1

            amount = round(random.uniform(scenario["min_amount"], scenario["max_amount"]), 2)

            agent = db.query(AgentIdentity).filter(AgentIdentity.id == scenario["agent_id"]).first()
            if not agent:
                return

            valid_from = datetime.datetime.utcnow() - datetime.timedelta(minutes=1)
            valid_until = datetime.datetime.utcnow() + datetime.timedelta(minutes=45)

            mandate = negotiate_mandate(
                db,
                agent_id=agent.id,
                merchant_id=self.merchant_id,
                category=scenario["category"],
                requested_cap=scenario["cap"],
                valid_from=valid_from,
                valid_until=valid_until,
                purpose=scenario["stated_purpose"]
            )
            db.add(mandate)
            db.commit()
            db.refresh(mandate)

            if self.broadcast_callback:
                await self.broadcast_callback({
                    "type": "negotiation",
                    "data": mandate.to_dict(),
                    "agent": mandate.agent.to_dict()
                })

            # Evaluate transaction against mandate
            decision_data = enforce_transaction(
                db,
                mandate=mandate,
                amount=amount,
                category=scenario["category"],
                timestamp=datetime.datetime.utcnow(),
                merchant_name=scenario["merchant_name"],
                item_description=scenario["item_description"]
            )

            tx = TransactionAttempt(
                id=f"tx_{uuid.uuid4().hex[:8]}",
                mandate_id=mandate.id,
                agent_id=agent.id,
                amount=amount,
                category=scenario["category"],
                timestamp=datetime.datetime.utcnow(),
                decision=decision_data["decision"],
                reason=decision_data["reason"]
            )

            print(f"Simulator: Triggering Semantic Intent Mismatch on '{agent.id}' (Stated: '{scenario['stated_purpose']}', Actual: '{scenario['item_description']}', Amount: ₹{amount:,.2f})...")
            await self.log_and_broadcast(db, tx, intent_eval=decision_data.get("intent_eval"))
        finally:
            db.close()

    async def simulate_direct_violations(self):
        """Simulate direct single-transaction rule blocks (wrong category, over cap)."""
        db = SessionLocal()
        try:
            agent = db.query(AgentIdentity).filter(AgentIdentity.id == "agent_temp_guest").first()
            if not agent:
                return

            category = "electronics"
            
            # Negotiate a mandate with a small cap of ₹5,000
            mandate = negotiate_mandate(
                db,
                agent_id=agent.id,
                merchant_id=self.merchant_id,
                category=category,
                requested_cap=5000.0,
                valid_from=datetime.datetime.utcnow(),
                valid_until=datetime.datetime.utcnow() + datetime.timedelta(minutes=10),
                purpose="Short scope electronics buy"
            )
            db.add(mandate)
            db.commit()
            db.refresh(mandate)

            # Violation A: Exceeding Cap (Attempts ₹12,000 against ₹5,000 cap)
            over_cap_amount = 12000.0
            decision_data = enforce_transaction(
                db,
                mandate=mandate,
                amount=over_cap_amount,
                category=category,
                timestamp=datetime.datetime.utcnow()
            )
            tx = TransactionAttempt(
                id=f"tx_{uuid.uuid4().hex[:8]}",
                mandate_id=mandate.id,
                agent_id=agent.id,
                amount=over_cap_amount,
                category=category,
                timestamp=datetime.datetime.utcnow(),
                decision=decision_data["decision"],
                reason=decision_data["reason"]
            )
            await self.log_and_broadcast(db, tx)

            await asyncio.sleep(0.5)

            # Violation B: Wrong Category (Attempts 'food_delivery' on 'electronics' mandate)
            wrong_category = "food_delivery"
            decision_data_cat = enforce_transaction(
                db,
                mandate=mandate,
                amount=1800.0,
                category=wrong_category,
                timestamp=datetime.datetime.utcnow()
            )
            tx_cat = TransactionAttempt(
                id=f"tx_{uuid.uuid4().hex[:8]}",
                mandate_id=mandate.id,
                agent_id=agent.id,
                amount=1800.0,
                category=wrong_category,
                timestamp=datetime.datetime.utcnow(),
                decision=decision_data_cat["decision"],
                reason=decision_data_cat["reason"]
            )
            await self.log_and_broadcast(db, tx_cat)
        finally:
            db.close()

    async def simulate_expired_mandate_edge_case(self):
        """Simulate the edge case: Mandate expires mid-transaction."""
        db = SessionLocal()
        try:
            agent = db.query(AgentIdentity).filter(AgentIdentity.id == "agent_office_runner").first()
            if not agent:
                return

            category = "office_supplies"
            
            # Create a mandate with very short validity (1 second)
            valid_from = datetime.datetime.utcnow()
            valid_until = datetime.datetime.utcnow() + datetime.timedelta(seconds=1.0)

            mandate = negotiate_mandate(
                db,
                agent_id=agent.id,
                merchant_id=self.merchant_id,
                category=category,
                requested_cap=8000.0,
                valid_from=valid_from,
                valid_until=valid_until,
                purpose="Urgent office supplies (short validity)"
            )
            db.add(mandate)
            db.commit()
            db.refresh(mandate)

            if self.broadcast_callback:
                await self.broadcast_callback({
                    "type": "negotiation",
                    "data": mandate.to_dict(),
                    "agent": mandate.agent.to_dict()
                })

            # Sleep 2.5 seconds to ensure the mandate expires
            print("Simulator: Pausing 2.5 seconds to trigger expired-mid-transaction edge case...")
            await asyncio.sleep(2.5)

            attempt_time = datetime.datetime.utcnow()
            decision_data = enforce_transaction(
                db,
                mandate=mandate,
                amount=2400.0,
                category=category,
                timestamp=attempt_time
            )

            tx = TransactionAttempt(
                id=f"tx_{uuid.uuid4().hex[:8]}",
                mandate_id=mandate.id,
                agent_id=agent.id,
                amount=2400.0,
                category=category,
                timestamp=attempt_time,
                decision=decision_data["decision"],
                reason=decision_data["reason"]
            )
            await self.log_and_broadcast(db, tx)
        finally:
            db.close()

    async def simulate_escalation_event(self):
        """Simulate realistic, varied events requiring manual human-in-the-loop review."""
        db = SessionLocal()
        try:
            scenarios = [
                {
                    "agent_id": "agent_temp_guest",
                    "tier": "new",
                    "category": "electronics",
                    "cap": 16000.0,
                    "amount": 14750.0,
                    "purpose": "Developer 4K Monitor & Dock Order",
                    "reason": "Cap Exhaustion Alert: 'new' tier agent 'agent_temp_guest' attempting transaction of ₹14,750.00 consuming 92.2% of granted mandate cap (₹16,000.00). Awaiting merchant confirmation."
                },
                {
                    "agent_id": "agent_office_runner",
                    "tier": "flagged",
                    "category": "office_supplies",
                    "cap": 4000.0,
                    "amount": 3400.0,
                    "purpose": "Emergency Ergonomic Equipment Order",
                    "reason": "Flagged Agent Anomaly: Constrained agent 'agent_office_runner' attempting high-value transaction of ₹3,400.00 (85.0% of constrained cap ₹4,000.00). Escalated for manual merchant authorization."
                },
                {
                    "agent_id": "agent_procure_bot",
                    "tier": "established",
                    "category": "cloud_services",
                    "cap": 240000.0,
                    "amount": 185000.0,
                    "purpose": "Quarterly GPU Cluster Compute Reservation",
                    "reason": "High-Value Single Transaction: Infrastructure charge of ₹185,000.00 in 'cloud_services' exceeds merchant automated clearance limit (₹100,000.00). Awaiting manual sign-off."
                },
                {
                    "agent_id": "agent_travel_planner",
                    "tier": "new",
                    "category": "food_delivery",
                    "cap": 12000.0,
                    "amount": 10500.0,
                    "purpose": "Corporate Event Catering Order",
                    "reason": "Anomalous Velocity Spike: Rapid surge order of ₹10,500.00 in 'food_delivery' (87.5% of cap) following 2 recent purchases. Gated for merchant security verification."
                },
                {
                    "agent_id": "agent_temp_guest",
                    "tier": "new",
                    "category": "office_supplies",
                    "cap": 12000.0,
                    "amount": 10900.0,
                    "purpose": "Bulk Department Stationery Restock",
                    "reason": "First-Time Buyer Anomaly: Unverified buyer agent 'agent_temp_guest' attempting initial bulk purchase of ₹10,900.00 (90.8% of cap). Gated for merchant initial clearance."
                }
            ]

            scenario = scenarios[self.escalation_index % len(scenarios)]
            self.escalation_index += 1

            agent = db.query(AgentIdentity).filter(AgentIdentity.id == scenario["agent_id"]).first()
            if agent:
                agent.risk_tier = scenario["tier"]
                db.commit()

            mandate = negotiate_mandate(
                db,
                agent_id=scenario["agent_id"],
                merchant_id=self.merchant_id,
                category=scenario["category"],
                requested_cap=scenario["cap"],
                valid_from=datetime.datetime.utcnow(),
                valid_until=datetime.datetime.utcnow() + datetime.timedelta(minutes=10),
                purpose=scenario["purpose"]
            )
            mandate.amount_cap = scenario["cap"]
            db.add(mandate)
            db.commit()
            db.refresh(mandate)

            if self.broadcast_callback:
                await self.broadcast_callback({
                    "type": "negotiation",
                    "data": mandate.to_dict(),
                    "agent": mandate.agent.to_dict()
                })

            decision_data = enforce_transaction(
                db,
                mandate=mandate,
                amount=scenario["amount"],
                category=scenario["category"],
                timestamp=datetime.datetime.utcnow()
            )

            escalated_reason = decision_data["reason"] if decision_data["decision"] == "escalated" else scenario["reason"]

            tx = TransactionAttempt(
                id=f"tx_{uuid.uuid4().hex[:8]}",
                mandate_id=mandate.id,
                agent_id=scenario["agent_id"],
                amount=scenario["amount"],
                category=scenario["category"],
                timestamp=datetime.datetime.utcnow(),
                decision="escalated",
                reason=escalated_reason
            )
            await self.log_and_broadcast(db, tx)
        finally:
            db.close()

    async def simulate_countered_negotiation(self):
        """Simulate an over-ask mandate request that generates an LLM-negotiated COUNTERED mandate."""
        db = SessionLocal()
        try:
            over_ask_scenarios = [
                {
                    "agent_id": "agent_temp_guest",
                    "category": "electronics",
                    "requested_cap": 32000.0,  # exceeds ₹16,000 policy limit by 100%
                    "purpose": "Bulk High-End Developer Monitor & Workstation Bundle",
                },
                {
                    "agent_id": "agent_office_runner",
                    "category": "office_supplies",
                    "requested_cap": 25000.0,  # exceeds ₹12,000 policy limit by 108%
                    "purpose": "Quarterly Ergonomic Furniture Restocking",
                },
                {
                    "agent_id": "agent_travel_planner",
                    "category": "food_delivery",
                    "requested_cap": 18000.0,  # exceeds ₹8,000 limit
                    "purpose": "All-Hands Annual Company Buffet Catering",
                },
                {
                    "agent_id": "agent_procure_bot",
                    "category": "cloud_services",
                    "requested_cap": 360000.0,  # exceeds ₹240,000 established limit by 50%
                    "purpose": "Extended High-Performance GPU Model Training Cluster",
                }
            ]

            scenario = random.choice(over_ask_scenarios)
            agent = db.query(AgentIdentity).filter(AgentIdentity.id == scenario["agent_id"]).first()
            if not agent:
                return

            valid_from = datetime.datetime.utcnow()
            valid_until = datetime.datetime.utcnow() + datetime.timedelta(hours=1)

            mandate = negotiate_mandate(
                db,
                agent_id=agent.id,
                merchant_id=self.merchant_id,
                category=scenario["category"],
                requested_cap=scenario["requested_cap"],
                valid_from=valid_from,
                valid_until=valid_until,
                purpose=scenario["purpose"]
            )
            db.add(mandate)
            db.commit()
            db.refresh(mandate)

            if self.broadcast_callback:
                await self.broadcast_callback({
                    "type": "negotiation",
                    "data": mandate.to_dict(),
                    "agent": mandate.agent.to_dict()
                })

            # Follow up with a compliant transaction within the countered cap (e.g. 40% of countered cap)
            spend_amount = round(mandate.amount_cap * 0.40, 2)
            decision_data = enforce_transaction(
                db,
                mandate=mandate,
                amount=spend_amount,
                category=scenario["category"],
                timestamp=datetime.datetime.utcnow()
            )
            tx = TransactionAttempt(
                id=f"tx_{uuid.uuid4().hex[:8]}",
                mandate_id=mandate.id,
                agent_id=agent.id,
                amount=spend_amount,
                category=scenario["category"],
                timestamp=datetime.datetime.utcnow(),
                decision=decision_data["decision"],
                reason=decision_data["reason"]
            )
            await self.log_and_broadcast(db, tx)
        finally:
            db.close()

    async def simulate_velocity_pattern(self):
        """Simulate a velocity attack: 5 rapid transactions for a single agent totaling >= ₹12,000."""
        db = SessionLocal()
        try:
            agent_id = "agent_office_runner"
            agent = db.query(AgentIdentity).filter(AgentIdentity.id == agent_id).first()
            if agent:
                # Reset tier to new so it can negotiate a ₹12,000+ cap
                agent.risk_tier = "new"
                db.commit()

            category = "office_supplies"
            cap = 20000.0

            mandate = negotiate_mandate(
                db,
                agent_id=agent_id,
                merchant_id=self.merchant_id,
                category=category,
                requested_cap=cap,
                valid_from=datetime.datetime.utcnow(),
                valid_until=datetime.datetime.utcnow() + datetime.timedelta(minutes=10),
                purpose="Bulk office restocking scope"
            )
            db.add(mandate)
            db.commit()
            db.refresh(mandate)

            print(f"Simulator: Triggering velocity pattern on '{agent_id}'...")

            # Run 5 fast transactions of ₹3,000 each (sum ₹15,000 >= ₹12,000 threshold)
            for i in range(5):
                amount = 3000.0
                decision_data = enforce_transaction(
                    db,
                    mandate=mandate,
                    amount=amount,
                    category=category,
                    timestamp=datetime.datetime.utcnow()
                )

                tx = TransactionAttempt(
                    id=f"tx_{uuid.uuid4().hex[:8]}",
                    mandate_id=mandate.id,
                    agent_id=agent_id,
                    amount=amount,
                    category=category,
                    timestamp=datetime.datetime.utcnow(),
                    decision=decision_data["decision"],
                    reason=decision_data["reason"]
                )
                
                flag = None
                if tx.decision == "approved":
                    db.add(tx)
                    db.commit()
                    flag = detect_velocity(db, agent_id, self.merchant_id)

                await self.log_and_broadcast(db, tx, flag)
                await asyncio.sleep(0.3)
        finally:
            db.close()

    async def simulate_collusion_pattern(self):
        """Simulate collusion: 3 distinct agents executing transactions summing to >= ₹24,000 within 30s."""
        db = SessionLocal()
        try:
            agents_to_use = [
                {"id": "agent_procure_bot", "cap": 30000.0, "amount": 8800.0},
                {"id": "agent_travel_planner", "cap": 30000.0, "amount": 9200.0},
                {"id": "agent_temp_guest", "cap": 16000.0, "amount": 8500.0}
            ]
            category = "electronics"

            print("Simulator: Triggering collusion pattern across agents...")

            for item in agents_to_use:
                agent_id = item["id"]
                agent = db.query(AgentIdentity).filter(AgentIdentity.id == agent_id).first()
                if agent and agent.risk_tier == "flagged":
                    agent.risk_tier = "established" if "bot" in agent_id or "planner" in agent_id else "new"
                    db.commit()

                mandate = negotiate_mandate(
                    db,
                    agent_id=agent_id,
                    merchant_id=self.merchant_id,
                    category=category,
                    requested_cap=item["cap"],
                    valid_from=datetime.datetime.utcnow(),
                    valid_until=datetime.datetime.utcnow() + datetime.timedelta(minutes=10),
                    purpose="Coordinated purchase scope"
                )
                db.add(mandate)
                db.commit()
                db.refresh(mandate)

                decision_data = enforce_transaction(
                    db,
                    mandate=mandate,
                    amount=item["amount"],
                    category=category,
                    timestamp=datetime.datetime.utcnow()
                )

                tx = TransactionAttempt(
                    id=f"tx_{uuid.uuid4().hex[:8]}",
                    mandate_id=mandate.id,
                    agent_id=agent_id,
                    amount=item["amount"],
                    category=category,
                    timestamp=datetime.datetime.utcnow(),
                    decision=decision_data["decision"],
                    reason=decision_data["reason"]
                )

                flag = None
                if tx.decision == "approved":
                    db.add(tx)
                    db.commit()
                    flag = detect_collusion(db, self.merchant_id)

                await self.log_and_broadcast(db, tx, flag)
                await asyncio.sleep(0.4)
        finally:
            db.close()
