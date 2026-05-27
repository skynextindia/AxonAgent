
        // High-contrast realtime WebSocket UI controller
        class DashboardController {
            constructor() {
                window.dashboardController = this;
                this.ws = null;
                this.reconnectInterval = 2000;
                this.startTime = Date.now();
                
                // Track Bid/Ask prices for live level pip-distance calculations
                this.currentBid = 0.0;
                this.currentAsk = 0.0;
                this.currentRes = 0.0;
                this.currentSup = 0.0;
                this.pipMult = 0.0001; // default EURUSD

                this.tickHistory = [];

                // Real-time tab selection & mock trading state
                this.activeTab = "cockpit";
                this.mockPositions = [];
                this.latestAccountData = null;

                // Candlestick Chart variables
                this.m15Candles = [];
                this.h1Candles = [];
                this.h4Candles = [];
                this.currentTimeframe = 'M15';
                this.priceLines = [];
                this.asianHighLine = null;
                this.asianLowLine = null;
                this.chartMarkers = [];
                this.timeframeLoaded = { M15: false, H1: false, H4: false };
                this.sessionsCache = { M15: [], H1: [], H4: [] };

                this.initializeUI();
                this.initChart();
                this.connect();
                this.startClock();
            }

            initializeUI() {
                // DOM bindings
                this.ticker = document.getElementById("header-ticker");
                this.uptime = document.getElementById("header-uptime");
                this.latency = document.getElementById("header-latency");
                
                this.socketLight = document.getElementById("socket-light");
                this.socketStatus = document.getElementById("socket-status");

                this.priceBid = document.getElementById("price-bid");
                this.priceAsk = document.getElementById("price-ask");
                
                this.beliefVal = document.getElementById("belief-val");
                this.beliefBar = document.getElementById("belief-bar");
                this.beliefAbortFlag = document.getElementById("belief-abort-flag");
                
                this.regimeType = document.getElementById("regime-type");
                this.regimeVol = document.getElementById("regime-vol");
                this.regimeConf = document.getElementById("regime-conf");

                this.spreadVal = document.getElementById("spread-val");
                this.spreadBox = document.getElementById("spread-box");
                this.spreadLabel = document.getElementById("spread-label");
                this.spreadStatusText = document.getElementById("spread-status-text");

                this.accBalance = document.getElementById("acc-balance");
                this.accEquity = document.getElementById("acc-equity");
                this.accMargin = document.getElementById("acc-margin");
                this.accFreeMargin = document.getElementById("acc-freemargin");
                this.accMarginLevel = document.getElementById("acc-marginlevel");
                this.accProfit = document.getElementById("acc-profit");

                this.statsDetected = document.getElementById("stats-detected");
                this.statsFired = document.getElementById("stats-fired");
                this.statsSkipped = document.getElementById("stats-skipped");
                this.cooldownVal = document.getElementById("ctrl-cooldown");
                this.cooldownBar = document.getElementById("cooldown-bar");

                this.levelResVal = document.getElementById("level-res-val");
                this.levelResTime = document.getElementById("level-res-time");
                this.levelResDist = document.getElementById("level-res-dist");
                this.levelResBar = document.getElementById("level-res-bar");
                
                this.levelSupVal = document.getElementById("level-sup-val");
                this.levelSupTime = document.getElementById("level-sup-time");
                this.levelSupDist = document.getElementById("level-sup-dist");
                this.levelSupBar = document.getElementById("level-sup-bar");

                this.sweepRadar = document.getElementById("sweep-radar");
                this.sweepIcon = document.getElementById("sweep-icon");
                this.sweepMsg = document.getElementById("sweep-msg");

                this.decisionPanel = document.getElementById("decision-panel");
                this.decisionStatus = document.getElementById("decision-status");
                this.decisionAction = document.getElementById("decision-action");
                this.decisionTime = document.getElementById("decision-time");
                this.prevDecision = document.getElementById("prev-decision-lbl");

                this.consoleBody = document.getElementById("console-body");
                this.eventsLog = document.getElementById("events-log-container");
                this.tickActivityStrip = document.getElementById("tick-activity-strip");

                // Clear console button
                const clearConsoleBtn = document.getElementById("clear-console-btn-intel");
                if (clearConsoleBtn) {
                    clearConsoleBtn.onclick = () => {
                        this.consoleBody.innerHTML = `<div style='color:var(--cyan);opacity:0.6;font-size:10px;'>[STREAM_CLEARED] Active.</div>`;
                        this.triggerBeep(800, 0.05);
                    };
                }

                // === MULTI-CURRENCY PAIR NAVIGATOR ===
                const pairBtns = document.querySelectorAll('.pair-btn');
                pairBtns.forEach(btn => {
                    btn.addEventListener('click', () => {
                        pairBtns.forEach(b => b.classList.remove('active'));
                        btn.classList.add('active');

                        const pair = btn.dataset.pair;
                        const mt5  = btn.dataset.mt5;
                        const label = btn.textContent.replace('/', '');

                        const ticker = document.getElementById('header-ticker');
                        if (ticker) ticker.textContent = label;

                        this.pipMult = (pair.includes('JPY') || pair.includes('XAU')) ? 0.01 : 0.0001;

                        this.m15Candles = [];
                        this.h1Candles  = [];
                        this.h4Candles  = [];
                        this.timeframeLoaded = { M15: false, H1: false, H4: false };
                        this.currentBid = 0;
                        this.currentAsk = 0;
                        if (this.chart) this.candleSeries.setData([]);

                        if (this.ws && this.ws.readyState === WebSocket.OPEN) {
                            this.ws.send(JSON.stringify({ type: 'switch_pair', pair: pair, mt5: mt5 }));
                        }

                        this.triggerBeep(1000, 0.03);
                    });
                });


                // Timeframe toggle buttons
                const m15Btn = document.getElementById("tf-m15-btn");
                const h1Btn = document.getElementById("tf-h1-btn");
                const h4Btn = document.getElementById("tf-h4-btn");
                if (m15Btn && h1Btn && h4Btn) {
                    const selectTimeframe = (tf) => {
                        this.currentTimeframe = tf;
                        m15Btn.className = tf === 'M15' ? "border border-[#00f0ff] bg-[#00f0ff]/10 text-[#00f0ff] text-[8px] px-2 py-0.5 font-bold font-mono uppercase transition-all" : "border border-[#1f1f2e] text-zinc-500 text-[8px] px-2 py-0.5 font-bold font-mono uppercase transition-all";
                        h1Btn.className = tf === 'H1' ? "border border-[#00f0ff] bg-[#00f0ff]/10 text-[#00f0ff] text-[8px] px-2 py-0.5 font-bold font-mono uppercase transition-all" : "border border-[#1f1f2e] text-zinc-500 text-[8px] px-2 py-0.5 font-bold font-mono uppercase transition-all";
                        h4Btn.className = tf === 'H4' ? "border border-[#00f0ff] bg-[#00f0ff]/10 text-[#00f0ff] text-[8px] px-2 py-0.5 font-bold font-mono uppercase transition-all" : "border border-[#1f1f2e] text-zinc-500 text-[8px] px-2 py-0.5 font-bold font-mono uppercase transition-all";
                        this.renderActiveCandles();
                    };
                    m15Btn.onclick = () => selectTimeframe('M15');
                    h1Btn.onclick = () => selectTimeframe('H1');
                    h4Btn.onclick = () => selectTimeframe('H4');
                }

                // Settings modal triggers
                const modal = document.getElementById("settings-modal");
                const settingsBtn = document.getElementById("settings-trigger-btn");
                const closeSettingsBtn = document.getElementById("close-settings-btn");
                
                if (settingsBtn && modal) {
                    settingsBtn.onclick = () => {
                        modal.style.opacity = "1";
                        modal.style.pointerEvents = "auto";
                        this.triggerBeep(900, 0.04);
                    };
                }
                
                if (closeSettingsBtn && modal) {
                    closeSettingsBtn.onclick = () => {
                        modal.style.opacity = "0";
                        modal.style.pointerEvents = "none";
                        this.triggerBeep(700, 0.04);
                    };
                }

                // Settings form submit handler
                const form = document.getElementById("settings-form");
                if (form) {
                    form.onsubmit = async (e) => {
                        e.preventDefault();
                        const formData = new FormData(form);
                        const data = {
                            tick_poll_interval_ms: parseInt(formData.get("tick_poll_interval_ms")),
                            realtime_suppress_asian: formData.get("realtime_suppress_asian") === "on",
                            realtime_level_reset_atr_multiple: parseFloat(formData.get("realtime_level_reset_atr_multiple")),
                            indicator_rsi_length: parseInt(formData.get("indicator_rsi_length")),
                            indicator_ema_fast: parseInt(formData.get("indicator_ema_fast")),
                            indicator_ema_slow: parseInt(formData.get("indicator_ema_slow"))
                        };
                        
                        try {
                            const res = await fetch("/config", {
                                method: "POST",
                                headers: { "Content-Type": "application/json" },
                                body: JSON.stringify(data)
                            });
                            const result = await res.json();
                            if (result.status === "success") {
                                logger("[SETTINGS] New configuration successfully applied.");
                                this.triggerBeep(1200, 0.1);
                                if (modal) {
                                    modal.style.opacity = "0";
                                    modal.style.pointerEvents = "none";
                                }
                                // Auto-fill form inputs for consistency next time
                                Object.keys(result.config).forEach(key => {
                                    const input = form.querySelector(`[name="${key}"]`);
                                    if (input) {
                                        if (input.type === "checkbox") {
                                            input.checked = !!result.config[key];
                                        } else {
                                            input.value = result.config[key];
                                        }
                                    }
                                });
                            }
                        } catch (err) {
                            logger("[SETTINGS ERROR] " + err);
                        }
                    };
                    
                    // Fetch and pre-populate current settings dynamically from the server
                    (async () => {
                        try {
                            const response = await fetch("/config");
                            const resData = await response.json();
                            if (resData.status === "success" && resData.config) {
                                const cfg = resData.config;
                                if (cfg.tick_poll_interval_ms !== undefined) form.querySelector('[name="tick_poll_interval_ms"]').value = cfg.tick_poll_interval_ms;
                                if (cfg.realtime_suppress_asian !== undefined) form.querySelector('[name="realtime_suppress_asian"]').checked = !!cfg.realtime_suppress_asian;
                                if (cfg.realtime_level_reset_atr_multiple !== undefined) form.querySelector('[name="realtime_level_reset_atr_multiple"]').value = cfg.realtime_level_reset_atr_multiple;
                                if (cfg.indicator_rsi_length !== undefined) form.querySelector('[name="indicator_rsi_length"]').value = cfg.indicator_rsi_length;
                                if (cfg.indicator_ema_fast !== undefined) form.querySelector('[name="indicator_ema_fast"]').value = cfg.indicator_ema_fast;
                                if (cfg.indicator_ema_slow !== undefined) form.querySelector('[name="indicator_ema_slow"]').value = cfg.indicator_ema_slow;
                            } else {
                                // Fallback defaults
                                form.querySelector('[name="tick_poll_interval_ms"]').value = 100;
                                form.querySelector('[name="realtime_suppress_asian"]').checked = true;
                                form.querySelector('[name="realtime_level_reset_atr_multiple"]').value = 2.0;
                                form.querySelector('[name="indicator_rsi_length"]').value = 14;
                                form.querySelector('[name="indicator_ema_fast"]').value = 20;
                                form.querySelector('[name="indicator_ema_slow"]').value = 50;
                            }
                        } catch (e) {
                            logger("Failed to fetch server active config on load: " + e);
                        }
                    })();
                }

                // Main Tabs switches
                ["cockpit", "intel"].forEach(t => {
                    const btn = document.getElementById(`main-tab-${t}-btn`);
                    if (btn) {
                        btn.onclick = () => {
                            this.switchTab(t);
                        };
                    }
                });

                // Keyboard hotkeys listener
                window.addEventListener('keydown', (e) => {
                    const isInput = e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA' || e.target.isContentEditable;
                    
                    // Switch via numbers 1, 2 (when not typing in an input) or Alt + 1, 2
                    if ((!isInput && (e.key === "1" || e.key === "2")) || (e.altKey && (e.key === "1" || e.key === "2"))) {
                        e.preventDefault();
                        const tabMap = { "1": "cockpit", "2": "intel" };
                        this.switchTab(tabMap[e.key]);
                        return;
                    }

                    // Cycle via ArrowLeft and ArrowRight when not typing in an input
                    if (!isInput && (e.key === "ArrowLeft" || e.key === "ArrowRight")) {
                        e.preventDefault();
                        const tabs = ["cockpit", "intel"];
                        let idx = tabs.indexOf(this.activeTab);
                        if (idx === -1) idx = 0;
                        const direction = e.key === "ArrowLeft" ? -1 : 1;
                        let nextIdx = (idx + direction + tabs.length) % tabs.length;
                        this.switchTab(tabs[nextIdx]);
                        return;
                    }
                });
            }

            connect() {
                const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
                const wsUri = `${protocol}//${window.location.host}/ws`;
                
                this.socketLight.className = "w-1.5 h-1.5 bg-amber-500 animate-pulse shadow-sm shadow-amber-500";
                this.socketStatus.innerText = "CONNECTING";
                this.socketStatus.className = "text-amber-500 font-extrabold text-[8px] uppercase tracking-widest";

                logger("Dashboard WS: connecting to " + wsUri);
                this.ws = new WebSocket(wsUri);

                this.ws.onopen = () => {
                    this.socketLight.style.background = 'var(--green)';
                    this.socketLight.style.borderRadius = '50%';
                    this.socketStatus.innerText = 'ONLINE';
                    this.socketStatus.style.color = 'var(--green)';
                    logger('Dashboard WS: connected.');
                    this.triggerBeep(1100, 0.08);
                    if (this.pingInterval) clearInterval(this.pingInterval);
                    this.pingInterval = setInterval(() => {
                        if (this.ws && this.ws.readyState === WebSocket.OPEN) {
                            this.ws.send(JSON.stringify({ type: 'ping', timestamp: Date.now() }));
                        }
                    }, 2000);
                };

                this.ws.onclose = () => {
                    this.socketLight.style.background = 'var(--red)';
                    this.socketStatus.innerText = 'OFFLINE';
                    this.socketStatus.style.color = 'var(--red)';
                    if (this.latency) this.latency.innerText = '--ms';
                    if (this.pingInterval) { clearInterval(this.pingInterval); this.pingInterval = null; }
                    logger('Dashboard WS: disconnected. Retrying in ' + this.reconnectInterval + 'ms');
                    setTimeout(() => this.connect(), this.reconnectInterval);
                };

                this.ws.onerror = (err) => {
                    logger("Dashboard WS: error: " + err);
                };

                this.ws.onmessage = (msg) => {
                    try {
                        const data = JSON.parse(msg.data);
                        this.handleMessage(data);
                    } catch (e) {
                        logger("Dashboard WS: failed to parse packet: " + e);
                    }
                };
            }

            handleMessage(data) {
                if (data.type === "pong") {
                    const rtt = Date.now() - data.timestamp;
                    this.latency.innerText = `${rtt}ms`;
                    return;
                }

                switch (data.type) {
                    case "tick":
                        this.handleTick(data);
                        break;
                    case "regime":
                        this.handleRegime(data);
                        break;
                    case "levels":
                        this.handleLevels(data);
                        break;
                    case "account":
                        this.handleAccount(data);
                        break;
                    case "candle":
                        this.handleCandle(data);
                        break;
                    case "candles":
                        this.handleCandles(data);
                        break;
                    case "event":
                        this.handleEvent(data);
                        break;
                    case "agent":
                        this.handleAgent(data);
                        break;
                    case "decision":
                        this.handleDecision(data);
                        break;
                    case "news_data":
                        this.handleNewsData(data);
                        break;
                }
            }

            initChart() {
                const chartElement = document.getElementById('price-chart');
                this.chart = LightweightCharts.createChart(chartElement, {
                    watermark: {
                        visible: false,
                    },
                    layout: { 
                        attributionLogo: false,
                        background: { color: '#000000' }, 
                        textColor: '#00f0ff',
                        fontFamily: '"JetBrains Mono", monospace'
                    },
                    grid: { vertLines: { color: 'rgba(255, 255, 255, 0.03)' }, horzLines: { color: 'rgba(255, 255, 255, 0.03)' } },
                    crosshair: {
                        vertLine: {
                            color: 'rgba(255, 255, 255, 0.12)',
                            width: 1,
                            style: LightweightCharts.LineStyle.Dashed,
                        },
                        horzLine: {
                            color: 'rgba(255, 255, 255, 0.12)',
                            width: 1,
                            style: LightweightCharts.LineStyle.Dashed,
                        },
                    },
                    width: chartElement.clientWidth || 800,
                    height: chartElement.clientHeight || 300,
                    rightPriceScale: {
                        borderColor: '#1f1f2e',
                        autoScale: true,
                        alignLabels: true,
                        entireTextOnly: true,
                        minimumWidth: 110, // Ensure ample horizontal space for full detailed labels (e.g. R [S:3] 1.16153)
                        scaleMargins: {
                            top: 0.05,
                            bottom: 0.05,
                        },
                    },
                    timeScale: {
                        borderColor: '#1f1f2e',
                        timeVisible: true,
                        secondsVisible: false,
                        barSpacing: 8,
                        minBarSpacing: 3,
                        rightOffset: 5,
                        tickMarkFormatter: (time, tickMarkType, locale) => {
                            const date = new Date(time * 1000);
                            const hr = String(date.getUTCHours()).padStart(2, '0');
                            const min = String(date.getUTCMinutes()).padStart(2, '0');
                            
                            if (tickMarkType === 0) {
                                return String(date.getUTCFullYear());
                            }
                            if (tickMarkType === 1) {
                                const months = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
                                return months[date.getUTCMonth()];
                            }
                            if (tickMarkType === 2) {
                                return String(date.getUTCDate());
                            }
                            return `${hr}:${min}`;
                        }
                    },
                    localization: {
                        priceFormatter: price => {
                            if (typeof price !== 'number') return '';
                            if (price > 200) return price.toFixed(2); // Gold / Indices
                            if (price > 50) return price.toFixed(3);  // JPY
                            return price.toFixed(5);                 // Forex
                        },
                        timeFormatter: (time) => {
                            const date = new Date(time * 1000);
                            const y = date.getUTCFullYear();
                            const m = String(date.getUTCMonth() + 1).padStart(2, '0');
                            const d = String(date.getUTCDate()).padStart(2, '0');
                            const hr = String(date.getUTCHours()).padStart(2, '0');
                            const min = String(date.getUTCMinutes()).padStart(2, '0');
                            return `${y}-${m}-${d} ${hr}:${min} UTC`;
                        }
                    }
                });

                // Create tooltip element
                const toolTip = document.createElement('div');
                toolTip.style = `
                    position: absolute;
                    display: none;
                    padding: 8px;
                    box-sizing: border-box;
                    font-size: 10px;
                    text-align: left;
                    z-index: 1000;
                    pointer-events: none;
                    border: 1px solid #1f1f2e;
                    border-radius: 4px;
                    background: rgba(7, 7, 11, 0.95);
                    color: #00f0ff;
                    font-family: "JetBrains Mono", monospace;
                    box-shadow: 0 4px 12px rgba(0,0,0,0.5);
                `;
                chartElement.appendChild(toolTip);

                this.chart.subscribeCrosshairMove(param => {
                    if (
                        param.point === undefined ||
                        !param.time ||
                        param.point.x < 0 ||
                        param.point.x > chartElement.clientWidth ||
                        param.point.y < 0 ||
                        param.point.y > chartElement.clientHeight
                    ) {
                        toolTip.style.display = 'none';
                        return;
                    }

                    const t = Number(param.time);
                    const markers = this.getMarkersForTimeframe(this.currentTimeframe);
                    const hoveredMarkerGroup = markers.find(m => m.time === t);

                    if (hoveredMarkerGroup) {
                        const matchingMarkers = this.chartMarkers.filter(m => {
                            const tf = (m.details && m.details.timeframe) ? m.details.timeframe : 'M15';
                            if (tf !== this.currentTimeframe) return false;

                            let mt = m.time;
                            if (this.currentTimeframe === 'M15') {
                                mt = Math.floor(mt / 900) * 900;
                            } else if (this.currentTimeframe === 'H1') {
                                mt = Math.floor(mt / 3600) * 3600;
                            } else if (this.currentTimeframe === 'H4') {
                                mt = Math.floor(mt / 14400) * 14400;
                            }
                            return mt === hoveredMarkerGroup.time && m.position === hoveredMarkerGroup.position;
                        });

                        if (matchingMarkers.length > 0) {
                            let content = `<div style="font-weight: bold; margin-bottom: 4px; border-bottom: 1px solid #1f1f2e; padding-bottom: 2px;">MARKET EVENTS</div>`;
                            matchingMarkers.forEach(m => {
                                content += `
                                    <div style="margin-bottom: 4px;">
                                        <span style="color: ${m.isBull ? '#00ff66' : '#ff0055'}; font-weight: bold;">
                                            ${m.isBull ? '↑' : '↓'} [${m.priority}] ${m.event_type.toUpperCase()}
                                        </span>
                                        ${m.details && m.details.pattern ? `<br/><span style="color: #888888;">Pattern: ${m.details.pattern}</span>` : ''}
                                        ${m.details && m.details.level_price ? `<br/><span style="color: #888888;">Level: ${m.details.level_price.toFixed(5)}</span>` : ''}
                                    </div>
                                `;
                            });
                            
                            toolTip.innerHTML = content;
                            toolTip.style.display = 'block';
                            
                            const x = param.point.x;
                            const y = param.point.y;
                            
                            let left = x + 15;
                            let top = y + 15;
                            
                            if (left > chartElement.clientWidth - 150) {
                                left = x - 160;
                            }
                            if (top > chartElement.clientHeight - 140) {
                                top = y - 110;
                            }
                            
                            toolTip.style.left = left + 'px';
                            toolTip.style.top = top + 'px';
                            return;
                        }
                    }
                    toolTip.style.display = 'none';
                });
                const seriesOptions = {
                    upColor: '#00ff66',
                    downColor: '#ff0055',
                    borderUpColor: '#00ff66',
                    borderDownColor: '#ff0055',
                    wickUpColor: '#00ff66',
                    wickDownColor: '#ff0055',
                    priceFormat: {
                        type: 'custom',
                        minMove: 0.00001,
                        formatter: price => {
                            if (typeof price !== 'number') return '';
                            if (price > 200) return price.toFixed(2); // Gold / Indices
                            if (price > 50) return price.toFixed(3);  // JPY
                            return price.toFixed(5);                 // Forex
                        }
                    },
                };
                if (typeof this.chart.addCandlestickSeries === 'function') {
                    this.candleSeries = this.chart.addCandlestickSeries(seriesOptions);
                } else {
                    this.candleSeries = this.chart.addSeries(LightweightCharts.CandlestickSeries, seriesOptions);
                }

                const resizeObserver = new ResizeObserver(entries => {
                    if (entries.length === 0 || !entries[0].contentRect) return;
                    const { width, height } = entries[0].contentRect;
                    if (width > 0 && height > 0) {
                        this.chart.resize(width, height);
                        setTimeout(() => this.drawSessions(), 50);
                    }
                });
                resizeObserver.observe(chartElement);

                this.chart.timeScale().subscribeVisibleLogicalRangeChange(() => {
                    this.drawSessions();
                });
            }

            handleCandles(data) {
                if (data.timeframe === 'M15') {
                    this.m15Candles = data.candles;
                } else if (data.timeframe === 'H1') {
                    this.h1Candles = data.candles;
                } else if (data.timeframe === 'H4') {
                    this.h4Candles = data.candles;
                }
                this.sessionsCache[data.timeframe] = this.getSessions(data.candles);
                if (data.timeframe === this.currentTimeframe) {
                    this.renderActiveCandles();
                }
            }

             renderActiveCandles() {
                 let target = this.h1Candles;
                 if (this.currentTimeframe === 'M15') {
                     target = this.m15Candles;
                 } else if (this.currentTimeframe === 'H1') {
                     target = this.h1Candles;
                 } else if (this.currentTimeframe === 'H4') {
                     target = this.h4Candles;
                 }
                 
                 const seenTimes = new Set();
                 const formatted = [];
                 
                 // Sort by time ascending
                 const sortedTarget = [...target].sort((a, b) => Number(a.time) - Number(b.time));
                 
                 for (const c of sortedTarget) {
                     if (c && Number(c.open) > 0 && Number(c.high) > 0 && Number(c.low) > 0 && Number(c.close) > 0) {
                         const t = Number(c.time);
                         if (!seenTimes.has(t)) {
                             seenTimes.add(t);
                             formatted.push({
                                 time: t,
                                 open: Number(c.open),
                                 high: Number(c.high),
                                 low: Number(c.low),
                                 close: Number(c.close)
                             });
                         }
                     }
                 }
                 
                 this.candleSeries.setData(formatted);
                 this.updateMarkers(this.getMarkersForTimeframe(this.currentTimeframe));
                 if (this.chart) {
                     if (!this.timeframeLoaded[this.currentTimeframe]) {
                         this.chart.timeScale().fitContent();
                         this.timeframeLoaded[this.currentTimeframe] = true;
                     } else {
                         this.chart.timeScale().scrollToRealTime();
                     }
                 }
                 this.drawSessions();
             }

            updateMarkers(markers) {
                if (typeof this.candleSeries.setMarkers === 'function') {
                    this.candleSeries.setMarkers(markers);
                } else if (window.LightweightCharts && typeof window.LightweightCharts.createSeriesMarkers === 'function') {
                    if (!this.markersPrimitive) {
                        this.markersPrimitive = window.LightweightCharts.createSeriesMarkers(this.candleSeries, markers);
                        this.candleSeries.attachPrimitive(this.markersPrimitive);
                    } else {
                        this.markersPrimitive.setMarkers(markers);
                    }
                }
            }

            getMarkersForTimeframe(timeframe) {
                let targetCandles = [];
                if (timeframe === 'M15') {
                    targetCandles = this.m15Candles || [];
                } else if (timeframe === 'H1') {
                    targetCandles = this.h1Candles || [];
                } else if (timeframe === 'H4') {
                    targetCandles = this.h4Candles || [];
                }
                const activeTimes = new Set(targetCandles.map(c => Number(c.time)));

                const filtered = this.chartMarkers.filter(m => {
                    const tf = (m.details && m.details.timeframe) ? m.details.timeframe : 'M15';
                    return tf === timeframe;
                });

                const grouped = {};
                
                // Enforce max-100 markers limit per timeframe
                const sorted = [...filtered].sort((a, b) => a.time - b.time).slice(-100);
                for (const m of sorted) {
                    let t = m.time;
                    if (timeframe === 'M15') {
                        t = Math.floor(t / 900) * 900;
                    } else if (timeframe === 'H1') {
                        t = Math.floor(t / 3600) * 3600;
                    } else if (timeframe === 'H4') {
                        t = Math.floor(t / 14400) * 14400;
                    }
                    
                    // Only draw on active, existing candles on the chart to prevent collapsed stacks over gaps
                    if (!activeTimes.has(t)) {
                        continue;
                    }
                    
                    const position = m.position; // 'aboveBar' or 'belowBar'
                    const key = `${t}_${position}`;
                    
                    if (!grouped[key]) {
                        grouped[key] = {
                            time: t,
                            position: position,
                            color: m.color,
                            shape: m.shape,
                            text: m.shortText
                        };
                    }
                }
                
                return Object.values(grouped).map(g => {
                    return {
                        time: g.time,
                        position: g.position,
                        color: g.color,
                        shape: g.shape,
                        text: g.text
                    };
                });
            }

            updateMTFPanel(data) {
                const colorDir = (d) => {
                    if (!d) return '<span class="text-zinc-500">--</span>';
                    const upper = d.toUpperCase();
                    if (upper.includes('BULL') || upper === 'UP') 
                        return `<span style="color:#00ff66">▲ ${upper}</span>`;
                    if (upper.includes('BEAR') || upper === 'DOWN') 
                        return `<span style="color:#ff0055">▼ ${upper}</span>`;
                    return `<span style="color:#888888">► ${upper}</span>`;
                };

                const h4El = document.getElementById('mtf-h4');
                const h1El = document.getElementById('mtf-h1');
                const m15El = document.getElementById('mtf-m15');
                if (h4El) h4El.innerHTML = colorDir(data.trend_h4);
                if (h1El) h1El.innerHTML = colorDir(data.trend_h1);
                if (m15El) m15El.innerHTML = colorDir(data.trend_m15);
                
                // LED-style bar U+2588 character for filled and empty, guaranteeing perfect monospace widths!
                const bar = (val, min=-1, max=1, colorClass="text-[#00f0ff]") => {
                    const pct = Math.max(0, Math.min(1, (val - min) / (max - min)));
                    const filled = Math.round(pct * 10);
                    let html = '';
                    for (let i = 0; i < 10; i++) {
                        if (i < filled) {
                            html += `<span class="${colorClass}">█</span>`;
                        } else {
                            html += `<span class="text-zinc-950">█</span>`;
                        }
                    }
                    return html;
                };
                
                const eurBar = document.getElementById('eur-bar');
                const eurVal = document.getElementById('eur-val');
                if (eurBar && data.eur_strength !== undefined) {
                    eurBar.innerHTML = bar(data.eur_strength, -1, 1, "text-[#00ff66]");
                    eurVal.textContent = data.eur_strength.toFixed(2);
                }
                const usdBar = document.getElementById('usd-bar');
                const usdVal = document.getElementById('usd-val');
                if (usdBar && data.usd_strength !== undefined) {
                    usdBar.innerHTML = bar(data.usd_strength, -1, 1, "text-[#00f0ff]");
                    usdVal.textContent = data.usd_strength.toFixed(2);
                }
                
                const scoresDiv = document.getElementById('regime-scores');
                if (scoresDiv && data.regime_scores) {
                    scoresDiv.innerHTML = Object.entries(data.regime_scores)
                        .sort((a,b) => b[1]-a[1])
                        .map(([k,v]) => {
                            const barHtml = bar(v, 0, 1, "text-amber-500");
                            return `<div class="flex justify-between items-center py-0.5">
                                <span class="uppercase font-bold w-20 text-zinc-400">${k}</span>
                                <div class="flex items-center gap-2">
                                    <span>${barHtml}</span>
                                    <span class="text-white font-bold w-8 text-right">${v.toFixed(2)}</span>
                                </div>
                            </div>`;
                        })
                        .join('');
                }
                
                const ldnBias = document.getElementById('london-bias');
                if (ldnBias) ldnBias.innerHTML = colorDir(data.london_open_bias);

                const ldnHigh = document.getElementById('london-high-val');
                if (ldnHigh) {
                    if (data.london_range_high && data.london_range_high > 0) {
                        ldnHigh.textContent = data.london_range_high.toFixed(5);
                    } else {
                        ldnHigh.textContent = "--";
                    }
                }

                const ldnLow = document.getElementById('london-low-val');
                if (ldnLow) {
                    if (data.london_range_low && data.london_range_low > 0) {
                        ldnLow.textContent = data.london_range_low.toFixed(5);
                    } else {
                        ldnLow.textContent = "--";
                    }
                }
            }

            getSessions(candles) {
                if (!candles || candles.length === 0) return [];
                const sessions = [];
                const days = {};
                
                // Single pass to cache date parts and group by day
                for (let i = 0; i < candles.length; i++) {
                    const c = candles[i];
                    const date = new Date(c.time * 1000);
                    const y = date.getUTCFullYear();
                    const m = date.getUTCMonth() + 1;
                    const d = date.getUTCDate();
                    const hour = date.getUTCHours();
                    const dayKey = `${y}-${m}-${d}`;
                    if (!days[dayKey]) {
                        days[dayKey] = [];
                    }
                    days[dayKey].push({ c, hour });
                }

                const configs = [
                    {
                        name: 'Asian',
                        startHour: 0,
                        endHour: 8,
                        color: 'rgba(255, 170, 0, 0.04)',
                        strokeColor: 'rgba(255, 170, 0, 0.18)',
                        lineColor: 'rgba(255, 170, 0, 0.35)'
                    },
                    {
                        name: 'London',
                        startHour: 8,
                        endHour: 16,
                        color: 'rgba(157, 0, 255, 0.04)',
                        strokeColor: 'rgba(157, 0, 255, 0.18)',
                        lineColor: 'rgba(157, 0, 255, 0.35)'
                    },
                    {
                        name: 'NY',
                        startHour: 13,
                        endHour: 21,
                        color: 'rgba(0, 240, 255, 0.04)',
                        strokeColor: 'rgba(0, 240, 255, 0.18)',
                        lineColor: 'rgba(0, 240, 255, 0.35)'
                    }
                ];

                for (const dayKey in days) {
                    const dayItems = days[dayKey];
                    for (let j = 0; j < configs.length; j++) {
                        const cfg = configs[j];
                        const sessCandles = [];
                        for (let k = 0; k < dayItems.length; k++) {
                            const item = dayItems[k];
                            if (item.hour >= cfg.startHour && item.hour < cfg.endHour) {
                                sessCandles.push(item.c);
                            }
                        }

                        if (sessCandles.length > 0) {
                            const startTime = sessCandles[0].time;
                            const endTime = sessCandles[sessCandles.length - 1].time;
                            
                            let high = sessCandles[0].high;
                            let low = sessCandles[0].low;
                            for (let k = 1; k < sessCandles.length; k++) {
                                if (sessCandles[k].high > high) high = sessCandles[k].high;
                                if (sessCandles[k].low < low) low = sessCandles[k].low;
                            }
                            const open = sessCandles[0].open;
                            const close = sessCandles[sessCandles.length - 1].close;

                            sessions.push({
                                name: cfg.name,
                                startTime,
                                endTime,
                                high,
                                low,
                                open,
                                close,
                                color: cfg.color,
                                strokeColor: cfg.strokeColor,
                                lineColor: cfg.lineColor
                            });
                        }
                    }
                }
                return sessions;
            }

            drawSessions() {
                if (!this.chart || !this.candleSeries) return;

                let svg = document.getElementById('chart-overlay-svg');
                if (!svg) {
                    const chartElement = document.getElementById('price-chart');
                    svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
                    svg.id = 'chart-overlay-svg';
                    svg.style.position = 'absolute';
                    svg.style.top = '0';
                    svg.style.left = '0';
                    svg.style.width = '100%';
                    svg.style.height = '100%';
                    svg.style.pointerEvents = 'none';
                    svg.style.zIndex = '5';
                    chartElement.style.position = 'relative';
                    chartElement.appendChild(svg);
                }

                svg.innerHTML = '';

                const sessions = this.sessionsCache[this.currentTimeframe];
                if (!sessions || sessions.length === 0) return;

                const gridWidth = this.chart.timeScale().width();
                if (gridWidth === null || isNaN(gridWidth)) return;

                const visibleRange = this.chart.timeScale().getVisibleRange();
                const fromTime = visibleRange ? visibleRange.from : null;
                const toTime = visibleRange ? visibleRange.to : null;

                sessions.forEach(sess => {
                    // Skip drawing if completely off-screen
                    if (fromTime !== null && toTime !== null) {
                        if (sess.endTime < fromTime || sess.startTime > toTime) {
                            return;
                        }
                    }

                    const xStart = this.chart.timeScale().timeToCoordinate(sess.startTime);
                    const xEndLast = this.chart.timeScale().timeToCoordinate(sess.endTime);
                    const yHigh = this.candleSeries.priceToCoordinate(sess.high);
                    const yLow = this.candleSeries.priceToCoordinate(sess.low);
                    const yOpen = this.candleSeries.priceToCoordinate(sess.open);
                    const yClose = this.candleSeries.priceToCoordinate(sess.close);

                    if (xStart === null || xEndLast === null || yHigh === null || yLow === null) return;
                    if (isNaN(xStart) || isNaN(xEndLast) || isNaN(yHigh) || isNaN(yLow)) return;

                    const barSpacing = this.chart.timeScale().options().barSpacing || 6;
                    const boxX = xStart - barSpacing / 2;
                    const width = (xEndLast - xStart) + barSpacing;
                    const height = yLow - yHigh;

                    if (width <= 0 || height <= 0) return;

                    const g = document.createElementNS('http://www.w3.org/2000/svg', 'g');

                    const rect = document.createElementNS('http://www.w3.org/2000/svg', 'rect');
                    rect.setAttribute('x', boxX);
                    rect.setAttribute('y', yHigh);
                    rect.setAttribute('width', width);
                    rect.setAttribute('height', height);
                    rect.setAttribute('fill', sess.color);
                    rect.setAttribute('stroke', sess.strokeColor);
                    rect.setAttribute('stroke-width', '1');
                    rect.setAttribute('stroke-dasharray', '2,2');
                    g.appendChild(rect);

                    if (yOpen !== null && !isNaN(yOpen) && yOpen >= yHigh && yOpen <= yLow) {
                        const openLine = document.createElementNS('http://www.w3.org/2000/svg', 'line');
                        openLine.setAttribute('x1', boxX);
                        openLine.setAttribute('y1', yOpen);
                        openLine.setAttribute('x2', boxX + width);
                        openLine.setAttribute('y2', yOpen);
                        openLine.setAttribute('stroke', sess.lineColor);
                        openLine.setAttribute('stroke-width', '1');
                        openLine.setAttribute('stroke-dasharray', '4,4');
                        g.appendChild(openLine);

                        const openText = document.createElementNS('http://www.w3.org/2000/svg', 'text');
                        openText.setAttribute('x', boxX + 5);
                        openText.setAttribute('y', yOpen - 3);
                        openText.setAttribute('fill', sess.lineColor);
                        openText.style.fontFamily = '"JetBrains Mono", monospace';
                        openText.style.fontSize = '7px';
                        openText.style.fontWeight = 'bold';
                        openText.textContent = `OPEN: ${sess.open.toFixed(5)}`;
                        g.appendChild(openText);
                    }

                    if (yClose !== null && !isNaN(yClose) && yClose >= yHigh && yClose <= yLow) {
                        const closeLine = document.createElementNS('http://www.w3.org/2000/svg', 'line');
                        closeLine.setAttribute('x1', boxX);
                        closeLine.setAttribute('y1', yClose);
                        closeLine.setAttribute('x2', boxX + width);
                        closeLine.setAttribute('y2', yClose);
                        closeLine.setAttribute('stroke', sess.lineColor);
                        closeLine.setAttribute('stroke-width', '1');
                        closeLine.setAttribute('stroke-dasharray', '4,4');
                        g.appendChild(closeLine);

                        const closeText = document.createElementNS('http://www.w3.org/2000/svg', 'text');
                        closeText.setAttribute('x', boxX + width - 5);
                        closeText.setAttribute('y', yClose - 3);
                        closeText.setAttribute('text-anchor', 'end');
                        closeText.setAttribute('fill', sess.lineColor);
                        closeText.style.fontFamily = '"JetBrains Mono", monospace';
                        closeText.style.fontSize = '7px';
                        closeText.style.fontWeight = 'bold';
                        closeText.textContent = `CLOSE: ${sess.close.toFixed(5)}`;
                        g.appendChild(closeText);
                    }

                    const titleText = document.createElementNS('http://www.w3.org/2000/svg', 'text');
                    titleText.setAttribute('x', boxX + 5);
                    titleText.setAttribute('y', yHigh + 10);
                    titleText.setAttribute('fill', sess.lineColor);
                    titleText.style.fontFamily = '"JetBrains Mono", monospace';
                    titleText.style.fontSize = '8px';
                    titleText.style.fontWeight = 'bold';
                    titleText.style.textShadow = '0 0 2px rgba(0,0,0,0.8)';
                    titleText.textContent = sess.name.toUpperCase();
                    g.appendChild(titleText);

                    svg.appendChild(g);
                });

                // Draw S/R levels on the SVG overlay so they don't squash the chart scale
                this.drawLevels(svg, gridWidth);
            }

            drawLevels(svg, gridWidth) {
                if (!this.latestZones || this.latestZones.length === 0) return;

                this.latestZones.forEach(zone => {
                    const price = Number(zone.price);
                    const y = this.candleSeries.priceToCoordinate(price);
                    if (y === null || isNaN(y)) return;

                    const isRes = zone.type === 'resistance';
                    const color = isRes ? 'rgba(255, 0, 85, 0.45)' : 'rgba(0, 255, 102, 0.45)';
                    const title = `${isRes ? 'R' : 'S'} [S:${zone.strength}]`;

                    // Horizontal dotted line across the grid
                    const line = document.createElementNS('http://www.w3.org/2000/svg', 'line');
                    line.setAttribute('x1', 0);
                    line.setAttribute('y1', y);
                    line.setAttribute('x2', gridWidth);
                    line.setAttribute('y2', y);
                    line.setAttribute('stroke', color);
                    line.setAttribute('stroke-width', '1');
                    line.setAttribute('stroke-dasharray', '2,4');
                    svg.appendChild(line);

                    // Price label right next to the line
                    const text = document.createElementNS('http://www.w3.org/2000/svg', 'text');
                    text.setAttribute('x', gridWidth - 5);
                    text.setAttribute('y', y - 4);
                    text.setAttribute('text-anchor', 'end');
                    text.setAttribute('fill', color);
                    text.style.fontFamily = '"JetBrains Mono", monospace';
                    text.style.fontSize = '8px';
                    text.style.fontWeight = 'bold';
                    text.style.textShadow = '0 0 2px rgba(0,0,0,0.9)';
                    text.textContent = `${title}: ${price.toFixed(5)}`;
                    svg.appendChild(text);
                });
            }

            handleTick(data) {
                if (data.symbol) {
                    this.ticker.innerText = data.symbol;
                }
                const prevBid = this.currentBid;
                this.currentBid = data.bid;
                this.currentAsk = data.ask;
                
                // Live prices
                this.priceBid.innerText = data.bid.toFixed(5);
                this.priceAsk.innerText = data.ask.toFixed(5);
                
                // Live spread
                this.spreadVal.innerText = data.spread.toFixed(1);

                // Real-time dynamic mock position calculations
                if (this.mockPositions && this.mockPositions.length > 0) {
                    this.mockPositions.forEach(p => {
                        p.price_current = p.type === "BUY" ? data.bid : data.ask;
                        const factor = p.symbol.includes("JPY") ? 100 : 1;
                        p.profit = (p.type === "BUY" ? (data.bid - p.price_open) : (p.price_open - data.ask)) * p.volume * 100000 * factor;
                    });
                    if (this.latestAccountData) {
                        this.handleAccount(this.latestAccountData);
                    }
                }

                // Re-calculate swing level pip distances on tick
                this.updatePipDistances();

                // Stream tick activity visually to strip
                this.updateTickActivity(data.bid, prevBid);

                // Flash live tick dot indicator next to chart title
                const dot = document.getElementById("live-tick-dot");
                if (dot) {
                    dot.className = "w-1.5 h-1.5 rounded-full bg-[#00ff66] shadow-[0_0_8px_#00ff66] transition-all duration-0 flex-shrink-0";
                    setTimeout(() => {
                        dot.className = "w-1.5 h-1.5 rounded-full bg-zinc-700 shadow-none transition-all duration-300 flex-shrink-0";
                    }, 80);
                }

                // Live-update both timeframe charts in the background on every tick!
                if (this.candleSeries) {
                    const timeframes = [
                        { name: 'M15', target: this.m15Candles, periodSec: 900 },
                        { name: 'H1', target: this.h1Candles, periodSec: 3600 },
                        { name: 'H4', target: this.h4Candles, periodSec: 14400 }
                    ];

                    timeframes.forEach(tf => {
                        const target = tf.target;
                        if (target && target.length > 0) {
                            let tickTime = Math.floor(Date.now() / 1000);
                            if (data.time) {
                                tickTime = Math.floor(data.time);
                            } else if (data.timestamp) {
                                try {
                                    const parsed = Date.parse(data.timestamp.replace(' ', 'T'));
                                    if (!isNaN(parsed)) {
                                        tickTime = Math.floor(parsed / 1000);
                                    }
                                } catch (e) {
                                    // fallback
                                }
                            }
                            const periodStart = Math.floor(tickTime / tf.periodSec) * tf.periodSec;
                            const lastCandle = target[target.length - 1];
                            const chartPrice = data.bid; // Standardize on Bid price to avoid mismatch with MT5

                            if (periodStart === Number(lastCandle.time)) {
                                lastCandle.close = chartPrice;
                                lastCandle.high = Math.max(Number(lastCandle.high), chartPrice);
                                lastCandle.low = Math.min(Number(lastCandle.low), chartPrice);
                                
                                if (tf.name === this.currentTimeframe) {
                                    this.candleSeries.update({
                                        time: Number(lastCandle.time),
                                        open: Number(lastCandle.open),
                                        high: Number(lastCandle.high),
                                        low: Number(lastCandle.low),
                                        close: Number(lastCandle.close)
                                    });
                                }
                            } else if (periodStart > Number(lastCandle.time)) {
                                const newCandle = {
                                    time: periodStart,
                                    open: chartPrice,
                                    high: chartPrice,
                                    low: chartPrice,
                                    close: chartPrice
                                };
                                target.push(newCandle);
                                this.sessionsCache[tf.name] = this.getSessions(target);
                                
                                if (tf.name === this.currentTimeframe) {
                                    this.candleSeries.update(newCandle);
                                    this.chart.timeScale().scrollToRealTime();
                                }
                            }
                        }
                    });
                }
            }

            updateTickActivity(newBid, prevBid) {
                if (prevBid === 0.0) return;
                
                let colorClass = "bg-[#252538]";
                if (newBid > prevBid) colorClass = "bg-[#00ff66] shadow-sm shadow-[#00ff66]";
                if (newBid < prevBid) colorClass = "bg-[#ff0055] shadow-sm shadow-[#ff0055]";
                
                const block = document.createElement("span");
                block.className = `w-1.5 h-3.5 flex-shrink-0 transition-all ${colorClass}`;
                
                this.tickActivityStrip.appendChild(block);
                if (this.tickActivityStrip.children.length > 8) {
                    this.tickActivityStrip.removeChild(this.tickActivityStrip.firstChild);
                }
                this.triggerBeep(1200, 0.005); // high density audio ticking tick
            }

            handleRegime(data) {
                let sym = data.symbol || "EURUSD";
                if (sym.endsWith("=X")) sym = sym.replace("=X", "m");
                this.ticker.innerText = sym;

                // Conviction Score
                this.beliefVal.innerText = data.belief.toFixed(2);
                this.beliefBar.style.width = `${Math.min(100, data.belief * 100)}%`;
                
                if (data.belief >= 0.60 && data.should_run_graph) {
                    this.beliefVal.className = "text-lg font-black text-white leading-none";
                    this.beliefAbortFlag.classList.add("hidden");
                } else {
                    this.beliefVal.className = "text-lg font-black text-[#ff0055] leading-none animate-pulse";
                    if (data.abort_reason) {
                        this.beliefAbortFlag.classList.remove("hidden");
                        this.beliefAbortFlag.innerText = data.abort_reason.toUpperCase().replace('_', ' ');
                    }
                }

                // Dominant Regime
                this.regimeType.innerText = data.dominant;
                this.regimeConf.innerText = `${(data.confidence * 100).toFixed(0)}%`;
                this.regimeVol.innerText = `VOL: ${data.volatility.toUpperCase()} | ATR: ${data.atr.toFixed(5)}`;

                if (data.dominant === "trending" || data.dominant === "breakout") {
                    this.regimeType.className = "text-sm font-black text-[#00ff66] uppercase tracking-wider leading-none";
                } else if (data.dominant === "ranging" || data.dominant === "compression") {
                    this.regimeType.className = "text-sm font-black text-amber-500 uppercase tracking-wider leading-none";
                } else {
                    this.regimeType.className = "text-sm font-black text-[#ff0055] uppercase tracking-wider leading-none animate-pulse";
                }

                // Active Session Timeline
                this.marketClosed = !!data.market_closed;
                this.marketResumeTimestamp = data.market_resume_timestamp || 0;
                this.updateMarketStatusUI();

                if (data.session_details) {
                    this.updateSessionTimeline(data.session_details);
                }

                // Spread safety styling (using robust DOM guards)
                if (this.spreadLabel) {
                    if (data.spread_safe) {
                        this.spreadBox.className = "px-2.5 py-0.5 border border-[#00ff66]/30 bg-[#00ff66]/5 flex items-center justify-center";
                        this.spreadStatusText.innerText = "SAFE";
                        this.spreadStatusText.className = "text-[#00ff66] font-bold text-[7px] tracking-wider uppercase";
                        this.spreadLabel.innerText = "TELEMETRY_SAFE";
                        this.spreadLabel.className = "text-[6px] text-zinc-500 uppercase font-bold tracking-widest";
                    } else {
                        this.spreadBox.className = "px-2.5 py-0.5 border border-[#ff0055]/30 bg-[#ff0055]/5 flex items-center justify-center animate-pulse";
                        this.spreadStatusText.innerText = "WIDE";
                        this.spreadStatusText.className = "text-[#ff0055] font-bold text-[7px] tracking-wider uppercase";
                        this.spreadLabel.innerText = "WIDE_GATED";
                        this.spreadLabel.className = "text-[6px] text-[#ff0055] uppercase font-bold tracking-widest";
                    }
                }

                // Sync Python Core Daemon status, stats, and real uptime
                if (data.events_detected !== undefined) {
                    this.statsDetected.innerText = data.events_detected;
                    this.statsFired.innerText = data.events_fired;
                    this.statsSkipped.innerText = data.events_skipped;
                }

                if (data.daemon_start_time) {
                    this.startTime = Number(data.daemon_start_time);
                }

                if (data.cooldown_remaining !== undefined && data.cooldown_remaining > 0) {
                    this.cooldownVal.innerText = `${data.cooldown_remaining}s`;
                    this.cooldownVal.className = "text-amber-500 font-bold";
                    this.cooldownBar.style.width = `${(data.cooldown_remaining / 300 * 100).toFixed(0)}%`;
                } else if (data.cooldown_remaining === 0) {
                    this.cooldownVal.innerText = "READY";
                    this.cooldownVal.className = "text-[#00ff66] font-bold";
                    this.cooldownBar.style.width = "0%";
                }

                // Clean up legacy full-width session range price lines
                if (this.asianHighLine) { this.candleSeries.removePriceLine(this.asianHighLine); this.asianHighLine = null; }
                if (this.asianLowLine) { this.candleSeries.removePriceLine(this.asianLowLine); this.asianLowLine = null; }
                if (this.londonHighLine) { this.candleSeries.removePriceLine(this.londonHighLine); this.londonHighLine = null; }
                if (this.londonLowLine) { this.candleSeries.removePriceLine(this.londonLowLine); this.londonLowLine = null; }
                if (this.nyHighLine) { this.candleSeries.removePriceLine(this.nyHighLine); this.nyHighLine = null; }
                if (this.nyLowLine) { this.candleSeries.removePriceLine(this.nyLowLine); this.nyLowLine = null; }

                this.drawSessions();

                // Update MTF Sentiment Panel
                this.updateMTFPanel(data);
            }

            updateMarketStatusUI() {
                const badge = document.getElementById('market-status-badge');
                const countdownPanel = document.getElementById('market-closed-countdown');
                const sessionStack = document.getElementById('session-stack');
                
                if (!badge || !countdownPanel || !sessionStack) return;

                if (this.marketClosed) {
                    badge.innerText = 'MARKET_CLOSED';
                    badge.className = 'text-[6.5px] bg-rose-950/30 text-rose-500 border border-rose-500/20 px-1.5 py-0.2 font-bold tracking-wider uppercase rounded-sm';
                    sessionStack.classList.add('hidden');
                    countdownPanel.classList.remove('hidden');
                } else {
                    badge.innerText = 'MARKET_OPEN';
                    badge.className = 'text-[6.5px] bg-[#00ff66]/10 text-[#00ff66] border border-[#00ff66]/20 px-1.5 py-0.2 font-bold tracking-wider uppercase rounded-sm';
                    sessionStack.classList.remove('hidden');
                    countdownPanel.classList.add('hidden');
                }
            }

            updateSessionTimeline(details) {
                const rows = document.querySelectorAll('#session-stack .sess-row');
                rows.forEach(row => {
                    const sessName = row.getAttribute('data-sess');
                    const info = details.find(d => d.name === sessName);
                    if (!info) return;

                    const nameEl = row.querySelector('.sess-name');
                    const timeEl = row.querySelector('.sess-time');
                    const barEl = row.querySelector('.sess-bar');
                    const trackEl = row.querySelector('.sess-track');

                    if (info.active) {
                        // ACTIVE: colored name, glowing bar, show remaining time
                        nameEl.style.color = info.color;
                        nameEl.style.textShadow = `0 0 6px ${info.color}40`;
                        timeEl.style.color = info.color;
                        timeEl.innerText = `${info.remaining_min}m LEFT`;
                        trackEl.style.borderColor = info.color + '40';
                        barEl.style.width = `${(info.progress * 100).toFixed(1)}%`;
                        barEl.style.background = `linear-gradient(90deg, ${info.color}CC, ${info.color}40)`;
                        barEl.style.boxShadow = `0 0 4px ${info.color}60`;
                    } else {
                        // INACTIVE: dim everything
                        nameEl.style.color = '#444';
                        nameEl.style.textShadow = 'none';
                        timeEl.style.color = '#333';
                        const openH = String(Math.floor(info.open_utc)).padStart(2,'0');
                        const closeH = String(Math.floor(info.close_utc)).padStart(2,'0');
                        timeEl.innerText = `${openH}-${closeH} UTC`;
                        trackEl.style.borderColor = '#222';
                        barEl.style.width = '0%';
                        barEl.style.background = '#222';
                        barEl.style.boxShadow = 'none';
                    }
                });
            }

            handleLevels(data) {
                const highs = data.swing_highs;
                const lows = data.swing_lows;
                
                if (highs && highs.length > 0) {
                    const priceSample = highs[0].price;
                    this.pipMult = priceSample > 200 ? 0.01 : 0.0001; // Gold/JPY vs standard FX
                }

                if (highs && highs.length > 0) {
                    const latestH = highs[0];
                    this.currentRes = latestH.price;
                    this.levelResVal.innerText = latestH.price.toFixed(5);
                    this.levelResTime.innerText = `SH AT ${latestH.time.slice(-8)}`;
                }

                if (lows && lows.length > 0) {
                    const latestL = lows[0];
                    this.currentSup = latestL.price;
                    this.levelSupVal.innerText = latestL.price.toFixed(5);
                    this.levelSupTime.innerText = `SL AT ${latestL.time.slice(-8)}`;
                }

                // Clean up any remaining legacy price lines
                if (this.priceLines) {
                    this.priceLines.forEach(line => this.candleSeries.removePriceLine(line));
                }
                this.priceLines = [];
                this.latestZones = [];

                if (data.sr_zones) {
                    // Sort by strength descending
                    const sortedZones = [...data.sr_zones]
                        .sort((a, b) => (b.strength || 0) - (a.strength || 0));

                    const filteredZones = [];
                    const minDistance = this.pipMult * 5; // 5 pips minimum distance to prevent visual overlaps

                    for (const zone of sortedZones) {
                        const price = Number(zone.price);
                        const isClose = filteredZones.some(z => Math.abs(Number(z.price) - price) < minDistance);
                        if (!isClose) {
                            filteredZones.push(zone);
                        }
                        if (filteredZones.length >= 6) break;
                    }

                    this.latestZones = filteredZones;
                }

                this.drawSessions();
                this.updatePipDistances();
            }

            updatePipDistances() {
                if (this.currentBid === 0.0) return;

                // Resistance proximity
                if (this.currentRes > 0.0) {
                    const distPips = (this.currentRes - this.currentBid) / this.pipMult;
                    this.levelResDist.innerText = `${distPips.toFixed(1)} Pips`;
                    
                    const proxPercent = Math.max(0, 100 - (distPips / 50 * 100));
                    this.levelResBar.style.width = `${proxPercent}%`;

                    if (distPips <= 2.5) {
                        this.levelResDist.className = "bg-rose-950/20 border border-[#ff0055]/40 px-1.5 py-0.2 text-[#ff0055] font-extrabold animate-pulse";
                    } else if (distPips <= 10.0) {
                        this.levelResDist.className = "bg-rose-950/10 border border-[#ff0055]/20 px-1.5 py-0.2 text-[#ff0055] font-bold";
                    } else {
                        this.levelResDist.className = "bg-zinc-950 border border-zinc-900 px-1.5 py-0.2 text-zinc-500";
                    }
                }

                // Support proximity
                if (this.currentSup > 0.0) {
                    const distPips = (this.currentBid - this.currentSup) / this.pipMult;
                    this.levelSupDist.innerText = `${distPips.toFixed(1)} Pips`;
                    
                    const proxPercent = Math.max(0, 100 - (distPips / 50 * 100));
                    this.levelSupBar.style.width = `${proxPercent}%`;

                    if (distPips <= 2.5) {
                        this.levelSupDist.className = "bg-emerald-950/20 border border-[#00ff66]/40 px-1.5 py-0.2 text-[#00ff66] font-extrabold animate-pulse";
                    } else if (distPips <= 10.0) {
                        this.levelSupDist.className = "bg-emerald-950/10 border border-[#00ff66]/20 px-1.5 py-0.2 text-[#00ff66] font-bold";
                    } else {
                        this.levelSupDist.className = "bg-zinc-950 border border-zinc-900 px-1.5 py-0.2 text-zinc-500";
                    }
                }
            }

            handleAccount(data) {
                this.latestAccountData = data;
                
                // Sum mock profits
                let mockProfitSum = 0;
                if (this.mockPositions && this.mockPositions.length > 0) {
                    this.mockPositions.forEach(p => mockProfitSum += p.profit);
                }
                
                const totalBalance = data.balance;
                const totalProfit = data.profit + mockProfitSum;
                const totalEquity = data.equity + mockProfitSum;
                const totalMargin = data.margin;
                const totalFreeMargin = data.free_margin + mockProfitSum;
                const totalMarginLevel = totalMargin > 0 ? (totalEquity / totalMargin * 100) : 0;

                // Update Left Sidebar metrics if present
                if (this.accBalance) this.accBalance.innerText = `$${totalBalance.toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2})}`;
                if (this.accEquity) this.accEquity.innerText = `$${totalEquity.toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2})}`;
                if (this.accMargin) this.accMargin.innerText = `$${totalMargin.toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2})}`;
                if (this.accFreeMargin) this.accFreeMargin.innerText = `$${totalFreeMargin.toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2})}`;
                if (this.accMarginLevel) {
                    this.accMarginLevel.innerText = totalMarginLevel ? `${totalMarginLevel.toFixed(2)}%` : "0.00%";
                }
                
                if (this.accProfit) {
                    this.accProfit.innerText = (totalProfit >= 0 ? "+" : "") + `$${totalProfit.toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2})}`;
                    this.accProfit.className = totalProfit > 0.0 ? "font-extrabold text-[#00ff66]" : (totalProfit < 0.0 ? "font-extrabold text-[#ff0055]" : "font-extrabold text-white");
                }

                // Update Broker panel metrics
                const balEl = document.getElementById("broker-balance-val");
                const eqEl = document.getElementById("broker-equity-val");
                const pnlEl = document.getElementById("broker-profit-val");
                const margEl = document.getElementById("broker-margin-val");
                const mlEl = document.getElementById("broker-marginlevel-val");

                if (balEl) balEl.innerText = `$${totalBalance.toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2})}`;
                if (eqEl) eqEl.innerText = `$${totalEquity.toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2})}`;
                if (pnlEl) {
                    pnlEl.innerText = (totalProfit >= 0 ? "+" : "") + `$${totalProfit.toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2})}`;
                    pnlEl.className = totalProfit > 0 ? "text-[#00ff66] font-black text-xs mt-0.5" : (totalProfit < 0 ? "text-[#ff0055] font-black text-xs mt-0.5" : "text-white font-black text-xs mt-0.5");
                }
                if (margEl) margEl.innerText = `$${totalMargin.toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2})}`;
                if (mlEl) mlEl.innerText = totalMarginLevel ? `${totalMarginLevel.toFixed(2)}%` : "0.00%";

                // Render active positions list
                const tableBody = document.getElementById("positions-table-body");
                if (tableBody) {
                    const allPositions = [...(data.positions || []), ...(this.mockPositions || [])];
                    if (allPositions.length > 0) {
                        tableBody.innerHTML = "";
                        allPositions.forEach(p => {
                            const pnlColor = p.profit > 0 ? "text-[#00ff66]" : (p.profit < 0 ? "text-[#ff0055]" : "text-white");
                            const typeColor = p.type === "BUY" ? "text-[#00ff66]" : "text-[#ff0055]";
                            
                            const div = document.createElement("div");
                            div.className = "grid grid-cols-8 gap-2 p-1 border-b border-[#1f1f2e]/50 text-center font-mono hover:bg-white/5 transition-all";
                            div.innerHTML = `
                                <div class="text-zinc-400 font-bold">${p.ticket}</div>
                                <div class="text-white">${p.symbol}</div>
                                <div class="${typeColor} font-bold">${p.type}</div>
                                <div class="text-zinc-300">${p.volume.toFixed(2)}</div>
                                <div class="text-zinc-300">${p.price_open.toFixed(5)}</div>
                                <div class="text-zinc-300">${p.price_current.toFixed(5)}</div>
                                <div class="text-zinc-500">${p.sl.toFixed(5)} / ${p.tp.toFixed(5)}</div>
                                <div class="${pnlColor} font-bold">${p.profit >= 0 ? "+" : ""}${p.profit.toFixed(2)}</div>
                            `;
                            tableBody.appendChild(div);
                        });
                    } else {
                        tableBody.innerHTML = `<div class="text-zinc-600 text-center py-4 italic">No active trading positions found.</div>`;
                    }
                }
            }

            handleCandle(data) {
                logger(`Candle closed: timeframe=${data.timeframe} | C=${data.close.toFixed(5)}`);
                
                const candleTime = data.time ? Number(data.time) : Math.floor(Date.now() / 1000);
                const newCandle = {
                    time: candleTime,
                    open: Number(data.open),
                    high: Number(data.high),
                    low: Number(data.low),
                    close: Number(data.close)
                };

                let targetArray = null;
                if (data.timeframe === 'M15') {
                    targetArray = this.m15Candles;
                } else if (data.timeframe === 'H1') {
                    targetArray = this.h1Candles;
                } else if (data.timeframe === 'H4') {
                    targetArray = this.h4Candles;
                }

                if (targetArray) {
                    const existingIdx = targetArray.findIndex(c => c && Number(c.time) === candleTime);
                    if (existingIdx !== -1) {
                        targetArray[existingIdx] = newCandle;
                    } else {
                        targetArray.push(newCandle);
                        if (targetArray.length > 500) {
                            targetArray.shift();
                        }
                    }
                    this.sessionsCache[data.timeframe] = this.getSessions(targetArray);
                    
                    if (data.timeframe === this.currentTimeframe) {
                        this.renderActiveCandles();
                    }
                }
            }

            handleEvent(data) {
                const isBackfill = data.historical === true || data.reason === "historical data" || (data.id && typeof data.id === 'string' && data.id.startsWith('h-'));

                if (data.events_detected !== undefined) {
                    this.statsDetected.innerText = data.events_detected;
                } else if (data.id && !isBackfill) {
                    this.statsDetected.innerText = data.id;
                }
                
                const div = document.createElement("div");
                div.className = `flex justify-between items-start bg-[#07070b] border border-zinc-900 px-2 py-1.5 text-[8.5px] font-mono leading-normal w-full gap-1.5 ${isBackfill ? 'opacity-60' : ''}`;
                
                let priorityColor = "text-zinc-600 font-mono";
                if (!isBackfill) {
                    if (data.priority === "HIGH") priorityColor = "text-amber-500 font-semibold";
                    if (data.priority === "CRITICAL") priorityColor = "text-[#ff0055] font-bold animate-pulse";
                }

                let statusBadge = "";
                if (isBackfill) {
                    statusBadge = `<span class="text-zinc-500 bg-zinc-950 border border-zinc-800 px-1.5 py-0.5 text-[7.5px] font-bold">HISTORICAL</span>`;
                } else if (data.status === "detected") {
                    statusBadge = `<span class="text-[#00f0ff] bg-cyan-950/20 border border-[#00f0ff]/30 px-1.5 py-0.5 text-[7.5px] font-bold">DETECTED</span>`;
                } else if (data.status === "skipped") {
                    statusBadge = `<span class="text-amber-500 bg-amber-950/20 border border-amber-500/30 px-1.5 py-0.5 text-[7.5px] font-bold">GATED</span>`;
                    if (data.events_skipped !== undefined) {
                        this.statsSkipped.innerText = data.events_skipped;
                    } else {
                        const skippedCount = parseInt(this.statsSkipped.innerText) || 0;
                        this.statsSkipped.innerText = skippedCount + 1;
                    }
                } else if (data.status === "firing") {
                    statusBadge = `<span class="text-[#ff0055] bg-rose-950/30 border border-[#ff0055]/30 px-1.5 py-0.5 text-[7.5px] font-bold animate-pulse">GRAPH FIRE</span>`;
                    if (data.events_fired !== undefined) {
                        this.statsFired.innerText = data.events_fired;
                    } else {
                        const firedCount = parseInt(this.statsFired.innerText) || 0;
                        this.statsFired.innerText = firedCount + 1;
                    }
                    
                    // Activate live execution HUD classes (only for real-time live execution!)
                    this.decisionPanel.className = "col-span-7 cyber-panel py-1.5 px-2.5 flex flex-col justify-between border-l-[3px] border-l-[#ff0055] relative overflow-hidden border-glow-rose";
                    this.decisionStatus.innerText = "COGNITIVE GRAPH ENGINE RUNNING SCANS LIVE...";
                    this.decisionStatus.className = "text-[#ff0055] font-bold text-[9px] tracking-widest block mb-1.5 animate-pulse";
                }

                let eventName = data.event_type.replace('_', ' ').toUpperCase();
                if (data.event_type === "candle_pattern" && data.details && data.details.pattern) {
                    const pat = data.details.pattern.replace('_', ' ').toUpperCase();
                    const tf = data.details.timeframe || "M15";
                    eventName = `${pat} (${tf})`;
                } else if (data.details && data.details.timeframe) {
                    eventName = `${eventName} (${data.details.timeframe})`;
                }

                let detailsStr = "";
                if (data.details) {
                    if (data.event_type === "candle_pattern" && data.details.pattern) {
                        const lvl = data.details.at_level ? data.details.at_level.toFixed(data.details.at_level > 50 ? 2 : 5) : "";
                        detailsStr = `
                            <div class="text-[7.5px] text-zinc-500 mt-1 font-mono leading-normal flex flex-col gap-0.5">
                                <span>└─ CONFIRMED AT: <span class="text-zinc-300 font-bold">${lvl}</span></span>
                                <span class="pl-3">INTERACTIONS: <span class="${isBackfill ? 'text-zinc-500' : 'text-[#00f0ff]'}">${data.details.level_interactions}</span> | MOMENTUM: <span class="${isBackfill ? 'text-zinc-500' : 'text-[#00f0ff]'}">${data.details.momentum_intensity}x</span></span>
                            </div>
                        `;
                    } else if (data.event_type === "level_breach" && data.details.level) {
                        const dir = data.details.direction ? data.details.direction.replace('_', ' ').toUpperCase() : "";
                        const lvl = data.details.level.toFixed(data.details.level > 50 ? 2 : 5);
                        detailsStr = `<div class="text-[7.5px] text-zinc-500 mt-1 font-mono leading-normal">└─ ${dir} BREACH ON LEVEL ${lvl}</div>`;
                    } else if (data.event_type === "sweep_detected" && data.details.swept_level) {
                        const dir = data.details.direction ? data.details.direction.replace('_', ' ').toUpperCase() : "";
                        const lvl = data.details.swept_level.toFixed(data.details.swept_level > 50 ? 2 : 5);
                        detailsStr = `<div class="text-[7.5px] text-zinc-500 mt-1 font-mono leading-normal">└─ ${dir} SWEEP ON LEVEL ${lvl}</div>`;
                    } else if (data.event_type === "volatility_spike" && data.details.range_pips) {
                        detailsStr = `<div class="text-[7.5px] text-zinc-500 mt-1 font-mono leading-normal">└─ RANGE: ${data.details.range_pips.toFixed(1)} PIPS (AVG: ${data.details.avg_range_pips.toFixed(1)} | RATIO: ${data.details.ratio.toFixed(1)}x)</div>`;
                    }
                }

                if (data.status === "skipped" && data.reason) {
                    detailsStr += `<div class="text-[7.5px] ${isBackfill ? 'text-zinc-500' : 'text-amber-500 font-bold'} mt-1 font-mono leading-normal">└─ GATED REASON: ${data.reason.toUpperCase()}</div>`;
                }

                const displayTime = data.timestamp ? (data.timestamp.includes(" ") ? data.timestamp.split(" ")[1] : data.timestamp) : new Date().toLocaleTimeString();
                div.innerHTML = `
                    <div class="flex flex-col flex-grow text-left">
                        <div class="flex items-center gap-2 flex-wrap">
                            <span class="text-zinc-600">${displayTime}</span>
                            ${isBackfill ? '<span class="text-[7.5px] px-1 py-0.2 bg-zinc-950 text-zinc-500 border border-zinc-800 font-bold uppercase tracking-wider">[PRE-SESSION]</span>' : ''}
                            <span class="${priorityColor}">[${data.priority}]</span>
                            <span class="${isBackfill ? 'text-zinc-500 font-normal' : 'text-white font-medium'}">${eventName}</span>
                        </div>
                        ${detailsStr}
                    </div>
                    <div class="flex items-center gap-1.5 flex-shrink-0">
                        ${statusBadge}
                    </div>
                `;

                if (this.eventsLog.children.length === 1 && this.eventsLog.firstElementChild && this.eventsLog.firstElementChild.classList.contains("italic")) {
                    this.eventsLog.innerHTML = "";
                }

                this.eventsLog.insertBefore(div, this.eventsLog.firstChild);
                if (this.eventsLog.children.length > 20) {
                    this.eventsLog.removeChild(this.eventsLog.lastChild);
                }

                // Only print on chart when high-priority tradable structures are detected (Sweep or Structure Break)
                const isSignificant = data.event_type === "sweep_detected" || 
                                      data.event_type === "structure_break";
                if (isSignificant && data.details && data.details.trigger_candle) {
                    const tc = data.details.trigger_candle;
                    let timeSec = typeof tc.open_time === 'number' ? tc.open_time : Math.floor(Date.parse(tc.open_time) / 1000);
                    
                    if (timeSec) {
                        let shortText = "";
                        let shape = "circle";
                        let color = "#00ff66";
                        let position = "belowBar";
                        let isBull = false;

                        if (data.event_type === "sweep_detected") {
                            isBull = data.details.direction ? data.details.direction.includes("bull") : false;
                            position = isBull ? "belowBar" : "aboveBar";
                            color = isBull ? "#00f0ff" : "#ff00d0"; // Neon Cyan for Bullish Sweep, Neon Magenta for Bearish Sweep
                            shape = isBull ? "arrowUp" : "arrowDown";
                            shortText = "";
                        } else if (data.event_type === "candle_pattern") {
                            const p = data.details && data.details.pattern ? data.details.pattern.toLowerCase() : "";
                            isBull = p.includes("bull");
                            position = isBull ? "belowBar" : "aboveBar";
                            color = isBull ? "#00ff66" : "#ff0055";
                            shape = "circle"; // Circle shape for patterns to differentiate
                            shortText = "";
                        } else if (data.event_type === "structure_break") {
                            isBull = data.event_type.includes("bull") || (data.details.direction && data.details.direction.includes("bull"));
                            position = isBull ? "belowBar" : "aboveBar";
                            color = isBull ? "#00ff66" : "#ff0055";
                            shape = isBull ? "arrowUp" : "arrowDown";
                            shortText = "";
                        } else if (data.event_type === "level_breach") {
                            isBull = data.event_type.includes("bull") || (data.details.direction && data.details.direction.includes("bull"));
                            position = isBull ? "belowBar" : "aboveBar";
                            color = isBull ? "#00ff66" : "#ff0055";
                            shape = "square"; // Square shape for level breach to differentiate
                            shortText = "";
                        } else if (data.event_type === "momentum_divergence") {
                            isBull = data.event_type.includes("bull") || (data.details.direction && data.details.direction.includes("bull"));
                            position = isBull ? "belowBar" : "aboveBar";
                            color = isBull ? "#9d00ff" : "#ffaa00";
                            shape = isBull ? "arrowUp" : "arrowDown";
                            shortText = "";
                        } else if (data.event_type === "volatility_spike") {
                            isBull = false; // Neutral
                            position = "aboveBar";
                            color = "#ffa500"; // Neon Orange
                            shape = "circle"; // Circle shape for volatility spike (non-directional!)
                            shortText = "";
                        }

                        const marker = {
                            time: timeSec,
                            position: position,
                            color: color,
                            shape: shape,
                            shortText: shortText,
                            event_type: data.event_type,
                            details: data.details,
                            priority: data.priority,
                            isBull: isBull
                        };
                        
                        this.chartMarkers.push(marker);
                        if (this.chartMarkers.length > 100) {
                            this.chartMarkers = this.chartMarkers.slice(-100);
                        }
                        this.updateMarkers(this.getMarkersForTimeframe(this.currentTimeframe));
                    }
                }

                if (!isBackfill && data.event_type === "sweep_detected" && data.status === "detected") {
                    this.triggerSweepAlarm(data.details.level_price || data.price);
                }
            }

            triggerSweepAlarm(price) {
                this.sweepRadar.className = "bg-rose-950/20 border border-[#ff0055]/40 px-2 py-0.5 flex items-center gap-1.5 animate-pulse";
                this.sweepIcon.className = "w-1.5 h-1.5 bg-[#ff0055] flex-shrink-0 animate-ping";
                this.sweepMsg.innerText = `SWEEP @ ${price.toFixed(5)}`;
                this.sweepMsg.className = "text-[6.5px] text-[#ff0055] font-bold tracking-wider";

                this.triggerBeep(1400, 0.15);
                setTimeout(() => this.triggerBeep(980, 0.12), 120);

                setTimeout(() => {
                    this.sweepRadar.className = "bg-black border border-zinc-800 px-2 py-0.5 flex items-center gap-1.5";
                    this.sweepIcon.className = "w-1.5 h-1.5 bg-zinc-700 flex-shrink-0 animate-pulse";
                    this.sweepMsg.innerText = "LIQ MONITOR";
                    this.sweepMsg.className = "text-[6.5px] text-zinc-500 font-bold uppercase";
                }, 8000);
            }

            handleAgent(data) {
                const isHistorical = data.historical === true;
                const consoleDiv = document.createElement("div");
                consoleDiv.className = `border-b border-zinc-900 pb-2.5 last:border-0 ${isHistorical ? 'opacity-50' : ''}`;

                                  let agentColor = "text-cyan-400 bg-cyan-950/20 border border-cyan-800/30";
                  if (isHistorical) {
                      agentColor = "text-zinc-500 bg-zinc-950 border border-zinc-900";
                  } else {
                      const name = data.agent_name.toUpperCase();
                      if (["WYCKOFF", "KEYNES", "REUTERS", "LIVERMORE"].includes(name)) {
                          agentColor = "text-[#00f0ff] bg-cyan-950/20 border border-[#00f0ff]/30";
                      } else if (["BUFFETT", "SOROS"].includes(name)) {
                          agentColor = "text-[#ffaa00] bg-amber-950/20 border border-[#ffaa00]/30";
                      } else if (name === "MUNGER") {
                          agentColor = "text-[#9d00ff] bg-purple-950/20 border border-[#9d00ff]/30 font-bold";
                      } else if (name === "TUDOR") {
                          agentColor = "text-[#00ff66] bg-emerald-950/20 border border-[#00ff66]/30 font-bold";
                      } else if (["SIMONS", "DALIO", "MARKS"].includes(name)) {
                          agentColor = "text-[#ff6600] bg-orange-950/20 border border-[#ff6600]/30";
                      } else if (name === "DRUCKENMILLER") {
                          agentColor = "text-[#ff0055] bg-rose-950/20 border border-[#ff0055]/30 font-bold";
                      }
                  }

            handleDecision(data) {
                const action = data.signal.toUpperCase();
                const isHistorical = data.historical === true;
                
                this.decisionAction.innerText = action;
                this.decisionTime.innerText = isHistorical ? `RESTORED PRIOR DECISION AT ${data.timestamp}` : `DECISION STAGED AT ${data.timestamp}`;
                
                if (action.includes("BUY")) {
                    this.decisionPanel.className = `col-span-7 cyber-panel py-1.5 px-2.5 flex flex-col justify-between border-l-[3px] border-l-[#00ff66] bg-[#00ff66]/5 relative overflow-hidden border-glow-emerald hazard-bg-buy ${isHistorical ? 'opacity-70' : ''}`;
                    this.decisionStatus.innerText = isHistorical ? "RESTORED PREVIOUS COGNITIVE GRAPH DECISION" : "COGNITIVE ARCHITECTURE GRAPH DECISION FINALIZED";
                    this.decisionStatus.className = `text-[#00ff66] font-extrabold text-[8px] tracking-widest block mb-0.5 ${isHistorical ? '' : 'animate-pulse'}`;
                    this.decisionAction.className = "text-xl font-extrabold tracking-[0.2em] text-[#00ff66] uppercase py-0.5 border-y border-[#00ff66]/20 px-8 w-full text-center";
                    if (!isHistorical) {
                        this.triggerBeep(650, 0.12);
                        setTimeout(() => this.triggerBeep(850, 0.1), 100);
                    }
                } else if (action.includes("SELL")) {
                    this.decisionPanel.className = `col-span-7 cyber-panel py-1.5 px-2.5 flex flex-col justify-between border-l-[3px] border-l-[#ff0055] bg-[#ff0055]/5 relative overflow-hidden border-glow-rose hazard-bg-sell ${isHistorical ? 'opacity-70' : ''}`;
                    this.decisionStatus.innerText = isHistorical ? "RESTORED PREVIOUS COGNITIVE GRAPH DECISION" : "COGNITIVE ARCHITECTURE GRAPH DECISION FINALIZED";
                    this.decisionStatus.className = `text-[#ff0055] font-extrabold text-[8px] tracking-widest block mb-0.5 ${isHistorical ? '' : 'animate-pulse'}`;
                    this.decisionAction.className = "text-xl font-extrabold tracking-[0.2em] text-[#ff0055] uppercase py-0.5 border-y border-[#ff0055]/20 px-8 w-full text-center";
                    if (!isHistorical) {
                        this.triggerBeep(450, 0.12);
                        setTimeout(() => this.triggerBeep(350, 0.1), 100);
                    }
                } else if (action.includes("ERROR")) {
                    this.decisionPanel.className = `col-span-7 cyber-panel py-1.5 px-2.5 flex flex-col justify-between border-l-[3px] border-l-[#ff0055] bg-[#ff0055]/10 relative overflow-hidden border-glow-rose ${isHistorical ? 'opacity-70' : ''}`;
                    this.decisionStatus.innerText = "COGNITIVE ARCHITECTURE GRAPH EXECUTION ERROR";
                    this.decisionStatus.className = "text-[#ff0055] font-extrabold text-[8px] tracking-widest block mb-0.5 animate-pulse";
                    this.decisionAction.className = "text-xl font-extrabold tracking-[0.1em] text-[#ff0055] uppercase py-0.5 border-y border-[#ff0055]/20 px-8 w-full text-center";
                    if (!isHistorical) {
                        this.triggerBeep(250, 0.2);
                    }
                } else {
                    this.decisionPanel.className = "col-span-7 cyber-panel py-1.5 px-2.5 flex flex-col justify-between border-l-[3px] border-l-[#9d00ff] bg-black/40 relative overflow-hidden";
                    this.decisionStatus.innerText = "COGNITIVE ENGINE ACTIVE MONITORING";
                    this.decisionStatus.className = "text-[#9d00ff] text-[8px] font-bold uppercase tracking-widest block mb-0.5";
                    this.decisionAction.className = "text-xl font-black tracking-[0.2em] text-[#00f0ff] uppercase py-0.5 border-y border-zinc-900 px-8 w-full text-center";
                    if (!isHistorical) {
                        this.triggerBeep(1000, 0.02);
                    }
                }

                this.prevDecision.innerText = action;
                this.prevDecision.className = action.includes("BUY") ? "text-[#00ff66] font-bold" : (action.includes("SELL") ? "text-[#ff0055] font-bold" : (action.includes("ERROR") ? "text-[#ff0055] font-bold" : "text-[#9d00ff] font-bold"));
            }

            startClock() {
                setInterval(() => {
                    const elapsed = Math.floor((Date.now() - this.startTime) / 1000);
                    const hours = String(Math.floor(elapsed / 3600)).padStart(2, '0');
                    const minutes = String(Math.floor((elapsed % 3600) / 60)).padStart(2, '0');
                    const seconds = String(elapsed % 60).padStart(2, '0');
                    this.uptime.innerText = `${hours}:${minutes}:${seconds}`;

                    this.updateCooldownCountdown();
                    this.updateMarketResumeCountdown();
                }, 1000);
            }

            updateCooldownCountdown() {
                const val = this.cooldownVal.innerText;
                if (val !== "READY" && !isNaN(parseInt(val))) {
                    let rem = parseInt(val) - 1;
                    if (rem <= 0) {
                        this.cooldownVal.innerText = "READY";
                        this.cooldownVal.className = "text-[#00ff66] font-bold";
                        this.cooldownBar.style.width = "0%";
                    } else {
                        this.cooldownVal.innerText = `${rem}s`;
                        this.cooldownVal.className = "text-amber-500 font-bold";
                        this.cooldownBar.style.width = `${(rem / 300 * 100).toFixed(0)}%`;
                    }
                }
            }

            updateMarketResumeCountdown() {
                if (this.marketClosed && this.marketResumeTimestamp) {
                    const now = Math.floor(Date.now() / 1000);
                    const diff = this.marketResumeTimestamp - now;
                    const timerEl = document.getElementById('countdown-timer');
                    if (!timerEl) return;

                    if (diff > 0) {
                        const days = Math.floor(diff / 86400);
                        const hours = Math.floor((diff % 86400) / 3600);
                        const mins = Math.floor((diff % 3600) / 60);
                        const secs = diff % 60;
                        
                        const daysStr = days > 0 ? `${days}d ` : '';
                        const hoursStr = String(hours).padStart(2, '0') + 'h ';
                        const minsStr = String(mins).padStart(2, '0') + 'm ';
                        const secsStr = String(secs).padStart(2, '0') + 's';
                        
                        timerEl.innerText = `${daysStr}${hoursStr}${minsStr}${secsStr}`;
                        timerEl.style.color = '#ff0055';
                        timerEl.style.textShadow = '0 0 8px rgba(255, 0, 85, 0.4)';
                    } else {
                        timerEl.innerText = 'RESUMING NOW...';
                        timerEl.style.color = '#00ff66';
                        timerEl.style.textShadow = '0 0 8px rgba(0, 255, 102, 0.4)';
                    }
                }
            }

            switchTab(tabName) {
                this.activeTab = tabName;
                const tabs = ["cockpit", "intel"];
                
                tabs.forEach(t => {
                    const btn = document.getElementById(`main-tab-${t}-btn`);
                    const panel = document.getElementById(`main-tab-panel-${t}`);
                    
                    if (btn) {
                        const isCurrent = t === tabName;
                        const dot = btn.querySelector("span");
                        
                        if (isCurrent) {
                            btn.className = "border border-[#00f0ff] bg-[#00f0ff]/10 text-[#00f0ff] px-5 py-2 uppercase transition-all rounded-sm flex items-center gap-2 font-extrabold shadow-md shadow-[#00f0ff]/5";
                            if (dot) {
                                dot.className = "w-1.5 h-1.5 bg-[#00f0ff] rounded-full animate-pulse";
                            }
                        } else {
                            btn.className = "border border-[#1f1f2e] text-zinc-500 px-5 py-2 uppercase transition-all rounded-sm flex items-center gap-2 font-extrabold";
                            if (dot) {
                                dot.className = "w-1.5 h-1.5 bg-zinc-700 rounded-full";
                            }
                        }
                    }
                    
                    if (panel) {
                        if (t === tabName) {
                            panel.classList.remove("hidden");
                        } else {
                            panel.classList.add("hidden");
                        }
                    }
                });

                this.triggerBeep(850, 0.05);
            }

            clearActiveTab() {
                const tab = this.activeTab || "reason";
                if (tab === "reason") {
                    if (this.consoleBody) {
                        this.consoleBody.innerHTML = `<div style='color:var(--cyan);opacity:0.6;font-size:10px;'>[STREAM_CLEARED] Active.</div>`;
                    }
                } else if (tab === "broker") {
                    this.mockPositions = [];
                    if (this.latestAccountData) {
                        this.handleAccount(this.latestAccountData);
                    } else {
                        const tableBody = document.getElementById("positions-table-body");
                        if (tableBody) {
                            tableBody.innerHTML = `<div class="text-zinc-600 text-center py-4 italic">No active trading positions found.</div>`;
                        }
                    }
                } else if (tab === "news") {
                    const container = document.getElementById("news-log-container");
                    if (container) {
                        container.innerHTML = `<div class="text-zinc-600 text-center py-8 italic font-sans uppercase tracking-widest text-[8px]">Awaiting active news ingestion stream...</div>`;
                    }
                } else if (tab === "events") {
                    if (this.eventsLog) {
                        this.eventsLog.innerHTML = `<div class="text-zinc-600 text-center py-8 italic">Awaiting structural triggers...</div>`;
                    }
                    const statsDet = document.getElementById("stats-detected");
                    const statsFir = document.getElementById("stats-fired");
                    const statsSkip = document.getElementById("stats-skipped");
                    if (statsDet) statsDet.innerText = "0";
                    if (statsFir) statsFir.innerText = "0";
                    if (statsSkip) statsSkip.innerText = "0";
                }
                
                this.triggerBeep(600, 0.08);
            }

            mockOrder(action) {
                if (action === 'CLOSE') {
                    if (this.mockPositions.length === 0) {
                        const div = document.createElement("div");
                        div.className = "text-zinc-500 italic pb-1";
                        div.innerText = `[EXECUTION] No mock positions active to close.`;
                        if (this.consoleBody) this.consoleBody.appendChild(div);
                        return;
                    }
                    this.mockPositions.forEach(p => {
                        const pnlColor = p.profit > 0 ? "text-[#00ff66]" : (p.profit < 0 ? "text-[#ff0055]" : "text-white");
                        const div = document.createElement("div");
                        div.className = "border-b border-[#1a1a24] pb-1.5 flex flex-col gap-0.5";
                        div.innerHTML = `
                            <div class="flex items-center gap-1.5">
                                <span class="text-zinc-500 font-bold">[${new Date().toLocaleTimeString()}]</span>
                                <span class="text-[#ff0055] font-extrabold">[EXECUTION_CLOSED]</span>
                                <span class="text-white">Mock position ticket <span class="text-zinc-400 font-bold">#${p.ticket}</span> closed at rate ${p.price_current.toFixed(5)}.</span>
                                <span class="${pnlColor} font-black font-mono">PnL: $${p.profit >= 0 ? "+" : ""}${p.profit.toFixed(2)}</span>
                            </div>
                        `;
                        if (this.consoleBody) {
                            this.consoleBody.appendChild(div);
                            this.consoleBody.scrollTop = this.consoleBody.scrollHeight;
                        }
                    });
                    this.mockPositions = [];
                    this.triggerBeep(600, 0.15);
                    if (this.latestAccountData) {
                        this.handleAccount(this.latestAccountData);
                    }
                    return;
                }

                const tickerEl = document.getElementById("header-ticker");
                const symbol = tickerEl ? tickerEl.innerText.replace(/\s*\/\s*/g, "") : "EURUSD";
                const ticket = Math.floor(Math.random() * 900000) + 100000;
                const lotSize = 0.10;
                
                let openPrice = 0.0;
                let currentPrice = 0.0;
                if (action === 'BUY') {
                    openPrice = this.currentAsk || 1.08520;
                    currentPrice = this.currentBid || 1.08500;
                } else {
                    openPrice = this.currentBid || 1.08500;
                    currentPrice = this.currentAsk || 1.08520;
                }

                const pos = {
                    ticket: ticket,
                    symbol: symbol,
                    type: action,
                    volume: lotSize,
                    price_open: openPrice,
                    price_current: currentPrice,
                    sl: 0.0,
                    tp: 0.0,
                    profit: 0.0
                };
                
                this.mockPositions.push(pos);
                this.triggerBeep(1200, 0.1);

                const div = document.createElement("div");
                div.className = "border-b border-[#1a1a24] pb-1.5 flex flex-col gap-0.5";
                div.innerHTML = `
                    <div class="flex items-center gap-1.5">
                        <span class="text-zinc-500 font-bold">[${new Date().toLocaleTimeString()}]</span>
                        <span class="text-[#00ff66] font-extrabold">[EXECUTION_FILLED]</span>
                        <span class="text-white">Mock <span class="font-bold text-[#00f0ff]">${action}</span> order filled for <span class="text-[#00ff66]">${symbol}</span>.</span>
                        <span class="text-zinc-400 font-mono">Ticket: #${ticket} | Price: ${openPrice.toFixed(5)} | Volume: ${lotSize.toFixed(2)} Lots</span>
                    </div>
                `;
                if (this.consoleBody) {
                    this.consoleBody.appendChild(div);
                    this.consoleBody.scrollTop = this.consoleBody.scrollHeight;
                }

                if (this.latestAccountData) {
                    this.handleAccount(this.latestAccountData);
                }
            }

            handleNewsData(data) {
                const container = document.getElementById("news-log-container");
                if (!container) return;

                container.innerHTML = "";
                this.triggerBeep(1000, 0.05);

                const createFeedSection = (title, contentText, borderColor, textColor, badgeBg) => {
                    if (!contentText || contentText.trim().length === 0) return null;

                    const sec = document.createElement("div");
                    sec.className = "flex flex-col gap-2 mb-4";
                    
                    const header = document.createElement("div");
                    header.className = `flex justify-between items-center border-b border-zinc-800 pb-1.5`;
                    header.innerHTML = `
                        <span class="text-[9px] ${textColor} font-black tracking-widest uppercase font-mono flex items-center gap-1.5">
                            <span class="w-1.5 h-1.5 ${badgeBg} rounded-full animate-ping"></span> ${title}
                        </span>
                        <span class="text-[7.5px] text-zinc-500 font-bold font-mono">UPDATED: ${data.timestamp || new Date().toLocaleTimeString()}</span>
                    `;
                    sec.appendChild(header);

                    const list = document.createElement("div");
                    list.className = "flex flex-col gap-2";

                    const lines = contentText.split("\n").map(l => l.trim()).filter(l => l.length > 0);
                    if (lines.length === 0) {
                        list.innerHTML = `<div class="text-zinc-600 italic py-2">No news items found.</div>`;
                    } else {
                        lines.forEach(line => {
                            const cleanLine = line.replace(/^[\s\-\*\•\d\.\)]+/, '').trim();
                            if (!cleanLine) return;

                            const item = document.createElement("div");
                            item.className = `cyber-panel bg-[#07070b]/90 border ${borderColor} p-2 text-[8.5px] font-mono leading-normal transition-all hover:bg-white/5 relative group`;
                            item.innerHTML = `
                                <div class="absolute left-0 top-0 bottom-0 w-[2px] ${badgeBg}"></div>
                                <div class="pl-2 flex flex-col gap-1">
                                    <div class="text-zinc-100 font-semibold leading-relaxed">${cleanLine}</div>
                                </div>
                            `;
                            list.appendChild(item);
                        });
                    }
                    sec.appendChild(list);
                    return sec;
                };

                const forexSec = createFeedSection("ForexLive RSS Sentiment Feed", data.forex_social, "border-pink-950/40 hover:border-pink-500/30", "text-pink-500", "bg-pink-500");
                const newsSec = createFeedSection("Global Market Intelligence News", data.news, "border-[#00f0ff]/30 hover:border-[#00f0ff]/60", "text-[#00f0ff]", "bg-[#00f0ff]");
                const redditSec = createFeedSection("Reddit Retail Momentum Stream", data.reddit, "border-emerald-950/40 hover:border-[#00ff66]/30", "text-[#00ff66]", "bg-[#00ff66]");

                let added = false;
                if (forexSec) { container.appendChild(forexSec); added = true; }
                if (newsSec) { container.appendChild(newsSec); added = true; }
                if (redditSec) { container.appendChild(redditSec); added = true; }

                if (!added) {
                    container.innerHTML = `<div class="text-zinc-600 text-center py-8 italic font-sans uppercase tracking-widest text-[8px]">Awaiting active news ingestion stream...</div>`;
                }
            }

            triggerBeep(frequency, duration) {
                try {
                    const audioCtx = new (window.AudioContext || window.webkitAudioContext)();
                    const osc = audioCtx.createOscillator();
                    const gain = audioCtx.createGain();

                    osc.type = "sine";
                    osc.frequency.setValueAtTime(frequency, audioCtx.currentTime);
                    
                    gain.gain.setValueAtTime(0.003, audioCtx.currentTime);
                    gain.gain.exponentialRampToValueAtTime(0.00001, audioCtx.currentTime + duration);

                    osc.connect(gain);
                    gain.connect(audioCtx.destination);

                    osc.start();
                    osc.stop(audioCtx.currentTime + duration);
                } catch(e) {}
            }
        }

        function logger(msg) {
            console.log(msg);
        }

        window.onload = () => {
            new DashboardController();
        };
    