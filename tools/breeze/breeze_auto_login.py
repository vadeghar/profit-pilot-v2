#!/usr/bin/env python3
"""
ICICI Breeze Automated Login with Telegram OTP
Updated with exact DOM selectors from inspection
"""

import os
import sys
import time
import re
from datetime import datetime
from urllib.parse import quote, urlparse, parse_qs
from typing import Optional, Dict, Any
import asyncio


def _ensure_tls_cabundle():
    """Point Python's OpenSSL at a modern CA bundle before breeze_connect import.

    breeze_connect performs a network call (security-master download) at import
    time using urllib/http.client, which trust Python's *default* OpenSSL CA
    bundle. The python.org framework build ships an empty/stale bundle missing
    the newer GlobalSign roots used by api.icicidirect.com, which surfaces as:
    "SSLError: self-signed certificate in certificate chain".
    certifi (installed with requests) carries the correct roots.
    """
    try:
        import certifi
        os.environ.setdefault("SSL_CERT_FILE", certifi.where())
        os.environ.setdefault("REQUESTS_CA_BUNDLE", certifi.where())
    except ImportError:
        pass


_ensure_tls_cabundle()

try:
    from playwright.async_api import async_playwright, Page, Browser
except ImportError:
    print("❌ Playwright not installed. Run: pip install playwright && playwright install")
    sys.exit(1)

import requests


class BreezeLoginAutomator:
    def __init__(self, env_file_path: str = None):
        if env_file_path is None:
            # tools/breeze/ -> project root
            _root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            env_file_path = os.path.join(_root, '.env')

        self.env_file_path = env_file_path
        self.env = self.load_env()

        # Breeze credentials
        self.api_key = self.env.get('BREEZE_API_KEY', '')
        self.user_id = self.env.get('BREEZE_USER_ID', '')
        self.password = self.env.get('BREEZE_PASSWORD', '')

        # Telegram config
        self.telegram_bot_token = self.env.get('TELEGRAM_BOT_TOKEN', '')
        self.telegram_chat_id = self.env.get('TELEGRAM_HOME_CHANNEL') or self.env.get('TELEGRAM_CHAT_ID', '')

        self.session_token: Optional[str] = None
        self.last_update_id = 0

    def load_env(self) -> Dict[str, str]:
        """Load environment variables from .env"""
        env = {}
        if os.path.exists(self.env_file_path):
            with open(self.env_file_path) as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith('#') and '=' in line:
                        k, v = line.split('=', 1)
                        env[k.strip()] = v.strip()
        return env

    def validate_config(self) -> bool:
        """Validate required configuration"""
        required = ['BREEZE_API_KEY', 'BREEZE_USER_ID', 'BREEZE_PASSWORD',
                    'TELEGRAM_BOT_TOKEN', 'TELEGRAM_HOME_CHANNEL']
        missing = [k for k in required if not self.env.get(k)]

        if missing:
            print(f"❌ Missing required config in .env: {', '.join(missing)}")
            return False

        print("✓ Configuration validated")
        return True

    def send_telegram_message(self, text: str) -> bool:
        """Send message via Telegram Bot API"""
        url = f"https://api.telegram.org/bot{self.telegram_bot_token}/sendMessage"
        payload = {
            "chat_id": self.telegram_chat_id,
            "text": text,
            "parse_mode": "Markdown"
        }

        try:
            resp = requests.post(url, json=payload, timeout=10)
            if resp.status_code == 200:
                print(f"✓ Telegram message sent: {text[:50]}...")
                return True
            else:
                print(f"⚠ Telegram send failed: {resp.status_code} - {resp.text}")
                return False
        except Exception as e:
            print(f"⚠ Telegram error: {e}")
            return False

    def get_telegram_otp(self, timeout: int = 90, max_retries: int = 3) -> Optional[str]:
        """Poll Telegram for OTP reply"""
        url = f"https://api.telegram.org/bot{self.telegram_bot_token}/getUpdates"

        print(f"⏳ Waiting for OTP reply via Telegram (timeout: {timeout}s)...")

        # Get baseline offset so we only look for new messages
        try:
            resp = requests.get(url, params={"timeout": 1}, timeout=5)
            if resp.status_code == 200:
                updates = resp.json().get('result', [])
                if updates:
                    self.last_update_id = updates[-1]['update_id']
                    print(f"✓ Baseline update_id: {self.last_update_id}")
        except:
            pass

        start_time = time.time()
        attempt = 0

        while attempt < max_retries and (time.time() - start_time) < timeout:
            try:
                params = {"offset": self.last_update_id + 1, "timeout": 10}
                resp = requests.get(url, params=params, timeout=15)

                if resp.status_code != 200:
                    print(f"⚠ Telegram API error: {resp.status_code}")
                    time.sleep(3)
                    continue

                data = resp.json()
                if not data.get('ok'):
                    time.sleep(3)
                    continue

                updates = data.get('result', [])

                for update in updates:
                    self.last_update_id = max(self.last_update_id, update['update_id'])

                    message = update.get('message', {})
                    chat_id = str(message.get('chat', {}).get('id', ''))
                    text = message.get('text', '').strip()

                    # Match from configured chat
                    if chat_id == str(self.telegram_chat_id):
                        otp_match = re.search(r'\b(\d{4,8})\b', text)
                        if otp_match:
                            otp = otp_match.group(1)
                            print(f"✓ OTP received from Telegram: {otp}")
                            return otp

                time.sleep(2)
                attempt += 1

            except Exception as e:
                print(f"⚠ Error polling Telegram: {e}")
                time.sleep(3)
                attempt += 1

        print(f"❌ Timeout waiting for OTP after {timeout}s")
        return None

    async def perform_login(self, headless: bool = True) -> bool:
        """Perform automated login with exact selectors"""

        login_url = f"https://api.icicidirect.com/apiuser/login?api_key={quote(self.api_key)}"
        print(f"\n🌐 Login URL: {login_url}")

        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=headless,
                args=['--disable-blink-features=AutomationControlled']
            )
            context = await browser.new_context(
                viewport={'width': 1280, 'height': 800},
                ignore_https_errors=True
            )
            page = await context.new_page()

            captured_session = None

            def handle_request(request):
                nonlocal captured_session
                if request.method == "POST":
                    try:
                        post_data = request.post_data
                        if post_data:
                            # Check for various token parameter names in POST data
                            for param_name in ['API_Session', 'API_SessionToken', 'apisession', 'apisessiontoken',
                                               'APISESSION', 'APISESSIONTOKEN']:
                                if param_name in post_data:
                                    match = re.search(rf'{param_name}=([^&\s]+)', post_data)
                                    if match:
                                        captured_session = match.group(1)
                                        print(f"✓ Captured {param_name} from POST: {captured_session[:20]}...")
                                        break
                    except:
                        pass

            page.on("request", handle_request)

            try:
                print("🚀 Navigating to login page...")
                await page.goto(login_url, wait_until='networkidle', timeout=30000)
                await asyncio.sleep(2)

                await page.screenshot(path='breeze_login_step1.png')
                print("📸 Screenshot: breeze_login_step1.png")

                # Fill User ID using exact ID
                print("📝 Filling User ID (id=txtuid)...")
                await page.fill('#txtuid', self.user_id)
                print("✓ Filled User ID")

                # Fill Password using exact ID
                print("📝 Filling Password (id=txtPass)...")
                await page.fill('#txtPass', self.password)
                print("✓ Filled Password")

                # Check Terms & Conditions checkbox if present
                try:
                    chk = await page.query_selector('#chkssTnc')
                    if chk:
                        is_checked = await chk.is_checked()
                        if not is_checked:
                            await chk.check()
                            print("✓ Checked Terms & Conditions (#chkssTnc)")
                except Exception as e:
                    print(f"ℹ Checkbox notice: {e}")

                await page.screenshot(path='breeze_login_step2.png')
                print("📸 Screenshot: breeze_login_step2.png")

                # Click Login button using exact ID
                print("🔐 Clicking Login button (#btnSubmit)...")
                await page.click('#btnSubmit')
                print("✓ Clicked Login button")

                # Wait for response (page transition or OTP input)
                await asyncio.sleep(4)
                await page.screenshot(path='breeze_login_step3.png')
                print("📸 Screenshot: breeze_login_step3.png")

                current_url = page.url
                print(f"📍 Current URL: {current_url}")

                # Check if we were redirected directly
                parsed = urlparse(current_url)
                query_params = parse_qs(parsed.query)

                # Check for various token parameter names in URL
                token_param = None
                for param_name in ['apisession', 'apisessiontoken', 'API_Session', 'API_SessionToken']:
                    if param_name in query_params:
                        token_param = param_name
                        self.session_token = query_params[param_name][0]
                        print(f"✓ Extracted session token from URL ({param_name}): {self.session_token}")
                        break

                if token_param:
                    await browser.close()
                    return True

                # Check if OTP screen is shown
                # Look for OTP inputs or DOB inputs
                otp_input = await page.query_selector('input[type="password"], input[name*="otp" i], input[id*="otp" i], input[placeholder*="otp" i], input[id*="txtotp" i]')

                if not otp_input:
                    # Let's inspect what inputs exist now
                    inputs = await page.query_selector_all('input')
                    print(f"ℹ Current inputs on page ({len(inputs)}):")
                    for i, inp in enumerate(inputs):
                        t = await inp.get_attribute('type')
                        idx = await inp.get_attribute('id')
                        print(f"   [{i}] type={t} id={idx}")

                # Send Telegram message asking for OTP
                print("\n📱 Requesting OTP via Telegram...")
                self.send_telegram_message(
                    "🔐 *ICICI Breeze Login*\n\n"
                    "OTP requested by Breeze. Please reply to this bot with the numeric OTP.\n\n"
                    f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
                )

                # Poll Telegram for reply
                otp = self.get_telegram_otp(timeout=90, max_retries=3)

                if not otp:
                    print("❌ Failed to receive OTP via Telegram")
                    await page.screenshot(path='breeze_error_no_otp.png')
                    await browser.close()
                    return False

                # Fill OTP into 6 separate input fields (one digit each)
                print(f"📝 Filling OTP digit by digit (6 fields)...")
                otp_filled = False

                # The OTP page has 6 separate inputs with tg-nm="otp"
                # Fill each digit individually
                otp_digits = list(str(otp).strip())
                if len(otp_digits) != 6:
                    print(f"⚠ OTP length is {len(otp_digits)}, expected 6. Using what we have.")

                otp_inputs = await page.query_selector_all('input[tg-nm="otp"]')
                print(f"ℹ Found {len(otp_inputs)} OTP input fields")

                for i, digit_input in enumerate(otp_inputs):
                    if i < len(otp_digits):
                        await digit_input.fill(otp_digits[i])
                        print(f"✓ Filled digit {otp_digits[i]} into input {i+1}")
                    else:
                        await digit_input.fill('')

                if len(otp_inputs) == 6 and len(otp_digits) == 6:
                    otp_filled = True
                    print("✓ Filled all 6 OTP digits individually")
                else:
                    # Fallback: try hidden field approach
                    hidden_otp = await page.query_selector('#hiotp')
                    if hidden_otp:
                        await page.evaluate('document.getElementById("hiotp").value = arguments[0]', otp)
                        print(f"✓ Set hidden #hiotp field")
                    otp_filled = True

                if not otp_filled:
                    print("❌ Could not find OTP field to fill")
                    await page.screenshot(path='breeze_error_otp_field.png')
                    await browser.close()
                    return False

                await page.screenshot(path='breeze_login_step4_otp_filled.png')

                # Wait a moment for the OTP processing
                await asyncio.sleep(2)

                # Submit OTP
                print("🔐 Submitting OTP...")
                submit_buttons = [
                    '#btnSubmit',
                    '#btnValidate',
                    '#btnVerify',
                    'input[value*="Submit" i]',
                    'input[value*="Verify" i]',
                    'input[value*="Login" i]',
                    'button[type="submit"]',
                    'input[type="button"]'
                ]

                otp_submitted = False
                for sel in submit_buttons:
                    try:
                        el = await page.query_selector(sel)
                        if el and await el.is_visible():
                            await el.click()
                            print(f"✓ Clicked submit button: {sel}")
                            otp_submitted = True
                            break
                    except:
                        continue

                if not otp_submitted:
                    await page.keyboard.press('Enter')
                    print("✓ Pressed Enter")

                # Wait for redirect after OTP
                print("⏳ Waiting for redirect after OTP submission...")
                await asyncio.sleep(6)

                current_url = page.url
                print(f"📍 Post-OTP URL: {current_url}")

                # Extract session token from redirect
                parsed = urlparse(current_url)
                query_params = parse_qs(parsed.query)

                if 'apisession' in query_params:
                    self.session_token = query_params['apisession'][0]
                    print(f"✓ Extracted session token from URL: {self.session_token}")
                elif captured_session:
                    self.session_token = captured_session
                    print(f"✓ Extracted session token from POST capture: {self.session_token}")
                else:
                    # Let's check page body for token
                    body_text = await page.inner_text('body')
                    token_match = re.search(r'apisession[=:]\s*([a-zA-Z0-9]+)', body_text, re.IGNORECASE)
                    if token_match:
                        self.session_token = token_match.group(1)
                        print(f"✓ Found session token in body text: {self.session_token}")
                    else:
                        await page.screenshot(path='breeze_error_redirect.png')
                        print("❌ Could not find session token in URL or network capture")
                        await browser.close()
                        return False

                await page.screenshot(path='breeze_login_success.png')
                print("✅ Login successful!")

                await browser.close()
                return True

            except Exception as e:
                print(f"❌ Login error: {e}")
                try:
                    await page.screenshot(path='breeze_error_exception.png')
                except:
                    pass
                await browser.close()
                return False

    def update_env_file(self) -> bool:
        """Update BREEZE_SESSION_TOKEN in .env"""
        if not self.session_token:
            print("❌ No session token to update")
            return False

        try:
            with open(self.env_file_path, 'r') as f:
                lines = f.readlines()

            updated = False
            for i, line in enumerate(lines):
                if line.strip().startswith('BREEZE_SESSION_TOKEN='):
                    lines[i] = f'BREEZE_SESSION_TOKEN={self.session_token}\n'
                    updated = True
                    print(f"✓ Replaced BREEZE_SESSION_TOKEN in .env")
                    break

            if not updated:
                lines.append(f'\nBREEZE_SESSION_TOKEN={self.session_token}\n')
                print(f"✓ Added BREEZE_SESSION_TOKEN to .env")

            with open(self.env_file_path, 'w') as f:
                f.writelines(lines)

            print(f"✅ .env updated at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
            return True

        except Exception as e:
            print(f"❌ Failed to update .env: {e}")
            return False

    def authenticate_breeze(self) -> Dict[str, Any]:
        """Authenticate to ICICI Breeze using the freshly captured session token.

        Mirrors brokers/icici.py: generate_session() then get_customer_details().
        Returns {'ok': bool, 'detail': str} — never raises.
        """
        api_key = self.env.get('BREEZE_API_KEY', '')
        api_secret = self.env.get('BREEZE_API_SECRET', '')
        if not api_key or not api_secret or not self.session_token:
            missing = [n for n, v in (('BREEZE_API_KEY', api_key),
                                      ('BREEZE_API_SECRET', api_secret),
                                      ('BREEZE_SESSION_TOKEN', self.session_token)) if not v]
            print(f"⚠ Skipping Breeze auth — missing config: {', '.join(missing)}")
            return {'ok': False, 'detail': f"missing config: {', '.join(missing)}"}

        print("\n" + "="*60)
        print("AUTHENTICATING TO BREEZE WITH NEW SESSION TOKEN")
        print("="*60)
        try:
            from utils.breeze_sdk import import_breeze_connect
            BreezeConnect = import_breeze_connect()
            breeze = BreezeConnect(api_key=api_key)
            breeze.generate_session(api_secret=api_secret, session_token=self.session_token)
            details = breeze.get_customer_details(api_session=self.session_token)
            if details and details.get('Status') == 200:
                name = (details.get('Result') or {}).get('Name') or details.get('Result', {})
                print(f"✅ Breeze authentication SUCCESSFUL ({name})")
                return {'ok': True, 'detail': f"authenticated as {name}"}
            err = details.get('Error') if details else 'no response'
            print(f"❌ Breeze authentication failed: {err}")
            return {'ok': False, 'detail': f"auth rejected: {err}"}
        except Exception as e:
            print(f"❌ Breeze authentication exception: {e}")
            return {'ok': False, 'detail': f"auth exception: {e}"}

    async def run(self, headless: bool = True) -> bool:
        """Main execution flow"""
        print("="*60)
        print("ICICI BREEZE AUTOMATED LOGIN")
        print("="*60)
        print(f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"env_file: {self.env_file_path}")
        print()

        if not self.validate_config():
            return False

        print(f"User ID: {self.user_id}")
        print(f"Telegram Chat: {self.telegram_chat_id}")
        print()

        success = await self.perform_login(headless=headless)

        if not success:
            print("\n❌ Login process failed")
            self.send_telegram_message("❌ ICICI Breeze auto-login failed. Please check screenshots.")
            return False

        if self.update_env_file():
            # Authenticate to Breeze with the freshly-written token before notifying
            auth = self.authenticate_breeze()
            auth_line = (f"✅ Breeze auth: {auth['detail']}\n" if auth['ok']
                         else f"⚠️ Breeze auth: {auth['detail']}\n")
            self.send_telegram_message(
                f"✅ *ICICI Breeze Login Successful*\n\n"
                f"Session token captured and updated in `.env`.\n"
                f"{auth_line}"
                f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            )
            print("\n✅ Process completed successfully!")
            return True
        else:
            self.send_telegram_message("⚠ Login succeeded but failed to write token to .env.")
            return False


async def main():
    import argparse

    parser = argparse.ArgumentParser(description='ICICI Breeze Automated Login')
    parser.add_argument('--headless', action='store_true', help='Run in headless mode')
    parser.add_argument('--visible', action='store_true', help='Run with visible browser')
    parser.add_argument('--env-file', type=str, help='Path to .env')

    args = parser.parse_args()

    headless = args.headless or not args.visible

    automator = BreezeLoginAutomator(env_file_path=args.env_file)
    success = await automator.run(headless=headless)

    sys.exit(0 if success else 1)


if __name__ == '__main__':
    asyncio.run(main())
