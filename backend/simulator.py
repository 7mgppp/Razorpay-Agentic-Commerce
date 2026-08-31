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
                await asyncio.sleep(2.5)
                counter += 1
                
                # Setup base environment & policies
                self.seed_static_data()

                # Action triggers based on timing loops to keep the dashboard interesting:
                if counter % 25 == 0:
                    # 1. Trigger Collusion Attack Pattern (3 distinct agents, short window, high sum)
                    await self.simulate_collusion_pattern()
                elif counter % 18 == 0:
                    # 2. Trigger Velocity Attack Pattern (1 agent, rapid transactions)
                    await self.simulate_velocity_pattern()
                elif counter % 12 == 0:
                    # 3. Trigger Expired Mandate Edge Case (Expires mid-transaction)
                    await self.simulate_expired_mandate_edge_case()
                elif counter % 8 == 0:
                    # 4. Trigger Escalation / Human Approval Event (>85% cap consumption)
                    await self.simulate_escalation_event()
                elif counter % 5 == 0:
                    # 5. Trigger Single-Transaction Violations (Direct block: wrong category, over cap)
                    await self.simulate_direct_violations()
                else:
                    # 6. Normal compliant transaction (steady stream of APPROVED transactions)
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

    async def log_and_broadcast(self, db: Session, tx: TransactionAttempt, flag: Flag = None):
        """Helper to commit transactions and notify websocket listener."""
        db.add(tx)
        db.commit()
        db.refresh(tx)
        
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
            
            mandate = negotiate_mandate(
                db, 
                agent_id=agent.id,
                merchant_id=self.merchant_id,
                category=category,
                requested_cap=requested_cap,
                valid_from=valid_from,
                valid_until=valid_until,
                purpose=f"Standard business expenditure for {category}"
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
                timestamp=datetime.datetime.utcnow()
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
            
            await self.log_and_broadcast(db, tx)
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
        """Simulate an event that requires manual review (>85% cap consumed for new agent)."""
        db = SessionLocal()
        try:
            agent_id = "agent_temp_guest"
            agent = db.query(AgentIdentity).filter(AgentIdentity.id == agent_id).first()
            if agent:
                agent.risk_tier = "new"  # Ensure it is in 'new' tier for >85% escalation rule
                db.commit()

            category = "electronics"
            cap = 10000.0
            
            mandate = negotiate_mandate(
                db,
                agent_id=agent_id,
                merchant_id=self.merchant_id,
                category=category,
                requested_cap=cap,
                valid_from=datetime.datetime.utcnow(),
                valid_until=datetime.datetime.utcnow() + datetime.timedelta(minutes=10),
                purpose="High-value electronics request"
            )
            db.add(mandate)
            db.commit()
            db.refresh(mandate)

            # Attempt a transaction that is 92% of cap (₹9,200.00)
            large_amount = 9200.0
            decision_data = enforce_transaction(
                db,
                mandate=mandate,
                amount=large_amount,
                category=category,
                timestamp=datetime.datetime.utcnow()
            )

            tx = TransactionAttempt(
                id=f"tx_{uuid.uuid4().hex[:8]}",
                mandate_id=mandate.id,
                agent_id=agent_id,
                amount=large_amount,
                category=category,
                timestamp=datetime.datetime.utcnow(),
                decision=decision_data["decision"],  # 'escalated'
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
