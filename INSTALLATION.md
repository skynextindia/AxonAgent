# AxonAI Trading System - Installation Guide

## Quick Start (Windows)

### **Option 1: Automated Installation (Recommended)**

1. **Download installer:**
   ```
   INSTALL_AXONAI.bat
   ```

2. **Run the installer:**
   - Right-click `INSTALL_AXONAI.bat`
   - Select "Run as administrator"
   - Follow the prompts (takes ~10 minutes)

3. **Installation does:**
   ✓ Checks Python 3.10+  
   ✓ Clones AxonAgent repository  
   ✓ Creates Python virtual environment  
   ✓ Installs all dependencies  
   ✓ Creates batch shortcuts  
   ✓ Verifies installation  

4. **After installation:**
   - Batch files appear in: `C:\Users\{your-username}\AxonAI\`
   - Run: `RUN_LIVE.bat` (live trading)
   - Or: `RUN_PAPER.bat` (safe test mode)

---

### **Option 2: Manual Installation**

If automated installation fails, follow these steps:

#### **Step 1: Prerequisites**
```powershell
# Check Python version (need 3.10+)
python --version

# Check Git is installed
git --version

# Check pip
pip --version
```

If any are missing:
- Python: https://www.python.org/downloads/
- Git: https://git-scm.com/download/win

#### **Step 2: Clone Repository**
```bash
cd C:\Users\{your-username}
git clone -b velocity https://github.com/skynextindia/AxonAgent.git AxonAI
cd AxonAI
```

#### **Step 3: Create Virtual Environment**
```bash
python -m venv venv
venv\Scripts\activate.bat
```

#### **Step 4: Install Dependencies**
```bash
pip install --upgrade pip
pip install -e .
```

#### **Step 5: Verify Installation**
```bash
python -c "import axonai; print('AxonAI installed')"
```

#### **Step 6: Create Shortcuts (Optional)**
Copy batch files to your installation directory:
- `RUN_LIVE.bat`
- `RUN_PAPER.bat`
- `RUN_LIVE_PORT_9000.bat` (for port conflicts)

---

## System Requirements

| Component | Requirement |
|-----------|-------------|
| **OS** | Windows 10/11 |
| **Python** | 3.10 or higher |
| **RAM** | 4GB minimum, 8GB recommended |
| **MT5 Exness** | Required (data feed) |
| **MT5 MetaQuotes** | Required (order execution) |
| **Network** | Stable internet connection |

---

## Running the System

### **Option A: Batch Shortcuts (Easiest)**
```
C:\Users\{username}\AxonAI\RUN_LIVE.bat          (live trading)
C:\Users\{username}\AxonAI\RUN_PAPER.bat         (safe test)
C:\Users\{username}\AxonAI\RUN_LIVE_PORT_9000.bat (custom port)
```

### **Option B: Command Line**
```bash
cd C:\Users\{username}\AxonAI
venv\Scripts\activate.bat
python run.py --direct --symbol EURUSD --live
```

### **Option C: Custom Configuration**
```bash
# Different port (if 8000 in use)
python run.py --direct --symbol EURUSD --live --port 9000

# Paper trading (safe test mode)
python run.py --direct --symbol EURUSD --paper

# Custom MT5 paths
python run.py --direct --symbol EURUSD --live \
  --feed-path "C:\Program Files\MetaTrader 5 EXNESS\terminal64.exe" \
  --exec-path "C:\Program Files\MetaTrader 5\terminal64.exe"
```

---

## First Run Setup

### **1. Configure MT5 Terminals**

You need **two MetaTrader 5 installations:**

**A. Exness Terminal (Data Feed)**
- Download: https://www.exness.com/
- Installation path: `C:\Program Files\MetaTrader 5 EXNESS\terminal64.exe`
- Purpose: Read market data (EURUSD ticks)

**B. MetaQuotes Terminal (Order Execution)**
- Download: https://www.metatrader5.com/
- Installation path: `C:\Program Files\MetaTrader 5\terminal64.exe`
- Purpose: Execute buy/sell orders on demo account

### **2. Start the System**
- Double-click `RUN_LIVE.bat` (or use batch shortcut)
- Wait for dashboard to appear (~10-15 seconds)
- Browser opens to: http://127.0.0.1:8000

### **3. Monitor Logs**
```
C:\Users\{username}\.axonai\logs\axon.log
```

Look for:
- `[POLL_TICKS]` — data feed working
- `[TICKENGINE]` — tick processing active
- `[DAEMON]` — main system ready

---

## Troubleshooting

### **"Python is not installed"**
- Install from: https://www.python.org/downloads/
- **Important:** Check "Add Python to PATH" during installation
- Restart terminal after installation

### **"Module not found" errors**
```bash
cd C:\Users\{username}\AxonAI
venv\Scripts\activate.bat
pip install --upgrade pip
pip install -e .
```

### **"Port 8000 already in use"**
Use alternative port:
```bash
python run.py --direct --symbol EURUSD --live --port 9000
```
Then access: http://127.0.0.1:9000

### **"MetaTrader5 connection failed"**
- Ensure MT5 Exness terminal is running
- Check terminal path in batch file matches your installation
- Verify demo account is active in MT5

### **Dashboard won't load**
- Check logs: `C:\Users\{username}\.axonai\logs\axon.log`
- Try different port (see above)
- Restart system

---

## Updating the System

To get latest code:

```bash
cd C:\Users\{username}\AxonAI
git fetch origin
git checkout velocity
git pull origin velocity
```

Or run installer again to update everything.

---

## File Structure

```
C:\Users\{username}\AxonAI\
├── RUN_LIVE.bat                    # Start live trading
├── RUN_PAPER.bat                   # Start paper trading
├── RUN_LIVE_PORT_9000.bat          # Start on port 9000
├── venv\                           # Python virtual environment
├── axonai\                         # Main source code
├── run.py                          # Entry point
├── requirements.txt                # Dependencies
└── README.md                       # Project info
```

---

## Support

For issues:
1. Check logs: `C:\Users\{username}\.axonai\logs\axon.log`
2. Review this guide
3. Check GitHub: https://github.com/skynextindia/AxonAgent/issues

---

## Next Steps

1. **Read docs:** `D:\AXON.AI\AxonAgent-Agy\docs\`
2. **Test in paper mode:** Use `RUN_PAPER.bat` first
3. **Monitor live trading:** Use dashboard at http://127.0.0.1:8000
4. **Check logs regularly:** Watch for warnings/errors

---

**Version:** 0.2.5  
**Last Updated:** 2026-06-26
