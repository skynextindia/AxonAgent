import sys
import os
import re

html_path = r"d:\work\TradingAgents\cli\static\index.html"

with open(html_path, "r", encoding="utf-8") as f:
    content = f.read()

# 1. Throttling WebSocket UI updates
# Find: this.ws.onmessage = (event) => {
# Add a queue and RAF wrapper.
throttle_code = """
                this.ws.onmessage = (event) => {
                    const data = JSON.parse(event.data);
                    if (!this._msgQueue) this._msgQueue = [];
                    this._msgQueue.push(data);
                    
                    if (!this._rafPending) {
                        this._rafPending = true;
                        requestAnimationFrame(() => {
                            this._rafPending = false;
                            const msgs = this._msgQueue;
                            this._msgQueue = [];
                            for(let msg of msgs) {
                                this._processMessage(msg);
                            }
                        });
                    }
                };
                
                _processMessage(data) {
"""

# Replace the original onmessage with the wrapper
if "this.ws.onmessage = (event) => {" in content and "_processMessage(data)" not in content:
    content = content.replace("this.ws.onmessage = (event) => {", throttle_code, 1)
    # Also find the end of the onmessage block and close the new method
    # Actually, it's easier to just replace "const data = JSON.parse(event.data);" with the throttle logic if we can.
    # Wait, simple replacement:
    content = content.replace(
        "const data = JSON.parse(event.data);",
        "// data is already parsed in throttle wrapper"
    )

# 2. Add Tactical Overrides and Gates above the Chart Panel
hud_injection = """
                <!-- TACTICAL OVERRIDES & SECURITY GATES -->
                <div class="grid grid-cols-12 gap-3 h-[60px] flex-shrink-0">
                    <div class="col-span-6 cyber-panel p-2 flex flex-col border-t border-t-[#ff0055] bg-[#040406] justify-center">
                        <span class="text-[8.5px] text-zinc-400 uppercase tracking-widest font-bold mb-1">TACTICAL_OVERRIDES</span>
                        <div class="flex gap-2">
                            <button onclick="fetch('/api/emergency_stop', {method: 'POST'})" class="flex-1 bg-rose-950/40 hover:bg-rose-900 border border-[#ff0055]/50 text-[#ff0055] text-[9px] font-bold py-1 uppercase transition-all hazard-bg-sell">HALT DAEMON</button>
                            <button onclick="fetch('/api/close_all', {method: 'POST'})" class="flex-1 bg-amber-950/40 hover:bg-amber-900 border border-amber-500/50 text-amber-500 text-[9px] font-bold py-1 uppercase transition-all">CLOSE ALL TRADES</button>
                            <button onclick="fetch('/api/pause_llm', {method: 'POST'})" class="flex-1 bg-[#00f0ff]/10 hover:bg-[#00f0ff]/20 border border-[#00f0ff]/50 text-[#00f0ff] text-[9px] font-bold py-1 uppercase transition-all">TOGGLE LLM PAUSE</button>
                        </div>
                    </div>
                    <div class="col-span-6 cyber-panel p-2 flex flex-col border-t border-t-[#00f0ff] bg-[#040406] justify-center">
                        <span class="text-[8.5px] text-zinc-400 uppercase tracking-widest font-bold mb-1">SECURITY_GATES_MATRIX</span>
                        <div class="flex justify-between text-[8px] font-mono text-zinc-500 font-bold tracking-wider">
                            <div class="flex items-center gap-1"><span id="gate-state" class="w-2 h-2 bg-zinc-800 rounded-full"></span> STATE</div>
                            <div class="flex items-center gap-1"><span id="gate-spread" class="w-2 h-2 bg-zinc-800 rounded-full"></span> SPREAD</div>
                            <div class="flex items-center gap-1"><span id="gate-convict" class="w-2 h-2 bg-zinc-800 rounded-full"></span> CONVICTION</div>
                            <div class="flex items-center gap-1"><span id="gate-rate" class="w-2 h-2 bg-zinc-800 rounded-full"></span> RATE_LIMIT</div>
                            <div class="flex items-center gap-1"><span id="gate-context" class="w-2 h-2 bg-zinc-800 rounded-full"></span> CONTEXT</div>
                            <div class="flex items-center gap-1"><span id="gate-paused" class="w-2 h-2 bg-zinc-800 rounded-full"></span> LLM_PAUSED</div>
                        </div>
                    </div>
                </div>
"""

# Insert above PRICE CHART PANEL
chart_panel_marker = "<!-- PRICE CHART PANEL -->"
if "TACTICAL_OVERRIDES" not in content:
    content = content.replace(chart_panel_marker, hud_injection + "\n                " + chart_panel_marker)

# 3. Modify Top Stats Row for Macro Consensus
# Original: col-span-3 for MARKET REGIME. We make it col-span-2, and insert MACRO CONSENSUS as col-span-2.
# Original: col-span-5 for SESSION_TIMELINE. We make it col-span-4.
content = content.replace(
    'class="cyber-panel p-3 flex flex-col justify-between border-t border-t-[#00f0ff] col-span-3 bg-[#040406]">',
    'class="cyber-panel p-3 flex flex-col justify-between border-t border-t-[#00f0ff] col-span-2 bg-[#040406]">'
)

macro_panel = """
                    <!-- MACRO CONSENSUS -->
                    <div class="cyber-panel p-3 flex flex-col justify-between border-t border-t-[#00ff66] col-span-2 bg-[#040406]">
                        <span class="text-[8.5px] text-zinc-400 uppercase tracking-widest font-bold">SYSTEM_2_MACRO</span>
                        <div class="flex justify-between items-end mt-1">
                            <div class="flex flex-col">
                                <span id="macro-bias" class="text-sm font-black text-zinc-500 uppercase tracking-wider leading-none">WAIT</span>
                                <span id="macro-lvl" class="text-[6.5px] text-zinc-500 mt-1 uppercase font-bold">LVL: 0.00000</span>
                            </div>
                            <div class="flex flex-col items-end">
                                <span id="macro-conf" class="text-xs font-bold text-white">--%</span>
                            </div>
                        </div>
                    </div>
"""

# Insert macro_panel after the regime panel ends
regime_panel_end = """                            <div class="flex flex-col items-end">
                                <span id="regime-conf" class="text-xs font-bold text-white">--%</span>
                            </div>
                        </div>
                    </div>"""

if "SYSTEM_2_MACRO" not in content:
    content = content.replace(regime_panel_end, regime_panel_end + "\n" + macro_panel)

# Fix Session col-span from 5 to 4
content = content.replace(
    'class="cyber-panel p-3 flex flex-col justify-between col-span-5 border-t border-t-[#9d00ff] bg-[#040406]">',
    'class="cyber-panel p-3 flex flex-col justify-between col-span-4 border-t border-t-[#9d00ff] bg-[#040406]">'
)

# 4. Add JS updates for gates and macro
js_updates = """
                if (data.macro_bias) {
                    const mbias = document.getElementById("macro-bias");
                    if (mbias) {
                        mbias.innerText = data.macro_bias;
                        if (data.macro_bias === "BUY") mbias.className = "text-sm font-black text-[#00ff66] uppercase tracking-wider leading-none";
                        else if (data.macro_bias === "SELL") mbias.className = "text-sm font-black text-[#ff0055] uppercase tracking-wider leading-none";
                        else mbias.className = "text-sm font-black text-zinc-500 uppercase tracking-wider leading-none";
                        
                        document.getElementById("macro-lvl").innerText = `LVL: ${data.macro_key_level.toFixed(5)}`;
                        document.getElementById("macro-conf").innerText = `${(data.macro_confidence * 100).toFixed(0)}%`;
                    }
                }
                
                if (data.gate_status) {
                    const updateGate = (id, passed) => {
                        const el = document.getElementById(id);
                        if (el) el.className = `w-2 h-2 rounded-full ${passed ? 'bg-[#00ff66]' : 'bg-[#ff0055]'}`;
                    };
                    updateGate("gate-state", data.gate_status.state_passed);
                    updateGate("gate-spread", data.gate_status.spread_passed);
                    updateGate("gate-convict", data.gate_status.conviction_passed);
                    updateGate("gate-rate", data.gate_status.rate_limit_passed);
                    updateGate("gate-context", data.gate_status.context_passed);
                    updateGate("gate-paused", data.gate_status.llm_paused);
                }
"""

if "if (data.macro_bias)" not in content:
    content = content.replace(
        "this.regimeType.innerText = data.dominant;",
        js_updates + "\n                this.regimeType.innerText = data.dominant;"
    )

with open(html_path, "w", encoding="utf-8") as f:
    f.write(content)
print("UI Successfully Patched!")
