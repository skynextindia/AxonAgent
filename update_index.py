import re

with open('cli/static/index.html', 'r', encoding='utf-8') as f:
    content = f.read()

old_block = '''                  let agentColor = "text-cyan-400 bg-cyan-950/20 border border-cyan-800/30";
                  if (isHistorical) {
                      agentColor = "text-zinc-500 bg-zinc-950 border border-zinc-900";
                  } else {
                      if (data.agent_name.includes("Market")) agentColor = "text-emerald-400 bg-emerald-950/20 border border-emerald-800/30 font-bold";
                      if (data.agent_name.includes("Sentiment") || data.agent_name.includes("Social")) agentColor = "text-blue-400 bg-blue-950/20 border border-blue-800/30";
                      if (data.agent_name.includes("News") || data.agent_name.includes("Fundamental")) agentColor = "text-amber-500 bg-amber-950/20 border border-amber-800/30";
                      if (data.agent_name.includes("Trader")) agentColor = "text-[#ff0055] bg-rose-950/20 border border-rose-800/30 font-bold";
                      if (data.agent_name.includes("Portfolio")) agentColor = "text-[#9d00ff] bg-purple-950/20 border border-purple-800/30 font-bold";
                  }'''

new_block = '''                  let agentColor = "text-cyan-400 bg-cyan-950/20 border border-cyan-800/30";
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
                  }'''

# Replace exactly using standard string replacement
if old_block in content:
    content = content.replace(old_block, new_block)
    with open('cli/static/index.html', 'w', encoding='utf-8') as f:
        f.write(content)
    print("Replaced index.html agentColor block successfully.")
else:
    # Try regex if exact match fails due to whitespace
    print("Exact match failed. Trying regex.")
    pattern = re.compile(r'let agentColor = "text-cyan-400.*?}\s*}', re.DOTALL)
    if pattern.search(content):
        content = pattern.sub(new_block, content, count=1)
        with open('cli/static/index.html', 'w', encoding='utf-8') as f:
            f.write(content)
        print("Replaced via regex.")
    else:
        print("Failed to find block.")
