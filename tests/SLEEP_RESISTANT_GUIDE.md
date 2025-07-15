# Sleep-Resistant Market Data Automation Guide

## 🎯 **Problem Solved**
Your automation will now work **even when your laptop goes to sleep**! 

## 🚀 **Quick Setup (Recommended)**

### **Option 1: Simple Batch File (Easiest)**
```bash
# Right-click and "Run as administrator"
setup_sleep_resistant_automation.bat
```

This creates Windows scheduled tasks that:
- ✅ Start automation at 9:15 AM daily
- ✅ Stop automation at 3:30 PM daily  
- ✅ Work even when laptop sleeps
- ✅ Run in background

### **Option 2: Windows Task Scheduler (Advanced)**
```bash
python utils/windows_task_scheduler.py
```

### **Option 3: Windows Service (Most Robust)**
```bash
# Install pywin32 first
pip install pywin32

# Then setup service
python utils/windows_service_setup.py install
python utils/windows_service_setup.py start
```

## 🔧 **How It Works**

### **Windows Task Scheduler Method**
1. **Creates scheduled tasks** that run at specific times
2. **Tasks execute even when laptop sleeps** (Windows wakes up briefly)
3. **Automatic start/stop** based on market hours
4. **Background execution** - no visible windows

### **Windows Service Method**
1. **Installs as Windows service** (like antivirus, etc.)
2. **Runs continuously** in background
3. **Survives sleep/wake cycles**
4. **Auto-restart** if process crashes

## 📋 **Setup Steps**

### **Step 1: Login to Kite (One-time)**
```bash
python login_kite.py
```

### **Step 2: Setup Sleep-Resistant Automation**
```bash
# Run as administrator
setup_sleep_resistant_automation.bat
```

### **Step 3: Verify Setup**
```bash
# Check if tasks were created
schtasks /query /tn "NIFTY50*"
```

## 🕐 **Schedule**

| Time | Action |
|------|--------|
| **9:15 AM** | Automation starts automatically |
| **9:15 AM - 3:30 PM** | Fetches data every minute/5min/15min |
| **3:30 PM** | Automation stops automatically |
| **Weekends** | No automation (market closed) |

## 🔍 **Monitoring**

### **Check if Automation is Running**
```bash
# Check running processes
tasklist | findstr python

# Check logs
type logs\market_data_automation.log
```

### **View Scheduled Tasks**
```bash
schtasks /query /tn "NIFTY50*"
```

### **Check Database**
```python
from utils.db_func import fetch_market_data
df = fetch_market_data(symbol="NIFTY50", interval="1m")
print(f"Latest data: {len(df)} rows")
```

## 🛠️ **Troubleshooting**

### **"Access Denied" Error**
**Solution**: Run as administrator
- Right-click batch file → "Run as administrator"

### **Tasks Not Running**
**Solutions**:
1. Check Windows Task Scheduler
2. Verify Python path in tasks
3. Check logs for errors

### **Service Not Starting**
**Solutions**:
1. Install pywin32: `pip install pywin32`
2. Run as administrator
3. Check Windows Services

### **Remove All Tasks**
```bash
schtasks /delete /tn "NIFTY50*" /f
```

## 🎯 **Benefits**

✅ **Sleep-Resistant**: Works even when laptop sleeps
✅ **Automatic**: No manual intervention needed
✅ **Market-Aware**: Only runs during trading hours
✅ **Background**: Runs silently in background
✅ **Reliable**: Windows manages execution
✅ **Cost-Efficient**: Only fetches during market hours

## 🔄 **Daily Operation**

1. **9:15 AM**: Windows automatically starts your automation
2. **Market Hours**: Data fetched every minute/5min/15min
3. **3:30 PM**: Windows automatically stops your automation
4. **Sleep/Wake**: Tasks continue working through sleep cycles

## 📊 **Data Storage**

All data is stored in your existing database:
- `market_data_1m` - 1-minute data
- `market_data_5m` - 5-minute data
- `market_data_15m` - 15-minute data

## 🚨 **Important Notes**

1. **Administrator Rights**: Setup requires administrator privileges
2. **Internet Required**: Laptop needs internet connection
3. **Token Expiry**: Kite tokens expire daily - may need re-login
4. **Power Settings**: Ensure laptop doesn't hibernate (sleep is OK)
5. **Weekend Handling**: Tasks run daily but automation checks market hours

## 🎉 **You're All Set!**

After setup, your automation will:
- 🕐 Start automatically at 9:15 AM
- 📊 Fetch data throughout market hours
- 🛑 Stop automatically at 3:30 PM
- 💤 Work even when laptop sleeps
- 📈 Store data in your database

# Stop the service
python utils/windows_service_setup.py stop

# If you want to completely remove it
python utils/windows_service_setup.py uninstall

**No more manual intervention needed!** 🚀 

