"""
Achaqui Worker — produção
Estratégia: login via API (AES), cookie injetado no Playwright, consulta via form submit JS
GitHub Actions 5min cron, loop interno 290s
"""
import asyncio, json, os, base64, hashlib, re, time, urllib.request
from datetime import datetime, timezone
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad, unpad
from curl_cffi import requests as cf_req
from playwright.async_api import async_playwright

try:
    from playwright_stealth import Stealth
    HAS_STEALTH = True
except ImportError:
    HAS_STEALTH = False

# ── Config ───────────────────────────────────────────────────────────────────
FK          = os.environ.get('FIREBASE_API_KEY', 'AIzaSyAoZYnDTl8WoCG5K3q6hjFQnVFmkAS6PZ8')
FS_BASE     = 'https://firestore.googleapis.com/v1/projects/bancadamatriz-9f797/databases/(default)/documents'
NTFY        = 'https://ntfy.sh/achaqui-zapia-guga-secret-2025'
POLL_SEC    = 20
NTFY_SEC    = 5
RUN_DURATION = int(os.environ.get('RUN_DURATION', '0'))

BASE        = 'https://detetiveforense.com'
DF_USER     = 'edson102'
DF_PASS     = '123456789'
DF_PIN      = '162738'
DEVTOOL_CHUNK = '0888c0b2fc92ae80.js'
FAKE_JS     = '(globalThis.TURBOPACK||(globalThis.TURBOPACK=[])).push([null,98226,(e,t,n)=>{t.exports=()=>{}}])'

# Mapa productId -> módulo
MODULE_MAP = {
    'cpf-basico': {'url': '/app/modulos/investigador-osint', 'field': 'cpf', 'tab': 'documentos'},
    'cpf-completo': {'url': '/app/modulos/investigador-osint', 'field': 'cpf', 'tab': 'documentos'},
    'cpf-pro': {'url': '/app/modulos/investigador-osint', 'field': 'cpf', 'tab': 'documentos'},
    'nome-cpf': {'url': '/app/modulos/investigador-osint', 'field': 'cpf', 'tab': 'documentos'},
    'parentes': {'url': '/app/modulos/investigador-osint', 'field': 'cpf', 'tab': 'documentos'},
    'historico-enderecos': {'url': '/app/modulos/investigador-osint', 'field': 'cpf', 'tab': 'documentos'},
    'telefones': {'url': '/app/modulos/investigador-osint', 'field': 'cpf', 'tab': 'documentos'},
    'foto-redes': {'url': '/app/modulos/investigador-osint', 'field': 'cpf', 'tab': 'documentos'},
    'cnh-consulta': {'url': '/app/modulos/investigador-osint', 'field': 'cpf', 'tab': 'documentos'},
    'historico-criminal': {'url': '/app/modulos/investigador-osint', 'field': 'cpf', 'tab': 'documentos'},
    'processos-cpf': {'url': '/app/modulos/investigador-osint', 'field': 'cpf', 'tab': 'documentos'},
    'score-credito': {'url': '/app/modulos/investigador-osint', 'field': 'cpf', 'tab': 'documentos'},
    'placa-basico': {'url': '/app/modulos/mega-placa', 'field': 'placa', 'tab': 'placa'},
    'placa-historico': {'url': '/app/modulos/mega-placa-2', 'field': 'placa', 'tab': 'placa'},
    'cnpj-basico': {'url': '/app/modulos/investigador-osint', 'field': 'cnpj', 'tab': 'documentos'},
}

# ── Crypto ───────────────────────────────────────────────────────────────────
i_map = {"utc":[77,114,88,110],"est":[55,76,98,72],"cst":[107,51,87,113],"mst":[82,104,74,53],"pst":[100,65,111,89],"gmt":[70,115,54,106],"cet":[113,78,103,52],"eet":[88,109,66,120],"ist":[57,85,116,75],"jst":[108,71,56,99],"kst":[80,105,90,55],"nst":[101,68,110,83],"hst":[66,119,77,97],"akt":[118,48,102,79],"wst":[106,81,72,114],"aet":[51,107,89,69]}
KEY_O = ''.join(chr(v) for vals in i_map.values() for v in vals)

def evp(pw, salt):
    d, di = b'', b''
    while len(d) < 48:
        di = hashlib.md5(di + pw + salt).digest(); d += di
    return d[:32], d[32:48]

def enc_str(pt, ks):
    salt = os.urandom(8); k, iv = evp(ks.encode(), salt)
    return base64.b64encode(b"Salted__" + salt + AES.new(k, AES.MODE_CBC, iv).encrypt(pad(pt.encode(), 16))).decode()

def enc_raw(pt, kh, ivh):
    return base64.b64encode(AES.new(bytes.fromhex(kh), AES.MODE_CBC, bytes.fromhex(ivh)).encrypt(pad(pt.encode(), 16))).decode()

def encrypt_req(pt):
    k, iv = os.urandom(32), os.urandom(16)
    return base64.b64encode(json.dumps({"dracula":{"encryptedAesKey":enc_str(k.hex(),KEY_O),"encryptedText":enc_raw(pt,k.hex(),iv.hex()),"iv":iv.hex()}}).encode()).decode()

def dec_str(ct, ks):
    raw = base64.b64decode(ct); k, iv = evp(ks.encode(), raw[8:16])
    return unpad(AES.new(k, AES.MODE_CBC, iv).decrypt(raw[16:]), 16).decode()

def decrypt_resp(ct):
    try:
        raw = json.loads(base64.b64decode(ct))
        if 'dracula' in raw:
            d = raw['dracula']; kh = dec_str(d['encryptedAesKey'], KEY_O)
            k, iv = bytes.fromhex(kh), bytes.fromhex(d['iv'])
            return json.loads(unpad(AES.new(k, AES.MODE_CBC, iv).decrypt(base64.b64decode(d['encryptedText'])), 16).decode())
    except: return None

# ── Firestore helpers ─────────────────────────────────────────────────────────
def fs_req(path, method='GET', body=None):
    url = f'{FS_BASE}/{path}?key={FK}'
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(url, data=data, headers={'Content-Type': 'application/json'}, method=method)
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())

def fs_query():
    url = f'{FS_BASE}:runQuery?key={FK}'
    body = {"structuredQuery": {
        "from": [{"collectionId": "orders"}],
        "where": {"fieldFilter": {"field": {"fieldPath": "status"}, "op": "EQUAL", "value": {"stringValue": "processing"}}},
        "limit": 5
    }}
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={'Content-Type': 'application/json'}, method='POST')
    with urllib.request.urlopen(req, timeout=10) as r:
        docs = json.loads(r.read())
    orders = []
    for d in docs:
        if 'document' not in d: continue
        f = d['document']['fields']
        oid = d['document']['name'].split('/')[-1]
        g = lambda k: (f.get(k) or {}).get('stringValue') or ''
        orders.append({'id': oid, 'productId': g('productId'), 'productName': g('productName'), 'queryData': g('queryData')})
    return orders

def fs_save(oid, result):
    now = datetime.now(timezone.utc).isoformat()
    fields = ['status', 'result', 'deliveredAt']
    mask = '&'.join(f'updateMask.fieldPaths={f}' for f in fields)
    url = f'{FS_BASE}/orders/{oid}?key={FK}&{mask}'
    body = {"fields": {"status": {"stringValue": "done"}, "result": {"stringValue": result}, "deliveredAt": {"stringValue": now}}}
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={'Content-Type': 'application/json'}, method='PATCH')
    with urllib.request.urlopen(req, timeout=10) as r: r.read()

def fs_error(oid, msg):
    fields = ['status', 'result']
    mask = '&'.join(f'updateMask.fieldPaths={f}' for f in fields)
    url = f'{FS_BASE}/orders/{oid}?key={FK}&{mask}'
    body = {"fields": {"status": {"stringValue": "error"}, "result": {"stringValue": f"⚠️ Erro: {msg[:200]}\n\nContato: +55 68 98101-4570"}}}
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={'Content-Type': 'application/json'}, method='PATCH')
    with urllib.request.urlopen(req, timeout=10) as r: r.read()

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
                if msg.get('event') == 'message': ids.append(msg.get('message','').strip())
            except: pass
        return ids
    except: return []

# ── Login via API ─────────────────────────────────────────────────────────────
def api_login():
    s = cf_req.Session(impersonate="chrome124")
    s.get(f"{BASE}/auth/login", timeout=20)
    H = {"Content-Type": "text/plain", "Referer": f"{BASE}/auth/login", "Origin": BASE}
    decrypt_resp(s.post(f"{BASE}/api/auth/login",
        data=encrypt_req(json.dumps({"username": DF_USER, "password": DF_PASS, "visitorId": None})),
        headers=H, timeout=15).text)
    r2 = decrypt_resp(s.post(f"{BASE}/api/auth/login-pin",
        data=encrypt_req(json.dumps({"username": DF_USER, "password": DF_PASS, "pin": DF_PIN, "visitorId": None})),
        headers=H, timeout=15).text)
    if r2 and r2.get('success'):
        token = dict(s.cookies).get('accessToken', '')
        print(f'[Login] API OK, token: {token[:20]}...')
        return token
    print('[Login] API falhou')
    return None

# ── Playwright consulta ───────────────────────────────────────────────────────
async def consultar(page, module_info, query_data):
    url = f"{BASE}{module_info['url']}"
    field = module_info['field']

    # Navega para o módulo
    await page.goto(url, wait_until='domcontentloaded', timeout=20000)
    await page.wait_for_timeout(3000)
    print(f'[Consulta] URL: {page.url}')

    # Preenche via page.type() - simula digitação real (funciona com React Hook Form)
    try:
        await page.wait_for_timeout(2000)
        cpf_input = None

        # Tenta selectors em ordem de especificidade
        for selector in [
            f'input[name="{field}"]',
            'input[inputmode="numeric"]',
            'input[type="text"]:visible',
            'input:visible',
        ]:
            try:
                el = await page.wait_for_selector(selector, timeout=3000, state='visible')
                if el:
                    cpf_input = selector
                    break
            except:
                continue

        if cpf_input:
            await page.click(cpf_input)
            await page.fill(cpf_input, '')
            await page.type(cpf_input, query_data, delay=50)
            await page.wait_for_timeout(500)
            # Enter para submit
            await page.keyboard.press('Enter')
            await page.wait_for_timeout(1000)
            # Tenta também clicar no botão de busca
            for btn_sel in ['button[type="submit"]', 'button:has-text("Pesquisar")', 'button:has-text("Buscar")']:
                try:
                    btn = await page.query_selector(btn_sel)
                    if btn:
                        await btn.click()
                        break
                except:
                    pass
            result = {'status': 'typed', 'selector': cpf_input, 'value': query_data}
        else:
            result = {'status': 'no_input_found'}

    except Exception as e:
        result = {'status': 'error', 'msg': str(e)[:100]}

    print(f'[Consulta] Form submit: {result}')


    # Aguarda resultado aparecer na página (10s)
    # Aguarda resultado (15s para o React processar a Server Action)
    await page.wait_for_timeout(15000)

    # Captura o conteúdo resultante
    try:
        content = await page.evaluate("""
            () => {
                // Pega todo texto dos cards de resultado
                const selectors = [
                    '[class*="result"]', '[class*="card"]', 'main article',
                    '[class*="consulta"]', '[class*="dados"]', 'main'
                ];
                for (const sel of selectors) {
                    const el = document.querySelector(sel);
                    if (el && el.innerText && el.innerText.length > 100) {
                        return el.innerText;
                    }
                }
                return document.body.innerText;
            }
        """)
        print(f'[Consulta] Resultado ({len(content)} chars): {content[:500]}')
        return content
    except Exception as e:
        body = await page.inner_text('body')
        print(f'[Consulta] Fallback ({len(body)} chars): {body[:500]}')
        return body

def formatar(raw, product_name, query_data):
    now = datetime.now().strftime('%d/%m/%Y às %H:%M')
    skip = ['Copiar', 'Exportar', 'Fechar', 'Buscar Mandados', 'Validar Foto',
            'Galeria', '100% gratuito', 'caráter histórico', 'PRINCIPAL', 'Módulos']
    linhas = [l.strip() for l in raw.split('\n') if l.strip() and not any(s in l for s in skip)]
    out  = f'🔍 RELATÓRIO — ACHAQUI\n'
    out += f'━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n'
    out += f'📋 {product_name}\n'
    out += f'🔎 Consulta: {query_data}\n'
    out += f'📅 {now}\n'
    out += f'━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n'
    out += '\n'.join(linhas[:100])
    return out

# ── Main ──────────────────────────────────────────────────────────────────────
async def main():
    print('[Achaqui Worker] Iniciando...')
    start = time.time()
    processed = set()

    # Login via API
    access_token = api_login()
    if not access_token:
        print('[Worker] Sem token, abortando')
        return

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            args=['--no-sandbox', '--disable-dev-shm-usage', '--disable-blink-features=AutomationControlled']
        )
        context = await browser.new_context(
            user_agent='Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/125.0.0.0 Safari/537.36',
            viewport={'width': 1280, 'height': 800},
            locale='pt-BR', timezone_id='America/Sao_Paulo'
        )

        if HAS_STEALTH:
            await Stealth().apply_stealth_async(context)

        # Injeta o accessToken
        await context.add_cookies([{
            'name': 'accessToken', 'value': access_token,
            'domain': 'detetiveforense.com', 'path': '/', 'httpOnly': True, 'secure': True
        }])

        page = await context.new_page()

        # Bloqueia o DevtoolDisabler
        async def handle_route(route):
            if DEVTOOL_CHUNK in route.request.url:
                print(f'[Bypass] Bloqueado DevtoolDisabler')
                await route.fulfill(status=200, content_type='application/javascript', body=FAKE_JS)
            else:
                await route.continue_()

        await page.route('**/*', handle_route)

        print('[Worker] Browser OK, entrando no loop...')

        last_fs = 0
        last_ntfy = 0

        while True:
            now = time.time()
            if RUN_DURATION > 0 and (now - start) >= RUN_DURATION:
                print(f'[Worker] {RUN_DURATION}s atingido. Encerrando.')
                break

            orders = []

            if now - last_fs >= POLL_SEC:
                last_fs = now
                try:
                    orders += fs_query()
                except Exception as e:
                    print(f'[Worker] Firestore erro: {e}')

            if now - last_ntfy >= NTFY_SEC:
                last_ntfy = now
                try:
                    for oid in ntfy_poll():
                        if oid and oid not in processed:
                            try:
                                doc = fs_req(f'orders/{oid}')
                                f = doc.get('fields', {})
                                g = lambda k: (f.get(k) or {}).get('stringValue') or ''
                                if g('status') == 'processing':
                                    orders.append({'id': oid, 'productId': g('productId'), 'productName': g('productName'), 'queryData': g('queryData')})
                            except: pass
                except: pass

            for order in orders:
                oid = order['id']
                if oid in processed: continue
                processed.add(oid)

                try:
                    doc = fs_req(f'orders/{oid}')
                    g = lambda k: (doc.get('fields', {}).get(k) or {}).get('stringValue') or ''
                    if g('status') == 'done': continue
                except: continue

                print(f'[Worker] Processando {oid}: {order["productId"]} / {order["queryData"]}')

                module_info = MODULE_MAP.get(order['productId'])
                if not module_info:
                    # Fallback: investigador-osint com o dado como CPF
                    module_info = {'url': '/app/modulos/investigador-osint', 'field': 'cpf', 'tab': 'documentos'}

                try:
                    raw = await consultar(page, module_info, order['queryData'])
                    resultado = formatar(raw, order.get('productName', order['productId']), order['queryData'])
                    fs_save(oid, resultado)
                    print(f'[Worker] ✅ {oid} ({len(resultado)} chars)')
                except Exception as e:
                    print(f'[Worker] ❌ {oid}: {e}')
                    try: fs_error(oid, str(e))
                    except: pass

            await asyncio.sleep(NTFY_SEC)

        await browser.close()

asyncio.run(main())
