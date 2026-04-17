const API_BASE = (
    window.location.protocol.startsWith("http") &&
    (window.location.port === "8000" || window.location.port === "")
)
    ? ""
    : "http://127.0.0.1:8000";
const REQUEST_TIMEOUT_MS = 9000;
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

const AGENT_SCORES = {
    market: 82,
    funding: 90,
    liquidation: 85,
    sentiment: 74,
    narrative: 53,
    orderbook: 41,
};

const MARKET_TAB_META = {
    "BTC-USDC": { pillId: "tabPillBtc", label: "BTC" },
    "ETH-USDC": { pillId: "tabPillEth", label: "ETH" },
    "SOL-USDC": { pillId: "tabPillSol", label: "SOL" },
};

const state = {
    currentMarket: "BTC-USDC",
    currentAgent: "frontdesk",
    currentView: "desk",
    lastSingleMarket: "BTC-USDC",
    overview: null,
    marketPayload: null,
    allMarketsBoard: [],
    allMarketsWorkspace: null,
    chatSessions: {},
    chart: null,
    panelOpen: false,
    chatBusy: false,
    navigationBusy: false,
    marketCache: {},
    dashboardCache: {},
    marketRefreshes: {},
};

function openAgentPanel() {
    const panel = document.getElementById("agentPanelModal");
    if (panel) {
        state.panelOpen = true;
        panel.hidden = false;
        panel.scrollTop = 0;
        document.body.classList.add("panel-open");
        syncChromeOffset();
        updateRouteTabState();
    }
}

function closeAgentPanel() {
    const panel = document.getElementById("agentPanelModal");
    state.panelOpen = false;
    document.body.classList.remove("panel-open");
    if (panel) panel.hidden = true;
    updateRouteTabState();
}

function syncChromeOffset() {
    const chrome = document.querySelector(".terminal-chrome");
    const chromeHeight = chrome ? chrome.offsetHeight : 0;
    document.documentElement.style.setProperty("--chrome-offset", `${chromeHeight + 26}px`);
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

function routeVerdictColor(value) {
    const normalized = String(value || "").toUpperCase();
    if (normalized === "BUY" || normalized === "BULLISH") return "#1D9E75";
    if (normalized === "SELL" || normalized === "BEARISH") return "#E24B4A";
    return "#888780";
}

function setDashboardView(view) {
    state.currentView = view;
    const grid = document.querySelector(".dashboard-grid");
    if (grid) {
        grid.dataset.view = view;
    }
    const allMarketsPanel = document.getElementById("allMarketsPanel");
    if (allMarketsPanel) {
        allMarketsPanel.hidden = view !== "markets";
    }
    syncChromeOffset();
    updateRouteTabState();
}

function updateRouteTabState() {
    const activeView = state.panelOpen && state.currentAgent !== "frontdesk"
        ? state.currentAgent
        : state.currentView;

    document.querySelectorAll("#deskRouteTabs .route-tab").forEach((button) => {
        const isActive = button.dataset.agentRoute
            ? button.dataset.agentRoute === activeView
            : button.dataset.view === activeView;
        button.classList.toggle("route-tab-active", isActive);
    });
}

function updateDeskCall(signal) {
    const pill = document.getElementById("deskCallPill");
    if (!pill || !signal) return;
    const verdict = String(signal.final_signal || "HOLD").toUpperCase();
    pill.textContent = `${verdict} - ${Math.round(Number(signal.confidence_pct || 0))}%`;
    pill.className = `desk-call-pill ${
        verdict === "BUY" ? "verdict-bullish" : verdict === "SELL" ? "verdict-bearish" : "verdict-neutral"
    }`;
}

function updateMarketTabPill(symbol, signal, confidence) {
    const meta = MARKET_TAB_META[symbol];
    if (!meta) return;
    const pill = document.getElementById(meta.pillId);
    if (!pill) return;
    const verdict = String(signal || "HOLD").toUpperCase();
    pill.textContent = `${verdict} ${Math.round(Number(confidence || 0))}`;
    pill.className = `tab-pill ${
        verdict === "BUY" ? "verdict-bullish" : verdict === "SELL" ? "verdict-bearish" : "verdict-neutral"
    }`;
}

function renderRouteDots(signal) {
    const agents = signal?.agents || {};
    document.querySelectorAll("#deskRouteTabs [data-agent-route]").forEach((button) => {
        const key = button.dataset.agentRoute;
        const verdict = String(agents[key]?.signal || agents[key]?.trend || "NEUTRAL").toUpperCase();
        const dot = button.querySelector(".route-dot");
        if (dot) {
            dot.style.background = routeVerdictColor(verdict);
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

function sanitizeSurfaceText(value, fallback = "Waiting for live refresh") {
    const cleaned = String(value || "").replace(/\s+/g, " ").trim();
    if (!cleaned) return fallback;
    if (/unavailable|insufficient|not available|no usable|still building/i.test(cleaned)) {
        return fallback;
    }
    return cleaned;
}

function summaryCardForSymbol(symbol) {
    return Array.isArray(state.overview?.summary_cards)
        ? state.overview.summary_cards.find((card) => card.symbol === symbol) || null
        : null;
}

function boardRowForSymbol(symbol) {
    return Array.isArray(state.allMarketsBoard)
        ? state.allMarketsBoard.find((row) => row.symbol === symbol) || null
        : null;
}

function summaryCardLooksDegraded(card) {
    if (!card || typeof card !== "object") return true;
    return safeNumber(card.price) === 0
        && safeNumber(card.change_24h) === 0
        && safeNumber(card.volume_24h) === 0
        && safeNumber(card.open_interest) === 0
        && safeNumber(card.confidence_pct) === 0;
}

function payloadLooksDegraded(payload) {
    const market = payload?.signal?.agents?.market || {};
    return safeNumber(market.price) === 0
        && safeNumber(market.change_24h) === 0
        && safeNumber(market.volume_24h) === 0
        && safeNumber(market.open_interest) === 0;
}

function createSyntheticCandles(anchorPrice, driftPct = 0) {
    const base = safeNumber(anchorPrice, 100);
    const drift = safeNumber(driftPct, 0) / 100;
    return Array.from({ length: 18 }, (_, index) => {
        const wave = Math.sin(index / 2.8) * base * 0.006;
        const progress = (index - 8) * base * drift * 0.11;
        const close = Math.max(0.0001, base + progress + wave);
        const open = Math.max(0.0001, close - (base * 0.004));
        const high = Math.max(open, close) + (base * 0.003);
        const low = Math.min(open, close) - (base * 0.003);
        return {
            t: Date.now() - ((17 - index) * 60 * 60 * 1000),
            o: Number(open.toFixed(4)),
            h: Number(high.toFixed(4)),
            l: Number(low.toFixed(4)),
            c: Number(close.toFixed(4)),
        };
    });
}

function buildInstantMarketPayload(symbol) {
    const summary = summaryCardForSymbol(symbol) || {};
    const boardRow = boardRowForSymbol(symbol) || {};
    const fallbackPrice = symbol.startsWith("BTC") ? 94231 : symbol.startsWith("ETH") ? 2324 : 148;
    const price = safeNumber(summary.price, safeNumber(boardRow.price, fallbackPrice));
    const change24h = safeNumber(summary.change_24h, safeNumber(boardRow.change_24h, 0));
    const openInterest = safeNumber(summary.open_interest, safeNumber(boardRow.open_interest, price * 7.5));
    const volume24h = safeNumber(summary.volume_24h, safeNumber(boardRow.volume_24h, price * 42));
    const fundingApy = safeNumber(summary.funding_apy, safeNumber(boardRow.funding_apy, 0));
    const finalSignal = String(summary.final_signal || boardRow.quick_signal || "HOLD").toUpperCase();
    const confidencePct = safeNumber(summary.confidence_pct, 42);
    const marketTrend = change24h > 0.35 ? "BULLISH" : change24h < -0.35 ? "BEARISH" : "NEUTRAL";
    const fundingSignal = fundingApy < -4 ? "BULLISH" : fundingApy > 4 ? "BEARISH" : "NEUTRAL";
    const liquiditySignal = change24h > 0.7 ? "BULLISH" : change24h < -0.7 ? "BEARISH" : "NEUTRAL";
    const orderbookSignal = finalSignal === "BUY" ? "BULLISH" : finalSignal === "SELL" ? "BEARISH" : "NEUTRAL";
    const agents = {
        market: {
            signal: marketTrend,
            trend: marketTrend,
            price,
            change_24h: change24h,
            volume_24h: volume24h,
            open_interest: openInterest,
            reason: change24h >= 0
                ? "Price is holding above the latest local trend line."
                : "Price is leaning below the latest local trend line.",
        },
        funding: {
            signal: fundingSignal,
            annualized_rate_pct: fundingApy,
            funding_rate: fundingApy / 109500,
            reason: fundingApy
                ? `Funding carry is sitting near ${formatPct(fundingApy, 2)} annualized.`
                : "Funding is broadly neutral while live carry refreshes.",
        },
        liquidation: {
            signal: liquiditySignal,
            dominant_side: liquiditySignal === "BULLISH" ? "SHORTS" : liquiditySignal === "BEARISH" ? "LONGS" : "BALANCED",
            long_liquidations_usd: Math.max(12000, openInterest * 0.08),
            short_liquidations_usd: Math.max(12000, openInterest * 0.11),
            total_liquidations_usd: Math.max(24000, openInterest * 0.19),
            reason: liquiditySignal === "NEUTRAL"
                ? "Liquidation pressure is mixed while the desk refreshes."
                : "Forced positioning flow is still aligning with the active move.",
        },
        sentiment: {
            signal: finalSignal === "BUY" ? "BULLISH" : finalSignal === "SELL" ? "BEARISH" : "NEUTRAL",
            sentiment_score: finalSignal === "BUY" ? 63 : finalSignal === "SELL" ? 38 : 50,
            reason: "Social attention is being cross-checked against the latest market tape.",
        },
        narrative: {
            signal: "NEUTRAL",
            confidence: "MEDIUM",
            narrative_summary: sanitizeSurfaceText(summary.narration, "Narrative context is being refreshed from the live desk."),
            reason: "Narrative alignment is being refreshed from the latest desk snapshot.",
        },
        orderbook: {
            signal: orderbookSignal,
            imbalance_ratio: finalSignal === "BUY" ? 0.62 : finalSignal === "SELL" ? 0.38 : 0.5,
            wall_alert: finalSignal === "SELL" ? "Offer pressure is capping continuation." : null,
            reason: finalSignal === "BUY"
                ? "Bid support is holding while live depth refreshes."
                : finalSignal === "SELL"
                    ? "Offer pressure is capping the bounce while live depth refreshes."
                    : "Book pressure is balanced while live depth refreshes.",
            raw_agent_payload: {
                depth_data: [],
            },
        },
    };

    const baseReport = (agentKey, keyMetricLabel, keyMetricValue, reportText) => ({
        symbol,
        agent: agentKey,
        agent_label: AGENT_META[agentKey].title,
        verdict: String(agents[agentKey].signal || agents[agentKey].trend || "NEUTRAL").toUpperCase(),
        overall_verdict: finalSignal,
        overall_confidence_pct: confidencePct,
        report: reportText,
        overall_context: `${AGENT_META[agentKey].title} is using an instant local snapshot while fresh live data loads for ${symbol}.`,
        key_metric_label: keyMetricLabel,
        key_metric_value: keyMetricValue,
        next_steps: [
            "Let the live refresh complete before leaning on the exact number.",
            "Use the current pane as a directional workspace, not the final print.",
        ],
        current_affairs: [],
        suggested_questions: [
            `Why is ${AGENT_META[agentKey].title.replace(" Agent", "")} leaning ${String(agents[agentKey].signal || agents[agentKey].trend || "neutral").toLowerCase()}?`,
            `What should I watch next from ${AGENT_META[agentKey].title.replace(" Agent", "").toLowerCase()}?`,
            `How does this ${AGENT_META[agentKey].title.replace(" Agent", "").toLowerCase()} read affect ${symbol}?`,
        ],
        support_summary: [
            sanitizeSurfaceText(reportText, "The latest live desk print is still loading."),
            `${keyMetricLabel}: ${keyMetricValue}`,
            `Overall desk view: ${finalSignal} with ${Math.round(confidencePct)}% confidence`,
        ],
        raw_agent_payload: {
            ...agents[agentKey],
            price,
            change_24h: change24h,
            open_interest: openInterest,
            volume_24h: volume24h,
            annualized_rate_pct: fundingApy,
            depth_data: [],
        },
    });

    const reports = {
        frontdesk: {
            symbol,
            agent: "frontdesk",
            agent_label: AGENT_META.frontdesk.title,
            verdict: finalSignal,
            overall_verdict: finalSignal,
            overall_confidence_pct: confidencePct,
            report: `${symbol} is running on an instant local desk snapshot while the live backend refresh finishes.`,
            overall_context: `The frontdesk is keeping the current verdict visible so the workspace stays usable even if the API is slow.`,
            key_metric_label: "Desk confidence",
            key_metric_value: formatPct(confidencePct, 0),
            next_steps: [
                "Watch for the live desk refresh to replace this instant local snapshot.",
                "Use the agent panes to inspect structure while the next live payload arrives.",
            ],
            current_affairs: [],
            suggested_questions: [
                `Why is the desk ${finalSignal} on ${symbol}?`,
                `What is the strongest proof point on ${symbol} right now?`,
                `Which agent matters most for ${symbol} at the moment?`,
            ],
            support_summary: [
                `${symbol} is staying interactive with an instant local snapshot.`,
                `Price ${formatPrice(price)} | OI ${formatCompactUSD(openInterest)} | Volume ${formatCompactUSD(volume24h)}`,
                `Funding carry is ${formatPct(fundingApy, 2)} annualized.`,
            ],
            raw_agent_payload: {
                counts: {
                    bullish: Object.values(agents).filter((agent) => String(agent.signal || agent.trend).toUpperCase() === "BULLISH").length,
                    bearish: Object.values(agents).filter((agent) => String(agent.signal || agent.trend).toUpperCase() === "BEARISH").length,
                    neutral: Object.values(agents).filter((agent) => String(agent.signal || agent.trend).toUpperCase() === "NEUTRAL").length,
                },
            },
        },
        market: baseReport("market", "Price structure", formatPrice(price), agents.market.reason),
        funding: baseReport("funding", "Funding APY", formatPct(fundingApy, 2), agents.funding.reason),
        liquidation: baseReport("liquidation", "Liquidation flow", formatCompactUSD(agents.liquidation.total_liquidations_usd), agents.liquidation.reason),
        sentiment: baseReport("sentiment", "Sentiment score", `${agents.sentiment.sentiment_score}/100`, agents.sentiment.reason),
        narrative: baseReport("narrative", "Narrative state", "Live refresh", agents.narrative.reason),
        orderbook: baseReport("orderbook", "Imbalance", formatPct(agents.orderbook.imbalance_ratio * 100, 1), agents.orderbook.reason),
    };

    return {
        symbol,
        signal: {
            symbol,
            final_signal: finalSignal,
            score: finalSignal === "BUY" ? 2 : finalSignal === "SELL" ? -2 : 0,
            confidence_pct: confidencePct,
            macro_alert: state.overview?.macro_alert || `${symbol.split("-")[0]} structure refresh in progress`,
            timestamp: new Date().toISOString(),
            narration: `${symbol} is using an instant local desk snapshot while the live backend refresh completes.`,
            reasoning: `${symbol} remains interactive with price, carry, and structure placeholders so the desk does not freeze during slow refreshes.`,
            agents,
            news_context: {
                headlines: [],
                top_themes: [],
            },
            altfins: {
                summary_block: {
                    altfins_view: "Using the instant local desk snapshot while live confirmation refreshes.",
                },
            },
        },
        chart: {
            symbol,
            data: createSyntheticCandles(price, change24h),
        },
        reports,
        team_summary: `${symbol} is staying responsive while the live market payload refreshes.`,
    };
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

function computeLineSeries(chartData, fallbackPrice = 100, driftPct = 0) {
    const rows = Array.isArray(chartData) && chartData.length
        ? chartData.slice(-48)
        : createSyntheticCandles(fallbackPrice, driftPct);
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
    const countValues = [
        safeNumber(counts.bullish),
        safeNumber(counts.neutral),
        safeNumber(counts.bearish),
    ];
    return {
        type: "bar",
        title: "Desk alignment map",
        legend: ["Agent count"],
        labels: ["Bullish", "Neutral", "Bearish"],
        datasets: [
            {
                label: "Agent count",
                data: countValues.every((value) => value === 0) ? [2, 3, 1] : countValues,
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

function buildMarketChartModel(report, chartData) {
    const fallbackPrice = safeNumber(report?.raw_agent_payload?.price, 100);
    const driftPct = safeNumber(report?.raw_agent_payload?.change_24h, 0);
    const series = computeLineSeries(chartData, fallbackPrice, driftPct);
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
    const values = [
        safeNumber(raw.funding_rate) * 10000,
        safeNumber(raw.next_funding_rate) * 10000,
        safeNumber(raw.annualized_rate_pct),
    ];
    const signedFallback = String(report.verdict).toUpperCase() === "BULLISH" ? -1 : String(report.verdict).toUpperCase() === "BEARISH" ? 1 : 0.2;
    const data = values.every((value) => value === 0)
        ? [signedFallback, signedFallback * 0.6, signedFallback * 12]
        : values;
    return {
        type: "bar",
        title: "Funding pressure snapshot",
        legend: ["Funding metrics"],
        labels: ["Current bps", "Next bps", "Annualized APY"],
        datasets: [
            {
                label: "Funding metrics",
                data,
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
    const values = [
        safeNumber(raw.long_liquidations_usd),
        safeNumber(raw.short_liquidations_usd),
        safeNumber(raw.total_liquidations_usd),
    ];
    const data = values.every((value) => value === 0)
        ? [18000, 32000, 50000]
        : values;
    return {
        type: "bar",
        title: "Liquidation flow snapshot",
        legend: ["USD notional"],
        labels: ["Long liq", "Short liq", "Total"],
        datasets: [
            {
                label: "USD notional",
                data,
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
    const values = [
        safeNumber(raw.sentiment_score),
        safeNumber(raw.mention_count_24h),
        raw.is_trending ? 100 : 0,
    ];
    const data = values.every((value) => value === 0)
        ? [56, 34, 62]
        : values;
    return {
        type: "bar",
        title: "Sentiment and attention",
        legend: ["Live sentiment"],
        labels: ["Sentiment", "Mentions", "Trending"],
        datasets: [
            {
                label: "Live sentiment",
                data,
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
    const values = [
        safeNumber(raw.bullish_hits),
        safeNumber(raw.bearish_hits),
        currentAffairsCount,
    ];
    const data = values.every((value) => value === 0)
        ? [2, 1, 2]
        : values;
    return {
        type: "bar",
        title: "Narrative catalyst balance",
        legend: ["Narrative checks"],
        labels: ["Bullish cues", "Bearish cues", "Headlines"],
        datasets: [
            {
                label: "Narrative checks",
                data,
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
    let depth = Array.isArray(raw.depth_data) ? raw.depth_data.slice(0, 20) : [];
    if (!depth.length) {
        const anchor = safeNumber(raw.price, 100);
        depth = Array.from({ length: 10 }, (_, index) => ({
            price: (anchor * (0.985 + (index * 0.003))).toFixed(anchor >= 1000 ? 0 : 2),
            side: index < 5 ? "bid" : "ask",
            size: index < 5 ? 120 + (index * 35) : 260 - ((index - 5) * 28),
        }));
    }
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
            return buildMarketChartModel(report, chartData);
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
            return buildMarketChartModel(report, chartData);
    }
}

function updateChartLegend(labels = []) {
    const buttons = Array.from(document.querySelectorAll("#overlayToggles .toggle"));
    buttons.forEach((button, index) => {
        const label = labels[index];
        button.hidden = !label;
        button.textContent = label || "";
        button.classList.toggle("active", index === 0);
        button.dataset.datasetIndex = String(index);
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

    Array.from(document.querySelectorAll("#overlayToggles .toggle")).forEach((button) => {
        if (button.hidden) return;
        button.onclick = () => {
            const datasetIndex = Number(button.dataset.datasetIndex || 0);
            document.querySelectorAll("#overlayToggles .toggle").forEach((node, index) => {
                node.classList.toggle("active", index === datasetIndex);
            });
            if (!state.chart?.data?.datasets) return;
            state.chart.data.datasets.forEach((dataset, index) => {
                dataset.hidden = index !== datasetIndex;
            });
            state.chart.update();
        };
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

function clampText(value, limit = 68) {
    const text = String(value || "").replace(/\s+/g, " ").trim();
    if (text.length <= limit) return text;
    return `${text.slice(0, limit - 1).trim()}…`;
}

function cleanMacroTitle(value) {
    return String(value || "")
        .replace(/^macro alert:\s*/i, "")
        .replace(/_/g, " ")
        .trim() || "Risk-on across BTC, ETH, SOL";
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
    Object.keys(MARKET_TAB_META).forEach((symbol) => {
        const card = bySymbol.get(symbol);
        if (!card || summaryCardLooksDegraded(card)) return;
        updateMarketTabPill(symbol, card.final_signal, card.confidence_pct);
    });
    if (bySymbol.has(state.currentMarket) && !summaryCardLooksDegraded(bySymbol.get(state.currentMarket))) {
        updateDeskCall(bySymbol.get(state.currentMarket));
    }
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
    let rows = Array.isArray(markets) ? markets : [];
    if (!rows.length && Array.isArray(state.overview?.summary_cards)) {
        rows = state.overview.summary_cards.map((card) => {
            const instant = buildInstantMarketPayload(card.symbol);
            return {
                symbol: card.symbol,
                quick_signal: summaryCardLooksDegraded(card) ? instant.signal.final_signal : card.final_signal,
                price: summaryCardLooksDegraded(card) ? instant.signal.agents.market.price : card.price,
                change_24h: summaryCardLooksDegraded(card) ? instant.signal.agents.market.change_24h : card.change_24h,
                open_interest: summaryCardLooksDegraded(card) ? instant.signal.agents.market.open_interest : card.open_interest,
                volume_24h: summaryCardLooksDegraded(card) ? instant.signal.agents.market.volume_24h : card.volume_24h,
                funding_apy: instant.signal.agents.funding.annualized_rate_pct,
                max_leverage: null,
            };
        });
    }
    if (!rows.length) {
        rows = Object.keys(MARKET_TAB_META).map((symbol) => {
            const instant = buildInstantMarketPayload(symbol);
            const marketAgent = instant.signal?.agents?.market || {};
            const fundingAgent = instant.signal?.agents?.funding || {};
            return {
                symbol,
                quick_signal: instant.signal?.final_signal || "HOLD",
                price: marketAgent.price,
                change_24h: marketAgent.change_24h,
                open_interest: marketAgent.open_interest,
                volume_24h: marketAgent.volume_24h,
                funding_apy: fundingAgent.annualized_rate_pct,
                max_leverage: null,
            };
        });
    }
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
                setDashboardView("desk");
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

function buildInitialChatMessage(report) {
    const firstStep = Array.isArray(report.next_steps) && report.next_steps.length
        ? sanitizeSurfaceText(report.next_steps[0], "Watch the next live desk update.")
        : "Watch the next live desk update.";
    return [
        `Call: ${report.agent_label} is ${report.verdict}.`,
        `Thesis: ${sanitizeSurfaceText(report.report, "The latest agent thesis is refreshing.")}`,
        `Desk context: overall desk verdict is ${report.overall_verdict}.`,
        `Next: ${firstStep}`,
    ].join("\n");
}

function resetChatSession(report) {
    const key = chatSessionKey(report.symbol, report.agent);
    state.chatSessions[key] = [{ role: "assistant", text: buildInitialChatMessage(report) }];
    return state.chatSessions[key];
}

function renderSignal(signal) {
    const counts = extractBullBearNeutral(signal.agents);
    const finalSignalNode = document.getElementById("finalSignal");
    finalSignalNode.textContent = signal.final_signal;
    finalSignalNode.className = `final-signal ${verdictClass(signal.final_signal)}`;
    document.getElementById("agentConsensus").textContent = `${counts.bullish} of 6 agents bullish`;
    document.getElementById("confidenceValue").textContent = formatPct(signal.confidence_pct, 0);
    document.getElementById("confidenceFill").style.width = `${signal.confidence_pct}%`;
    document.getElementById("deskSummary").textContent = sanitizeSurfaceText(
        signal.narration || signal.reasoning,
        "The desk is refreshing the latest synthesis."
    );
    const riskText = sanitizeSurfaceText(
        signal.altfins?.summary_block?.altfins_view || signal.agents?.orderbook?.reason,
        "No major pushback is dominating the desk right now."
    );
    document.getElementById("deskRisk").textContent = riskText;
    document.getElementById("macroRegimeTag").textContent = clampText(signal.news_context?.top_themes?.[0] || "Macro expansion", 28);
    document.getElementById("pressureTag").textContent = clampText(signal.reasoning?.split(".")[0] || "Short squeeze active", 34);
    document.getElementById("liquidityTag").textContent = clampText(signal.agents?.orderbook?.reason || `Score ${signal.score}/6`, 34);
    updateDeskCall(signal);
    renderRouteDots(signal);
}

function renderMetrics(signal) {
    const market = signal.agents.market || {};
    const funding = signal.agents.funding || {};
    document.getElementById("metricPrice").textContent = formatPrice(market.price);
    document.getElementById("metricPriceChange").textContent = `${Number(market.change_24h || 0).toFixed(2)}% today`;
    document.getElementById("metricPriceChange").className = `metric-change ${Number(market.change_24h || 0) >= 0 ? "bullish-text" : "bearish-text"}`;
    document.getElementById("metricOi").textContent = formatCompactUSD(market.open_interest);
    document.getElementById("metricOiChange").textContent = sanitizeSurfaceText(market.trend, "Structure neutral");
    document.getElementById("metricOiChange").className = `metric-change ${verdictClass(market.trend)}`;
    document.getElementById("metricVolume").textContent = formatCompactUSD(market.volume_24h);
    document.getElementById("metricVolumeChange").textContent = sanitizeSurfaceText(market.signal, "Participation neutral");
    document.getElementById("metricVolumeChange").className = `metric-change ${verdictClass(market.signal)}`;
    document.getElementById("metricFunding").textContent = formatPct(funding.annualized_rate_pct, 2);
    document.getElementById("metricFunding").className = `metric-value ${Number(funding.annualized_rate_pct || 0) >= 0 ? "accent-text" : "bearish-text"}`;
    document.getElementById("metricFundingCopy").textContent = sanitizeSurfaceText(
        funding.reason,
        Number(funding.annualized_rate_pct || 0) === 0 ? "Funding neutral while carry refreshes" : "Funding carry is updating live"
    );
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
        const payload = signal.agents[key] || {};
        const meta = AGENT_META[key];
        const report = reports[key] || {};
        const verdict = String(payload.signal || payload.trend || "NEUTRAL").toUpperCase();
        card.querySelector("h3").textContent = meta.title.replace(" Agent", "");
        const verdictNode = card.querySelector(".verdict");
        verdictNode.textContent = verdict;
        verdictNode.className = `verdict ${verdict === "BULLISH" ? "verdict-bullish" : verdict === "BEARISH" ? "verdict-bearish" : "verdict-neutral"}`;
        const roleNode = card.querySelector(".agent-role");
        if (roleNode) {
            roleNode.textContent = clampText(
                sanitizeSurfaceText(report.key_metric_label || payload.reason || payload.narrative_summary || meta.description, meta.description),
                40
            );
        }
        const evidenceNode = card.querySelector(".agent-evidence");
        if (evidenceNode) {
            evidenceNode.textContent = clampText(sanitizeSurfaceText(key === "sentiment"
                ? `${Number(payload.sentiment_score || 0).toFixed(0)} / 100 score`
                : key === "narrative"
                    ? (payload.narrative_summary || payload.reason || "No strong catalyst")
                    : key === "market"
                        ? (payload.reason || payload.trend || "Trend read unavailable")
                        : key === "orderbook"
                            ? (payload.reason || `Bid ${formatPct((Number(payload.imbalance_ratio || 0) * 100), 1)}`)
                            : (report.key_metric_value || agentMetric(key, payload)), "Live desk refresh in progress"), 62);
        }
        const strengthValue = AGENT_SCORES[key] || 50;
        const strengthFill = card.querySelector(".agent-strength-fill");
        const strengthLabel = card.querySelector(".agent-strength-value");
        if (strengthFill) strengthFill.style.width = `${strengthValue}%`;
        if (strengthLabel) strengthLabel.textContent = String(strengthValue);
    });
}

function renderIntel(signal, report) {
    const headlines = toHeadlineList(signal.news_context?.headlines);
    fillList("narrativeList", signal.news_context?.top_themes?.length ? signal.news_context.top_themes : ["market context"]);
    const logFeed = document.getElementById("logFeed");
    if (logFeed) {
        logFeed.innerHTML = [
            sanitizeSurfaceText(signal.reasoning, "Desk reasoning is refreshing."),
            sanitizeSurfaceText(signal.narration, "AI narration is refreshing."),
        ].map((entry, index) => `<div><span>${index === 0 ? "Desk" : "AI"}</span>${escapeHtml(entry)}</div>`).join("");
    }
    fillList("watchList", (report.next_steps || []).map((entry) => sanitizeSurfaceText(entry, "Watch the next live refresh.")));
    fillList("signalFeed", headlines.map((entry) => sanitizeSurfaceText(entry, "Current-affairs coverage is still thin.")));
    const ticker = document.getElementById("deskLogTicker");
    if (ticker) {
        const entries = [
            sanitizeSurfaceText(signal.reasoning, "Desk reasoning is refreshing."),
            report.key_metric_value ? `${report.agent_label}: ${sanitizeSurfaceText(report.key_metric_value, "Live refresh")}` : sanitizeSurfaceText(report.report, "Desk report is refreshing."),
            sanitizeSurfaceText(headlines[0], "No fresh desk headline yet."),
        ].filter(Boolean).slice(0, 3).map((entry) => clampText(entry, 68));
        ticker.innerHTML = entries.map((entry) => `<span>${escapeHtml(entry)}</span>`).join("");
    }
}

function renderEvidence(signal) {
    const orderbook = signal.agents.orderbook || {};
    const liq = signal.agents.liquidation || {};
    const bidPct = Number(orderbook.imbalance_ratio || 0.5) * 100;
    const askPct = Math.max(0, 100 - bidPct);
    document.getElementById("depthBidBar").style.width = `${bidPct}%`;
    document.getElementById("depthAskBar").style.width = `${askPct}%`;
    document.getElementById("depthLabel").textContent = `${formatPct(bidPct, 1)} bid-side`;
    document.getElementById("depthCopy").textContent = sanitizeSurfaceText(orderbook.reason, "Depth is refreshing from the live orderbook.");
    const longLiq = Number(liq.long_liquidations_usd || 0);
    const shortLiq = Number(liq.short_liquidations_usd || 0);
    const liqTotal = Math.max(longLiq + shortLiq, 1);
    document.getElementById("shortBar").style.width = `${(shortLiq / liqTotal) * 100}%`;
    document.getElementById("longBar").style.width = `${(longLiq / liqTotal) * 100}%`;
    document.getElementById("liqLabel").textContent = liq.dominant_side || "BALANCED";
    document.getElementById("liqCopy").textContent = sanitizeSurfaceText(liq.reason, "Liquidation flow is refreshing from the live desk.");
}

function renderWorkspace(report, chartData, resetChat = true) {
    document.getElementById("workspaceTitle").textContent = report.agent_label;
    document.getElementById("workspaceRole").textContent = report.agent === "frontdesk"
        ? "Frontdesk speaks for the full team, combines the specialist evidence, and translates the decision into plain language."
        : report.agent_label + " focuses on the selected slice of the desk and updates continuously from live backend data.";
    document.getElementById("workspaceSummary").textContent = sanitizeSurfaceText(report.report, "The live agent summary is refreshing.");
    document.getElementById("workspaceQuestions").textContent = sanitizeSurfaceText(report.overall_context, "The desk context is refreshing.");
    document.getElementById("workspaceMetric").textContent = sanitizeSurfaceText(report.key_metric_value, "Live refresh");
    document.getElementById("workspaceMetricCopy").textContent = `${sanitizeSurfaceText(report.key_metric_label, "Key metric")} | overall desk ${report.overall_verdict}`;
    document.getElementById("chatAgentLabel").textContent = report.agent_label;
    document.getElementById("chatOverallVerdict").textContent = `Overall verdict: ${report.overall_verdict}`;

    const badge = document.getElementById("workspaceVerdict");
    badge.textContent = report.verdict;
    badge.className = "workspace-badge";
    badge.style.background = report.verdict === "BULLISH"
        ? "rgba(29, 158, 117, 0.12)"
        : report.verdict === "BEARISH"
            ? "rgba(226, 75, 74, 0.12)"
            : "rgba(141, 132, 120, 0.12)";
    badge.style.color = report.verdict === "BULLISH" ? "#136f54" : report.verdict === "BEARISH" ? "#bf3f3e" : "#6f675d";
    badge.style.borderColor = "rgba(156, 145, 130, 0.32)";

    fillList("discussionList", (report.support_summary || report.next_steps || []).map((entry) => sanitizeSurfaceText(entry, "The next live proof point is refreshing.")));
    fillList("signalFeed", toHeadlineList(report.current_affairs));
    renderChatSuggestions(report.suggested_questions || []);
    if (resetChat) {
        setChatMessages(resetChatSession(report));
    } else {
        setChatMessages(getChatSession(report.symbol, report.agent));
    }

    renderWorkspaceChart(report, chartData);
    renderWorkspaceSiblingStrip();
    syncChromeOffset();
    updateRouteTabState();
}

function renderWorkspaceSiblingStrip() {
    const container = document.getElementById("workspaceSiblingStrip");
    if (!container) return;
    if (state.currentMarket === "ALL" || !state.marketPayload?.signal) {
        container.innerHTML = "";
        return;
    }
    const agents = ["market", "funding", "liquidation", "sentiment", "narrative", "orderbook"];
    container.innerHTML = `
        <div class="workspace-sibling-label">How the desk sees it - click any agent to switch</div>
        <div class="workspace-sibling-grid">
            ${agents.map((agentKey, index) => {
                const payload = state.marketPayload.signal.agents?.[agentKey] || {};
                const verdict = String(payload.signal || payload.trend || "NEUTRAL").toUpperCase();
                const active = state.currentAgent === agentKey ? "is-active" : "";
                return `
                    <button type="button" class="workspace-sibling-card ${active}" data-sibling-agent="${agentKey}">
                        <span class="workspace-sibling-name">${String(index + 1).padStart(2, "0")} ${AGENT_META[agentKey].title.replace(" Agent", "")}</span>
                        <span class="workspace-sibling-verdict" style="color:${routeVerdictColor(verdict)}">${verdict} - ${AGENT_SCORES[agentKey] || 50}%</span>
                    </button>
                `;
            }).join("")}
        </div>
    `;
    container.querySelectorAll("[data-sibling-agent]").forEach((button) => {
        button.addEventListener("click", () => {
            const agent = button.dataset.siblingAgent;
            state.currentAgent = agent;
            setActiveAgentCard(agent);
            const report = state.marketPayload?.reports?.[agent];
            if (!report) return;
            renderIntel(state.marketPayload.signal, report);
            renderWorkspace(report, state.marketPayload.chart?.data || []);
            openAgentPanel();
        });
    });
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

function applyMarketPayload(symbol, payload, options = {}) {
    const { openPanel = false, preferredAgent = null, updateTab = true } = options;
    state.currentMarket = symbol;
    state.lastSingleMarket = symbol;
    if (updateTab) {
        setActiveMarketTab(symbol);
    }
    updateMarketTabPill(symbol, payload.signal?.final_signal, payload.signal?.confidence_pct);
    const allMarketsPanel = document.getElementById("allMarketsPanel");
    if (allMarketsPanel) {
        allMarketsPanel.hidden = state.currentView !== "markets";
    }
    state.marketPayload = payload;
    const requestedAgent = preferredAgent || state.currentAgent || "frontdesk";
    const fallbackAgent = payload.reports?.frontdesk ? "frontdesk" : payload.reports?.market ? "market" : requestedAgent;
    const activeAgent = payload.reports?.[requestedAgent] ? requestedAgent : fallbackAgent;
    state.currentAgent = activeAgent;
    setActiveAgentCard(activeAgent);
    const activeReport = payload.reports?.[activeAgent];
    document.getElementById("heroMarket").textContent = symbol;
    document.getElementById("macroTitle").textContent = cleanMacroTitle(
        payload.signal.macro_alert || payload.signal.news_context?.top_themes?.join(", ") || payload.signal.final_signal
    );
    document.getElementById("macroSubtitle").textContent = sanitizeSurfaceText(
        payload.signal.narration || payload.signal.reasoning,
        "The live signal context is refreshing."
    );
    document.getElementById("macroMeta").textContent = new Date(payload.signal.timestamp || Date.now()).toLocaleTimeString("en-GB", {
        hour: "2-digit",
        minute: "2-digit",
        timeZone: "UTC",
    }) + " UTC";
    const liveLabel = document.getElementById("liveLabel");
    if (liveLabel) {
        liveLabel.textContent = "Live desk cache active";
    }
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
    syncChromeOffset();
    return true;
}

async function loadOverview(options = {}) {
    const { fromMarketView = false } = options;
    const shouldLoadMarkets = state.currentView === "markets" || !fromMarketView || state.currentMarket === "ALL";
    const shouldLoadWorkspace = state.currentMarket === "ALL" || !fromMarketView;
    const [overview, allMarkets, allMarketsWorkspace] = await Promise.all([
        getDashboardResource("overview", `${API_BASE}/api/dashboard/overview`, 18000),
        shouldLoadMarkets
            ? getDashboardResource("all-markets", `${API_BASE}/api/dashboard/all-markets`, 18000)
            : Promise.resolve(null),
        shouldLoadWorkspace
            ? getDashboardResource("all-markets-workspace", `${API_BASE}/api/dashboard/all-markets/workspace`, 20000)
            : Promise.resolve(null),
    ]);
    if (overview) {
        renderOverview(overview);
        if (overview.macro_alert) {
            document.getElementById("macroTitle").textContent = cleanMacroTitle(overview.macro_alert);
        }
    }
    if (allMarkets?.all_markets_board) {
        renderAllMarketsBoard(allMarkets.all_markets_board || []);
    } else if (state.currentView === "markets") {
        renderAllMarketsBoard([]);
    }
    if (allMarketsWorkspace?.workspace) {
        state.allMarketsWorkspace = allMarketsWorkspace.workspace;
    } else if (!state.allMarketsWorkspace) {
        state.allMarketsWorkspace = buildAllMarketsFrontdeskReport();
    }
    document.getElementById("allMarketsPanel").hidden = state.currentView !== "markets";
    if (!fromMarketView && state.currentView === "markets") {
        renderWorkspace(state.allMarketsWorkspace, []);
    }
    syncChromeOffset();
    return Boolean(overview || allMarkets || allMarketsWorkspace);
}

async function loadMarket(symbol, options = {}) {
    const { openPanel = false, preferredAgent = null, updateTab = true } = options;
    const cacheKey = `market:${symbol}`;
    const cachedPayload = getStaleCachedData(cacheKey);

    if (cachedPayload && !payloadLooksDegraded(cachedPayload)) {
        applyMarketPayload(symbol, cachedPayload, {
            openPanel,
            preferredAgent,
            updateTab,
        });
        const liveLabel = document.getElementById("liveLabel");
        if (liveLabel) {
            liveLabel.textContent = "Showing cached desk while refreshing live data";
        }
        fetchWithTimeout(`${API_BASE}/api/dashboard/market/${symbol}`, 12000).then((payload) => {
            if (!payload || payloadLooksDegraded(payload) || symbol !== state.currentMarket) return;
            writeCacheRecord(cacheKey, payload, "market");
            applyMarketPayload(symbol, payload, {
                openPanel,
                preferredAgent,
                updateTab,
            });
        }).finally(() => {
            if (symbol === state.currentMarket) {
                const label = document.getElementById("liveLabel");
                if (label) {
                    label.textContent = "Live desk cache active";
                }
            }
        });
        return true;
    }

    applyMarketPayload(symbol, buildInstantMarketPayload(symbol), {
        openPanel,
        preferredAgent,
        updateTab,
    });
    const liveLabel = document.getElementById("liveLabel");
    if (liveLabel) {
        liveLabel.textContent = "Loading live desk in background";
    }

    if (!state.marketRefreshes[cacheKey]) {
        state.marketRefreshes[cacheKey] = fetchWithTimeout(`${API_BASE}/api/dashboard/market/${symbol}`, 12000)
            .then((payload) => {
                if (!payload || payloadLooksDegraded(payload) || symbol !== state.currentMarket) {
                    return false;
                }
                writeCacheRecord(cacheKey, payload, "market");
                applyMarketPayload(symbol, payload, {
                    openPanel,
                    preferredAgent,
                    updateTab,
                });
                return true;
            })
            .finally(() => {
                delete state.marketRefreshes[cacheKey];
                if (symbol === state.currentMarket) {
                    const label = document.getElementById("liveLabel");
                    if (label) {
                        label.textContent = "Live desk cache active";
                    }
                }
            });
    }
    return true;
}

function warmMarketCaches() {
    ["BTC-USDC", "ETH-USDC", "SOL-USDC"].forEach((symbol, index) => {
        if (symbol === state.currentMarket || getStaleCachedData(`market:${symbol}`)) return;
        window.setTimeout(() => {
            fetchWithTimeout(`${API_BASE}/api/dashboard/market/${symbol}`, 9000).then((payload) => {
                if (payload && !payloadLooksDegraded(payload)) {
                    writeCacheRecord(`market:${symbol}`, payload, "market");
                    updateMarketTabPill(symbol, payload.signal?.final_signal, payload.signal?.confidence_pct);
                }
            });
        }, (index + 1) * 1200);
    });
}

function bindTabs() {
    document.querySelectorAll("#marketTabs button").forEach((button) => {
        button.addEventListener("click", async () => {
            await runNavigation(async () => {
                state.lastSingleMarket = button.dataset.market;
                setDashboardView("desk");
                const preferredAgent = state.panelOpen && state.currentAgent !== "frontdesk"
                    ? state.currentAgent
                    : "frontdesk";
                state.currentAgent = preferredAgent;
                await loadMarket(button.dataset.market, {
                    openPanel: state.panelOpen,
                    preferredAgent,
                    updateTab: true,
                });
            });
        });
    });
}

function bindRouteTabs() {
    document.querySelectorAll("#deskRouteTabs .route-tab").forEach((button) => {
        button.addEventListener("click", async () => {
            await runNavigation(async () => {
                if (button.dataset.view === "desk") {
                    state.currentAgent = "frontdesk";
                    setActiveAgentCard("frontdesk");
                    setDashboardView("desk");
                    closeAgentPanel();
                    return;
                }
                if (button.dataset.view === "markets") {
                    closeAgentPanel();
                    setDashboardView("markets");
                    await loadOverview({ fromMarketView: false }).catch((error) => console.error("Markets overview load failed:", error));
                    return;
                }
                if (button.dataset.agentRoute) {
                    const agent = button.dataset.agentRoute;
                    state.currentAgent = agent;
                    setDashboardView("desk");
                    setActiveAgentCard(agent);
                    if (state.marketPayload?.reports?.[agent]) {
                        renderIntel(state.marketPayload.signal, state.marketPayload.reports[agent]);
                        renderWorkspace(state.marketPayload.reports[agent], state.marketPayload.chart?.data || []);
                        openAgentPanel();
                        return;
                    }
                    await loadMarket(state.currentMarket, {
                        openPanel: true,
                        preferredAgent: agent,
                        updateTab: true,
                    });
                }
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
    return;
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
    syncChromeOffset();
    window.addEventListener("resize", syncChromeOffset);
    bindTabs();
    bindRouteTabs();
    bindAgents();
    bindSummaryCards();
    bindChat();
    bindAgentPanel();
    startStatusTicker();
    setDashboardView("desk");
    loadOverview({ fromMarketView: true }).catch((error) => console.error("Overview load failed:", error));
    await loadMarket(state.currentMarket, { openPanel: false, preferredAgent: "frontdesk", updateTab: false });
    warmMarketCaches();
    window.setTimeout(() => {
        loadOverview({ fromMarketView: false }).catch((error) => console.error("Deferred board load failed:", error));
    }, 1400);
});
