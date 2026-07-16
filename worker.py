"""
Achaqui Worker — processa pedidos do Firestore via Playwright + curl_cffi
Roda em loop no GitHub Actions (5min cron) ou Railway (24/7)
"""

import asyncio
import json
import os
import re
import time
import urllib.request
import urllib.parse
from datetime import datetime, timezone
from playwright.async_api import async_playwright

try:
    from curl_cffi import requests as cf_requests
    HAS_CURL_CFFI = True
except ImportError:
    HAS_CURL_CFFI = False
    print("[Worker] curl_cffi nao disponivel, usando Playwright puro")

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
        g   = lambda k, f=f: (f.get(k) or {}).get('stringValue') or (f.get(k) or {}).get('integerValue') or ''
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

# ── Login via curl_cffi ──────────────────────────────────────────────────────
def login_via_curl_cffi():
    """Login via HTTP real (curl_cffi impersonando Chrome), retorna (cookies, session)."""
    print("[Login] Iniciando via curl_cffi (bypass Cloudflare)...")
    session = cf_requests.Session(impersonate="chrome124")

    r = session.get(DF_LOGIN_URL, timeout=20)
    print(f"[Login] GET login: {r.status_code}, URL final: {r.url}")
    if r.status_code != 200:
        raise Exception(f"Pagina de login retornou {r.status_code}")

    # Extrai CSRF se existir
    csrf_match = re.search(r'"csrfToken"\s*:\s*"([^"]+)"', r.text)
    csrf_token = csrf_match.group(1) if csrf_match else None
    if csrf_token:
        print(f"[Login] CSRF: {csrf_token[:20]}...")

    # Detecta a estrutura da API de login
    login_payload = {"usuario": DF_USER, "senha": DF_PASS}
    headers = {
        "Content-Type": "application/json",
        "Referer": DF_LOGIN_URL,
        "Origin": DF_BASE,
    }
    if csrf_token:
        headers["X-CSRF-Token"] = csrf_token

    login_ok = False
    for endpoint in ["/api/auth/login", "/api/login", "/api/users/login", "/auth/login"]:
        try:
            r2 = session.post(f"{DF_BASE}{endpoint}", json=login_payload, headers=headers, timeout=15)
            print(f"[Login] POST {endpoint}: {r2.status_code} — {r2.text[:150]}")
            if r2.status_code in [200, 201]:
                body = r2.json() if r2.text else {}
                if body.get('token') or body.get('accessToken') or body.get('success'):
                    login_ok = True
                    print(f"[Login] Login via API OK!")
                    # Guarda token JWT se houver
                    token = body.get('token') or body.get('accessToken') or body.get('jwt')
                    if token:
                        session.headers.update({'Authorization': f'Bearer {token}'})
                    break
        except Exception as e:
            print(f"[Login] {endpoint} erro: {e}")

    if not login_ok:
        print("[Login] API login nao funcionou, retornando cookies da sessao mesmo assim")

    cookies = dict(session.cookies)
    print(f"[Login] Cookies: {list(cookies.keys())}")
    return cookies, session

# ── Login via Playwright ─────────────────────────────────────────────────────
async def login_detetive_playwright(page, pre_cookies=None):
    if pre_cookies:
        print("[Login] Injetando cookies no Playwright...")
        cookie_list = [
            {"name": k, "value": v, "domain": "detetiveforense.com", "path": "/"}
            for k, v in pre_cookies.items()
        ]
        await page.context.add_cookies(cookie_list)

    try:
        await page.goto(DF_LOGIN_URL, wait_until='domcontentloaded', timeout=20000)
        await page.wait_for_timeout(2000)
    except Exception as e:
        print(f"[Login] goto erro: {e}")

    current_url = page.url
    title = await page.title()
    print(f"[Login] URL: {current_url} | titulo: {title}")

    # Tenta obter conteúdo da página
    try:
        body_text = await page.inner_text('body')
        print(f"[Login] Body (200 chars): {body_text[:200]}")
    except:
        pass

    # Se já logado
    if '/app/' in current_url or '/dashboard' in current_url:
        print("[Login] Ja autenticado!")
        return True

    # Tenta formulário
    try:
        await page.wait_for_selector('input[placeholder="Digite seu usuario"]', timeout=8000)
        print("[Login] Formulario encontrado!")
        await page.fill('input[placeholder="Digite seu usuario"]', DF_USER)
        await page.wait_for_timeout(300)
        await page.fill('input[placeholder="Digite sua senha"]', DF_PASS)
        await page.wait_for_timeout(300)
        await page.click('button:has-text("Entrar")')
        await page.wait_for_timeout(3000)
        try:
            pin = await page.query_selector('input[maxlength="6"]')
            if pin:
                await pin.fill(DF_PIN)
                await page.keyboard.press('Enter')
                await page.wait_for_timeout(2000)
        except:
            pass
        print(f"[Login] Apos submit: {page.url}")
        return True
    except Exception as e:
        print(f"[Login] Formulario nao encontrado: {e}")
        return False

# ── Consulta ─────────────────────────────────────────────────────────────────
async def consultar(page, modulo, query_data):
    url = f'https://detetiveforense.com/app/modulos/{modulo}'
    await page.goto(url, wait_until='domcontentloaded', timeout=30000)
    await page.wait_for_timeout(2000)
    print(f"[Consulta] URL: {page.url}")

    input_sel = 'input[placeholder], input[type="text"], input[type="search"]'
    await page.wait_for_selector(input_sel, timeout=15000)
    inputs = await page.query_selector_all(input_sel)
    if inputs:
        await inputs[0].fill(query_data)
        await page.wait_for_timeout(500)
        try:
            btn = await page.query_selector('button[type="submit"], button:has-text("Buscar"), button:has-text("Pesquisar")')
            if btn:
                await btn.click()
            else:
                await inputs[0].press('Enter')
        except:
            await inputs[0].press('Enter')

    await page.wait_for_timeout(5000)
    try:
        return await page.inner_text('main, [class*="result"], [class*="card"], article')
    except:
        return await page.inner_text('body')

def formatar_resultado(raw_text, product_name, query_data):
    now = datetime.now().strftime('%d/%m/%Y as %H:%M')
    linhas = [l.strip() for l in raw_text.split('\n') if l.strip()]
    skip_words = ['Copiar Dados', 'Exportar PDF', 'Adicionar em', 'Fechar', 'Buscar Mandados',
                  'Validar Foto', 'Galeria de Fotos', '100% gratuito', 'Busca em tempo real']
    linhas = [l for l in linhas if not any(s in l for s in skip_words)]
    out  = '🔍 RELATORIO DE CONSULTA — ACHAQUI\n'
    out += '━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n'
    out += f'📋 Produto: {product_name}\n'
    out += f'🔎 Dado: {query_data}\n'
    out += f'📅 Data: {now}\n'
    out += '━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n'
    out += '\n'.join(linhas[:80])
    return out

# ── Main ─────────────────────────────────────────────────────────────────────
async def main():
    print('[Achaqui Worker] Iniciando...')
    processed = set()

    pre_cookies = None
    if HAS_CURL_CFFI:
        try:
            pre_cookies, _ = login_via_curl_cffi()
        except Exception as e:
            print(f"[Worker] curl_cffi falhou: {e}")

    print("[Worker] Iniciando Playwright...")
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            args=['--no-sandbox', '--disable-dev-shm-usage',
                  '--disable-blink-features=AutomationControlled', '--window-size=1280,800']
        )
        context = await browser.new_context(
            user_agent='Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
            viewport={'width': 1280, 'height': 800},
            locale='pt-BR',
            timezone_id='America/Sao_Paulo',
        )
        page = await context.new_page()
        print("[Worker] Chrome OK")

        print('[Worker] Fazendo login...')
        try:
            ok = await login_detetive_playwright(page, pre_cookies)
            print(f'[Worker] Login {"OK" if ok else "falhou, continuando"}')
        except Exception as e:
            print(f'[Worker] Erro login: {e}')

        last_fs_check   = 0
        last_ntfy_check = 0
        start_time      = time.time()

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
                    for oid in ntfy_poll():
                        if oid and oid not in processed:
                            try:
                                doc = fs_request(f'orders/{oid}')
                                f = doc.get('fields', {})
                                g = lambda k, f=f: (f.get(k) or {}).get('stringValue') or ''
                                if g('status') != 'done':
                                    orders_to_process.append({
                                        'id': oid, 'productId': g('productId'),
                                        'productName': g('productName'), 'queryData': g('queryData'),
                                    })
                            except Exception as e:
                                print(f'[Worker] ntfy order {oid}: {e}')
                except Exception as e:
                    print(f'[Worker] ntfy poll erro: {e}')

            for order in orders_to_process:
                oid = order['id']
                if oid in processed:
                    continue
                try:
                    doc = fs_request(f'orders/{oid}')
                    f = doc.get('fields', {})
                    g = lambda k, f=f: (f.get(k) or {}).get('stringValue') or ''
                    if g('status') == 'done':
                        processed.add(oid)
                        continue
                except Exception as e:
                    print(f'[Worker] Erro buscar {oid}: {e}')
                    continue

                print(f'[Worker] Processando {oid}: {order["productId"]} / {order["queryData"]}')
                processed.add(oid)
                try:
                    raw = await consultar(page, order['productId'], order['queryData'])
                    if not raw or len(raw) < 50:
                        await login_detetive_playwright(page, pre_cookies)
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
                        await login_detetive_playwright(page, pre_cookies)
                    except:
                        pass

            await asyncio.sleep(NTFY_SEC)

asyncio.run(main())
