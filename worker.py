"""
Achaqui Worker — processa pedidos do Firestore via Playwright
Roda em loop no GitHub Actions (5min cron), 24/7 via cron scheduler
"""

import asyncio
import json
import os
import re
import subprocess
import time
import urllib.request
import urllib.parse
from datetime import datetime, timezone
from playwright.async_api import async_playwright

try:
    from playwright_stealth import Stealth
    HAS_STEALTH = True
except ImportError:
    HAS_STEALTH = False
    print("[Worker] playwright-stealth nao disponivel")

try:
    from curl_cffi import requests as cf_requests
    HAS_CURL_CFFI = True
except ImportError:
    HAS_CURL_CFFI = False

# ── Config ──────────────────────────────────────────────────────────────────
FK       = os.environ.get('FIREBASE_API_KEY', 'AIzaSyAoZYnDTl8WoCG5K3q6hjFQnVFmkAS6PZ8')
FS_BASE  = 'https://firestore.googleapis.com/v1/projects/bancadamatriz-9f797/databases/(default)/documents'
NTFY     = 'https://ntfy.sh/achaqui-zapia-guga-secret-2025'
POLL_SEC = 20
NTFY_SEC = 5
RUN_DURATION = int(os.environ.get("RUN_DURATION", "0"))

DF_LOGIN_URL = 'https://detetiveforense.com/auth/login'
DF_BASE      = 'https://detetiveforense.com'
DF_USER      = 'edson102'
DF_PASS      = '123456789'
DF_PIN       = '162738'

# ── Diagnóstico de rede ──────────────────────────────────────────────────────
def diagnostico_rede():
    print("[Diag] Testando acesso a detetiveforense.com...")
    try:
        req = urllib.request.Request(
            DF_LOGIN_URL,
            headers={'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) Chrome/124.0.0.0 Safari/537.36'}
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            print(f"[Diag] urllib OK: status={r.status}, size={len(r.read())}b")
            return True
    except Exception as e:
        print(f"[Diag] urllib FALHOU: {e}")

    try:
        result = subprocess.run(
            ['curl', '-sv', '--max-time', '10', DF_LOGIN_URL],
            capture_output=True, text=True, timeout=15
        )
        lines = result.stderr.split('\n')
        for l in lines:
            if any(w in l for w in ['< HTTP', 'connected', 'SSL', 'error', 'Could not']):
                print(f"[Diag] curl: {l.strip()}")
        return result.returncode == 0
    except Exception as e:
        print(f"[Diag] curl falhou: {e}")
    return False

# ── Firestore helpers ────────────────────────────────────────────────────────
def fs_request(path, method='GET', body=None):
    url = f'{FS_BASE}/{path}?key={FK}'
    data = json.dumps(body).encode() if body else None
    req  = urllib.request.Request(url, data=data,
            headers={'Content-Type': 'application/json'}, method=method)
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())

def fs_query_processing():
    url = f'{FS_BASE}:runQuery?key={FK}'
    body = {"structuredQuery": {
        "from": [{"collectionId": "orders"}],
        "where": {"fieldFilter": {
            "field": {"fieldPath": "status"},
            "op": "EQUAL",
            "value": {"stringValue": "processing"}
        }},
        "limit": 5
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
        orders.append({
            'id':          oid,
            'productId':   g('productId'),
            'productName': g('productName'),
            'queryData':   g('queryData'),
        })
    return orders

def fs_save_result(order_id, result_text):
    now = datetime.now(timezone.utc).isoformat()
    path = f'orders/{order_id}'
    fields = ['status', 'result', 'deliveredAt']
    mask = '&'.join(f'updateMask.fieldPaths={f}' for f in fields)
    url  = f'{FS_BASE}/{path}?key={FK}&{mask}'
    body = {"fields": {
        "status":      {"stringValue": "done"},
        "result":      {"stringValue": result_text},
        "deliveredAt": {"stringValue": now},
    }}
    req = urllib.request.Request(url,
          data=json.dumps(body).encode(),
          headers={'Content-Type': 'application/json'}, method='PATCH')
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())

def fs_mark_error(order_id, msg):
    path  = f'orders/{order_id}'
    fields = ['status', 'result']
    mask  = '&'.join(f'updateMask.fieldPaths={f}' for f in fields)
    url   = f'{FS_BASE}/{path}?key={FK}&{mask}'
    body  = {"fields": {
        "status": {"stringValue": "error"},
        "result": {"stringValue": f"Erro no processamento: {msg}\n\nContato suporte: +55 68 98101-4570"},
    }}
    req = urllib.request.Request(url,
          data=json.dumps(body).encode(),
          headers={'Content-Type': 'application/json'}, method='PATCH')
    with urllib.request.urlopen(req, timeout=10) as r:
        r.read()

def ntfy_poll():
    try:
        url = f'{NTFY}/json?poll=1&since=2m'
        req = urllib.request.Request(url, headers={'Accept': 'application/json'})
        with urllib.request.urlopen(req, timeout=8) as r:
            raw = r.read().decode()
        ids = []
        for line in raw.strip().split('\n'):
            if not line: continue
            try:
                msg = json.loads(line)
                if msg.get('event') == 'message':
                    ids.append(msg.get('message','').strip())
            except: pass
        return ids
    except:
        return []

# ── Login via Playwright ─────────────────────────────────────────────────────
async def login_detetive(page):
    print('[Login] Tentando navegar para o login...')

    # Tenta múltiplas vezes com espera progressiva
    for attempt in range(4):
        try:
            await page.goto(DF_LOGIN_URL, wait_until='load', timeout=25000)
        except Exception as e:
            print(f'[Login] goto tentativa {attempt+1} erro: {e}')

        await page.wait_for_timeout(2000 + attempt * 1000)
        current_url = page.url

        if current_url != 'about:blank' and 'detetiveforense' in current_url:
            print(f'[Login] Pagina carregada! URL: {current_url}')
            break

        print(f'[Login] Tentativa {attempt+1}: URL={current_url}, aguardando...')

        # Tenta navegar diretamente para o IP ou via HTTP primeiro
        if attempt == 1:
            try:
                await page.goto('https://detetiveforense.com/', wait_until='domcontentloaded', timeout=15000)
                await page.wait_for_timeout(2000)
                await page.goto(DF_LOGIN_URL, wait_until='domcontentloaded', timeout=15000)
            except Exception as e:
                print(f'[Login] Fallback homepage erro: {e}')

    current_url = page.url
    title = await page.title()
    print(f'[Login] Estado final: URL={current_url} | Titulo={title}')

    # Log do conteudo
    try:
        body_text = await page.inner_text('body')
        print(f'[Login] Body: {body_text[:300]}')
    except:
        pass

    # Tira screenshot para debug
    try:
        await page.screenshot(path='/tmp/login_debug.png', full_page=True)
        print('[Login] Screenshot salvo em /tmp/login_debug.png')
    except:
        pass

    if current_url == 'about:blank':
        print('[Login] FALHA: pagina nao carregou. Site pode estar bloqueando o Playwright.')
        return False

    # Se ja logado
    if '/app/' in current_url or '/dashboard' in current_url:
        print('[Login] Ja autenticado!')
        return True

    # Preenche formulario
    try:
        await page.wait_for_selector('input[placeholder="Digite seu usuario"]', timeout=10000)
        print('[Login] Formulario encontrado!')
        await page.fill('input[placeholder="Digite seu usuario"]', DF_USER)
        await page.wait_for_timeout(400)
        await page.fill('input[placeholder="Digite sua senha"]', DF_PASS)
        await page.wait_for_timeout(400)
        await page.click('button:has-text("Entrar")')
        await page.wait_for_timeout(4000)

        # PIN
        try:
            pin = await page.query_selector('input[maxlength="6"]')
            if not pin:
                pin = await page.query_selector('input[placeholder*="PIN"], input[placeholder*="pin"]')
            if pin:
                print('[Login] PIN solicitado!')
                await pin.fill(DF_PIN)
                await page.keyboard.press('Enter')
                await page.wait_for_timeout(3000)
        except:
            pass

        print(f'[Login] Apos submit: {page.url}')
        return True
    except Exception as e:
        print(f'[Login] Formulario nao encontrado: {e}')
        return False

# ── Consulta ─────────────────────────────────────────────────────────────────
async def consultar(page, modulo, query_data):
    url = f'https://detetiveforense.com/app/modulos/{modulo}'
    await page.goto(url, wait_until='domcontentloaded', timeout=30000)
    await page.wait_for_timeout(2000)
    print(f'[Consulta] URL: {page.url}')

    # Se redirecionou para login, faz login novamente
    if 'auth/login' in page.url:
        await login_detetive(page)
        await page.goto(url, wait_until='domcontentloaded', timeout=30000)
        await page.wait_for_timeout(2000)

    input_sel = 'input[placeholder], input[type="text"], input[type="search"]'
    await page.wait_for_selector(input_sel, timeout=15000)
    inputs = await page.query_selector_all(input_sel)
    if inputs:
        await inputs[0].fill(query_data)
        await page.wait_for_timeout(500)
        try:
            btn = await page.query_selector('button[type="submit"], button:has-text("Buscar"), button:has-text("Pesquisar"), button:has-text("Consultar")')
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

def formatar_resultado(raw_text, product_name, query_data):
    now = datetime.now().strftime('%d/%m/%Y as %H:%M')
    linhas = [l.strip() for l in raw_text.split('\n') if l.strip()]
    skip_words = ['Copiar Dados', 'Exportar PDF', 'Adicionar em', 'Fechar', 'Buscar Mandados',
                  'Validar Foto', 'Galeria de Fotos', '100% gratuito', 'Busca em tempo real']
    linhas = [l for l in linhas if not any(s in l for s in skip_words)]

    out  = 'RELATORIO DE CONSULTA - ACHAQUI\n'
    out += '=' * 35 + '\n'
    out += f'Produto: {product_name}\n'
    out += f'Dado consultado: {query_data}\n'
    out += f'Data: {now}\n'
    out += '=' * 35 + '\n\n'
    out += '\n'.join(linhas[:80])
    return out

# ── Main ─────────────────────────────────────────────────────────────────────
async def main():
    print('[Achaqui Worker] Iniciando...')

    # Diagnóstico de rede
    diagnostico_rede()

    processed = set()

    print('[Worker] Iniciando Playwright...')
    async with async_playwright() as pw:
        # Configurações de browser mais stealth
        browser = await pw.chromium.launch(
            headless=True,
            args=[
                '--no-sandbox',
                '--disable-dev-shm-usage',
                '--disable-blink-features=AutomationControlled',
                '--disable-features=IsolateOrigins,site-per-process',
                '--disable-infobars',
                '--window-size=1366,768',
                '--start-maximized',
                '--disable-web-security',
                '--ignore-certificate-errors',
            ]
        )

        context = await browser.new_context(
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
            viewport={'width': 1366, 'height': 768},
            locale='pt-BR',
            timezone_id='America/Sao_Paulo',
            java_script_enabled=True,
            bypass_csp=True,
            extra_http_headers={
                'Accept-Language': 'pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7',
            }
        )

        # Aplica stealth se disponivel
        if HAS_STEALTH:
            stealth = Stealth(
                navigator_webdriver=True,
                navigator_languages=True,
                navigator_platform=True,
                navigator_user_agent=True,
            )
            await stealth.apply_stealth_async(context)
            print('[Worker] Stealth aplicado ao contexto')

        # Remove o header webdriver via CDP
        page = await context.new_page()
        try:
            await page.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
                Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3,4,5]});
                Object.defineProperty(navigator, 'languages', {get: () => ['pt-BR', 'pt', 'en']});
                window.chrome = {runtime: {}};
            """)
        except:
            pass

        print('[Worker] Chrome OK')

        # Login
        print('[Worker] Fazendo login...')
        login_ok = False
        try:
            login_ok = await login_detetive(page)
            if login_ok:
                print('[Worker] Login OK!')
            else:
                print('[Worker] Login falhou, continuando no loop mesmo assim...')
        except Exception as e:
            print(f'[Worker] Erro no login: {e}')

        last_fs_check   = 0
        last_ntfy_check = 0
        start_time = time.time()

        while True:
            now = time.time()
            if RUN_DURATION > 0 and (now - start_time) >= RUN_DURATION:
                print(f'[Worker] Tempo {RUN_DURATION}s atingido. Encerrando.')
                break

            orders_to_process = []

            if now - last_fs_check >= POLL_SEC:
                last_fs_check = now
                try:
                    orders_to_process += fs_query_processing()
                except Exception as e:
                    print(f'[Worker] Erro Firestore: {e}')

            if now - last_ntfy_check >= NTFY_SEC:
                last_ntfy_check = now
                try:
                    ntfy_ids = ntfy_poll()
                    for oid in ntfy_ids:
                        if oid and oid not in processed:
                            try:
                                doc = fs_request(f'orders/{oid}')
                                f = doc.get('fields', {})
                                g = lambda k: (f.get(k) or {}).get('stringValue') or ''
                                if g('status') != 'done':
                                    orders_to_process.append({
                                        'id': oid,
                                        'productId': g('productId'),
                                        'productName': g('productName'),
                                        'queryData': g('queryData'),
                                    })
                            except Exception as e:
                                print(f'[Worker] Erro ntfy order {oid}: {e}')
                except Exception as e:
                    print(f'[Worker] Erro ntfy: {e}')

            for order in orders_to_process:
                oid = order['id']
                if oid in processed:
                    continue
                try:
                    doc = fs_request(f'orders/{oid}')
                    g = lambda k: (doc.get('fields', {}).get(k) or {}).get('stringValue') or ''
                    if g('status') == 'done':
                        processed.add(oid)
                        continue
                except Exception as e:
                    print(f'[Worker] Erro ao buscar {oid}: {e}')
                    continue

                print(f'[Worker] Processando {oid}: {order["productId"]} / {order["queryData"]}')
                processed.add(oid)

                try:
                    raw = await consultar(page, order['productId'], order['queryData'])
                    if not raw or len(raw) < 50:
                        await login_detetive(page)
                        raw = await consultar(page, order['productId'], order['queryData'])

                    resultado = formatar_resultado(raw, order.get('productName', order['productId']), order['queryData'])
                    fs_save_result(oid, resultado)
                    print(f'[Worker] OK {oid} ({len(resultado)} chars)')

                except Exception as e:
                    print(f'[Worker] ERRO {oid}: {e}')
                    try:
                        fs_mark_error(oid, str(e)[:200])
                    except:
                        pass
                    try:
                        await login_detetive(page)
                    except:
                        pass

            await asyncio.sleep(NTFY_SEC)

asyncio.run(main())
