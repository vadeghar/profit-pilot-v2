"""Manual dry run of the ICICI Breeze login form (Playwright). Reads BREEZE_API_KEY and
BREEZE_USER_ID from the project .env; fills a dummy password and never submits."""
import asyncio
from pathlib import Path
from urllib.parse import quote

from playwright.async_api import async_playwright


def _env() -> dict:
    env = {}
    path = Path(__file__).resolve().parents[1] / ".env"
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip("'\"")
    return env


async def test_form():
    env = _env()
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()

        url = f'https://api.icicidirect.com/apiuser/login?api_key={quote(env["BREEZE_API_KEY"])}'
        await page.goto(url, wait_until='networkidle')

        await page.fill('#txtuid', env["BREEZE_USER_ID"])
        print('✅ User ID filled (#txtuid)')
        await page.fill('#txtPass', 'dummy')
        print('✅ Password filled (#txtPass)')

        chk = await page.query_selector('#chkssTnc')
        if chk:
            await chk.check()
            print('✅ Terms checkbox checked (#chkssTnc)')

        btn = await page.query_selector('#btnSubmit')
        if btn:
            print('✅ Login button ready (#btnSubmit)')

        await page.screenshot(path='breeze_dry_run.png')
        print('📸 Dry run screenshot: breeze_dry_run.png')
        await browser.close()

if __name__ == '__main__':
    asyncio.run(test_form())
