import asyncio
import os
from playwright.async_api import async_playwright

try:
    from playwright_stealth import Stealth
    HAS_STEALTH = True
except:
    HAS_STEALTH = False

RUN_DURATION = int(os.environ.get("RUN_DURATION", "0"))

async def main():
    print("[Diag] Iniciando diagnostico de navegacao Playwright...")

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            args=[
                '--no-sandbox',
                '--disable-dev-shm-usage',
                '--disable-blink-features=AutomationControlled',
            ]
        )

        context = await browser.new_context(
            user_agent='Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36',
            viewport={'width': 1280, 'height': 800},
        )

        if HAS_STEALTH:
            await Stealth().apply_stealth_async(context)
            print("[Diag] Stealth aplicado")

        page = await context.new_page()

        page.on("console", lambda msg: print(f"[BrowserConsole] {msg.type}: {msg.text}"))
        page.on("requestfailed", lambda req: print(f"[ReqFailed] {req.url[:80]} - {req.failure}"))
        page.on("response", lambda resp: print(f"[Resp] {resp.status} {resp.url[:80]}"))

        print(f"[Diag] Stealth: {HAS_STEALTH}")
        print(f"[Diag] URL inicial: {page.url}")

        # Teste 1: google.com
        print("--- Teste 1: google.com ---")
        try:
            r = await page.goto("https://www.google.com", wait_until='domcontentloaded', timeout=15000)
            print(f"[Diag] google: status={r.status if r else 'None'}, URL={page.url}")
        except Exception as e:
            print(f"[Diag] google ERRO: {e}")

        await page.wait_for_timeout(2000)

        # Teste 2: detetiveforense.com
        print("--- Teste 2: detetiveforense.com ---")
        try:
            r = await page.goto("https://detetiveforense.com/auth/login", wait_until='domcontentloaded', timeout=20000)
            print(f"[Diag] detetive: status={r.status if r else 'None'}, URL={page.url}")
        except Exception as e:
            print(f"[Diag] detetive ERRO: {e}")

        await page.wait_for_timeout(3000)
        print(f"[Diag] URL final: {page.url}")
        print(f"[Diag] Titulo: {await page.title()}")

        try:
            body = await page.inner_text('body')
            print(f"[Diag] Body (500c): {body[:500]}")
        except Exception as e:
            print(f"[Diag] body ERRO: {e}")

        await browser.close()

    print("[Diag] Concluido!")
    
    if RUN_DURATION > 0:
        import time
        remaining = RUN_DURATION - 120
        if remaining > 0:
            print(f"[Worker] Aguardando {remaining}s para completar o ciclo...")
            time.sleep(remaining)

asyncio.run(main())
