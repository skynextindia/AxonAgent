#!/usr/bin/env python3
"""
Restore the JS script block from git HEAD and re-apply only our valid changes:
1. The pair navigator click handler
2. The WS connect/disconnect style fixes (inline style instead of className)
3. Fix the ONE real bug: the innerHTML with style="..." double quotes in clear console handler
"""
import re, subprocess

# Get git HEAD version's script block
result = subprocess.run(['git', 'show', 'HEAD:cli/static/index.html'], 
                       capture_output=True, text=True, encoding='utf-8')
git_content = result.stdout

# Extract script block from git HEAD
scripts = list(re.finditer(r'<script>', git_content))
git_script_m = None
for m in scripts:
    end = git_content.find('</script>', m.end())
    body = git_content[m.end():end]
    if len(body) > 1000:
        git_script_m = m
        git_script_end = end
        git_script_body = body
        break

print(f"Git HEAD script: {len(git_script_body)} chars")

# Validate it's clean
with open('_git_script.js', 'w', encoding='utf-8') as f:
    f.write(git_script_body)
r = subprocess.run(['node', '--check', '_git_script.js'], capture_output=True, text=True)
print(f"Git HEAD script Node check: {'PASS' if r.returncode == 0 else 'FAIL'}")
if r.returncode != 0:
    print(r.stderr[:300])

# Now apply our valid changes to git HEAD script:

# 1. Fix the clear console innerHTML - use single-quotes for style
git_script_body = git_script_body.replace(
    "this.consoleBody.innerHTML = '<div class=\"text-[#00f0ff]/60 border-b border-[#1a1a24] pb-1.5 uppercase font-bold flex items-center gap-2\"><span>[STREAM_CLEARED] System dynamic logs stream active.</span><span class=\"cursor\"></span></div>';",
    "this.consoleBody.innerHTML = `<div style='color:var(--cyan);opacity:0.6;font-size:10px;'>[STREAM_CLEARED] Active.</div>`;"
)

# 2. Add pair navigator handler after the clear console handler
pair_nav_js = """
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

"""

# Insert after the clear console handler block
clear_console_marker = """                }

                // Timeframe toggle buttons"""
git_script_body = git_script_body.replace(
    clear_console_marker,
    "                }\n" + pair_nav_js + "\n                // Timeframe toggle buttons"
)

# 3. Fix WS onopen handler - use inline styles instead of className strings
old_onopen = '''this.ws.onopen = () => {
                    this.socketLight.className = "w-1.5 h-1.5 bg-[#00ff66] shadow-sm shadow-[#00ff66]";
                    this.socketStatus.innerText = "ONLINE";
                    this.socketStatus.className = "text-[#00ff66] font-extrabold text-[8px] uppercase tracking-widest";
                    logger("Dashboard WS: connected.");
                    this.triggerBeep(1100, 0.08); // high frequency startup chirp
                    
                    // Periodic active connection heartbeats
                    if (this.pingInterval) clearInterval(this.pingInterval);
                    this.pingInterval = setInterval(() => {
                        if (this.ws && this.ws.readyState === WebSocket.OPEN) {
                            this.ws.send(JSON.stringify({
                                type: "ping",
                                timestamp: Date.now()
                            }));
                        }
                    }, 2000);
                };'''

new_onopen = '''this.ws.onopen = () => {
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
                };'''

if old_onopen in git_script_body:
    git_script_body = git_script_body.replace(old_onopen, new_onopen)
    print("Applied WS onopen fix")
else:
    print("WARNING: WS onopen pattern not found - skipping")

old_onclose = '''this.ws.onclose = () => {
                    this.socketLight.className = "w-1.5 h-1.5 bg-rose-600 shadow-sm shadow-rose-600";
                    this.socketStatus.innerText = "OFFLINE";
                    this.socketStatus.className = "text-[#ff0055] font-extrabold text-[8px] uppercase tracking-widest";
                    this.latency.innerText = "--ms";
                    
                    if (this.pingInterval) {
                        clearInterval(this.pingInterval);
                        this.pingInterval = null;
                    }
                    
                    logger("Dashboard WS: disconnected. Retrying in " + this.reconnectInterval + "ms");
                    setTimeout(() => this.connect(), this.reconnectInterval);
                };'''

new_onclose = '''this.ws.onclose = () => {
                    this.socketLight.style.background = 'var(--red)';
                    this.socketStatus.innerText = 'OFFLINE';
                    this.socketStatus.style.color = 'var(--red)';
                    if (this.latency) this.latency.innerText = '--ms';
                    if (this.pingInterval) { clearInterval(this.pingInterval); this.pingInterval = null; }
                    logger('Dashboard WS: disconnected. Retrying in ' + this.reconnectInterval + 'ms');
                    setTimeout(() => this.connect(), this.reconnectInterval);
                };'''

if old_onclose in git_script_body:
    git_script_body = git_script_body.replace(old_onclose, new_onclose)
    print("Applied WS onclose fix")
else:
    print("WARNING: WS onclose pattern not found - skipping")

# Validate the patched git script
with open('_git_script_patched.js', 'w', encoding='utf-8') as f:
    f.write(git_script_body)
r = subprocess.run(['node', '--check', '_git_script_patched.js'], capture_output=True, text=True)
print(f"\nPatched script Node check: {'PASS' if r.returncode == 0 else 'FAIL'}")
if r.returncode != 0:
    print(r.stderr[:500])
    exit(1)

# Now apply to the current file - replace its corrupted script block with our clean one
path = 'cli/static/index.html'
current = open(path, encoding='utf-8').read()
curr_scripts = list(re.finditer(r'<script>', current))
curr_script_m = None
for m in curr_scripts:
    end = current.find('</script>', m.end())
    body = current[m.end():end]
    if len(body) > 1000:
        curr_script_m = m
        curr_script_end = end
        break

new_content = current[:curr_script_m.end()] + git_script_body + current[curr_script_end:]
open(path, 'w', encoding='utf-8').write(new_content)
print(f"\nRestored and patched script in {path}")
print(f"New file size: {len(new_content)} chars")
