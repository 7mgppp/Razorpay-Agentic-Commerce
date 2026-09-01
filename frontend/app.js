// WebSocket and API configuration
const API_BASE = `${window.location.origin}`;
const WS_URL = `ws://${window.location.host}/ws/live`;

// Page state cache
let state = {
    transactions: [],
    mandates: [],
    flags: [],
    agents: [],
    simulatorRunning: true,
    simulatorSpeed: 1.0,
    activeView: "view-overview",
    ledgerFilter: "all",
    threatFilter: "all",
    expandedTxIds: new Set(),
    expandedLedgerRows: new Set(),
    expandedThreatIds: new Set()
};

// WebSocket variable
let socket = null;

// Initialize on load
document.addEventListener("DOMContentLoaded", () => {
    initApp();
});

async function initApp() {
    setupEventListeners();
    handleHashNavigation();
    await fetchInitialData();
    connectWebSocket();
}

// 1. Navigation & View Switching
function switchView(viewId) {
    state.activeView = viewId;
    
    // Update nav tab active classes
    const tabs = document.querySelectorAll(".nav-tab");
    tabs.forEach(tab => {
        if (tab.getAttribute("data-view") === viewId) {
            tab.classList.add("active");
        } else {
            tab.classList.remove("active");
        }
    });

    // Update view sections visibility
    const views = document.querySelectorAll(".view-section");
    views.forEach(view => {
        if (view.id === viewId) {
            view.classList.add("active");
        } else {
            view.classList.remove("active");
        }
    });

    // Update URL hash without jumping
    const hashMapping = {
        "view-overview": "#overview",
        "view-ledger": "#ledger",
        "view-threats": "#security-threats",
        "view-negotiations": "#negotiations",
        "view-agents": "#agents",
        "view-reviews": "#reviews"
    };
    if (hashMapping[viewId] && window.location.hash !== hashMapping[viewId]) {
        history.replaceState(null, null, hashMapping[viewId]);
    }
}

function handleHashNavigation() {
    const hash = window.location.hash;
    const viewMapping = {
        "#overview": "view-overview",
        "#ledger": "view-ledger",
        "#security-threats": "view-threats",
        "#negotiations": "view-negotiations",
        "#agents": "view-agents",
        "#reviews": "view-reviews"
    };
    if (viewMapping[hash]) {
        switchView(viewMapping[hash]);
    }
}

// 2. Fetch data from REST APIs
async function fetchInitialData() {
    try {
        // Fetch Server Status
        const statusRes = await fetch(`${API_BASE}/api/status`);
        if (statusRes.ok) {
            const status = await statusRes.json();
            state.simulatorRunning = status.simulator_running;
            state.simulatorSpeed = status.speed || 1.0;
            updateSimulatorButtonUI();
            const speedSelect = document.getElementById("sim-speed-select");
            if (speedSelect) speedSelect.value = state.simulatorSpeed.toString();
        }

        // Fetch Transactions
        const txRes = await fetch(`${API_BASE}/api/transactions`);
        if (txRes.ok) {
            state.transactions = await txRes.json();
        }

        // Fetch Mandates
        const mandateRes = await fetch(`${API_BASE}/api/mandates`);
        if (mandateRes.ok) {
            state.mandates = await mandateRes.json();
        }

        // Fetch Flags
        const flagRes = await fetch(`${API_BASE}/api/flags`);
        if (flagRes.ok) {
            state.flags = await flagRes.json();
        }

        // Fetch Agents
        const agentRes = await fetch(`${API_BASE}/api/agents`);
        if (agentRes.ok) {
            state.agents = await agentRes.json();
        }

        renderAll();

    } catch (err) {
        console.error("Error loading initial data:", err);
    }
}

// 3. Connect WebSocket
function connectWebSocket() {
    const connectionBadge = document.getElementById("connection-badge");
    const connectionText = document.getElementById("connection-text");

    console.log("Connecting to WS:", WS_URL);
    socket = new WebSocket(WS_URL);

    socket.onopen = () => {
        if (connectionBadge) connectionBadge.className = "status-indicator connected";
        if (connectionText) connectionText.textContent = "Live Socket Connected";
        console.log("WebSocket connection established.");
    };

    socket.onmessage = async (event) => {
        try {
            const payload = JSON.parse(event.data);

            if (payload.type === "negotiation") {
                state.mandates.unshift(payload.data);
                if (state.mandates.length > 50) state.mandates.pop();
                
                const index = state.agents.findIndex(a => a.id === payload.agent.id);
                if (index !== -1) {
                    state.agents[index] = payload.agent;
                }
                renderMandates();
                renderAgents();
                renderOverview();
                updateNavBadges();

            } else if (payload.type === "transaction") {
                state.transactions.unshift(payload.data);
                if (state.transactions.length > 100) state.transactions.pop();

                const index = state.agents.findIndex(a => a.id === payload.agent.id);
                if (index !== -1) {
                    state.agents[index] = payload.agent;
                } else if (payload.agent && payload.agent.id) {
                    state.agents.push(payload.agent);
                }

                if (payload.flag) {
                    state.flags.unshift(payload.flag);
                    if (state.flags.length > 50) state.flags.pop();
                    renderFlags();
                }

                renderTransactions();
                renderOverview();
                renderEscalations();
                renderAgents();
                updateNavBadges();

            } else if (payload.type === "transaction_update") {
                const index = state.transactions.findIndex(t => t.id === payload.data.id);
                if (index !== -1) {
                    state.transactions[index] = payload.data;
                }
                
                const agentIdx = state.agents.findIndex(a => a.id === payload.agent.id);
                if (agentIdx !== -1) {
                    state.agents[agentIdx] = payload.agent;
                }
                
                renderTransactions();
                renderOverview();
                renderEscalations();
                renderAgents();
                updateNavBadges();

            } else if (payload.type === "agent_reset") {
                const index = state.agents.findIndex(a => a.id === payload.agent.id);
                if (index !== -1) {
                    state.agents[index] = payload.agent;
                    renderAgents();
                    renderOverview();
                    updateNavBadges();
                }
            }

        } catch (err) {
            console.error("Error parsing WS packet:", err);
        }
    };

    socket.onclose = () => {
        if (connectionBadge) connectionBadge.className = "status-indicator disconnected";
        if (connectionText) connectionText.textContent = "WebSocket Disconnected";
        console.log("WebSocket connection closed. Retrying in 3 seconds...");
        setTimeout(connectWebSocket, 3000);
    };

    socket.onerror = (err) => {
        console.error("WebSocket encountered error:", err);
        socket.close();
    };
}

// 4. Master Render All
function renderAll() {
    renderOverview();
    renderTransactions();
    renderFlags();
    renderMandates();
    renderAgents();
    renderEscalations();
    updateNavBadges();
}

function updateNavBadges() {
    const ledgerBadge = document.getElementById("ledger-nav-badge");
    const threatBadge = document.getElementById("threat-nav-badge");
    const negBadge = document.getElementById("negotiation-nav-badge");
    const agentBadge = document.getElementById("agent-nav-badge");
    const escBadge = document.getElementById("escalation-nav-count");

    if (ledgerBadge) ledgerBadge.textContent = state.transactions.length;
    if (threatBadge) threatBadge.textContent = state.flags.length;
    if (negBadge) negBadge.textContent = state.mandates.length;
    if (agentBadge) agentBadge.textContent = state.agents.length;

    const pendingEscalations = state.transactions.filter(t => t.decision === "escalated");
    if (escBadge) escBadge.textContent = pendingEscalations.length;

    // Threat category section count badges
    const velCountBadge = document.getElementById("threat-count-velocity");
    const colCountBadge = document.getElementById("threat-count-collusion");
    const intCountBadge = document.getElementById("threat-count-intent");

    if (velCountBadge) velCountBadge.textContent = `${state.flags.filter(f => f.type === "velocity").length} Alerts`;
    if (colCountBadge) colCountBadge.textContent = `${state.flags.filter(f => f.type === "collusion").length} Alerts`;
    if (intCountBadge) intCountBadge.textContent = `${state.flags.filter(f => f.type === "intent_mismatch").length} Alerts`;
}

// 5. Setup Event Listeners
function setupEventListeners() {
    const toggleBtn = document.getElementById("toggle-simulator-btn");
    if (toggleBtn) toggleBtn.addEventListener("click", toggleSimulator);

    window.addEventListener("hashchange", handleHashNavigation);
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
    if (!toggleBtn) return;

    if (state.simulatorRunning) {
        toggleBtn.className = "control-btn shadow-neon";
        toggleBtn.innerHTML = `<i class="fa-solid fa-pause"></i> <span>Pause Simulation</span>`;
    } else {
        toggleBtn.className = "control-btn paused shadow-neon";
        toggleBtn.innerHTML = `<i class="fa-solid fa-play"></i> <span>Resume Simulation</span>`;
    }
}

async function changeSimulatorSpeed(speedValue) {
    const speed = parseFloat(speedValue);
    try {
        const res = await fetch(`${API_BASE}/api/simulator/speed`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ speed: speed })
        });
        if (res.ok) {
            const data = await res.json();
            state.simulatorSpeed = data.speed;
            console.log(`Simulator speed set to ${data.speed}x (Interval: ${data.current_interval}s)`);
        }
    } catch (err) {
        console.error("Error changing simulator speed:", err);
    }
}

// 6. Render Utilities (Timezone-Aware UTC to Local Time Conversion)
function parseUtcDate(dateStr) {
    if (!dateStr) return null;
    if (dateStr instanceof Date) return dateStr;
    // If ISO string lacks timezone offset/Z, explicitly append Z so browser treats it as UTC
    if (typeof dateStr === "string" && !dateStr.endsWith("Z") && !dateStr.includes("+") && !dateStr.match(/T.*-\d{2}:\d{2}$/)) {
        return new Date(dateStr + "Z");
    }
    return new Date(dateStr);
}

function formatTime(dateStr) {
    const date = parseUtcDate(dateStr);
    if (!date || isNaN(date.getTime())) return "";
    return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

function formatDateTime(dateStr) {
    const date = parseUtcDate(dateStr);
    if (!date || isNaN(date.getTime())) return "";
    return date.toLocaleString();
}

function formatAlertSummary(flag) {
    const detail = flag.detail || "";
    const ftype = flag.type;
    const agentId = flag.agent_id || "agent";
    const txIds = flag.related_transaction_ids || [];

    if (ftype === "velocity") {
        const amtMatch = detail.match(/₹([\d,\.]+)/);
        const amt = amtMatch ? amtMatch[1] : "0.00";
        const countMatch = detail.match(/(\d+)\s+transactions/);
        const n = countMatch ? countMatch[1] : (txIds.length || 4);
        return `${agentId} made ${n} purchases in 30s — ₹${amt} (exceeds 4+ purchase threshold)`;

    } else if (ftype === "collusion") {
        const countMatch = detail.match(/(\d+)\s+distinct agents/);
        const n = countMatch ? countMatch[1] : "3";
        const merchMatch = detail.match(/at merchant '([^']+)'/);
        let merch = merchMatch ? merchMatch[1] : "Razorpay Shop";
        if (merch.includes("merchant_")) merch = merch.replace("merchant_", "").replace(/_/g, " ");
        const amtMatch = detail.match(/totaling\s+₹([\d,\.]+)/) || detail.match(/₹([\d,\.]+)/);
        const amt = amtMatch ? amtMatch[1] : "24,000.00";
        return `${n} agents coordinated at ${merch} to stay under ₹12,000 threshold — ₹${amt} total`;

    } else if (ftype === "intent_mismatch") {
        const purpMatch = detail.match(/Mandate scoped for:\s*([^.]+)\./);
        let purp = purpMatch ? purpMatch[1].trim() : "authorized scope";
        if (purp.includes(":")) {
            purp = purp.split(":")[0].trim();
        } else if (purp.split(" ").length > 5) {
            purp = purp.split(" ").slice(0, 4).join(" ");
        }

        const itemMatch = detail.match(/Actual charge:\s*([^(]+)\s*\((₹[\d,\.]+)\)/);
        let item = "unauthorized item";
        let amt = "₹0.00";
        if (itemMatch) {
            item = itemMatch[1].trim();
            amt = itemMatch[2].trim();
        }
        return `${agentId}: expected ${purp}, got ${item} (${amt})`;
    }

    return detail;
}

function setThreatFilter(filter) {
    state.threatFilter = filter;
    
    const pills = document.querySelectorAll("#threat-filter-group .filter-pill");
    pills.forEach(pill => {
        if (
            (filter === "all" && pill.textContent.trim().startsWith("All")) ||
            (filter === "velocity" && pill.classList.contains("pill-velocity")) ||
            (filter === "collusion" && pill.classList.contains("pill-collusion")) ||
            (filter === "intent_mismatch" && pill.classList.contains("pill-intent"))
        ) {
            pill.classList.add("active");
        } else {
            pill.classList.remove("active");
        }
    });
    
    renderFlags();
}

function toggleThreatExpand(flagId) {
    if (state.expandedThreatIds.has(flagId)) {
        state.expandedThreatIds.delete(flagId);
    } else {
        state.expandedThreatIds.add(flagId);
    }
    renderFlags();
}

// Render Overview View (KPIs & Top 3 Scannable Alerts)
function renderOverview() {
    const approvedTxs = state.transactions.filter(t => t.decision === "approved");
    const blockedTxs = state.transactions.filter(t => t.decision === "blocked");
    const escalatedTxs = state.transactions.filter(t => t.decision === "escalated");
    const flaggedAgents = state.agents.filter(a => a.risk_tier === "flagged");

    const totalApprovedSum = approvedTxs.reduce((sum, t) => sum + (t.amount || 0), 0);

    const kpiApprovedCount = document.getElementById("kpi-approved-count");
    const kpiApprovedSum = document.getElementById("kpi-approved-sum");
    const kpiBlockedCount = document.getElementById("kpi-blocked-count");
    const kpiBlockedSub = document.getElementById("kpi-blocked-sub");
    const kpiEscalatedCount = document.getElementById("kpi-escalated-count");
    const kpiFlaggedAgents = document.getElementById("kpi-flagged-agents-count");
    const kpiTotalAgents = document.getElementById("kpi-total-agents-count");

    if (kpiApprovedCount) kpiApprovedCount.textContent = approvedTxs.length;
    if (kpiApprovedSum) kpiApprovedSum.textContent = `₹${totalApprovedSum.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
    if (kpiBlockedCount) kpiBlockedCount.textContent = blockedTxs.length;
    if (kpiBlockedSub) kpiBlockedSub.textContent = `${blockedTxs.length} Out-of-Scope / Blocked`;
    if (kpiEscalatedCount) kpiEscalatedCount.textContent = escalatedTxs.length;
    if (kpiFlaggedAgents) kpiFlaggedAgents.textContent = flaggedAgents.length;
    if (kpiTotalAgents) kpiTotalAgents.textContent = `of ${state.agents.length} Identified`;

    // Render Recent Threats Preview (Single most recent alert from each of the 3 categories)
    const threatsList = document.getElementById("overview-threats-list");
    if (threatsList) {
        const latestVelocity = state.flags.find(f => f.type === "velocity");
        const latestCollusion = state.flags.find(f => f.type === "collusion");
        const latestIntent = state.flags.find(f => f.type === "intent_mismatch");

        const sampleFlags = [latestVelocity, latestCollusion, latestIntent].filter(Boolean);

        if (sampleFlags.length === 0) {
            threatsList.innerHTML = `
                <div class="empty-state">
                    <i class="fa-solid fa-shield-virus green-text empty-icon"></i>
                    <p>System secure. No suspicious activity or threat patterns detected.</p>
                </div>`;
        } else {
            threatsList.innerHTML = sampleFlags.map(flag => {
                const time = formatTime(flag.timestamp);
                const summary = formatAlertSummary(flag);
                const isIntent = flag.type === "intent_mismatch";
                const isVelocity = flag.type === "velocity";

                let cardTypeClass = "threat-card-collusion";
                let badgeClass = "badge-red";
                let badgeText = "COLLUSION — Agents working together?";
                let icon = "fa-network-wired";

                if (isVelocity) {
                    cardTypeClass = "threat-card-velocity";
                    badgeClass = "badge-orange";
                    badgeText = "VELOCITY — Spending too fast?";
                    icon = "fa-gauge-high";
                } else if (isIntent) {
                    cardTypeClass = "threat-card-intent";
                    badgeClass = "badge-cyan";
                    badgeText = "INTENT MISMATCH — Bought the wrong thing?";
                    icon = "fa-bullseye";
                }

                return `
                    <div class="overview-threat-card ${cardTypeClass}">
                        <div class="threat-summary-row">
                            <div class="threat-badge-group">
                                <span class="badge ${badgeClass}"><i class="fa-solid ${icon}"></i> ${badgeText}</span>
                                <span class="threat-summary-text">${summary}</span>
                            </div>
                            <span class="threat-time">${time}</span>
                        </div>
                    </div>
                `;
            }).join("");
        }
    }
}

// 7. Render Live Transaction Ledger (Compact Single-line Rows with Expandable Drawer)
function setLedgerFilter(filter) {
    state.ledgerFilter = filter;
    
    // Update filter pill active classes
    const pills = document.querySelectorAll("#ledger-filter-group .filter-pill");
    pills.forEach(pill => {
        if (
            (filter === "all" && pill.textContent.trim().startsWith("All")) ||
            (filter === "approved" && pill.classList.contains("pill-approved")) ||
            (filter === "blocked" && pill.classList.contains("pill-blocked")) ||
            (filter === "escalated" && pill.classList.contains("pill-escalated"))
        ) {
            pill.classList.add("active");
        } else {
            pill.classList.remove("active");
        }
    });

    renderTransactions();
}

function toggleTxExpand(txId) {
    if (state.expandedTxIds.has(txId)) {
        state.expandedTxIds.delete(txId);
    } else {
        state.expandedTxIds.add(txId);
    }
    renderTransactions();
}

function renderTransactions() {
    const feed = document.getElementById("transactions-feed");
    if (!feed) return;

    // Update filter counts
    const allCount = document.getElementById("filter-count-all");
    const appCount = document.getElementById("filter-count-approved");
    const blkCount = document.getElementById("filter-count-blocked");
    const escCount = document.getElementById("filter-count-escalated");

    const approvedTxs = state.transactions.filter(t => t.decision === "approved");
    const blockedTxs = state.transactions.filter(t => t.decision === "blocked");
    const escalatedTxs = state.transactions.filter(t => t.decision === "escalated");

    if (allCount) allCount.textContent = state.transactions.length;
    if (appCount) appCount.textContent = approvedTxs.length;
    if (blkCount) blkCount.textContent = blockedTxs.length;
    if (escCount) escCount.textContent = escalatedTxs.length;

    let filteredTxs = state.transactions;
    if (state.ledgerFilter !== "all") {
        filteredTxs = state.transactions.filter(t => t.decision === state.ledgerFilter);
    }

    if (filteredTxs.length === 0) {
        feed.innerHTML = `
            <div class="empty-state">
                <i class="fa-solid fa-folder-open empty-icon"></i>
                <p>No transactions matching filter '${state.ledgerFilter}'.</p>
            </div>`;
        return;
    }

    feed.innerHTML = filteredTxs.map(tx => {
        const isExpanded = state.expandedTxIds.has(tx.id);
        const decisionText = tx.decision.toUpperCase();
        const timestamp = formatTime(tx.timestamp);
        
        let decisionBadgeClass = "badge-gray";
        if (tx.decision === "approved") decisionBadgeClass = "badge-green";
        else if (tx.decision === "blocked") decisionBadgeClass = "badge-red";
        else if (tx.decision === "escalated") decisionBadgeClass = "badge-yellow";

        return `
            <div class="ledger-row-item ${tx.decision} ${isExpanded ? 'expanded' : ''}" id="tx-row-${tx.id}">
                <div class="ledger-row-summary" onclick="toggleTxExpand('${tx.id}')">
                    <span class="row-expand-icon"><i class="fa-solid fa-chevron-right"></i></span>
                    <span class="row-time">${timestamp}</span>
                    <span class="row-txid">${tx.id}</span>
                    <span class="row-agent"><i class="fa-solid fa-robot"></i> ${tx.agent_id}</span>
                    <span class="row-category">${tx.category ? tx.category.replace('_', ' ') : 'N/A'}</span>
                    <span class="row-amount">₹${tx.amount.toFixed(2)}</span>
                    <span class="row-decision"><span class="badge ${decisionBadgeClass}">${decisionText}</span></span>
                </div>
                <div class="ledger-row-drawer">
                    <div class="drawer-reason-box">
                        <strong>Evaluation Reason:</strong> ${tx.reason}
                    </div>
                    <div class="drawer-meta-row">
                        <span><strong>Mandate ID:</strong> ${tx.mandate_id || 'N/A'}</span>
                        <span><strong>Category:</strong> ${tx.category}</span>
                        <span><strong>Timestamp:</strong> ${formatDateTime(tx.timestamp)}</span>
                        ${tx.decision === 'escalated' ? `
                        <div style="display:flex; gap:8px;">
                            <button class="btn-approve" onclick="resolveEscalation('${tx.id}', 'approved')"><i class="fa-solid fa-check"></i> Approve</button>
                            <button class="btn-deny" onclick="resolveEscalation('${tx.id}', 'blocked')"><i class="fa-solid fa-ban"></i> Deny</button>
                        </div>` : ''}
                    </div>
                </div>
            </div>
        `;
    }).join("");
}

// Helper: Render an individual scannable threat card with drawer
function renderThreatCardHtml(flag) {
    const isExpanded = state.expandedThreatIds.has(flag.id);
    const time = formatTime(flag.timestamp);
    const summary = formatAlertSummary(flag);
    const isIntent = flag.type === "intent_mismatch";
    const isVelocity = flag.type === "velocity";

    let cardTypeClass = "threat-card-collusion";
    let badgeClass = "badge-red";
    let badgeText = "COLLUSION — Agents working together?";
    let icon = "fa-network-wired";

    if (isVelocity) {
        cardTypeClass = "threat-card-velocity";
        badgeClass = "badge-orange";
        badgeText = "VELOCITY — Spending too fast?";
        icon = "fa-gauge-high";
    } else if (isIntent) {
        cardTypeClass = "threat-card-intent";
        badgeClass = "badge-cyan";
        badgeText = "INTENT MISMATCH — Bought the wrong thing?";
        icon = "fa-bullseye";
    }

    const txLinks = (flag.related_transaction_ids || []).map(txid => `<span class="tx-chip">${txid}</span>`).join(" ");

    return `
        <div class="threat-alert-card ${cardTypeClass} ${isExpanded ? 'expanded' : ''}" id="threat-card-${flag.id}">
            <div class="threat-card-main">
                <div class="threat-summary-row">
                    <div class="threat-badge-group">
                        <span class="badge ${badgeClass}"><i class="fa-solid ${icon}"></i> ${badgeText}</span>
                        <span class="threat-summary-text">${summary}</span>
                    </div>
                    <div class="threat-action-group">
                        <span class="threat-time">${time}</span>
                        <button class="btn-toggle-details" onclick="toggleThreatExpand('${flag.id}')">
                            ${isExpanded ? 'Hide details <i class="fa-solid fa-chevron-up"></i>' : 'Show details <i class="fa-solid fa-chevron-down"></i>'}
                        </button>
                    </div>
                </div>
            </div>
            <div class="threat-details-drawer">
                <div class="threat-detail-text">
                    <strong>Finding Details:</strong> ${flag.detail}
                </div>
                ${flag.type === "intent_mismatch" || flag.ai_reasoning ? `
                <div class="threat-ai-reasoning-row">
                    <strong><i class="fa-solid fa-brain cyan-text"></i> AI Reasoning:</strong>
                    <span class="threat-ai-reasoning-text">${flag.ai_reasoning || 'Rule-based (AI unavailable)'}</span>
                    ${flag.source === 'ai_llm' ? `<span class="badge badge-cyan" style="font-size:0.7rem; padding: 2px 7px; margin-left: 6px;">${(flag.confidence || 'HIGH').toUpperCase()} CONFIDENCE</span>` : `<span class="badge badge-gray" style="font-size:0.7rem; padding: 2px 7px; margin-left: 6px;">Rule-based (AI unavailable)</span>`}
                </div>` : ''}
                <div class="threat-meta-row">
                    <div class="threat-tx-chips">
                        <strong>Linked Transactions:</strong> ${txLinks || 'None'}
                    </div>
                    <div class="threat-agent-tag">
                        <strong>Agent:</strong> <code>${flag.agent_id || 'Coordinated Syndicate'}</code>
                    </div>
                    <div class="threat-date-tag">
                        <strong>Recorded:</strong> ${formatDateTime(flag.timestamp)}
                    </div>
                </div>
            </div>
        </div>
    `;
}

// 8. Render Security Flags View (3 Stacked Vertical Sections)
function renderFlags() {
    const velContainer = document.getElementById("threats-velocity-list");
    const colContainer = document.getElementById("threats-collusion-list");
    const intContainer = document.getElementById("threats-intent-list");

    const velCountBadge = document.getElementById("threat-count-velocity");
    const colCountBadge = document.getElementById("threat-count-collusion");
    const intCountBadge = document.getElementById("threat-count-intent");

    const velFlags = state.flags.filter(f => f.type === "velocity");
    const colFlags = state.flags.filter(f => f.type === "collusion");
    const intFlags = state.flags.filter(f => f.type === "intent_mismatch");

    if (velCountBadge) velCountBadge.textContent = `${velFlags.length} Alerts`;
    if (colCountBadge) colCountBadge.textContent = `${colFlags.length} Alerts`;
    if (intCountBadge) intCountBadge.textContent = `${intFlags.length} Alerts`;

    if (velContainer) {
        if (velFlags.length === 0) {
            velContainer.innerHTML = `
                <div class="empty-state">
                    <i class="fa-solid fa-shield-virus green-text empty-icon"></i>
                    <p>No rapid spending alerts detected.</p>
                </div>`;
        } else {
            velContainer.innerHTML = velFlags.map(renderThreatCardHtml).join("");
        }
    }

    if (colContainer) {
        if (colFlags.length === 0) {
            colContainer.innerHTML = `
                <div class="empty-state">
                    <i class="fa-solid fa-shield-virus green-text empty-icon"></i>
                    <p>No multi-agent collusion alerts detected.</p>
                </div>`;
        } else {
            colContainer.innerHTML = colFlags.map(renderThreatCardHtml).join("");
        }
    }

    if (intContainer) {
        if (intFlags.length === 0) {
            intContainer.innerHTML = `
                <div class="empty-state">
                    <i class="fa-solid fa-shield-virus green-text empty-icon"></i>
                    <p>No wrong purchase (intent mismatch) alerts detected.</p>
                </div>`;
        } else {
            intContainer.innerHTML = intFlags.map(renderThreatCardHtml).join("");
        }
    }
}

// 9. Render Mandates Negotiations View (Full Page)
function renderMandates() {
    const list = document.getElementById("negotiations-list");
    const countBadge = document.getElementById("negotiation-count");
    
    if (countBadge) countBadge.textContent = `${state.mandates.length} Mandates`;

    if (!list) return;

    if (state.mandates.length === 0) {
        list.innerHTML = `
            <div class="empty-state">
                <i class="fa-solid fa-file-invoice empty-icon"></i>
                <p>No mandates negotiated yet.</p>
            </div>`;
        return;
    }

    list.innerHTML = state.mandates.map(mandate => {
        const logs = mandate.negotiation_log || [];
        const request = logs[0] || {};
        const outcome = logs[1] || {};
        const timestamp = formatTime(mandate.valid_from);
        
        const isCountered = outcome.status === "countered";
        const badgeClass = isCountered ? "badge-countered" : "badge-green";
        const badgeIcon = isCountered ? "fa-handshake-simple" : "fa-circle-check";
        const badgeText = isCountered ? "COUNTERED" : "APPROVED";
        const cardClass = isCountered ? "neg-card countered" : "neg-card approved";

        const requestedCap = request.requested !== undefined ? request.requested : mandate.amount_cap;
        const grantedCap = mandate.amount_cap;
        const reqWindow = request.requested_validity_minutes || 60;
        const grantedWindow = outcome.validity_minutes || reqWindow;
        const condition = outcome.condition || "Standard policy clearance";
        const llmReasoning = outcome.llm_reasoning || outcome.reason || "Evaluated against active merchant risk policies.";
        const statedPurpose = mandate.stated_purpose || request.purpose || "General enterprise procurement";

        return `
            <div class="${cardClass}">
                <div class="neg-header">
                    <div class="neg-agent-info">
                        <span class="neg-agent-name"><i class="fa-solid fa-robot"></i> <strong>${mandate.agent_id}</strong></span>
                        <span class="neg-arrow">&rarr;</span>
                        <span class="neg-merchant-name"><i class="fa-solid fa-shield-halved blue-text"></i> Mandate Layer</span>
                    </div>
                    <span class="neg-time">${timestamp}</span>
                </div>

                <!-- Stated Plain-Language Purpose -->
                <div class="neg-purpose-box">
                    <i class="fa-solid fa-bullseye cyan-text"></i>
                    <span><strong>Stated Purpose:</strong> <em>"${statedPurpose}"</em></span>
                </div>

                <div class="neg-scope-row">
                    <div class="neg-category-pill">
                        <i class="fa-solid fa-tag"></i> <span>${mandate.category ? mandate.category.replace('_', ' ') : 'General'}</span>
                    </div>
                    <div class="neg-caps-display">
                        ${isCountered ? `
                            <span class="neg-cap-requested">Requested: <del>₹${requestedCap.toFixed(2)}</del></span>
                            <span class="neg-cap-divider">&bull;</span>
                            <span class="neg-cap-countered">Counter-Cap: <strong>₹${grantedCap.toFixed(2)}</strong></span>
                        ` : `
                            <span class="neg-cap-approved">Granted Cap: <strong>₹${grantedCap.toFixed(2)}</strong></span>
                        `}
                    </div>
                </div>

                <!-- Terms Breakdown -->
                <div class="neg-terms-box">
                    <div class="counter-term-item">
                        <i class="fa-solid fa-clock-rotate-left"></i>
                        <span><strong>Validity:</strong> ${grantedWindow} mins ${isCountered ? `<span class="term-muted">(Requested: ${reqWindow} mins)</span>` : ''}</span>
                    </div>
                    <div class="counter-term-item">
                        <i class="fa-solid ${isCountered ? 'fa-scale-balanced purple-text' : 'fa-check-double green-text'}"></i>
                        <span><strong>Provisional Terms:</strong> <span class="${isCountered ? 'term-highlight' : ''}">${condition}</span></span>
                    </div>
                </div>

                <!-- LLM Reasoning Box -->
                <div class="neg-reason ${isCountered ? 'countered-reason' : 'approved-reason'}">
                    <div class="neg-reason-header">
                        <i class="fa-solid ${isCountered ? 'fa-brain purple-text' : 'fa-circle-info green-text'}"></i>
                        <strong>${isCountered ? 'LLM Counter-Proposal Rationale:' : 'Policy Evaluation:'}</strong>
                    </div>
                    <div class="neg-reason-body">
                        ${llmReasoning}
                    </div>
                </div>

                <div class="neg-footer">
                    <span class="neg-mandate-id"><strong>Mandate ID:</strong> ${mandate.id}</span>
                    <span class="badge ${badgeClass}"><i class="fa-solid ${badgeIcon}"></i> ${badgeText}</span>
                </div>
            </div>
        `;
    }).join("");
}

// 10. Render Agent Identities Registry View (Full Page)
function renderAgents() {
    const list = document.getElementById("agents-list");
    const countBadge = document.getElementById("agent-count");
    
    if (countBadge) countBadge.textContent = `${state.agents.length} Agents`;

    if (!list) return;

    if (state.agents.length === 0) {
        list.innerHTML = `
            <tr>
                <td colspan="5" style="text-align: center; color: var(--text-secondary); padding: 24px;">No agents identified.</td>
            </tr>`;
        return;
    }

    list.innerHTML = state.agents.map(agent => {
        let tierClass = "tier-new";
        if (agent.risk_tier === "established") tierClass = "tier-est";
        else if (agent.risk_tier === "flagged") tierClass = "tier-flagged";

        return `
            <tr>
                <td>
                    <div class="agent-name-cell">
                        <span class="agent-title">${agent.name}</span>
                        <span class="agent-sub-id">${agent.id}</span>
                    </div>
                </td>
                <td><span class="tier-pill ${tierClass}">${agent.risk_tier.toUpperCase()}</span></td>
                <td style="text-align: center;">
                    <span class="violation-count-badge ${agent.violation_count > 0 ? 'has-violations' : ''}">${agent.violation_count}</span>
                </td>
                <td>
                    <button class="btn-action-small btn-timeline" onclick="showHistoryModal('${agent.id}')">
                        <i class="fa-solid fa-clock-rotate-left"></i> View Timeline
                    </button>
                </td>
                <td style="text-align: right;">
                    <button class="btn-action-small" onclick="resetAgentTier('${agent.id}')" title="Reset risk tier">
                        <i class="fa-solid fa-rotate-left"></i> Reset
                    </button>
                </td>
            </tr>
        `;
    }).join("");
}

// 11. Render Pending Escalation Cards (Reviews View)
function renderEscalations() {
    const list = document.getElementById("escalations-list");
    const countBadge = document.getElementById("escalation-count");
    const navCountBadge = document.getElementById("escalation-nav-count");

    const escalations = state.transactions.filter(t => t.decision === "escalated");
    
    if (countBadge) countBadge.textContent = `${escalations.length} Pending`;
    if (navCountBadge) navCountBadge.textContent = escalations.length;

    if (!list) return;

    if (escalations.length === 0) {
        list.innerHTML = `
            <div class="empty-state">
                <i class="fa-solid fa-circle-check green-text empty-icon"></i>
                <p>No transactions awaiting manual review. System is running cleanly.</p>
            </div>`;
        return;
    }

    list.innerHTML = escalations.map(tx => {
        const time = formatTime(tx.timestamp);
        return `
            <div class="escalation-card">
                <div class="escalation-meta">
                    <span>TX ID: <strong>${tx.id}</strong></span>
                    <span>${time}</span>
                </div>
                <div class="escalation-info">
                    Requested <strong>₹${tx.amount.toFixed(2)}</strong> for ${tx.category ? tx.category.replace('_', ' ') : 'purchase'}
                </div>
                <div class="escalation-reason">
                    <strong>Escalation Reason:</strong> ${tx.reason}
                </div>
                <div class="escalation-actions">
                    <button class="btn-approve" onclick="resolveEscalation('${tx.id}', 'approved')">
                        <i class="fa-solid fa-check"></i> Approve Transaction
                    </button>
                    <button class="btn-deny" onclick="resolveEscalation('${tx.id}', 'blocked')">
                        <i class="fa-solid fa-ban"></i> Deny & Block
                    </button>
                </div>
            </div>
        `;
    }).join("");
}

// 12. Human-in-the-Loop Escalation Resolution Actions
async function resolveEscalation(txId, resolution) {
    try {
        const res = await fetch(`${API_BASE}/api/escalations/${txId}/resolve`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ resolution })
        });
        if (res.ok) {
            const data = await res.json();
            const index = state.transactions.findIndex(t => t.id === txId);
            if (index !== -1) {
                state.transactions[index].decision = resolution;
                state.transactions[index].reason = `[Manual Resolution: ${resolution.toUpperCase()}] ${state.transactions[index].reason}`;
            }
            renderAll();
        }
    } catch (err) {
        console.error("Error resolving escalation:", err);
    }
}

// 13. Reset Agent Tier Action
async function resetAgentTier(agentId) {
    try {
        const res = await fetch(`${API_BASE}/api/agents/${agentId}/reset`, { method: "POST" });
        if (res.ok) {
            const updatedAgent = await res.json();
            const index = state.agents.findIndex(a => a.id === agentId);
            if (index !== -1) {
                state.agents[index] = updatedAgent;
            }
            renderAll();
        }
    } catch (err) {
        console.error("Error resetting agent:", err);
    }
}

// 14. History Modal Dialog Logic
async function showHistoryModal(agentId) {
    const modal = document.getElementById("history-modal");
    const metaContainer = document.getElementById("modal-agent-meta");
    const timeline = document.getElementById("history-timeline");

    modal.classList.add("show");
    timeline.innerHTML = `<div class="empty-state"><i class="fa-solid fa-rotate spin empty-icon"></i><p>Loading agent history...</p></div>`;

    try {
        const agent = state.agents.find(a => a.id === agentId);
        if (agent) {
            metaContainer.innerHTML = `
                <strong>Agent:</strong> ${agent.name} (${agent.id})<br/>
                <strong>Current Risk Tier:</strong> <span class="badge ${agent.risk_tier === 'established' ? 'badge-green' : (agent.risk_tier === 'flagged' ? 'badge-red' : 'badge-blue')}">${agent.risk_tier.toUpperCase()}</span><br/>
                <strong>Total Violation Count:</strong> ${agent.violation_count}
            `;
        }

        const res = await fetch(`${API_BASE}/api/agents/${agentId}/history`);
        if (res.ok) {
            const history = await res.json();
            if (history.length === 0) {
                timeline.innerHTML = `
                    <div class="empty-state">
                        <i class="fa-solid fa-clock-rotate-left empty-icon"></i>
                        <p>No tier transitions logged. Agent has remained in its baseline tier.</p>
                    </div>`;
                return;
            }

            timeline.innerHTML = history.map(item => {
                const itemTime = formatDateTime(item.timestamp);
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
                        <div class="timeline-reason">${item.reason}</div>
                    </div>
                `;
            }).join("");
        }
    } catch (err) {
        console.error("Error loading agent history:", err);
        timeline.innerHTML = `<div class="empty-state"><i class="fa-solid fa-triangle-exclamation empty-icon"></i><p>Error loading transition history.</p></div>`;
    }
}

function closeHistoryModal() {
    const modal = document.getElementById("history-modal");
    if (modal) modal.classList.remove("show");
}

window.onclick = function(event) {
    const modal = document.getElementById("history-modal");
    if (event.target === modal) {
        modal.classList.remove("show");
    }
};
