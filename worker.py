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
NTFY_URL     = 'https://ntfy.sh/achaqui-zapia-guga-secret-2025'
POLL_SEC     = 20
NTFY_SEC     = 5
RUN_DURATION = int(os.environ.get('RUN_DURATION', '0'))

DF_BASE      = 'https://detetiveforense.com'
DF_LOGIN_URL = f'{DF_BASE}/auth/login'
DF_USER      = 'edson102'
DF_PASS      = '123456789'
DF_PIN       = '162738'

# Chunk da lib disable-devtool — retornar vazio impede o redirect anti-bot
DISABLE_DEVTOOL_CHUNK = '0888c0b2fc92ae80.js'
# Override: módulo Turbopack válido mas que exporta função vazia
DEVTOOL_OVERRIDE_JS = (
    '(globalThis.TURBOPACK||(globalThis.TURBOPACK=[])).push(['
    '"object"==typeof document?document.currentScript:void 0,'
    '98226,(e,t,n)=>{t.exports=function(){return null}}]);'
)

# ── Firestore helpers ─────────────────────────────────────────────────────────
def fs_get(path):
    url = f'{FS_BASE}/{path}?key={FK}'
    req = urllib.request.Request(url, headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())

def fs_patch(path, fields, field_names):
    mask = '&'.join(f'updateMask.fieldPaths={f}' for f in field_names)
    url  = f'{FS_BASE}/{path}?key={FK}&{mask}'
    body = json.dumps({'fields': fields}).encode()
    req  = urllib.request.Request(url, data=body,
           headers={'Content-Type': 'application/json'}, method='PATCH')
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())

def fs_query_processing():
    url  = f'{FS_BASE}:runQuery?key={FK}'
    body = json.dumps({'structuredQuery': {
        'from': [{'collectionId': 'orders'}],
        'where': {'fieldFilter': {
            'field': {'fieldPath': 'status'},
            'op': 'EQUAL',
            'value': {'stringValue': 'processing'}
        }},
        'limit': 5
    }}).encode()
    req = urllib.request.Request(url, data=body,
          headers={'Content-Type': 'application/json'}, method='POST')
    with urllib.request.urlopen(req, timeout=10) as r:
        docs = json.loads(r.read())
    orders = []
    for d in docs:
        if 'document' not in d:
            continue
        f   = d['document']['fields']
        oid = d['document']['name'].split('/')[-1]
        g   = lambda k: (f.get(k) or {}).get('stringValue') or ''
        orders.append({
            'id':          oid,
            'productId':   g('productId'),
            'productName': g('productName'),
            'queryData':   g('queryData'),
        })
    return orders

def fs_save(order_id, result_text):
    now = datetime.now(timezone.utc).isoformat()
    fs_patch(f'orders/{order_id}',
        {'status': {'stringValue': 'done'},
         'result': {'stringValue': result_text},
         'deliveredAt': {'stringValue': now}},
        ['status', 'result', 'deliveredAt'])

def fs_error(order_id, msg):
    fs_patch(f'orders/{order_id}',
        {'status': {'stringValue': 'error'},
         'result': {'stringValue': f'⚠️ Erro: {msg[:200]}\nSuporte: +55 68 98101-4570'}},
        ['status', 'result'])

def ntfy_poll():
    try:
        url = f'{NTFY_URL}/json?poll=1&since=2m'
        req = urllib.request.Request(url, headers={'Accept': 'application/json'})
        with urllib.request.urlopen(req, timeout=8) as r:
            raw = r.read().decode()
        ids = []
        for line in raw.strip().split('\n'):
            if not line:
                continue
            try:
                msg = json.loads(line)
                if msg.get('event') == 'message':
                    ids.append(msg.get('message', '').strip())
            except:
                pass
        return ids
    except:
        return []

# ── Diagnóstico de rede ───────────────────────────────────────────────────────
def diag_network():
    try:
        req = urllib.request.Request(DF_LOGIN_URL,
              headers={'User-Agent': 'Mozilla/5.0 Chrome/125'})
        with urllib.request.urlopen(req, timeout=10) as r:
            size = len(r.read())
        print(f'[Diag] HTTP OK: {r.status}, {size}b')
    except Exception as e:
        print(f'[Diag] HTTP ERRO: {e}')

# ── Login via Playwright ──────────────────────────────────────────────────────
async def setup_context(pw):
    """Cria contexto com stealth + bloqueio do DevtoolDisabler."""
    browser = await pw.chromium.launch(
        headless=True,
        args=[
            '--no-sandbox',
            '--disable-dev-shm-usage',
            '--disable-blink-features=AutomationControlled',
            '--disable-features=IsolateOrigins,site-per-process',
        ]
    )
    context = await browser.new_context(
        user_agent=(
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
            'AppleWebKit/537.36 (KHTML, like Gecko) '
            'Chrome/125.0.0.0 Safari/537.36'
        ),
        viewport={'width': 1280, 'height': 800},
        locale='pt-BR',
        timezone_id='America/Sao_Paulo',
    )

    if HAS_STEALTH:
        await Stealth().apply_stealth_async(context)
        print('[Setup] Stealth aplicado')

    # BLOQUEIA o chunk do DevtoolDisabler — retorna módulo vazio
    async def block_devtool(route):
        if DISABLE_DEVTOOL_CHUNK in route.request.url:
            print(f'[Bypass] Bloqueando DevtoolDisabler: {route.request.url}')
            await route.fulfill(
                status=200,
                content_type='application/javascript',
                body=DEVTOOL_OVERRIDE_JS
            )
        else:
            await route.continue_()

    await context.route('**/*.js', block_devtool)

    # Script injetado antes de qualquer JS: protege window.location
    await context.add_init_script("""
        (function() {
            // Sobrescreve location.href setter para bloquear redirects externos
            const allowedHosts = ['detetiveforense.com'];
            const _href_desc = Object.getOwnPropertyDescriptor(window.Location.prototype, 'href');
            
            Object.defineProperty(window.Location.prototype, 'href', {
                get: function() { return _href_desc.get.call(this); },
                set: function(val) {
                    try {
                        const host = new URL(val, window.location.href).hostname;
                        if (!allowedHosts.some(h => host.endsWith(h))) {
                            console.warn('[Achaqui] Redirect bloqueado para: ' + val);
                            return;
                        }
                    } catch(e) {}
                    _href_desc.set.call(this, val);
                },
                configurable: true
            });

            // Bloqueia window.location.replace e assign
            const origReplace = window.Location.prototype.replace;
            window.Location.prototype.replace = function(url) {
                try {
                    const host = new URL(url, window.location.href).hostname;
                    if (!['detetiveforense.com'].some(h => host.endsWith(h))) {
                        console.warn('[Achaqui] replace bloqueado: ' + url);
                        return;
                    }
                } catch(e) {}
                return origReplace.call(this, url);
            };

            const origAssign = window.Location.prototype.assign;
            window.Location.prototype.assign = function(url) {
                try {
                    const host = new URL(url, window.location.href).hostname;
                    if (!['detetiveforense.com'].some(h => host.endsWith(h))) {
                        console.warn('[Achaqui] assign bloqueado: ' + url);
                        return;
                    }
                } catch(e) {}
                return origAssign.call(this, url);
            };

            // Bloqueia window.history.back() que o disable-devtool usa
            const origBack = window.history.back.bind(window.history);
            window.history.back = function() {
                console.warn('[Achaqui] history.back() bloqueado');
            };
        })();
    """)

    return browser, context

async def login_detetive(page):
    print('[Login] Navegando...')
    try:
        resp = await page.goto(DF_LOGIN_URL, wait_until='domcontentloaded', timeout=25000)
        print(f'[Login] goto: status={resp.status if resp else "?"}, URL={page.url}')
    except Exception as e:
        print(f'[Login] goto erro: {e}')

    # Aguarda o React montar (sem esperar muito — o DevtoolDisabler seria carregado depois)
    await page.wait_for_timeout(3000)

    url = page.url
    print(f'[Login] URL após 3s: {url}')

    if url == 'about:blank' or 'detetiveforense' not in url:
        print('[Login] AINDA em about:blank — tentando novamente com espera maior')
        try:
            resp = await page.goto(DF_LOGIN_URL, wait_until='domcontentloaded', timeout=25000)
            await page.wait_for_timeout(4000)
            url = page.url
            print(f'[Login] 2a tentativa URL: {url}')
        except Exception as e:
            print(f'[Login] 2a tentativa erro: {e}')

    if 'detetiveforense' not in page.url:
        print('[Login] FALHA: não conseguiu navegar para o site')
        return False

    # Se já está logado
    if '/app/' in page.url:
        print('[Login] Já autenticado!')
        return True

    # Preenche formulário
    try:
        await page.wait_for_selector('input[placeholder="Digite seu usuário"]', timeout=12000)
        print('[Login] Formulário encontrado!')
        await page.fill('input[placeholder="Digite seu usuário"]', DF_USER)
        await page.wait_for_timeout(400)
        await page.fill('input[placeholder="Digite sua senha"]', DF_PASS)
        await page.wait_for_timeout(400)
        await page.click('button:has-text("Entrar")')
        await page.wait_for_timeout(3000)
        print(f'[Login] Após submit: {page.url}')

        # PIN se necessário
        try:
            pin = await page.query_selector('input[maxlength="6"]')
            if pin:
                print('[Login] PIN solicitado...')
                await pin.fill(DF_PIN)
                await page.keyboard.press('Enter')
                await page.wait_for_timeout(2000)
                print(f'[Login] Após PIN: {page.url}')
        except:
            pass

        return '/app/' in page.url or 'detetiveforense' in page.url

    except Exception as e:
        print(f'[Login] Formulário não encontrado: {e}')
        try:
            body = await page.inner_text('body')
            print(f'[Login] Conteúdo: {body[:300]}')
        except:
            pass
        return False

# ── Consulta ──────────────────────────────────────────────────────────────────
async def consultar(page, modulo, query_data):
    url = f'{DF_BASE}/app/modulos/{modulo}'
    await page.goto(url, wait_until='domcontentloaded', timeout=30000)
    await page.wait_for_timeout(2000)
    print(f'[Consulta] URL: {page.url}')

    # Campo de busca
    try:
        await page.wait_for_selector('input[placeholder], input[type="text"]', timeout=12000)
        inputs = await page.query_selector_all('input[placeholder], input[type="text"]')
        if inputs:
            await inputs[0].fill(query_data)
            await page.wait_for_timeout(400)
            btn = await page.query_selector('button[type="submit"], button:has-text("Buscar"), button:has-text("Consultar"), button:has-text("Pesquisar")')
            if btn:
                await btn.click()
            else:
                await inputs[0].press('Enter')
    except Exception as e:
        print(f'[Consulta] Erro campo busca: {e}')

    await page.wait_for_timeout(6000)

    # Coleta resultado
    try:
        result = await page.inner_text('main')
    except:
        try:
            result = await page.inner_text('body')
        except:
            result = ''

    return result

def formatar(raw, product_name, query_data):
    now = datetime.now().strftime('%d/%m/%Y às %H:%M')
    skip = ['Copiar Dados', 'Exportar PDF', 'Adicionar em', 'Fechar', 'Buscar Mandados',
            'Validar Foto', 'Galeria de Fotos', '100% gratuito', 'caráter histórico']
    linhas = [l.strip() for l in raw.split('\n')
              if l.strip() and not any(s in l for s in skip)]
    out  = f'🔍 RELATÓRIO — ACHAQUI\n'
    out += f'━━━━━━━━━━━━━━━━━━━━━━━━━━━\n'
    out += f'📋 Produto: {product_name}\n'
    out += f'🔎 Consulta: {query_data}\n'
    out += f'📅 Data: {now}\n'
    out += f'━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n'
    out += '\n'.join(linhas[:80])
    return out

# ── Main ──────────────────────────────────────────────────────────────────────
async def main():
    print('[Achaqui Worker] Iniciando...')
    diag_network()

    processed = set()
    start = time.time()

    async with async_playwright() as pw:
        browser, context = await setup_context(pw)
        page = await context.new_page()
        page.on('console', lambda m: print(f'[JS {m.type}] {m.text[:100]}') if m.type in ('warning', 'error') else None)
        print('[Worker] Chrome OK')

        # Login inicial
        print('[Worker] Fazendo login...')
        logged = await login_detetive(page)
        print(f'[Worker] Login: {"OK" if logged else "FALHOU"}')

        last_fs   = 0
        last_ntfy = 0

        while True:
            now = time.time()
            if RUN_DURATION > 0 and (now - start) >= RUN_DURATION:
                print(f'[Worker] {RUN_DURATION}s atingido. Encerrando.')
                break

            queue = []

            # Poll Firestore a cada 20s
            if now - last_fs >= POLL_SEC:
                last_fs = now
                try:
                    queue += fs_query_processing()
                except Exception as e:
                    print(f'[Worker] Firestore erro: {e}')

            # Poll ntfy a cada 5s
            if now - last_ntfy >= NTFY_SEC:
                last_ntfy = now
                try:
                    for oid in ntfy_poll():
                        if oid and oid not in processed:
                            try:
                                doc = fs_get(f'orders/{oid}')
                                f   = doc.get('fields', {})
                                g   = lambda k: (f.get(k) or {}).get('stringValue') or ''
                                if g('status') != 'done':
                                    queue.append({'id': oid, 'productId': g('productId'),
                                                  'productName': g('productName'), 'queryData': g('queryData')})
                            except Exception as e:
                                print(f'[Worker] ntfy order erro: {e}')
                except Exception as e:
                    print(f'[Worker] ntfy erro: {e}')

            for order in queue:
                oid = order['id']
                if oid in processed:
                    continue
                processed.add(oid)

                # Verifica se já processado
                try:
                    doc = fs_get(f'orders/{oid}')
                    if (doc.get('fields', {}).get('status') or {}).get('stringValue') == 'done':
                        continue
                except:
                    pass

                print(f'[Worker] Processando {oid}: {order["productId"]} / {order["queryData"]}')
                try:
                    # Re-login se necessário
                    if 'detetiveforense' not in page.url:
                        await login_detetive(page)

                    raw = await consultar(page, order['productId'], order['queryData'])
                    if not raw or len(raw) < 30:
                        print(f'[Worker] Resultado vazio, re-logando...')
                        await login_detetive(page)
                        raw = await consultar(page, order['productId'], order['queryData'])

                    resultado = formatar(raw, order.get('productName', order['productId']), order['queryData'])
                    fs_save(oid, resultado)
                    print(f'[Worker] OK {oid} ({len(resultado)} chars)')

                except Exception as e:
                    print(f'[Worker] ERRO {oid}: {e}')
                    try:
                        fs_error(oid, str(e))
                    except:
                        pass
                    try:
                        await login_detetive(page)
                    except:
                        pass

            await asyncio.sleep(NTFY_SEC)

asyncio.run(main())
