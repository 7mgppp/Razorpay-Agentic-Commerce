// WebSocket and API configuration
const API_BASE = `${window.location.origin}`;
const WS_URL = `ws://${window.location.host}/ws/live`;

// Page state cache
let state = {
    transactions: [],
    mandates: [],
    flags: [],
    agents: [],
    simulatorRunning: true
};

// WebSocket variable
let socket = null;

// Initialize on load
document.addEventListener("DOMContentLoaded", () => {
    initApp();
});

async function initApp() {
    setupEventListeners();
    await fetchInitialData();
    connectWebSocket();
}

// 1. Fetch data from REST APIs
async function fetchInitialData() {
    try {
        // Fetch Server Status
        const statusRes = await fetch(`${API_BASE}/api/status`);
        const status = await statusRes.json();
        state.simulatorRunning = status.simulator_running;
        updateSimulatorButtonUI();

        // Fetch Transactions
        const txRes = await fetch(`${API_BASE}/api/transactions`);
        state.transactions = await txRes.json();
        renderTransactions();

        // Fetch Mandates
        const mandateRes = await fetch(`${API_BASE}/api/mandates`);
        state.mandates = await mandateRes.json();
        renderMandates();

        // Fetch Flags
        const flagRes = await fetch(`${API_BASE}/api/flags`);
        state.flags = await flagRes.json();
        renderFlags();

        // Fetch Agents
        const agentRes = await fetch(`${API_BASE}/api/agents`);
        state.agents = await agentRes.json();
        renderAgents();

        // Render Escalations from transaction history
        renderEscalations();

    } catch (err) {
        console.error("Error loading initial data:", err);
    }
}

// 2. Connect WebSocket
function connectWebSocket() {
    const connectionBadge = document.getElementById("connection-badge");
    const connectionText = document.getElementById("connection-text");

    console.log("Connecting to WS:", WS_URL);
    socket = new WebSocket(WS_URL);

    socket.onopen = () => {
        connectionBadge.className = "status-indicator connected";
        connectionText.textContent = "Live Socket Connected";
        console.log("WebSocket connection established.");
    };

    socket.onmessage = async (event) => {
        try {
            const payload = JSON.parse(event.data);
            console.log("Received WS payload:", payload);

            if (payload.type === "negotiation") {
                // Add new mandate negotiation log
                state.mandates.unshift(payload.data);
                if (state.mandates.length > 50) state.mandates.pop();
                
                // Update agent risk tier in cache if matching
                const index = state.agents.findIndex(a => a.id === payload.agent.id);
                if (index !== -1) {
                    state.agents[index] = payload.agent;
                    renderAgents();
                }
                renderMandates();

            } else if (payload.type === "transaction") {
                // Add new transaction attempt
                state.transactions.unshift(payload.data);
                if (state.transactions.length > 100) state.transactions.pop();

                // Update agent info in cache
                const index = state.agents.findIndex(a => a.id === payload.agent.id);
                if (index !== -1) {
                    state.agents[index] = payload.agent;
                } else {
                    state.agents.push(payload.agent);
                }
                renderAgents();

                // Check for flag payload accompanying transaction
                if (payload.flag) {
                    state.flags.unshift(payload.flag);
                    if (state.flags.length > 50) state.flags.pop();
                    renderFlags();
                }

                // Render views
                renderTransactions();
                renderEscalations();

            } else if (payload.type === "transaction_update") {
                // Transaction was approved/denied manually
                const index = state.transactions.findIndex(t => t.id === payload.data.id);
                if (index !== -1) {
                    state.transactions[index] = payload.data;
                }
                
                // Update agent info
                const agentIdx = state.agents.findIndex(a => a.id === payload.agent.id);
                if (agentIdx !== -1) {
                    state.agents[agentIdx] = payload.agent;
                }
                
                renderTransactions();
                renderEscalations();
                renderAgents();

            } else if (payload.type === "agent_reset") {
                // Agent has been reset
                const index = state.agents.findIndex(a => a.id === payload.agent.id);
                if (index !== -1) {
                    state.agents[index] = payload.agent;
                    renderAgents();
                }
            }

        } catch (err) {
            console.error("Error parsing WS packet:", err);
        }
    };

    socket.onclose = () => {
        connectionBadge.className = "status-indicator disconnected";
        connectionText.textContent = "WebSocket Disconnected";
        console.log("WebSocket connection closed. Retrying in 4 seconds...");
        setTimeout(connectWebSocket, 4000);
    };

    socket.onerror = (err) => {
        console.error("WebSocket encountered error:", err);
        socket.close();
    };
}

// 3. Event Listeners Setup
function setupEventListeners() {
    const toggleBtn = document.getElementById("toggle-simulator-btn");
    toggleBtn.addEventListener("click", toggleSimulator);
}

async function toggleSimulator() {
    try {
        const res = await fetch(`${API_BASE}/api/simulator/toggle`, { method: "POST" });
        const data = await res.json();
        state.simulatorRunning = data.simulator_running;
        updateSimulatorButtonUI();
    } catch (err) {
        console.error("Error toggling simulator:", err);
    }
}

function updateSimulatorButtonUI() {
    const toggleBtn = document.getElementById("toggle-simulator-btn");
    if (state.simulatorRunning) {
        toggleBtn.className = "control-btn shadow-neon";
        toggleBtn.innerHTML = `<i class="fa-solid fa-pause"></i> Pause Simulation`;
    } else {
        toggleBtn.className = "control-btn paused shadow-neon";
        toggleBtn.innerHTML = `<i class="fa-solid fa-play"></i> Resume Simulation`;
    }
}

// 4. Render Utilities

function getCategoryIcon(category) {
    switch (category) {
        case "office_supplies": return "fa-box-archive";
        case "electronics": return "fa-laptop";
        case "cloud_services": return "fa-cloud";
        default: return "fa-bag-shopping";
    }
}

function formatTime(dateStr) {
    if (!dateStr) return "";
    const date = new Date(dateStr);
    return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

// Render Transactions ledger
function renderTransactions() {
    const feed = document.getElementById("transactions-feed");
    if (state.transactions.length === 0) {
        feed.innerHTML = `
            <div class="empty-state">
                <i class="fa-solid fa-receipt empty-icon"></i>
                <p>No transaction attempts logged.</p>
            </div>`;
        return;
    }

    feed.innerHTML = state.transactions.map(tx => {
        const icon = getCategoryIcon(tx.category);
        const decisionText = tx.decision.toUpperCase();
        const timestamp = formatTime(tx.timestamp);
        
        let decisionBadgeClass = "badge-gray";
        if (tx.decision === "approved") decisionBadgeClass = "badge-green";
        else if (tx.decision === "blocked") decisionBadgeClass = "badge-red";
        else if (tx.decision === "escalated") decisionBadgeClass = "badge-yellow";

        return `
            <div class="tx-card ${tx.decision}" id="card-tx-${tx.id}">
                <div class="tx-icon-wrapper">
                    <i class="fa-solid ${icon}"></i>
                </div>
                <div class="tx-main-content">
                    <div class="tx-top-row">
                        <span class="tx-agent-info">${tx.agent_id}</span>
                        <span class="tx-amount">₹${tx.amount.toFixed(2)}</span>
                    </div>
                    <div class="tx-mid-row">
                        <span>Category: <strong>${tx.category}</strong></span>
                        <span>Time: ${timestamp}</span>
                    </div>
                    <div class="tx-reason-box">
                        <strong>Reason:</strong> ${tx.reason}
                    </div>
                    <div class="tx-footer">
                        <span>TX ID: ${tx.id}</span>
                        <span class="badge ${decisionBadgeClass}">${decisionText}</span>
                    </div>
                </div>
            </div>
        `;
    }).join("");
}

// Render Mandates panel
function renderMandates() {
    const list = document.getElementById("negotiations-list");
    const countBadge = document.getElementById("negotiation-count");
    
    countBadge.textContent = state.mandates.length;

    if (state.mandates.length === 0) {
        list.innerHTML = `
            <div class="empty-state">
                <i class="fa-solid fa-file-invoice empty-icon"></i>
                <p>No mandates negotiated yet.</p>
            </div>`;
        return;
    }

    list.innerHTML = state.mandates.map(mandate => {
        // Last item inside the negotiation logs represents outcome
        const logs = mandate.negotiation_log || [];
        const request = logs[0] || {};
        const outcome = logs[1] || {};
        const timestamp = formatTime(mandate.valid_from);
        
        const badgeClass = outcome.status === "countered" ? "badge-yellow" : "badge-green";
        const badgeText = outcome.status === "countered" ? "COUNTERED" : "APPROVED";

        return `
            <div class="neg-card">
                <div class="neg-header">
                    <span>${mandate.agent_id} &rarr; Shop</span>
                    <span>${timestamp}</span>
                </div>
                <div class="neg-scope">
                    <strong>Category:</strong> ${mandate.category} <br/>
                    Requested: <del>₹${request.requested ? request.requested.toFixed(2) : '0.00'}</del> | 
                    Granted Cap: <strong>₹${mandate.amount_cap.toFixed(2)}</strong>
                </div>
                <div class="neg-reason">
                    ${outcome.reason || "Mandate negotiated successfully."}
                </div>
                <div style="display: flex; justify-content: space-between; font-size: 0.7rem; color: var(--text-secondary);">
                    <span>ID: ${mandate.id}</span>
                    <span class="badge ${badgeClass}">${badgeText}</span>
                </div>
            </div>
        `;
    }).join("");
}

// Render Security Flags panel
function renderFlags() {
    const list = document.getElementById("flags-list");
    const countBadge = document.getElementById("flag-count");
    
    countBadge.textContent = state.flags.length;

    if (state.flags.length === 0) {
        list.innerHTML = `
            <div class="empty-state">
                <i class="fa-solid fa-shield-virus empty-icon"></i>
                <p>System secure. No threats detected.</p>
            </div>`;
        return;
    }

    list.innerHTML = state.flags.map(flag => {
        const time = formatTime(flag.timestamp);
        const icon = flag.type === "velocity" ? "fa-gauge-high" : "fa-network-wired";
        
        return `
            <div class="flag-card">
                <div class="flag-header">
                    <span class="flag-title red-text">
                        <i class="fa-solid ${icon}"></i> ${flag.type.toUpperCase()} FLAG
                    </span>
                    <span style="font-size: 0.72rem; color: var(--text-secondary);">${time}</span>
                </div>
                <div class="flag-desc">
                    ${flag.detail}
                </div>
                <div class="flag-tx-links">
                    <strong>TX Links:</strong> ${flag.related_transaction_ids.join(", ")}
                </div>
            </div>
        `;
    }).join("");
}

// Render Agent Identities Directory
function renderAgents() {
    const list = document.getElementById("agents-list");
    const countBadge = document.getElementById("agent-count");
    
    countBadge.textContent = state.agents.length;

    if (state.agents.length === 0) {
        list.innerHTML = `
            <tr>
                <td colspan="4" style="text-align: center; color: var(--text-secondary);">No agents identified.</td>
            </tr>`;
        return;
    }

    list.innerHTML = state.agents.map(agent => {
        let tierClass = "tier-new";
        if (agent.risk_tier === "established") tierClass = "tier-est";
        else if (agent.risk_tier === "flagged") tierClass = "tier-flagged";

        return `
            <tr onclick="showHistoryModal('${agent.id}')">
                <td style="font-weight: 500;">
                    ${agent.name}<br/>
                    <small style="color: var(--text-secondary); font-size: 0.7rem;">${agent.id}</small>
                </td>
                <td class="${tierClass}">${agent.risk_tier.toUpperCase()}</td>
                <td style="text-align: center; font-weight: 600; color: ${agent.violation_count > 0 ? 'var(--color-red)' : 'var(--text-primary)'}">
                    ${agent.violation_count}
                </td>
                <td onclick="event.stopPropagation()">
                    <button class="btn-action-small" onclick="resetAgentTier('${agent.id}')">
                        <i class="fa-solid fa-rotate-left"></i> Reset
                    </button>
                </td>
            </tr>
        `;
    }).join("");
}

// Render Pending Escalation Cards
function renderEscalations() {
    const list = document.getElementById("escalations-list");
    const countBadge = document.getElementById("escalation-count");

    // Filter escalated tx attempts that have not yet been approved or denied (meaning they are still status 'escalated')
    const escalations = state.transactions.filter(t => t.decision === "escalated");
    
    countBadge.textContent = escalations.length;

    if (escalations.length === 0) {
        list.innerHTML = `
            <div class="empty-state">
                <i class="fa-solid fa-circle-check green-text empty-icon"></i>
                <p>No transactions awaiting manual review.</p>
            </div>`;
        return;
    }

    list.innerHTML = escalations.map(tx => {
        return `
            <div class="escalation-card">
                <div class="escalation-meta">
                    <span>${tx.agent_id}</span>
                    <span>${formatTime(tx.timestamp)}</span>
                </div>
                <div class="escalation-info">
                    Requested <strong>₹${tx.amount.toFixed(2)}</strong> for ${tx.category}
                </div>
                <div class="escalation-reason">
                    ${tx.reason}
                </div>
                <div class="escalation-actions">
                    <button class="btn-approve" onclick="resolveEscalation('${tx.id}', true)">
                        <i class="fa-solid fa-circle-check"></i> Approve
                    </button>
                    <button class="btn-deny" onclick="resolveEscalation('${tx.id}', false)">
                        <i class="fa-solid fa-circle-xmark"></i> Deny
                    </button>
                </div>
            </div>
        `;
    }).join("");
}

// 5. Merchant Manual Review Decisions
async function resolveEscalation(txId, approve) {
    try {
        const res = await fetch(`${API_BASE}/api/escalation/${txId}/resolve`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ approved: approve })
        });
        
        if (res.ok) {
            const updatedTx = await res.json();
            console.log("Escalation resolved successfully:", updatedTx);
            // The WS update will push the update, but we update locally immediately for better latency
            const index = state.transactions.findIndex(t => t.id === txId);
            if (index !== -1) {
                state.transactions[index] = updatedTx;
                renderTransactions();
                renderEscalations();
            }
        }
    } catch (err) {
        console.error("Error resolving escalation:", err);
    }
}

// 6. Reset Flagged Agents (Demo Helper)
async function resetAgentTier(agentId) {
    try {
        const res = await fetch(`${API_BASE}/api/agents/${agentId}/reset`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ target_tier: "established" })
        });
        
        if (res.ok) {
            const updatedAgent = await res.json();
            console.log("Agent reset successfully:", updatedAgent);
            // Local state cache update
            const index = state.agents.findIndex(a => a.id === agentId);
            if (index !== -1) {
                state.agents[index] = updatedAgent;
                renderAgents();
            }
        }
    } catch (err) {
        console.error("Error resetting agent:", err);
    }
}

// 7. Risk Tier History Modal
async function showHistoryModal(agentId) {
    const modal = document.getElementById("history-modal");
    const metaContainer = document.getElementById("modal-agent-meta");
    const timeline = document.getElementById("history-timeline");

    // Show modal loading state
    modal.classList.add("show");
    metaContainer.innerHTML = `Loading details for <strong>${agentId}</strong>...`;
    timeline.innerHTML = `<div class="empty-state"><i class="fa-solid fa-rotate spin empty-icon"></i><p>Loading history...</p></div>`;

    try {
        const agent = state.agents.find(a => a.id === agentId);
        if (agent) {
            metaContainer.innerHTML = `
                <strong>Agent ID:</strong> ${agent.id} <br/>
                <strong>Name:</strong> ${agent.name} <br/>
                <strong>Current Risk Tier:</strong> <span class="tier-${agent.risk_tier === 'established' ? 'est' : (agent.risk_tier === 'flagged' ? 'flagged' : 'new')}">${agent.risk_tier.toUpperCase()}</span> <br/>
                <strong>Total Violation Count:</strong> ${agent.violation_count}
            `;
        }

        const res = await fetch(`${API_BASE}/api/agents/${agentId}/history`);
        if (res.ok) {
            const history = await res.json();
            if (history.length === 0) {
                timeline.innerHTML = `
                    <div class="empty-state">
                        <i class="fa-solid fa-history empty-icon"></i>
                        <p>No tier transitions logged. Agent has remained in its baseline tier.</p>
                    </div>`;
                return;
            }

            timeline.innerHTML = history.map(item => {
                const itemTime = new Date(item.timestamp).toLocaleString();
                let itemClass = "new";
                if (item.new_tier === "established") itemClass = "established";
                else if (item.new_tier === "flagged") itemClass = "flagged";

                return `
                    <div class="timeline-item ${itemClass}">
                        <div class="timeline-header">
                            <span class="timeline-transition">
                                ${item.old_tier.toUpperCase()} &rarr; ${item.new_tier.toUpperCase()}
                            </span>
                            <span>${itemTime}</span>
                        </div>
                        <div class="timeline-reason">
                            ${item.reason}
                        </div>
                    </div>
                `;
            }).join("");
        }
    } catch (err) {
        console.error("Error loading agent history:", err);
        timeline.innerHTML = `<div class="empty-state"><i class="fa-solid fa-exclamation-triangle empty-icon"></i><p>Error loading transition history.</p></div>`;
    }
}

function closeHistoryModal() {
    const modal = document.getElementById("history-modal");
    modal.classList.remove("show");
}

// Close modal when clicking outside of modal content
window.onclick = function(event) {
    const modal = document.getElementById("history-modal");
    if (event.target === modal) {
        modal.classList.remove("show");
    }
};
