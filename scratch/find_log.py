import os

for root, dirs, files in os.walk("d:\\work\\TradingAgents"):
    for file in files:
        if file.endswith(".py"):
            path = os.path.join(root, file)
            try:
                with open(path, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read()
                    if "Set last tick time" in content:
                        print(f"Found in {path}")
            except Exception as e:
                pass
