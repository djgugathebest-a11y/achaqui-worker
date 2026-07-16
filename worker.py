"""
Diagnóstico 3: Login via API + Playwright com cookie injetado
Objetivo: capturar o endpoint de consulta real do módulo investigador-osint
"""
import asyncio, os, base64, json, hashlib
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad, unpad
from curl_cffi import requests as cf
from playwright.async_api import async_playwright

# ── Crypto ─────────────────────────────────────────────────────────────────────
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
    return base64.b64encode(json.dumps({"dracula": {"encryptedAesKey": enc_str(k.hex(), KEY_O), "encryptedText": enc_raw(pt, k.hex(), iv.hex()), "iv": iv.hex()}}).encode()).decode()

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
    except:
        return {}

BASE = "https://detetiveforense.com"
RUN_DURATION = int(os.environ.get("RUN_DURATION", "0"))

async def main():
    # Step 1: Login via API (sem browser)
    cf_s = cf.Session(impersonate="chrome124")
    cf_s.get(f"{BASE}/auth/login", timeout=20)
    H = {"Content-Type": "text/plain", "Referer": f"{BASE}/auth/login", "Origin": BASE}
    decrypt_resp(cf_s.post(f"{BASE}/api/auth/login",
        data=encrypt_req(json.dumps({"username": "edson102", "password": "123456789", "visitorId": None})),
        headers=H, timeout=15).text)
    r_pin = decrypt_resp(cf_s.post(f"{BASE}/api/auth/login-pin",
        data=encrypt_req(json.dumps({"username": "edson102", "password": "123456789", "pin": "162738", "visitorId": None})),
        headers=H, timeout=15).text)
    
    print(f"[API Login] success={r_pin.get('success')}")
    access_token = dict(cf_s.cookies).get("accessToken", "")
    print(f"[API Login] accessToken: {access_token[:30]}...")

    # Step 2: Playwright com cookie injetado
    DEVTOOL_CHUNK = "0888c0b2fc92ae80.js"
    FAKE_JS = '(globalThis.TURBOPACK||(globalThis.TURBOPACK=[])).push([null,98226,(e,t,n)=>{t.exports=()=>{}}])'

    captured = []

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-blink-features=AutomationControlled"]
        )
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 800}
        )

        # Injeta cookie de autenticação
        if access_token:
            await context.add_cookies([{
                "name": "accessToken",
                "value": access_token,
                "domain": "detetiveforense.com",
                "path": "/",
                "httpOnly": True,
                "secure": True
            }])
            print("[Cookie] accessToken injetado!")

        page = await context.new_page()

        # Bloqueia DevtoolDisabler
        async def handle_route(route):
            if DEVTOOL_CHUNK in route.request.url:
                print(f"[Bypass] Bloqueado: {route.request.url[-40:]}")
                await route.fulfill(status=200, content_type="application/javascript", body=FAKE_JS)
            else:
                await route.continue_()

        await page.route("**/*", handle_route)

        # Captura requests de consulta
        def on_request(req):
            url = req.url
            if "detetiveforense.com/api/" in url:
                skip = ["auth", "notification", "users/me", "users/balance", "last-not"]
                if not any(s in url for s in skip):
                    captured.append({"url": url, "method": req.method})
                    print(f"[API Req] {req.method} {url}")

        async def on_response(resp):
            url = resp.url
            if "detetiveforense.com/api/" in url:
                skip = ["auth", "notification", "users/me", "users/balance", "last-not"]
                if not any(s in url for s in skip):
                    try:
                        body = await resp.body()
                        print(f"[API Resp] {resp.status} {url} | body({len(body)}): {body[:100]}")
                    except:
                        pass

        page.on("request", on_request)
        page.on("response", lambda r: asyncio.ensure_future(on_response(r)))

        # Navega para o módulo (deve estar logado pelo cookie)
        print("[Nav] Indo para módulo investigador-osint...")
        r = await page.goto(f"{BASE}/app/modulos/investigador-osint", wait_until="domcontentloaded", timeout=25000)
        print(f"[Nav] status={r.status if r else 'None'}, url={page.url}")
        await page.wait_for_timeout(3000)

        # Verifica se está logado
        body_text = ""
        try:
            body_text = await page.inner_text("body")
            print(f"[Page] Body (300c): {body_text[:300]}")
        except:
            pass

        # Procura input e preenche CPF de teste
        try:
            inputs = await page.query_selector_all("input")
            print(f"[Form] {len(inputs)} inputs encontrados")
            for inp in inputs:
                ph = await inp.get_attribute("placeholder") or ""
                tp = await inp.get_attribute("type") or ""
                print(f"  input: placeholder='{ph}', type='{tp}'")

            if inputs:
                await inputs[0].fill("077.349.584-00")
                await page.wait_for_timeout(800)
                await page.keyboard.press("Enter")
                print("[Form] CPF preenchido e Enter pressionado!")
                await page.wait_for_timeout(8000)
                print(f"[Nav] URL após submit: {page.url}")
        except Exception as e:
            print(f"[Form] Erro: {e}")

        print(f"\n[Result] Requests capturadas: {json.dumps(captured, indent=2)}")
        await browser.close()

    if RUN_DURATION > 0:
        import time
        elapsed = 120
        remaining = RUN_DURATION - elapsed
        if remaining > 0:
            print(f"[Worker] Aguardando {remaining}s...")
            time.sleep(remaining)

asyncio.run(main())
