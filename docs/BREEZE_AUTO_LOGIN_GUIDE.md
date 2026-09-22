# ICICI Breeze Auto-Login Setup Guide

## Overview

Automated ICICI Breeze login script that:
1. Opens Breeze login page in browser
2. Fills credentials automatically
3. Requests OTP via Telegram
4. Waits for your OTP reply via Telegram
5. Submits OTP and captures session token
6. Updates `BREEZE_SESSION_TOKEN` in .env

## Prerequisites

### 1. Install Playwright

```bash
cd /Users/apple/work/automated-engines/trading-platform
source .venv/bin/activate

# Install playwright
pip install playwright

# Install browser binaries
playwright install chromium
```

### 2. Verify .env Configuration

Ensure these variables exist in `.env`:

```bash
# ICICI Breeze Credentials
BREEZE_API_KEY=your_api_key
BREEZE_USER_ID=your_user_id
BREEZE_PASSWORD=your_password
BREEZE_DOB=DD/MM/YYYY  # Optional, if required

# Telegram (for OTP)
TELEGRAM_BOT_TOKEN=your_bot_token
TELEGRAM_HOME_CHANNEL=your_chat_id
```

All these values already exist in your .env! ✅

## Usage

### Option 1: Visible Browser (Recommended for First Run)

Watch the automation in action:

```bash
cd /Users/apple/work/automated-engines/trading-platform
source .venv/bin/activate

python3 tools/breeze/breeze_auto_login.py --visible
```

### Option 2: Headless Mode (Production)

Run invisibly in background:

```bash
python3 tools/breeze/breeze_auto_login.py --headless
```

### Option 3: Default (Headless unless --visible specified)

```bash
python3 tools/breeze/breeze_auto_login.py
```

## How It Works

### Step-by-Step Flow

1. **Script starts and validates configuration**
   ```
   ✓ Configuration validated
   User ID: 36522465
   Telegram Chat: 213432076
   ```

2. **Opens Breeze login page**
   ```
   🌐 Login URL: https://api.icicidirect.com/apiuser/login?api_key=...
   🚀 Navigating to login page...
   ```

3. **Fills credentials automatically**
   ```
   📝 Filling User ID...
   ✓ Filled user ID
   📝 Filling Password...
   ✓ Filled password
   🔐 Clicking login button...
   ```

4. **Sends Telegram notification**
   ```
   ✓ Telegram message sent: 🔐 ICICI Breeze Login - OTP requested...
   ```

   You'll receive:
   ```
   🔐 ICICI Breeze Login
   
   OTP requested. Please reply with the 6-digit OTP you received.
   
   Time: 2026-09-17 01:30:00
   ```

5. **Waits for your OTP reply**
   ```
   ⏳ Waiting for OTP reply via Telegram (timeout: 90s)...
   ```

   Just reply to the Telegram bot with the OTP:
   ```
   123456
   ```

6. **Submits OTP and captures session**
   ```
   ✓ OTP received: 123456
   📝 Filling OTP...
   🔐 Submitting OTP...
   ✓ Extracted session token from URL: 57042270...
   ✅ Login successful!
   ```

7. **Updates .env**
   ```
   ✓ Updated existing BREEZE_SESSION_TOKEN
   ✅ .env updated successfully at 2026-09-17 01:31:00
   ```

## Screenshots

The script automatically saves screenshots at each step:

- `breeze_login_step1.png` - Login page loaded
- `breeze_login_step2.png` - Credentials filled
- `breeze_login_step3_otp.png` - OTP screen
- `breeze_login_success.png` - Success
- `breeze_error_*.png` - Error screenshots (if any)

## Automation Options

### Daily Cron Job (Session expires daily)

Add to your crontab:

```bash
# Edit crontab
crontab -e

# Add this line to run at 8:30 AM daily
30 8 * * * cd /Users/apple/work/automated-engines/trading-platform && source .venv/bin/activate && python3 tools/breeze/breeze_auto_login.py --headless >> logs/breeze_login.log 2>&1
```

### Manual Run When Needed

```bash
./breeze_auto_login.py --visible
```

## Troubleshooting

### Issue 1: Playwright not installed

**Error:**
```
❌ Playwright not installed. Run: pip install playwright && playwright install
```

**Fix:**
```bash
source .venv/bin/activate
pip install playwright
playwright install chromium
```

### Issue 2: Missing Telegram config

**Error:**
```
❌ Missing required config in .env: TELEGRAM_BOT_TOKEN, TELEGRAM_HOME_CHANNEL
```

**Fix:**
Ensure .env has:
```
TELEGRAM_BOT_TOKEN=8755963169:AAEmu9L0LgfG_HKsb1ASYRfdQUVn7nsUsZQ
TELEGRAM_HOME_CHANNEL=213432076
```

### Issue 3: OTP timeout

**Error:**
```
❌ Timeout waiting for OTP after 90s
```

**Fix:**
- Check your Telegram bot is working
- Ensure you reply to the correct chat
- Try running with `--visible` to see what's happening

### Issue 4: Could not find login fields

**Error:**
```
❌ Could not find User ID field. Screenshot saved.
```

**Fix:**
- Check `breeze_error_userid.png` screenshot
- Breeze may have changed their login page UI
- Report to developer to update selectors

## Testing

Test Telegram connectivity:

```python
python3 -c "
from breeze_auto_login import BreezeLoginAutomator
bot = BreezeLoginAutomator()
bot.send_telegram_message('Test message from Breeze auto-login')
print('Check your Telegram!')
"
```

## Security Notes

⚠️ **Important:**

1. **Never log passwords/OTPs** - Script masks these in logs
2. **Session token is sensitive** - Treat like a password
3. **.env security** - Keep permissions restricted:
   ```bash
   chmod 600 .env
   ```
4. **Telegram bot** - Only you should have access to the bot chat

## Error Recovery

If login fails:

1. Check screenshots in project directory
2. Try running with `--visible` to watch the process
3. Verify credentials in .env are correct
4. Check if Breeze website is accessible

## Advanced Options

### Custom .env location

```bash
python3 tools/breeze/breeze_auto_login.py --env-file /path/to/custom/.env --visible
```

### Integration with Trading Platform

After successful login, test the connection:

```bash
python3 tests/test_broker_credentials.py
```

You should see:
```
TESTING ICICI BREEZE
1. Authenticating...
   Status: ✅ SUCCESS
```

## Log Files

Logs are saved with timestamps. Recommended location:

```bash
mkdir -p logs
python3 tools/breeze/breeze_auto_login.py --headless >> logs/breeze_login_$(date +%Y%m%d).log 2>&1
```

## Success Confirmation

After successful login, you'll receive Telegram notification:

```
✅ Breeze Login Successful

Session token updated in .env.
Time: 2026-09-17 01:31:00
```

And .env will be updated with new session token.

## Next Steps

1. **First run:** Use `--visible` mode to see it work
2. **Verify:** Run `test_broker_credentials.py` to confirm
3. **Automate:** Set up daily cron job
4. **Integrate:** Use in your trading strategies

---

**Created:** September 17, 2026  
**Status:** Ready to use  
**Prerequisites:** ✅ Playwright installation required
