import asyncio
import datetime
import random
import uuid
import json
from sqlalchemy.orm import Session
from .database import SessionLocal
from .models import AgentIdentity, Mandate, TransactionAttempt, Flag, MerchantPolicy
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
                # Every tick (2 seconds), choose an action
                await asyncio.sleep(2.5)
                counter += 1
                
                # Setup base environment (seed agents & policies)
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
                    # 6. Normal compliant transaction
                    await self.simulate_normal_transaction()

            except Exception as e:
                print(f"Error in simulation loop: {e}")
                await asyncio.sleep(5)

    def seed_static_data(self):
        """Seed initial agents and merchant policies into the database if not present."""
        db = SessionLocal()
        try:
            # Clear old policies to ensure scaled INR values are loaded
            db.query(MerchantPolicy).delete()
            
            # Seed Merchant Policies
            policies = [
                ("electronics", "new", 12000.0),
                ("electronics", "established", 80000.0),
                ("electronics", "flagged", 2500.0),
                ("office_supplies", "new", 8000.0),
                ("office_supplies", "established", 40000.0),
                ("office_supplies", "flagged", 1200.0),
                ("cloud_services", "new", 32000.0),
                ("cloud_services", "established", 240000.0),
                ("cloud_services", "flagged", 4000.0),
            ]
            for category, risk_tier, limit in policies:
                db.add(MerchantPolicy(category=category, risk_tier=risk_tier, amount_limit=limit))

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
        
        # Reload agent status
        agent = db.query(AgentIdentity).filter(AgentIdentity.id == tx.agent_id).first()
        agent_dict = agent.to_dict() if agent else {}

        # Fetch latest mandate data
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
        """Simulate a standard, fully compliant transaction."""
        db = SessionLocal()
        try:
            agent_info = random.choice(self.active_agents)
            category = random.choice(["office_supplies", "electronics", "cloud_services"])
            requested_amount = round(random.uniform(800.0, 6500.0), 2)

            # 1. Negotiate Mandate
            valid_from = datetime.datetime.utcnow() - datetime.timedelta(minutes=1)
            valid_until = datetime.datetime.utcnow() + datetime.timedelta(minutes=30)
            
            # Ensure cap is scaled and slightly rounded above transaction amount
            requested_cap = round((requested_amount + 1500.0) / 100.0) * 100.0
            
            mandate = negotiate_mandate(
                db, 
                agent_id=agent_info["id"],
                merchant_id=self.merchant_id,
                category=category,
                requested_cap=requested_cap,
                valid_from=valid_from,
                valid_until=valid_until,
                purpose=f"Standard supply buy for {category}"
            )
            db.add(mandate)
            db.commit()
            db.refresh(mandate)

            # Broadcast negotiation event
            if self.broadcast_callback:
                await self.broadcast_callback({
                    "type": "negotiation",
                    "data": mandate.to_dict(),
                    "agent": mandate.agent.to_dict()
                })

            # 2. Execute Transaction
            decision_data = enforce_transaction(
                db, 
                mandate=mandate,
                amount=requested_amount,
                category=category,
                timestamp=datetime.datetime.utcnow()
            )

            tx = TransactionAttempt(
                id=f"tx_{uuid.uuid4().hex[:8]}",
                mandate_id=mandate.id,
                agent_id=agent_info["id"],
                amount=requested_amount,
                category=category,
                timestamp=datetime.datetime.utcnow(),
                decision=decision_data["decision"],
                reason=decision_data["reason"]
            )
            
            await self.log_and_broadcast(db, tx)
        finally:
            db.close()

    async def simulate_direct_violations(self):
        """Simulate a direct rule-based block (e.g. wrong category or transaction exceeding cap)."""
        db = SessionLocal()
        try:
            agent_info = random.choice(self.active_agents)
            category = "electronics"
            
            # Negotiate a mandate for electronics with small cap
            mandate = negotiate_mandate(
                db,
                agent_id=agent_info["id"],
                merchant_id=self.merchant_id,
                category=category,
                requested_cap=4000.0,
                valid_from=datetime.datetime.utcnow(),
                valid_until=datetime.datetime.utcnow() + datetime.timedelta(minutes=10),
                purpose="Short scope electronics"
            )
            db.add(mandate)
            db.commit()
            db.refresh(mandate)

            # Trigger Violation A: Over Cap limit
            over_cap_amount = 9500.0
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
                agent_id=agent_info["id"],
                amount=over_cap_amount,
                category=category,
                timestamp=datetime.datetime.utcnow(),
                decision=decision_data["decision"],
                reason=decision_data["reason"]
            )
            await self.log_and_broadcast(db, tx)

            # Trigger Violation B: Wrong Category
            wrong_category = "food_delivery"
            decision_data_cat = enforce_transaction(
                db,
                mandate=mandate,
                amount=1600.0,
                category=wrong_category,
                timestamp=datetime.datetime.utcnow()
            )
            tx_cat = TransactionAttempt(
                id=f"tx_{uuid.uuid4().hex[:8]}",
                mandate_id=mandate.id,
                agent_id=agent_info["id"],
                amount=1600.0,
                category=wrong_category,
                timestamp=datetime.datetime.utcnow(),
                decision=decision_data_cat["decision"],
                reason=decision_data_cat["reason"]
            )
            await self.log_and_broadcast(db, tx_cat)
        finally:
            db.close()

    async def simulate_expired_mandate_edge_case(self):
        """
        Simulate the hard edge case: Mandate expires mid-transaction.
        Creates a mandate valid for a split second, pauses, then transacts.
        Shows the transaction rejected gracefully.
        """
        db = SessionLocal()
        try:
            agent_info = random.choice(self.active_agents)
            category = "office_supplies"
            
            # Create a mandate with very short validity window
            valid_from = datetime.datetime.utcnow()
            valid_until = datetime.datetime.utcnow() + datetime.timedelta(seconds=1.5)  # Expires in 1.5 seconds
 
            mandate = negotiate_mandate(
                db,
                agent_id=agent_info["id"],
                merchant_id=self.merchant_id,
                category=category,
                requested_cap=6000.0,
                valid_from=valid_from,
                valid_until=valid_until,
                purpose="Urgent office supplies (expires quickly)"
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
 
            # Sleep 3 seconds to guarantee expiration
            print("Simulator: Pausing 3 seconds to trigger expired-mid-transaction edge case...")
            await asyncio.sleep(3.0)
 
            # Transaction is attempted after mandate expired
            attempt_time = datetime.datetime.utcnow()
            decision_data = enforce_transaction(
                db,
                mandate=mandate,
                amount=3500.0,
                category=category,
                timestamp=attempt_time
            )
 
            tx = TransactionAttempt(
                id=f"tx_{uuid.uuid4().hex[:8]}",
                mandate_id=mandate.id,
                agent_id=agent_info["id"],
                amount=3500.0,
                category=category,
                timestamp=attempt_time,
                decision=decision_data["decision"],
                reason=decision_data["reason"]
            )
            await self.log_and_broadcast(db, tx)
        finally:
            db.close()

    async def simulate_escalation_event(self):
        """Simulate an event that requires manual merchant approval (e.g. consumes > 85% cap)."""
        db = SessionLocal()
        try:
            # Use a new agent to trigger the escalation rule
            agent_id = "agent_temp_guest"
            category = "electronics"
            cap = 8000.0
            
            mandate = negotiate_mandate(
                db,
                agent_id=agent_id,
                merchant_id=self.merchant_id,
                category=category,
                requested_cap=cap,
                valid_from=datetime.datetime.utcnow(),
                valid_until=datetime.datetime.utcnow() + datetime.timedelta(minutes=10),
                purpose="One-off electronics purchase"
            )
            db.add(mandate)
            db.commit()
            db.refresh(mandate)

            # Attempt a transaction that is 90% of cap (₹7200.00)
            large_amount = 7200.0
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
                decision=decision_data["decision"],  # Will be 'escalated'
                reason=decision_data["reason"]
            )
            await self.log_and_broadcast(db, tx)
        finally:
            db.close()

    async def simulate_velocity_pattern(self):
        """Simulate a velocity attack: 5 small transactions in rapid succession for a single agent."""
        db = SessionLocal()
        try:
            agent_id = "agent_office_runner"
            category = "office_supplies"
            cap = 40000.0

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

            # Run 5 fast transactions, each completely compliant individually
            for i in range(5):
                amount = round(random.uniform(2800.0, 3500.0), 2)
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
                
                # Check for velocity alert trigger
                flag = None
                if tx.decision == "approved":
                    db.add(tx)
                    db.commit()
                    # Trigger velocity checker
                    flag = detect_velocity(db, agent_id, self.merchant_id)

                await self.log_and_broadcast(db, tx, flag)
                
                # Spacing of transactions is extremely tight (0.3s)
                await asyncio.sleep(0.3)
        finally:
            db.close()

    async def simulate_collusion_pattern(self):
        """Simulate collusion: 3+ distinct agents purchasing under their caps in a tight window."""
        db = SessionLocal()
        try:
            # We need 3 distinct agent identities
            agents_to_use = [
                {"id": "agent_procure_bot", "cap": 16000.0},
                {"id": "agent_travel_planner", "cap": 20000.0},
                {"id": "agent_temp_guest", "cap": 12000.0}
            ]
            category = "electronics"

            print("Simulator: Triggering collusion pattern across agents...")

            for item in agents_to_use:
                agent_id = item["id"]
                
                mandate = negotiate_mandate(
                    db,
                    agent_id=agent_id,
                    merchant_id=self.merchant_id,
                    category=category,
                    requested_cap=item["cap"],
                    valid_from=datetime.datetime.utcnow(),
                    valid_until=datetime.datetime.utcnow() + datetime.timedelta(minutes=10),
                    purpose="Team electronics budget"
                )
                db.add(mandate)
                db.commit()
                db.refresh(mandate)

                # Make an individual purchase under threshold
                amount = 9000.0
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
                    # Run collusion detector
                    flag = detect_collusion(db, self.merchant_id)

                await self.log_and_broadcast(db, tx, flag)
                await asyncio.sleep(0.4)
        finally:
            db.close()
