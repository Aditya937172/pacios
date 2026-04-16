const API_BASE = (
    window.location.protocol.startsWith("http") &&
    (window.location.port === "8000" || window.location.port === "")
)
    ? ""
    : "http://127.0.0.1:8000";
const REQUEST_TIMEOUT_MS = 20000;
const CLIENT_CACHE_TTL_MS = 20 * 1000;
const LOCAL_CACHE_PREFIX = "pacificaedge-dashboard:v6:";

const AGENT_META = {
    frontdesk: { title: "Frontdesk Agent", description: "Collaborative desk that combines all specialist views." },
    market: { title: "Market Agent", description: "Price structure, trend, and participation." },
    funding: { title: "Funding Agent", description: "Perp carry, crowding, and rate skew." },
    liquidation: { title: "Liquidation Agent", description: "Forced positioning exits and squeeze pressure." },
    sentiment: { title: "Sentiment Agent", description: "Elfa attention, ranking, and social heat." },
    narrative: { title: "Narrative Agent", description: "Catalysts, themes, and market story." },
    orderbook: { title: "Orderbook Agent", description: "Liquidity imbalance, walls, and microstructure." },
};

const state = {
    currentMarket: "BTC-USDC",
    currentAgent: "frontdesk",
    overview: null,
    marketPayload: null,
    allMarketsBoard: [],
    allMarketsWorkspace: null,
    chatSessions: {},
    chart: null,
    panelOpen: false,
    chatBusy: false,
    marketRequestId: 0,
    navigationBusy: false,
    marketCache: {},
    dashboardCache: {},
};

function openAgentPanel() {
    const panel = document.getElementById("agentPanelModal");
    if (panel) {
        state.panelOpen = true;
        panel.hidden = false;
        document.body.classList.add("panel-open");
    }
}

function closeAgentPanel() {
    const panel = document.getElementById("agentPanelModal");
    state.panelOpen = false;
    document.body.classList.remove("panel-open");
    if (panel) panel.hidden = true;
}

function setActiveMarketTab(symbol) {
    document.querySelectorAll("#marketTabs button").forEach((button) => {
        button.classList.toggle("active", button.dataset.market === symbol);
    });
}

function setActiveAgentCard(agentKey) {
    document.querySelectorAll(".agent-card").forEach((node) => {
        if (node.dataset.agent) {
            node.classList.toggle("active", node.dataset.agent === agentKey);
        }
    });
}

function chatSessionKey(symbol = state.currentMarket, agent = state.currentAgent) {
    return `${symbol}::${agent}`;
}

function getChatSession(symbol = state.currentMarket, agent = state.currentAgent) {
    const key = chatSessionKey(symbol, agent);
    if (!state.chatSessions[key]) {
        state.chatSessions[key] = [];
    }
    return state.chatSessions[key];
}

function fillList(id, entries) {
    const node = document.getElementById(id);
    if (!node) return;
    const items = Array.isArray(entries) && entries.length ? entries : ["No fresh items in this view yet."];
    node.innerHTML = items.map((entry) => `<li>${entry}</li>`).join("");
}

function formatPct(value, digits = 1) {
    const number = Number(value);
    return Number.isFinite(number) ? `${number.toFixed(digits)}%` : "n/a";
}

function formatCompactUSD(value) {
    const number = Number(value);
    if (!Number.isFinite(number)) return "n/a";
    const absolute = Math.abs(number);
    if (absolute >= 1_000_000_000) return `$${(number / 1_000_000_000).toFixed(2)}B`;
    if (absolute >= 1_000_000) return `$${(number / 1_000_000).toFixed(2)}M`;
    if (absolute >= 1_000) return `$${(number / 1_000).toFixed(2)}K`;
    return `$${number.toFixed(2)}`;
}

function formatPrice(value) {
    const number = Number(value);
    if (!Number.isFinite(number)) return "n/a";
    if (number >= 1000) return `$${number.toLocaleString(undefined, { maximumFractionDigits: 0 })}`;
    if (number >= 1) return `$${number.toLocaleString(undefined, { maximumFractionDigits: 2 })}`;
    return `$${number.toFixed(4)}`;
}

function extractBullBearNeutral(agents) {
    const counts = { bullish: 0, bearish: 0, neutral: 0 };
    Object.values(agents || {}).forEach((agent) => {
        const raw = String(agent.signal || agent.trend || "NEUTRAL").toUpperCase();
        if (raw === "BULLISH") counts.bullish += 1;
        else if (raw === "BEARISH") counts.bearish += 1;
        else counts.neutral += 1;
    });
    return counts;
}

function verdictClass(value) {
    const normalized = String(value || "").toUpperCase();
    if (normalized === "BUY" || normalized === "BULLISH") return "bullish-text";
    if (normalized === "SELL" || normalized === "BEARISH") return "bearish-text";
    return "accent-text";
}

function toHeadlineList(headlines) {
    if (!Array.isArray(headlines) || headlines.length === 0) {
        return ["No fresh current-affairs headlines available right now."];
    }
    return headlines.map((item) => {
        const source = item.source ? ` (${item.source})` : "";
        return `${item.title}${source}`;
    });
}

function computeLineSeries(chartData) {
    const rows = Array.isArray(chartData) ? chartData.slice(-48) : [];
    const labels = rows.map((row) => new Date(row.t).toLocaleTimeString("en-GB", {
        hour: "2-digit",
        minute: "2-digit",
        timeZone: "UTC",
    }));
    const close = rows.map((row) => Number(row.c));
    const ema = [];
    let previous = close[0] || 0;
    close.forEach((value) => {
        previous = previous ? (value * 0.35) + (previous * 0.65) : value;
        ema.push(Number(previous.toFixed(2)));
    });
    const lows = rows.map((row) => Number(row.l));
    return { labels, close, ema, lows };
}

function safeNumber(value, fallback = 0) {
    const number = Number(value);
    return Number.isFinite(number) ? number : fallback;
}

function buildFrontdeskChartModel(report) {
    const raw = report.raw_agent_payload || {};
    if (report.symbol === "ALL") {
        const markets = Array.isArray(raw.markets) ? raw.markets.slice(0, 5) : (state.allMarketsBoard || []).slice(0, 5);
        return {
            type: "bar",
            title: "All-markets activity map",
            legend: ["Open interest", "Funding APY"],
            labels: markets.map((row) => row.symbol),
            datasets: [
                {
                    label: "Open interest",
                    data: markets.map((row) => safeNumber(row.open_interest)),
                    backgroundColor: "rgba(116, 64, 214, 0.45)",
                    borderColor: "#cbaef5",
                    borderWidth: 1.4,
                    borderRadius: 10,
                },
                {
                    label: "Funding APY",
                    data: markets.map((row) => safeNumber(row.funding_apy)),
                    backgroundColor: "rgba(25, 169, 77, 0.42)",
                    borderColor: "#7ae29b",
                    borderWidth: 1.4,
                    borderRadius: 10,
                    yAxisID: "y1",
                },
            ],
            axes: { secondary: true },
        };
    }

    const counts = raw.counts || {};
    return {
        type: "bar",
        title: "Desk alignment map",
        legend: ["Agent count"],
        labels: ["Bullish", "Neutral", "Bearish"],
        datasets: [
            {
                label: "Agent count",
                data: [
                    safeNumber(counts.bullish),
                    safeNumber(counts.neutral),
                    safeNumber(counts.bearish),
                ],
                backgroundColor: [
                    "rgba(25, 169, 77, 0.45)",
                    "rgba(203, 174, 245, 0.38)",
                    "rgba(199, 97, 83, 0.45)",
                ],
                borderColor: ["#7ae29b", "#d7c5f5", "#e7aa9f"],
                borderWidth: 1.4,
                borderRadius: 10,
            },
        ],
    };
}

function buildMarketChartModel(chartData) {
    const series = computeLineSeries(chartData);
    return {
        type: "line",
        title: "Price structure and trend",
        legend: ["Price", "EMA", "Session low"],
        labels: series.labels,
        datasets: [
            {
                label: "Price",
                data: series.close,
                borderColor: "#d7c5f5",
                backgroundColor: "rgba(116, 64, 214, 0.16)",
                fill: true,
                borderWidth: 3,
                tension: 0.3,
                pointRadius: 0,
            },
            {
                label: "EMA",
                data: series.ema,
                borderColor: "#8c64df",
                borderWidth: 2,
                tension: 0.25,
                pointRadius: 0,
            },
            {
                label: "Session low",
                data: series.lows,
                borderColor: "#19a94d",
                borderWidth: 1.6,
                borderDash: [6, 6],
                tension: 0.15,
                pointRadius: 0,
            },
        ],
    };
}

function buildFundingChartModel(report) {
    const raw = report.raw_agent_payload || {};
    return {
        type: "bar",
        title: "Funding pressure snapshot",
        legend: ["Funding metrics"],
        labels: ["Current bps", "Next bps", "Annualized APY"],
        datasets: [
            {
                label: "Funding metrics",
                data: [
                    safeNumber(raw.funding_rate) * 10000,
                    safeNumber(raw.next_funding_rate) * 10000,
                    safeNumber(raw.annualized_rate_pct),
                ],
                backgroundColor: [
                    "rgba(116, 64, 214, 0.45)",
                    "rgba(210, 155, 53, 0.42)",
                    "rgba(25, 169, 77, 0.42)",
                ],
                borderColor: ["#cbaef5", "#f0bf63", "#7ae29b"],
                borderWidth: 1.4,
                borderRadius: 10,
            },
        ],
    };
}

function buildLiquidationChartModel(report) {
    const raw = report.raw_agent_payload || {};
    return {
        type: "bar",
        title: "Liquidation flow snapshot",
        legend: ["USD notional"],
        labels: ["Long liq", "Short liq", "Total"],
        datasets: [
            {
                label: "USD notional",
                data: [
                    safeNumber(raw.long_liquidations_usd),
                    safeNumber(raw.short_liquidations_usd),
                    safeNumber(raw.total_liquidations_usd),
                ],
                backgroundColor: [
                    "rgba(210, 155, 53, 0.42)",
                    "rgba(116, 64, 214, 0.45)",
                    "rgba(25, 169, 77, 0.42)",
                ],
                borderColor: ["#f0bf63", "#cbaef5", "#7ae29b"],
                borderWidth: 1.4,
                borderRadius: 10,
            },
        ],
    };
}

function buildSentimentChartModel(report) {
    const raw = report.raw_agent_payload || {};
    return {
        type: "bar",
        title: "Sentiment and attention",
        legend: ["Live sentiment"],
        labels: ["Sentiment", "Mentions", "Trending"],
        datasets: [
            {
                label: "Live sentiment",
                data: [
                    safeNumber(raw.sentiment_score),
                    safeNumber(raw.mention_count_24h),
                    raw.is_trending ? 100 : 0,
                ],
                backgroundColor: [
                    "rgba(116, 64, 214, 0.45)",
                    "rgba(25, 169, 77, 0.42)",
                    "rgba(210, 155, 53, 0.42)",
                ],
                borderColor: ["#cbaef5", "#7ae29b", "#f0bf63"],
                borderWidth: 1.4,
                borderRadius: 10,
            },
        ],
    };
}

function buildNarrativeChartModel(report) {
    const raw = report.raw_agent_payload || {};
    const currentAffairsCount = Array.isArray(report.current_affairs) ? report.current_affairs.length : 0;
    return {
        type: "bar",
        title: "Narrative catalyst balance",
        legend: ["Narrative checks"],
        labels: ["Bullish cues", "Bearish cues", "Headlines"],
        datasets: [
            {
                label: "Narrative checks",
                data: [
                    safeNumber(raw.bullish_hits),
                    safeNumber(raw.bearish_hits),
                    currentAffairsCount,
                ],
                backgroundColor: [
                    "rgba(25, 169, 77, 0.42)",
                    "rgba(199, 97, 83, 0.45)",
                    "rgba(116, 64, 214, 0.45)",
                ],
                borderColor: ["#7ae29b", "#e7aa9f", "#cbaef5"],
                borderWidth: 1.4,
                borderRadius: 10,
            },
        ],
    };
}

function buildOrderbookChartModel(report) {
    const raw = report.raw_agent_payload || {};
    const depth = Array.isArray(raw.depth_data) ? raw.depth_data.slice(0, 20) : [];
    const labels = depth.map((row) => String(row.price));
    return {
        type: "bar",
        title: "Orderbook depth by price",
        legend: ["Bid size", "Ask size"],
        labels,
        datasets: [
            {
                label: "Bid size",
                data: depth.map((row) => row.side === "bid" ? safeNumber(row.size) : 0),
                backgroundColor: "rgba(25, 169, 77, 0.42)",
                borderColor: "#7ae29b",
                borderWidth: 1.2,
                borderRadius: 6,
            },
            {
                label: "Ask size",
                data: depth.map((row) => row.side === "ask" ? safeNumber(row.size) : 0),
                backgroundColor: "rgba(199, 97, 83, 0.42)",
                borderColor: "#e7aa9f",
                borderWidth: 1.2,
                borderRadius: 6,
            },
        ],
    };
}

function buildAgentChartModel(report, chartData) {
    switch (report.agent) {
        case "frontdesk":
            return buildFrontdeskChartModel(report);
        case "market":
            return buildMarketChartModel(chartData);
        case "funding":
            return buildFundingChartModel(report);
        case "liquidation":
            return buildLiquidationChartModel(report);
        case "sentiment":
            return buildSentimentChartModel(report);
        case "narrative":
            return buildNarrativeChartModel(report);
        case "orderbook":
            return buildOrderbookChartModel(report);
        default:
            return buildMarketChartModel(chartData);
    }
}

function updateChartLegend(labels = []) {
    const buttons = Array.from(document.querySelectorAll("#overlayToggles .toggle"));
    buttons.forEach((button, index) => {
        const label = labels[index];
        button.hidden = !label;
        button.textContent = label || "";
        button.classList.toggle("active", index === 0);
    });
}

function renderWorkspaceChart(report, chartData) {
    const model = buildAgentChartModel(report, chartData);
    document.getElementById("chartTitle").textContent = model.title;
    updateChartLegend(model.legend || []);

    if (!window.Chart) {
        return;
    }

    const ctx = document.getElementById("workspaceChart");
    if (state.chart && typeof state.chart.destroy === "function") {
        state.chart.destroy();
    }

    const scales = {
        x: { grid: { color: "transparent" }, ticks: { color: "#97896f" } },
        y: { grid: { color: "rgba(255, 244, 214, 0.1)" }, ticks: { color: "#97896f" }, border: { color: "transparent" } },
    };
    if (model.axes?.secondary) {
        scales.y1 = {
            position: "right",
            grid: { drawOnChartArea: false, color: "transparent" },
            ticks: { color: "#97896f" },
            border: { color: "transparent" },
        };
    }

    state.chart = new Chart(ctx, {
        type: model.type,
        data: {
            labels: model.labels,
            datasets: model.datasets,
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: { legend: { display: false } },
            scales,
        },
    });
}

async function fetchJSON(path, options = {}) {
    return fetchWithTimeout(`${API_BASE}${path}`, REQUEST_TIMEOUT_MS, options);
}

async function fetchWithTimeout(url, ms = 10000, options = {}) {
    const controller = new AbortController();
    const timeoutId = window.setTimeout(() => controller.abort(), ms);
    try {
        const response = await fetch(url, {
            headers: { "Content-Type": "application/json" },
            cache: "no-store",
            signal: controller.signal,
            ...options,
        });
        if (!response.ok) {
            return null;
        }
        return await response.json();
    } catch (_error) {
        return null;
    } finally {
        window.clearTimeout(timeoutId);
    }
}

function escapeHtml(value) {
    return String(value || "")
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/\"/g, "&quot;")
        .replace(/'/g, "&#39;");
}

function cacheName(key) {
    return `${LOCAL_CACHE_PREFIX}${key}`;
}

function readCacheRecord(key) {
    const memoryRecord = state.dashboardCache[key] || state.marketCache[key];
    if (memoryRecord) return memoryRecord;
    try {
        const raw = window.localStorage.getItem(cacheName(key));
        if (!raw) return null;
        const parsed = JSON.parse(raw);
        if (!parsed || typeof parsed !== "object" || !("data" in parsed)) return null;
        return parsed;
    } catch (_error) {
        return null;
    }
}

function writeCacheRecord(key, data, target = "dashboard") {
    const record = { savedAt: Date.now(), data };
    if (target === "market") {
        state.marketCache[key] = record;
    } else {
        state.dashboardCache[key] = record;
    }
    try {
        window.localStorage.setItem(cacheName(key), JSON.stringify(record));
    } catch (_error) {
        // Local storage can fail when the browser is in private mode or the payload is too large.
    }
    return data;
}

function getFreshCachedData(key) {
    const record = readCacheRecord(key);
    if (!record) return null;
    const savedAt = Number(record.savedAt || 0);
    if (!savedAt || Date.now() - savedAt > CLIENT_CACHE_TTL_MS) return null;
    return record.data;
}

function getStaleCachedData(key) {
    const record = readCacheRecord(key);
    return record ? record.data : null;
}

async function getDashboardResource(key, url, ms = REQUEST_TIMEOUT_MS, target = "dashboard") {
    const data = await fetchWithTimeout(url, ms);
    if (data) return writeCacheRecord(key, data, target);
    const fresh = getFreshCachedData(key);
    if (fresh) return fresh;
    return null;
}

async function runNavigation(task) {
    if (state.navigationBusy) return false;
    state.navigationBusy = true;
    try {
        return await task();
    } finally {
        state.navigationBusy = false;
    }
}

function initChart() {
    if (!window.Chart) {
        state.chart = {
            destroy() {},
            update() {},
        };
        document.getElementById("chartTitle").textContent = "Chart unavailable - live data still active";
        return;
    }
    state.chart = null;
}

function renderOverview(overview) {
    state.overview = overview;
    const bySymbol = new Map((overview.summary_cards || []).map((card) => [card.symbol, card]));
    [
        ["BTC-USDC", "summaryBtcSignal", "summaryBtcText"],
        ["ETH-USDC", "summaryEthSignal", "summaryEthText"],
        ["SOL-USDC", "summarySolSignal", "summarySolText"],
    ].forEach(([symbol, signalId, textId]) => {
        const card = bySymbol.get(symbol);
        if (!card) return;
        const signalNode = document.getElementById(signalId);
        signalNode.textContent = card.final_signal;
        signalNode.className = `proof-value ${verdictClass(card.final_signal)}`;
        document.getElementById(textId).textContent = `${symbol} | ${formatPct(card.confidence_pct, 0)} confidence | ${formatPrice(card.price)}`;
    });
    if (overview.timestamp) {
        document.getElementById("statusTimestamp").textContent = new Date(overview.timestamp).toLocaleTimeString("en-GB", {
            hour: "2-digit",
            minute: "2-digit",
            timeZone: "UTC",
        }) + " UTC";
    }
}

function renderAllMarketsBoard(markets) {
    const container = document.getElementById("allMarketsGrid");
    if (!container) return;
    const rows = Array.isArray(markets) ? markets : [];
    state.allMarketsBoard = rows;
    container.innerHTML = rows.map((market, index) => `
        <button class="agent-card" data-market-card="${market.symbol}">
            <div class="agent-topline">
                <span class="agent-index">${String(index + 1).padStart(2, "0")}</span>
                <span class="verdict ${market.quick_signal === "BUY" ? "verdict-bullish" : market.quick_signal === "SELL" ? "verdict-bearish" : "verdict-neutral"}">${market.quick_signal}</span>
            </div>
            <h3>${market.symbol}</h3>
            <div class="agent-metric">${formatPrice(market.price)}</div>
            <p>${formatPct(market.change_24h, 2)} 24h change | ${formatCompactUSD(market.open_interest)} OI | ${formatPct(market.funding_apy, 2)} funding APY</p>
            <div class="agent-footer">
                <span>${formatCompactUSD(market.volume_24h)} volume</span>
                <span>${market.max_leverage ? `${market.max_leverage}x max` : "Perp"}</span>
            </div>
        </button>
    `).join("");
    container.querySelectorAll("[data-market-card]").forEach((button) => {
        button.addEventListener("click", async () => {
            await runNavigation(async () => {
                const symbol = button.dataset.marketCard;
                state.currentAgent = "frontdesk";
                setActiveAgentCard("frontdesk");
                await loadMarket(symbol, { openPanel: true, preferredAgent: "frontdesk", updateTab: true });
            });
        });
    });
}

function buildAllMarketsFrontdeskReport() {
    const topRows = (state.allMarketsBoard || []).slice(0, 5);
    return {
        agent: "frontdesk",
        symbol: "ALL",
        agent_label: "Frontdesk Agent",
        verdict: "SCAN",
        overall_verdict: "SCAN",
        report: topRows.length
            ? `Frontdesk is scanning the broader board. The most active names right now are ${topRows.map((row) => row.symbol).join(", ")}.`
            : "Frontdesk is waiting for the broader market board to populate.",
        overall_context: "Use this mode to explore fresh markets before drilling into a single symbol.",
        key_metric_label: "Markets scanned",
        key_metric_value: String((state.allMarketsBoard || []).length),
        next_steps: [
            "Ask for a market recommendation if you want a fresh symbol to explore.",
            "Click any market row to drill into the full desk for that symbol.",
        ],
        support_summary: topRows.map((row) => `${row.symbol} | ${row.quick_signal} | OI ${formatCompactUSD(row.open_interest)} | Funding ${formatPct(row.funding_apy, 2)}`),
        current_affairs: [],
        suggested_questions: [
            "Recommend a new market for me to explore.",
            "Which broader markets look most active right now?",
            "Which market has the cleanest setup on the board?",
        ],
        raw_agent_payload: { markets: topRows },
    };
}

function upsertInitialChatMessage(report) {
    const session = getChatSession(report.symbol, report.agent);
    if (session.length) return session;
    const firstStep = Array.isArray(report.next_steps) && report.next_steps.length
        ? report.next_steps[0]
        : "Watch the next live desk update.";
    const starter = [
        `Verdict: ${report.agent_label} is ${report.verdict}.`,
        `Why: ${report.report}`,
        `Team: Overall desk verdict is ${report.overall_verdict}.`,
        `Next: ${firstStep}`,
    ].join("\n");
    session.push({ role: "assistant", text: starter });
    return session;
}

function renderSignal(signal) {
    const counts = extractBullBearNeutral(signal.agents);
    const finalSignalNode = document.getElementById("finalSignal");
    finalSignalNode.textContent = signal.final_signal;
    finalSignalNode.className = `final-signal ${verdictClass(signal.final_signal)}`;
    document.getElementById("agentConsensus").textContent = `${counts.bullish} of 6 agents bullish`;
    document.getElementById("confidenceValue").textContent = formatPct(signal.confidence_pct, 0);
    document.getElementById("confidenceFill").style.width = `${signal.confidence_pct}%`;
    document.getElementById("deskSummary").textContent = signal.narration || signal.reasoning || "No synthesized desk summary available.";
    const riskText = signal.altfins?.summary_block?.altfins_view || signal.agents?.orderbook?.reason || "No specific pushback highlighted.";
    document.getElementById("deskRisk").textContent = riskText;
    document.getElementById("macroRegimeTag").textContent = signal.final_signal;
    document.getElementById("pressureTag").textContent = signal.news_context?.top_themes?.[0] || "market_context";
    document.getElementById("liquidityTag").textContent = `Score ${signal.score}/6`;
}

function renderMetrics(signal) {
    const market = signal.agents.market || {};
    const funding = signal.agents.funding || {};
    document.getElementById("metricPrice").textContent = formatPrice(market.price);
    document.getElementById("metricPriceChange").textContent = `${Number(market.change_24h || 0).toFixed(2)}% today`;
    document.getElementById("metricPriceChange").className = `metric-change ${Number(market.change_24h || 0) >= 0 ? "bullish-text" : "bearish-text"}`;
    document.getElementById("metricOi").textContent = formatCompactUSD(market.open_interest);
    document.getElementById("metricOiChange").textContent = market.trend || "NEUTRAL";
    document.getElementById("metricOiChange").className = `metric-change ${verdictClass(market.trend)}`;
    document.getElementById("metricVolume").textContent = formatCompactUSD(market.volume_24h);
    document.getElementById("metricVolumeChange").textContent = market.signal || "NEUTRAL";
    document.getElementById("metricVolumeChange").className = `metric-change ${verdictClass(market.signal)}`;
    document.getElementById("metricFunding").textContent = formatPct(funding.annualized_rate_pct, 2);
    document.getElementById("metricFunding").className = `metric-value ${Number(funding.annualized_rate_pct || 0) >= 0 ? "accent-text" : "bearish-text"}`;
    document.getElementById("metricFundingCopy").textContent = funding.reason || "Funding read unavailable";
}

function agentMetric(agentKey, payload) {
    if (agentKey === "market") return formatPrice(payload.price);
    if (agentKey === "funding") return formatPct(payload.annualized_rate_pct, 2);
    if (agentKey === "liquidation") return formatCompactUSD(payload.total_liquidations_usd);
    if (agentKey === "sentiment") return `${Number(payload.sentiment_score || 0).toFixed(1)} / 100`;
    if (agentKey === "narrative") return payload.confidence || "LOW";
    return `${formatPct((Number(payload.imbalance_ratio || 0) * 100), 1)} bids`;
}

function renderAgentCards(signal, reports = {}) {
    document.querySelectorAll(".agent-card").forEach((card) => {
        const key = card.dataset.agent;
        if (!key) return;
        if (key === "frontdesk") {
            const report = reports.frontdesk || {};
            card.querySelector("h3").textContent = AGENT_META.frontdesk.title;
            const verdictNode = card.querySelector(".verdict");
            verdictNode.textContent = report.overall_verdict || signal.final_signal || "HOLD";
            verdictNode.className = `verdict ${verdictNode.textContent === "BUY" ? "verdict-bullish" : verdictNode.textContent === "SELL" ? "verdict-bearish" : "verdict-neutral"}`;
            card.querySelector(".agent-metric").textContent = report.key_metric_value || formatPct(signal.confidence_pct, 0);
            card.querySelector("p").textContent = report.report || AGENT_META.frontdesk.description;
            const footer = card.querySelector(".agent-footer");
            footer.children[0].textContent = "Decision desk";
            footer.children[1].textContent = `${extractBullBearNeutral(signal.agents).bullish} bullish / 6`;
            return;
        }
        const payload = signal.agents[key] || {};
        const meta = AGENT_META[key];
        const verdict = String(payload.signal || payload.trend || "NEUTRAL").toUpperCase();
        card.querySelector("h3").textContent = meta.title;
        const verdictNode = card.querySelector(".verdict");
        verdictNode.textContent = verdict;
        verdictNode.className = `verdict ${verdict === "BULLISH" ? "verdict-bullish" : verdict === "BEARISH" ? "verdict-bearish" : "verdict-neutral"}`;
        card.querySelector(".agent-metric").textContent = agentMetric(key, payload);
        card.querySelector("p").textContent = payload.reason || payload.narrative_summary || meta.description;
        const footer = card.querySelector(".agent-footer");
        footer.children[0].textContent = `Desk ${signal.final_signal}`;
        footer.children[1].textContent = key === "sentiment"
            ? `Rank ${payload.rank_in_trending ?? "n/a"}`
            : key === "narrative"
                ? (payload.confidence || "LOW")
                : key === "market"
                    ? (payload.trend || "NEUTRAL")
                    : key === "orderbook"
                        ? `Bid ${formatPct((Number(payload.imbalance_ratio || 0) * 100), 1)}`
                        : (payload.signal || "NEUTRAL");
    });
}

function renderIntel(signal, report) {
    const headlines = toHeadlineList(signal.news_context?.headlines);
    fillList("narrativeList", signal.news_context?.top_themes?.length ? signal.news_context.top_themes : ["market_context"]);
    document.getElementById("logFeed").innerHTML = [
        signal.reasoning || "Reasoning unavailable.",
        signal.narration || "Narration unavailable.",
    ].map((entry, index) => `<div><span>${index === 0 ? "Desk" : "AI"}</span>${entry}</div>`).join("");
    fillList("watchList", report.next_steps || []);
    fillList("signalFeed", headlines);
}

function renderEvidence(signal) {
    const orderbook = signal.agents.orderbook || {};
    const liq = signal.agents.liquidation || {};
    const bidPct = Number(orderbook.imbalance_ratio || 0.5) * 100;
    const askPct = Math.max(0, 100 - bidPct);
    document.getElementById("depthBidBar").style.width = `${bidPct}%`;
    document.getElementById("depthAskBar").style.width = `${askPct}%`;
    document.getElementById("depthLabel").textContent = `${formatPct(bidPct, 1)} bid-side`;
    document.getElementById("depthCopy").textContent = orderbook.reason || "Orderbook read unavailable.";
    const longLiq = Number(liq.long_liquidations_usd || 0);
    const shortLiq = Number(liq.short_liquidations_usd || 0);
    const liqTotal = Math.max(longLiq + shortLiq, 1);
    document.getElementById("shortBar").style.width = `${(shortLiq / liqTotal) * 100}%`;
    document.getElementById("longBar").style.width = `${(longLiq / liqTotal) * 100}%`;
    document.getElementById("liqLabel").textContent = liq.dominant_side || "BALANCED";
    document.getElementById("liqCopy").textContent = liq.reason || "Liquidation read unavailable.";
}

function renderWorkspace(report, chartData, resetChat = true) {
    document.getElementById("workspaceTitle").textContent = report.agent_label;
    document.getElementById("workspaceRole").textContent = report.agent === "frontdesk"
        ? "Frontdesk speaks for the full team, combines the specialist evidence, and translates the decision into plain language."
        : report.agent_label + " focuses on the selected slice of the desk and updates continuously from live backend data.";
    document.getElementById("workspaceSummary").textContent = report.report;
    document.getElementById("workspaceQuestions").textContent = report.overall_context;
    document.getElementById("workspaceMetric").textContent = report.key_metric_value;
    document.getElementById("workspaceMetricCopy").textContent = `${report.key_metric_label} | overall desk ${report.overall_verdict}`;
    document.getElementById("chatAgentLabel").textContent = report.agent_label;
    document.getElementById("chatOverallVerdict").textContent = `Overall verdict: ${report.overall_verdict}`;

    const badge = document.getElementById("workspaceVerdict");
    badge.textContent = report.verdict;
    badge.className = "workspace-badge";
    badge.style.background = report.verdict === "BULLISH"
        ? "rgba(25, 169, 77, 0.14)"
        : report.verdict === "BEARISH"
            ? "rgba(199, 97, 83, 0.14)"
            : "rgba(255, 255, 255, 0.08)";
    badge.style.color = report.verdict === "BULLISH" ? "#7ae29b" : report.verdict === "BEARISH" ? "#e7aa9f" : "#f4ecd2";
    badge.style.borderColor = "rgba(255, 244, 214, 0.12)";

    fillList("discussionList", report.support_summary || report.next_steps || []);
    fillList("signalFeed", toHeadlineList(report.current_affairs));
    renderChatSuggestions(report.suggested_questions || []);
    if (resetChat) {
        setChatMessages(upsertInitialChatMessage(report));
    } else {
        setChatMessages(getChatSession(report.symbol, report.agent));
    }

    renderWorkspaceChart(report, chartData);
}

function setChatMessages(messages) {
    const container = document.getElementById("chatMessages");
    container.innerHTML = messages.map((message) => {
        const role = message.role === "user" ? "user" : "assistant";
        return `<div class="chat-message ${role}">${escapeHtml(message.text)}</div>`;
    }).join("");
    container.scrollTop = container.scrollHeight;
}

function appendChatMessage(role, text) {
    const session = getChatSession();
    session.push({ role, text });
    const container = document.getElementById("chatMessages");
    const node = document.createElement("div");
    node.className = `chat-message ${role}`;
    node.textContent = text;
    container.appendChild(node);
    container.scrollTop = container.scrollHeight;
}

function renderChatSuggestions(questions) {
    const container = document.getElementById("chatSuggestions");
    container.innerHTML = questions.map((question) => `<button type="button" class="chat-suggestion">${question}</button>`).join("");
    container.querySelectorAll(".chat-suggestion").forEach((button) => {
        button.addEventListener("click", () => {
            document.getElementById("agentChatInput").value = button.textContent;
            document.getElementById("agentChatForm").requestSubmit();
        });
    });
}

async function askAgent(question) {
    if (state.chatBusy) return null;
    state.chatBusy = true;
    const form = document.getElementById("agentChatForm");
    const submitButton = form ? form.querySelector("button[type='submit']") : null;
    if (submitButton) {
        submitButton.disabled = true;
        submitButton.textContent = "Thinking...";
    }
    appendChatMessage("user", question);
    try {
        const response = await fetchWithTimeout(`${API_BASE}/api/agent/ask`, 25000, {
            method: "POST",
            body: JSON.stringify({
                symbol: state.currentMarket,
                agent: state.currentAgent,
                question,
            }),
        });
        if (!response) {
            appendChatMessage(
                "assistant",
                "- Call: Fresh reply took too long.\n- Why: The latest live report is still on screen.\n- Next: Try a shorter question or ask again."
            );
            return null;
        }
        appendChatMessage("assistant", response.answer);
        if (response.workspace) {
            if (state.currentMarket === "ALL") {
                state.allMarketsWorkspace = response.workspace;
            } else if (state.marketPayload?.reports && state.currentAgent) {
                state.marketPayload.reports[state.currentAgent] = response.workspace;
            }
            if (state.currentMarket !== "ALL" && state.marketPayload?.signal) {
                renderIntel(state.marketPayload.signal, response.workspace);
            }
            renderWorkspace(response.workspace, state.currentMarket === "ALL" ? [] : (state.marketPayload?.chart?.data || []), false);
        }
        return response;
    } finally {
        state.chatBusy = false;
        if (submitButton) {
            submitButton.disabled = false;
            submitButton.textContent = "Ask";
        }
    }
}

async function loadOverview(options = {}) {
    const { fromMarketView = false } = options;
    const shouldLoadWorkspace = state.currentMarket === "ALL" || !fromMarketView;
    const [overview, allMarkets, allMarketsWorkspace] = await Promise.all([
        getDashboardResource("overview", `${API_BASE}/api/dashboard/overview`, 18000),
        getDashboardResource("all-markets", `${API_BASE}/api/dashboard/all-markets`, 18000),
        shouldLoadWorkspace
            ? getDashboardResource("all-markets-workspace", `${API_BASE}/api/dashboard/all-markets/workspace`, 20000)
            : Promise.resolve(null),
    ]);
    if (overview) {
        renderOverview(overview);
        if (overview.macro_alert) {
            document.getElementById("macroTitle").textContent = overview.macro_alert;
        }
    }
    if (allMarkets?.all_markets_board) {
        renderAllMarketsBoard(allMarkets.all_markets_board || []);
    }
    if (allMarketsWorkspace?.workspace) {
        state.allMarketsWorkspace = allMarketsWorkspace.workspace;
    } else if (!state.allMarketsWorkspace) {
        state.allMarketsWorkspace = buildAllMarketsFrontdeskReport();
    }
    document.getElementById("allMarketsPanel").hidden = state.currentMarket !== "ALL";
    if (!fromMarketView && state.currentMarket === "ALL") {
        renderWorkspace(state.allMarketsWorkspace, []);
    }
    return Boolean(overview || allMarkets || allMarketsWorkspace);
}

async function loadMarket(symbol, options = {}) {
    const { openPanel = false, preferredAgent = null, updateTab = true } = options;
    const previousMarket = state.currentMarket;
    const previousAgent = state.currentAgent;
    const requestId = ++state.marketRequestId;
    const cacheKey = `market:${symbol}`;
    const payload = await getDashboardResource(
        cacheKey,
        `${API_BASE}/api/dashboard/market/${symbol}`,
        25000,
        "market",
    );
    if (requestId !== state.marketRequestId) {
        return false;
    }
    if (!payload) {
        state.currentMarket = previousMarket;
        state.currentAgent = previousAgent;
        return false;
    }
    state.currentMarket = symbol;
    if (updateTab) {
        setActiveMarketTab(symbol);
    }
    document.getElementById("allMarketsPanel").hidden = true;
    state.marketPayload = payload;
    const requestedAgent = preferredAgent || state.currentAgent || "frontdesk";
    const fallbackAgent = payload.reports?.frontdesk ? "frontdesk" : payload.reports?.market ? "market" : requestedAgent;
    const activeAgent = payload.reports?.[requestedAgent] ? requestedAgent : fallbackAgent;
    state.currentAgent = activeAgent;
    setActiveAgentCard(activeAgent);
    const activeReport = payload.reports?.[activeAgent];
    document.getElementById("heroMarket").textContent = symbol;
    document.getElementById("macroTitle").textContent = payload.signal.macro_alert || payload.signal.news_context?.top_themes?.join(", ") || payload.signal.final_signal;
    document.getElementById("macroSubtitle").textContent = payload.signal.narration || payload.signal.reasoning || "Live signal context unavailable.";
    document.getElementById("macroMeta").textContent = `${payload.signal.timestamp || ""} | ${payload.signal.altfins?.summary_block?.altfins_view || "altFINS n/a"}`;
    renderSignal(payload.signal);
    renderMetrics(payload.signal);
    renderAgentCards(payload.signal, payload.reports || {});
    renderEvidence(payload.signal);
    if (activeReport) {
        renderIntel(payload.signal, activeReport);
        renderWorkspace(activeReport, payload.chart?.data || []);
    }
    if (openPanel) {
        openAgentPanel();
    }
    return true;
}

function bindTabs() {
    document.querySelectorAll("#marketTabs button").forEach((button) => {
        button.addEventListener("click", async () => {
            await runNavigation(async () => {
                if (button.dataset.market === "ALL") {
                    state.currentMarket = "ALL";
                    state.currentAgent = "frontdesk";
                    setActiveMarketTab("ALL");
                    setActiveAgentCard("frontdesk");
                    await loadOverview();
                    if (state.panelOpen) {
                        renderWorkspace(state.allMarketsWorkspace || buildAllMarketsFrontdeskReport(), []);
                    }
                    return;
                }
                state.currentAgent = "frontdesk";
                setActiveAgentCard("frontdesk");
                await loadMarket(button.dataset.market, {
                    openPanel: state.panelOpen,
                    preferredAgent: "frontdesk",
                    updateTab: true,
                });
            });
        });
    });
}

function bindAgents() {
    document.querySelectorAll(".agent-card").forEach((card) => {
        card.addEventListener("click", () => {
            if (!card.dataset.agent) return;
            state.currentAgent = card.dataset.agent;
            setActiveAgentCard(state.currentAgent);
            if (state.currentMarket === "ALL") {
                if (state.currentAgent !== "frontdesk") {
                    state.currentAgent = "frontdesk";
                    setActiveAgentCard("frontdesk");
                }
                const report = state.allMarketsWorkspace || buildAllMarketsFrontdeskReport();
                renderWorkspace(report, []);
                openAgentPanel();
                return;
            }
            if (!state.marketPayload) return;
            const report = state.marketPayload.reports?.[state.currentAgent];
            if (!report) return;
            renderIntel(state.marketPayload.signal, report);
            renderWorkspace(report, state.marketPayload.chart?.data || []);
            openAgentPanel();
        });
    });
}

function bindAgentPanel() {
    document.getElementById("closeAgentPanel").addEventListener("click", closeAgentPanel);
}

function bindSummaryCards() {
    [
        ["summaryCardBtc", "BTC-USDC"],
        ["summaryCardEth", "ETH-USDC"],
        ["summaryCardSol", "SOL-USDC"],
    ].forEach(([id, symbol]) => {
        const node = document.getElementById(id);
        if (!node) return;
        node.addEventListener("click", async () => {
            await runNavigation(async () => {
                state.currentAgent = "frontdesk";
                setActiveAgentCard("frontdesk");
                await loadMarket(symbol, { openPanel: true, preferredAgent: "frontdesk", updateTab: true });
            });
        });
    });
}

function bindChat() {
    document.getElementById("agentChatForm").addEventListener("submit", async (event) => {
        event.preventDefault();
        const input = document.getElementById("agentChatInput");
        const question = input.value.trim();
        if (!question) return;
        input.value = "";
        await askAgent(question);
    });
}

function startStatusTicker() {
    function tickStatus() {
        document.getElementById("liveLabel").textContent = "Live desk cache active";
        const stamp = new Date().toLocaleTimeString("en-GB", {
            hour: "2-digit",
            minute: "2-digit",
            timeZone: "UTC",
        });
        document.getElementById("statusTimestamp").textContent = `${stamp} UTC`;
        window.setTimeout(tickStatus, 60000);
    }

    tickStatus();
}

document.addEventListener("DOMContentLoaded", async () => {
    initChart();
    bindTabs();
    bindAgents();
    bindSummaryCards();
    bindChat();
    bindAgentPanel();
    startStatusTicker();
    await loadMarket(state.currentMarket, { openPanel: false, preferredAgent: "frontdesk", updateTab: false });
    window.setTimeout(() => {
        loadOverview({ fromMarketView: true }).catch((error) => console.error("Overview load failed:", error));
    }, 1500);
});
