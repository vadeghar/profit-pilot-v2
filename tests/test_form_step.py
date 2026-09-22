import asyncio
from playwright.async_api import async_playwright
from urllib.parse import quote

async def test_form():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()

        api_key = '%35N8B2`ZS19W30317921Y_$x188fq32'
        url = f'https://api.icicidirect.com/apiuser/login?api_key={quote(api_key)}'
        await page.goto(url, wait_until='networkidle')

        await page.fill('#txtuid', '36522465')
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
