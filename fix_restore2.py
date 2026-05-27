#!/usr/bin/env python3
"""
Full restoration: take git HEAD's entire HTML, apply only the CSS+header changes
from our new design (which are in the current file's HTML section), 
but restore the git HEAD's clean script block.
"""
import re, subprocess

path = 'cli/static/index.html'

# Get git HEAD version
result = subprocess.run(['git', 'show', 'HEAD:cli/static/index.html'], 
                       capture_output=True, text=True, encoding='utf-8')
git_content = result.stdout

# Get current file
current = open(path, encoding='utf-8').read()

# Find script tag positions in both
def find_big_script(content):
    scripts = list(re.finditer(r'<script>', content))
    for m in scripts:
        end = content.find('</script>', m.end())
        body = content[m.end():end]
        if len(body) > 1000:
            return m.end(), end, body
    return None, None, None

# Current file: extract HTML portion (before script) and after script
curr_script_start, curr_script_end, curr_script = find_big_script(current)
git_script_start, git_script_end, git_script = find_big_script(git_content)

print(f"Current script: {len(curr_script)} chars")
print(f"Git HEAD script: {len(git_script)} chars")

# Apply our valid patches to git script:
# 1. WS status inline styles  
git_script = git_script.replace(
    'this.socketLight.className = "w-1.5 h-1.5 bg-[#00ff66] shadow-sm shadow-[#00ff66]";',
    "this.socketLight.style.background = 'var(--green)'; this.socketLight.style.borderRadius = '50%';"
)
git_script = git_script.replace(
    'this.socketStatus.innerText = "ONLINE";',
    "this.socketStatus.innerText = 'ONLINE';"
)
git_script = git_script.replace(
    'this.socketStatus.className = "text-[#00ff66] font-extrabold text-[8px] uppercase tracking-widest";',
    "this.socketStatus.style.color = 'var(--green)';"
)
git_script = git_script.replace(
    'this.socketLight.className = "w-1.5 h-1.5 bg-rose-600 shadow-sm shadow-rose-600";',
    "this.socketLight.style.background = 'var(--red)'; this.socketLight.style.borderRadius = '50%';"
)
git_script = git_script.replace(
    'this.socketStatus.innerText = "OFFLINE";',
    "this.socketStatus.innerText = 'OFFLINE';"
)
git_script = git_script.replace(
    'this.socketStatus.className = "text-[#ff0055] font-extrabold text-[8px] uppercase tracking-widest";',
    "this.socketStatus.style.color = 'var(--red)';"
)
git_script = git_script.replace(
    'this.latency.innerText = "--ms";',
    "if (this.latency) this.latency.innerText = '--ms';"
)

# 2. Add pair navigator handler
pair_nav_js = '''
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
'''

# Insert before Timeframe toggle buttons section
tf_marker = '                // Timeframe toggle buttons'
if tf_marker in git_script:
    git_script = git_script.replace(tf_marker, pair_nav_js + tf_marker)
    print("Inserted pair navigator JS")
else:
    print("WARNING: Could not find Timeframe toggle insertion point")

# Build new content: current HTML before script + git script + current HTML after script
new_content = current[:curr_script_start] + git_script + current[curr_script_end:]

open(path, 'w', encoding='utf-8').write(new_content)
print(f"\nFile updated. Total size: {len(new_content)}")
print("Done - script restored from git HEAD with minimal patches")
