"""
Diagnóstico 2: interceptar TODOS os scripts e ver qual redireciona
"""
import asyncio
import os
import time
from playwright.async_api import async_playwright

try:
    from playwright_stealth import Stealth
    HAS_STEALTH = True
except:
    HAS_STEALTH = False

RUN_DURATION = int(os.environ.get("RUN_DURATION", "0"))

# Script injetado ANTES de qualquer JS do site
# Sobrescreve location.assign, location.replace e o setter de location.href
ANTI_REDIRECT_SCRIPT = """
(function() {
    // Bloqueia qualquer tentativa de redirect
    const _assign = window.location.assign.bind(window.location);
    const _replace = window.location.replace.bind(window.location);
    
    Object.defineProperty(window, 'location', {
        get: function() {
            return {
                href: window._real_href || document.URL,
                assign: function(url) { console.log('[BLOCKED assign] ' + url); },
                replace: function(url) { console.log('[BLOCKED replace] ' + url); },
                reload: function() { console.log('[BLOCKED reload]'); },
                toString: function() { return document.URL; }
            };
        },
        set: function(url) {
            console.log('[BLOCKED location=] ' + url);
        },
        configurable: false
    });
    
    // Bloqueia window.open para outros domínios
    const _open = window.open;
    window.open = function(url, ...args) {
        if (url && !url.includes('detetiveforense')) {
            console.log('[BLOCKED window.open] ' + url);
            return null;
        }
        return _open.apply(window, [url, ...args]);
    };
    
    console.log('[AntiRedirect] Proteção instalada');
})();
"""

async def main():
    print("[Diag2] Iniciando diagnóstico avançado de redirect...")

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
            print("[Diag2] Stealth aplicado")

        # Injeta o script anti-redirect ANTES de qualquer JS do site
        await context.add_init_script(ANTI_REDIRECT_SCRIPT)
        print("[Diag2] Anti-redirect script injetado")

        page = await context.new_page()

        # Log de TODOS os requests e responses
        blocked_scripts = []
        
        async def handle_route(route):
            url = route.request.url
            res_type = route.request.resource_type
            
            # Bloqueia scripts de analytics/tracking que podem fazer redirect
            if res_type == 'script':
                if any(x in url for x in ['fingerprint', 'botd', 'fpjs', 'fp.js', 'fp-pro', 'clarity', 'hotjar', 'sentry']):
                    print(f"[BLOCKED script] {url[:100]}")
                    blocked_scripts.append(url)
                    await route.abort()
                    return
            
            await route.continue_()

        await page.route("**/*", handle_route)

        # Log de console
        page.on("console", lambda msg: print(f"[JS] {msg.type}: {msg.text[:200]}"))
        page.on("requestfailed", lambda req: print(f"[ReqFailed] {req.url[:80]}"))

        print("[Diag2] Navegando para detetiveforense.com/auth/login...")
        try:
            r = await page.goto(
                "https://detetiveforense.com/auth/login",
                wait_until='domcontentloaded',
                timeout=20000
            )
            print(f"[Diag2] goto OK: status={r.status if r else 'None'}, URL={page.url}")
        except Exception as e:
            print(f"[Diag2] goto ERRO: {e}")

        # Espera o JS carregar e tentar redirecionar
        await page.wait_for_timeout(4000)

        url_after = page.url
        title_after = await page.title()
        print(f"[Diag2] URL após 4s: {url_after}")
        print(f"[Diag2] Título: {title_after}")

        try:
            body = await page.inner_text('body')
            print(f"[Diag2] Body (500c): {body[:500]}")
        except Exception as e:
            print(f"[Diag2] body ERRO: {e}")

        # Se ainda está no site, tenta preencher o formulário
        if 'detetiveforense' in url_after:
            print("[Diag2] SUCESSO! Site carregou. Tentando formulário...")
            try:
                await page.wait_for_selector('input', timeout=5000)
                inputs = await page.query_selector_all('input')
                print(f"[Diag2] Inputs encontrados: {len(inputs)}")
                for i, inp in enumerate(inputs):
                    ph = await inp.get_attribute('placeholder') or ''
                    tp = await inp.get_attribute('type') or ''
                    print(f"  Input {i}: type={tp}, placeholder={ph}")
            except Exception as e:
                print(f"[Diag2] inputs ERRO: {e}")
        else:
            print(f"[Diag2] REDIRECT detectado! URL virou: {url_after}")
            print(f"[Diag2] Scripts bloqueados: {len(blocked_scripts)}")

        await browser.close()

    print("[Diag2] Concluído!")

    if RUN_DURATION > 0:
        elapsed = 120
        remaining = RUN_DURATION - elapsed
        if remaining > 0:
            print(f"[Diag2] Aguardando {remaining}s para completar o ciclo...")
            time.sleep(remaining)

asyncio.run(main())
