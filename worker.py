"""
Achaqui Worker — processa pedidos do Firestore via Playwright
GitHub Actions 5min cron, loop interno de 290s
"""

import asyncio
import json
import os
import re
import time
import urllib.request
from datetime import datetime, timezone
from playwright.async_api import async_playwright

try:
    from playwright_stealth import Stealth
    HAS_STEALTH = True
except ImportError:
    HAS_STEALTH = False

# ── Config ───────────────────────────────────────────────────────────────────
FK           = os.environ.get('FIREBASE_API_KEY', 'AIzaSyAoZYnDTl8WoCG5K3q6hjFQnVFmkAS6PZ8')
FS_BASE      = 'https://firestore.googleapis.com/v1/projects/bancadamatriz-9f797/databases/(default)/documents'
NTFY         = 'https://ntfy.sh/achaqui-zapia-guga-secret-2025'
POLL_SEC     = 20
NTFY_SEC     = 5
RUN_DURATION = int(os.environ.get('RUN_DURATION', '0'))

DF_BASE      = 'https://detetiveforense.com'
DF_LOGIN_URL = f'{DF_BASE}/auth/login'
DF_USER      = 'edson102'
DF_PASS      = '123456789'
DF_PIN       = '162738'

# ── Firestore ─────────────────────────────────────────────────────────────────
def fs_req(path, method='GET', body=None):
    url  = f'{FS_BASE}/{path}?key={FK}'
    data = json.dumps(body).encode() if body else None
    req  = urllib.request.Request(url, data=data,
           headers={'Content-Type': 'application/json'}, method=method)
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())

def fs_query_processing():
    url  = f'{FS_BASE}:runQuery?key={FK}'
    body = {'structuredQuery': {
        'from': [{'collectionId': 'orders'}],
        'where': {'fieldFilter': {
            'field': {'fieldPath': 'status'},
            'op': 'EQUAL',
            'value': {'stringValue': 'processing'}
        }},
        'limit': 5
    }}
    req = urllib.request.Request(url,
          data=json.dumps(body).encode(),
          headers={'Content-Type': 'application/json'}, method='POST')
    with urllib.request.urlopen(req, timeout=10) as r:
        docs = json.loads(r.read())
    orders = []
    for d in docs:
        if 'document' not in d:
            continue
        f   = d['document']['fields']
        oid = d['document']['name'].split('/')[-1]
        g   = lambda k: (f.get(k) or {}).get('stringValue') or (f.get(k) or {}).get('integerValue') or ''
        orders.append({'id': oid, 'productId': g('productId'),
                       'productName': g('productName'), 'queryData': g('queryData')})
    return orders

def fs_save(order_id, result_text):
    now    = datetime.now(timezone.utc).isoformat()
    path   = f'orders/{order_id}'
    fields = ['status', 'result', 'deliveredAt']
    mask   = '&'.join(f'updateMask.fieldPaths={f}' for f in fields)
    url    = f'{FS_BASE}/{path}?key={FK}&{mask}'
    body   = {'fields': {
        'status':      {'stringValue': 'done'},
        'result':      {'stringValue': result_text},
        'deliveredAt': {'stringValue': now},
    }}
    req = urllib.request.Request(url, data=json.dumps(body).encode(),
          headers={'Content-Type': 'application/json'}, method='PATCH')
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())

def fs_error(order_id, msg):
    path  = f'orders/{order_id}'
    mask  = 'updateMask.fieldPaths=status&updateMask.fieldPaths=result'
    url   = f'{FS_BASE}/{path}?key={FK}&{mask}'
    body  = {'fields': {
        'status': {'stringValue': 'error'},
        'result': {'stringValue': f'Erro no processamento: {msg[:300]}\n\nSuporte: +55 68 98101-4570'},
    }}
    req = urllib.request.Request(url, data=json.dumps(body).encode(),
          headers={'Content-Type': 'application/json'}, method='PATCH')
    with urllib.request.urlopen(req, timeout=10) as r:
        r.read()

def ntfy_poll():
    try:
        req = urllib.request.Request(
            f'{NTFY}/json?poll=1&since=2m',
            headers={'Accept': 'application/json'})
        with urllib.request.urlopen(req, timeout=8) as r:
            raw = r.read().decode()
        ids = []
        for line in raw.strip().split('\n'):
            if not line: continue
            try:
                msg = json.loads(line)
                if msg.get('event') == 'message':
                    ids.append(msg.get('message', '').strip())
            except: pass
        return ids
    except:
        return []

# ── Anti-bot bypass ───────────────────────────────────────────────────────────
# O site detecta webdriver via scripts de proteção e redireciona para google.com
# Bloqueamos os scripts suspeitos via page.route() e injetamos overrides via addInitScript

ANTI_BOT_BLOCK_PATTERNS = [
    # Padrões de scripts de proteção conhecidos
    '**/anti-bot**',
    '**/bot-detect**',
    '**/fingerprintjs**',
    '**/fp.js**',
    '**/_next/static/chunks/bot**',
]

INIT_SCRIPT = """
// Remove webdriver flag
Object.defineProperty(navigator, 'webdriver', { get: () => false });

// Override automation-related chrome properties
if (window.chrome) {
    window.chrome.runtime = {};
}

// Bloqueia window.location redirect para domínios externos (ex: google.com)
// mantendo redirects internos (detetiveforense.com)
(function() {
    const originalDescriptor = Object.getOwnPropertyDescriptor(window, 'location');
    let _blocked = false;
    
    const handler = {
        get(target, prop) {
            return target[prop];
        },
        set(target, prop, value) {
            if (prop === 'href' && value && !value.includes('detetiveforense.com') && !value.startsWith('/')) {
                console.warn('[ACHAQUI] Redirect bloqueado para:', value);
                return true; // ignora redirect externo
            }
            target[prop] = value;
            return true;
        }
    };
    
    // Sobrescreve window.open para bloqueio de popups externos
    const origOpen = window.open;
    window.open = function(url, ...args) {
        if (url && !url.includes('detetiveforense.com')) {
            console.warn('[ACHAQUI] window.open bloqueado:', url);
            return null;
        }
        return origOpen.apply(this, [url, ...args]);
    };
    
    // DevTools detection bypass
    const noop = function(){};
    console.clear = noop;
    
    // Bloqueia tentativas de detect via performance timing
    const origNow = performance.now.bind(performance);
    let _lastNow = 0;
    performance.now = function() {
        const now = origNow();
        _lastNow = now;
        return now;
    };
})();
"""

async def setup_page_bypass(page):
    """Configura bypass anti-bot na página."""
    # Injeta script antes de qualquer JS do site
    await page.add_init_script(INIT_SCRIPT)

    # Intercepta e bloqueia scripts de detecção conhecidos
    async def route_handler(route):
        url = route.request.url
        # Bloqueia recursos externos suspeitos de anti-bot
        suspicious = any(p in url.lower() for p in [
            'fingerprintjs', 'fp.min.js', 'botd.js', 'challenge',
            'cf-challenge', 'antibot', 'bot-detect'
        ])
        if suspicious:
            print(f'[Bypass] Bloqueado: {url[:80]}')
            await route.abort()
        else:
            await route.continue_()

    await page.route('**/*', route_handler)
    print('[Bypass] Interceptor de rotas configurado')

# ── Login ─────────────────────────────────────────────────────────────────────
async def login_detetive(page):
    print('[Login] Iniciando...')

    # Configura bypass antes de navegar
    await setup_page_bypass(page)

    try:
        resp = await page.goto(DF_LOGIN_URL, wait_until='domcontentloaded', timeout=25000)
        print(f'[Login] goto: status={resp.status if resp else "?"}, URL={page.url}')
    except Exception as e:
        print(f'[Login] goto erro: {e}')

    # Aguarda estabilizar (JS roda depois do domcontentloaded)
    await page.wait_for_timeout(3000)

    current_url = page.url
    title       = await page.title()
    print(f'[Login] Apos espera: URL={current_url} | Titulo={title}')

    # Pega conteúdo para debug
    try:
        body = await page.inner_text('body')
        print(f'[Login] Body (300c): {body[:300]}')
    except:
        pass

    # Se foi redirecionado para fora do site
    if 'detetiveforense' not in current_url:
        print(f'[Login] REDIRECIONADO para fora: {current_url}. Tentando de novo...')
        # Segunda tentativa sem o route handler (pode ter interferido)
        try:
            await page.unroute('**/*')
        except: pass
        await page.add_init_script(INIT_SCRIPT)
        try:
            await page.goto(DF_LOGIN_URL, wait_until='load', timeout=30000)
            await page.wait_for_timeout(3000)
            current_url = page.url
            print(f'[Login] Tentativa 2: URL={current_url}')
        except Exception as e:
            print(f'[Login] Tentativa 2 erro: {e}')

    if 'detetiveforense' not in current_url:
        print('[Login] Falha total — site rejeitou a conexão')
        return False

    # Já logado?
    if '/app/' in current_url or '/dashboard' in current_url:
        print('[Login] Ja autenticado!')
        return True

    # Preenche formulário
    try:
        await page.wait_for_selector(
            'input[placeholder="Digite seu usuário"], input[placeholder="Digite seu usuario"]',
            timeout=12000)
        print('[Login] Formulario encontrado!')

        await page.fill('input[placeholder="Digite seu usuário"], input[placeholder="Digite seu usuario"]', DF_USER)
        await page.wait_for_timeout(400)
        await page.fill('input[placeholder="Digite sua senha"]', DF_PASS)
        await page.wait_for_timeout(400)
        await page.click('button:has-text("Entrar")')
        await page.wait_for_timeout(4000)

        # PIN
        try:
            pin = await page.query_selector('input[maxlength="6"], input[placeholder*="PIN"], input[placeholder*="pin"]')
            if pin:
                print('[Login] PIN solicitado...')
                await pin.fill(DF_PIN)
                await page.keyboard.press('Enter')
                await page.wait_for_timeout(3000)
        except: pass

        print(f'[Login] Apos submit: {page.url}')
        return True

    except Exception as e:
        print(f'[Login] Formulario nao encontrado: {e}')
        # Screenshot para debug
        try:
            await page.screenshot(path='/tmp/login_debug.png', full_page=True)
            print('[Login] Screenshot salvo em /tmp/login_debug.png')
        except: pass
        return False

# ── Consulta ──────────────────────────────────────────────────────────────────
async def consultar(page, modulo, query_data):
    url = f'{DF_BASE}/app/modulos/{modulo}'
    print(f'[Consulta] Navegando para {url}')
    await page.goto(url, wait_until='domcontentloaded', timeout=30000)
    await page.wait_for_timeout(2000)

    if 'detetiveforense' not in page.url:
        print(f'[Consulta] Redirecionado para {page.url}, relogando...')
        ok = await login_detetive(page)
        if ok:
            await page.goto(url, wait_until='domcontentloaded', timeout=30000)
            await page.wait_for_timeout(2000)

    print(f'[Consulta] URL apos nav: {page.url}')

    input_sel = 'input[placeholder], input[type="text"], input[type="search"]'
    await page.wait_for_selector(input_sel, timeout=15000)
    inputs = await page.query_selector_all(input_sel)
    if inputs:
        await inputs[0].fill(query_data)
        await page.wait_for_timeout(500)
        try:
            btn = await page.query_selector(
                'button[type="submit"], button:has-text("Buscar"), button:has-text("Pesquisar"), button:has-text("Consultar")')
            if btn:
                await btn.click()
            else:
                await inputs[0].press('Enter')
        except:
            await inputs[0].press('Enter')

    await page.wait_for_timeout(6000)

    try:
        result = await page.inner_text('main, [class*="result"], [class*="card"], article, .container')
        return result
    except:
        return await page.inner_text('body')

def formatar(raw, product_name, query_data):
    now   = datetime.now().strftime('%d/%m/%Y às %H:%M')
    linhas = [l.strip() for l in raw.split('\n') if l.strip()]
    skip  = ['Copiar Dados', 'Exportar PDF', 'Adicionar em', 'Fechar',
             'Buscar Mandados', 'Validar Foto', 'Galeria de Fotos',
             '100% gratuito', 'Busca em tempo real', 'caráter histórico']
    linhas = [l for l in linhas if not any(s in l for s in skip)]
    return (
        f'🔍 RELATÓRIO DE CONSULTA — ACHAQUI\n'
        f'━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n'
        f'📋 Produto: {product_name}\n'
        f'🔎 Dado consultado: {query_data}\n'
        f'📅 Data: {now}\n'
        f'━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n'
        + '\n'.join(linhas[:80])
    )

# ── Main loop ─────────────────────────────────────────────────────────────────
async def main():
    print('[Achaqui Worker] Iniciando...')

    # Diagnóstico de rede
    try:
        req = urllib.request.Request(DF_LOGIN_URL,
              headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=10) as r:
            print(f'[Diag] HTTP OK: {r.status}')
    except Exception as e:
        print(f'[Diag] HTTP ERRO: {e}')

    processed  = set()
    start_time = time.time()

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            args=[
                '--no-sandbox',
                '--disable-dev-shm-usage',
                '--disable-blink-features=AutomationControlled',
                '--window-size=1280,800',
            ]
        )
        context = await browser.new_context(
            user_agent='Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
                       '(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36',
            viewport={'width': 1280, 'height': 800},
            locale='pt-BR',
            timezone_id='America/Sao_Paulo',
        )

        if HAS_STEALTH:
            await Stealth(
                navigator_webdriver=True,
                chrome_runtime=True,
            ).apply_stealth_async(context)
            print('[Worker] Stealth aplicado')

        page = await context.new_page()
        print('[Worker] Chrome OK')

        # Login inicial
        print('[Worker] Fazendo login...')
        try:
            ok = await login_detetive(page)
            print(f'[Worker] Login: {"OK" if ok else "FALHOU"}')
        except Exception as e:
            print(f'[Worker] Login erro: {e}')

        last_fs   = 0
        last_ntfy = 0

        while True:
            now = time.time()
            if RUN_DURATION > 0 and (now - start_time) >= RUN_DURATION:
                print(f'[Worker] {RUN_DURATION}s atingido. Encerrando.')
                break

            orders = []

            if now - last_fs >= POLL_SEC:
                last_fs = now
                try:
                    orders += fs_query_processing()
                except Exception as e:
                    print(f'[Worker] Firestore erro: {e}')

            if now - last_ntfy >= NTFY_SEC:
                last_ntfy = now
                try:
                    for oid in ntfy_poll():
                        if oid and oid not in processed:
                            try:
                                doc = fs_req(f'orders/{oid}')
                                f   = doc.get('fields', {})
                                g   = lambda k: (f.get(k) or {}).get('stringValue') or ''
                                if g('status') != 'done':
                                    orders.append({
                                        'id': oid, 'productId': g('productId'),
                                        'productName': g('productName'), 'queryData': g('queryData'),
                                    })
                            except: pass
                except Exception as e:
                    print(f'[Worker] ntfy erro: {e}')

            for order in orders:
                oid = order['id']
                if oid in processed:
                    continue
                # Verifica se ainda está pendente
                try:
                    doc = fs_req(f'orders/{oid}')
                    g   = lambda k: (doc.get('fields', {}).get(k) or {}).get('stringValue') or ''
                    if g('status') == 'done':
                        processed.add(oid)
                        continue
                except Exception as e:
                    print(f'[Worker] Verificação {oid} erro: {e}')
                    continue

                print(f'[Worker] Processando {oid}: {order["productId"]} / {order["queryData"]}')
                processed.add(oid)

                try:
                    raw = await consultar(page, order['productId'], order['queryData'])
                    if not raw or len(raw) < 50:
                        await login_detetive(page)
                        raw = await consultar(page, order['productId'], order['queryData'])
                    resultado = formatar(raw, order.get('productName', order['productId']), order['queryData'])
                    fs_save(oid, resultado)
                    print(f'[Worker] OK {oid} ({len(resultado)} chars)')
                except Exception as e:
                    print(f'[Worker] ERRO {oid}: {e}')
                    try: fs_error(oid, str(e)[:200])
                    except: pass
                    try: await login_detetive(page)
                    except: pass

            await asyncio.sleep(NTFY_SEC)

asyncio.run(main())
