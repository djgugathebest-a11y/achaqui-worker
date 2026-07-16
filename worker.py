"""
Achaqui Worker — processa pedidos do Firestore via Playwright
Roda em loop no Railway, 24/7
"""

import asyncio
import json
import os
import time
import urllib.request
import urllib.parse
from datetime import datetime, timezone
from playwright.async_api import async_playwright

# ── Config ──────────────────────────────────────────────────────────────────
FK       = os.environ.get('FIREBASE_API_KEY', 'AIzaSyAoZYnDTl8WoCG5K3q6hjFQnVFmkAS6PZ8')
FS_BASE  = f'https://firestore.googleapis.com/v1/projects/bancadamatriz-9f797/databases/(default)/documents'
NTFY     = 'https://ntfy.sh/achaqui-zapia-guga-secret-2025'
POLL_SEC = 20   # verifica Firestore a cada 20s
NTFY_SEC = 5    # verifica ntfy a cada 5s

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
        "result": {"stringValue": f"⚠️ Erro no processamento: {msg}\n\nEntre em contato com o suporte: +55 68 98101-4570"},
    }}
    req = urllib.request.Request(url,
          data=json.dumps(body).encode(),
          headers={'Content-Type': 'application/json'}, method='PATCH')
    with urllib.request.urlopen(req, timeout=10) as r:
        r.read()

def ntfy_poll():
    """Retorna lista de orderIds recebidos via ntfy nos últimos 2min."""
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
                if msg.get('title') == 'ACHAQUI_PEDIDO':
                    body = json.loads(msg.get('message', '{}'))
                    oid  = body.get('orderId')
                    if oid and oid != 'teste123':
                        ids.append({'id': oid,
                                    'productId':  body.get('productId', ''),
                                    'productName': body.get('productName', ''),
                                    'queryData':  body.get('queryData', '')})
            except: pass
        return ids
    except Exception as e:
        print(f'[ntfy] erro: {e}')
        return []

# ── Detetive Forense via Playwright ─────────────────────────────────────────
MODULE_MAP = {
    'placa':            'placa',
    'placa-basica':     'placa',
    'placa-completa':   'placa',
    'placa-historico':  'placa',
}
# Todos os outros módulos usam investigador-osint

async def consultar(page, product_id, query_data):
    query_clean = ''.join(c for c in query_data if c.isalnum())

    # Decide módulo
    modulo = MODULE_MAP.get(product_id, 'investigador-osint')

    await page.goto(f'https://detetiveforense.com/app/modulos/{modulo}', wait_until='domcontentloaded', timeout=30000)
    await page.wait_for_timeout(1500)

    if modulo == 'investigador-osint':
        await page.fill('input[placeholder*="CPF"], input[aria-label*="CPF"]', query_clean)
    elif modulo == 'placa':
        await page.fill('input[placeholder*="Placa"], input[aria-label*="Placa"]', query_clean.upper())

    await page.click('button:has-text("Pesquisar")')
    await page.wait_for_timeout(6000)

    # Tenta esperar algum resultado
    try:
        await page.wait_for_selector('dialog[open], [role="dialog"]', timeout=15000)
    except:
        pass

    # Extrai texto do modal/resultado
    result = await page.evaluate('''() => {
        // Tenta pegar o modal de detalhes
        const dialog = document.querySelector('dialog[open]');
        if (dialog) return dialog.innerText;
        // Tenta pegar área de resultado
        const res = document.querySelector('[class*="result"], [class*="detail"], [class*="modal"]');
        if (res) return res.innerText;
        return document.body.innerText.substring(0, 5000);
    }''')

    return result or ''

async def login_detetive(page):
    await page.goto('https://detetiveforense.com/auth/login', wait_until='networkidle', timeout=30000)
    await page.wait_for_timeout(1500)

    # Aguarda campo Usuário (placeholder exato: "Digite seu usuário")
    await page.wait_for_selector('input[placeholder="Digite seu usuário"]', timeout=15000)

    await page.fill('input[placeholder="Digite seu usuário"]', 'edson102')
    await page.wait_for_timeout(300)
    await page.fill('input[placeholder="Digite sua senha"]', '123456789')
    await page.wait_for_timeout(300)
    await page.click('button:has-text("Entrar")')
    await page.wait_for_timeout(3000)

    # PIN se necessário
    try:
        pin_input = await page.query_selector('input[placeholder*="PIN"], input[placeholder*="pin"], input[maxlength="6"]')
        if pin_input:
            await pin_input.fill('162738')
            await page.click('button:has-text("Confirmar"), button:has-text("Entrar")')
            await page.wait_for_timeout(2000)
    except:
        pass

def formatar_resultado(raw_text, product_name, query_data):
    now = datetime.now().strftime('%d/%m/%Y às %H:%M')
    linhas = [l.strip() for l in raw_text.split('\n') if l.strip()]
    # Remove linhas de UI
    skip_words = ['Copiar Dados', 'Exportar PDF', 'Adicionar em', 'Fechar', 'Buscar Mandados',
                  'Validar Foto', 'Galeria de Fotos', 'Foto de Referência', '100% gratuito',
                  'Busca em tempo real', 'caráter histórico']
    linhas = [l for l in linhas if not any(s in l for s in skip_words)]

    out  = f'🔍 RELATÓRIO DE CONSULTA — ACHAQUI\n'
    out += f'━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n'
    out += f'📋 Produto: {product_name}\n'
    out += f'🔎 Dado consultado: {query_data}\n'
    out += f'📅 Data/Hora: {now}\n'
    out += f'━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n'
    out += '\n'.join(linhas[:200])
    out += f'\n\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n'
    out += f'🕵️ Relatório gerado por Achaqui\n'
    out += f'🔒 Documento confidencial — uso exclusivo do solicitante\n'
    out += f'achaqui.vercel.app'
    return out

# ── Main loop ────────────────────────────────────────────────────────────────
async def main():
    print('[Achaqui Worker] Iniciando...')
    processed = set()  # evita reprocessar

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=['--no-sandbox','--disable-dev-shm-usage'])
        context = await browser.new_context(viewport={'width':1280,'height':800})
        page    = await context.new_page()

        # Login inicial
        print('[Worker] Fazendo login no Detetive Forense...')
        try:
            await login_detetive(page)
            print('[Worker] Login OK')
        except Exception as e:
            print(f'[Worker] Erro no login: {e}')

        last_fs_check  = 0
        last_ntfy_check = 0

        while True:
            now = time.time()
            orders_to_process = []

            # Verifica Firestore a cada 20s
            if now - last_fs_check >= POLL_SEC:
                last_fs_check = now
                try:
                    orders_to_process += fs_query_processing()
                except Exception as e:
                    print(f'[Firestore] Erro: {e}')

            # Verifica ntfy a cada 5s
            if now - last_ntfy_check >= NTFY_SEC:
                last_ntfy_check = now
                try:
                    ntfy_orders = ntfy_poll()
                    for o in ntfy_orders:
                        if o['id'] not in [x['id'] for x in orders_to_process]:
                            orders_to_process.append(o)
                except Exception as e:
                    print(f'[ntfy] Erro: {e}')

            # Processa pedidos únicos não processados nessa sessão
            for order in orders_to_process:
                oid = order['id']
                if oid in processed:
                    continue

                # Busca dados completos do pedido se veio do ntfy
                if not order.get('queryData'):
                    try:
                        doc = fs_request(f'orders/{oid}')
                        f   = doc.get('fields', {})
                        g   = lambda k: (f.get(k) or {}).get('stringValue', '')
                        order['productId']   = g('productId')
                        order['productName'] = g('productName')
                        order['queryData']   = g('queryData')
                        if g('status') == 'done':
                            processed.add(oid)
                            continue
                    except Exception as e:
                        print(f'[Worker] Erro ao buscar pedido {oid}: {e}')
                        continue

                print(f'[Worker] Processando {oid}: {order["productId"]} / {order["queryData"]}')
                processed.add(oid)

                try:
                    raw = await consultar(page, order['productId'], order['queryData'])
                    if not raw or len(raw) < 50:
                        # Tenta relogin e consulta novamente
                        await login_detetive(page)
                        raw = await consultar(page, order['productId'], order['queryData'])

                    resultado = formatar_resultado(raw, order.get('productName', order['productId']), order['queryData'])
                    fs_save_result(oid, resultado)
                    print(f'[Worker] ✅ {oid} processado ({len(resultado)} chars)')

                except Exception as e:
                    print(f'[Worker] ❌ Erro ao processar {oid}: {e}')
                    try:
                        fs_mark_error(oid, str(e)[:200])
                    except: pass
                    # Tenta relogin para próximo
                    try:
                        await login_detetive(page)
                    except: pass

            await asyncio.sleep(NTFY_SEC)

asyncio.run(main())
