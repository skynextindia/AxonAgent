import re

with open("d:\\work\\TradingAgents\\cli\\static\\index.html", "r", encoding="utf-8") as f:
    content = f.read()

# Find all occurrences of document.getElementById(...)
matches = re.findall(r'document\.getElementById\([\'"]([^\'"]+)[\'"]\)', content)
print("Found IDs in document.getElementById:")
for m in sorted(list(set(matches))):
    print("-", m)
