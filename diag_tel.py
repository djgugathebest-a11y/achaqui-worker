#!/usr/bin/env python3
import re, json, base64, os
from curl_cffi import requests as cf_req
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad, unpad

BASE = "https://detetiveforense.com"
DF_USER = os.environ.get("DF_USER", "edson102")
DF_PASS = os.environ.get("DF_PASS", "123456789")
DF_PIN  = os.environ.get("DF_PIN",  "162738")
KEY_P = b"p3rson4l_k3y_4_s"
KEY_O = b"0v3r_k3y_4_pubk3y"

def encrypt(data, key):
    iv = b"1234567890123456"
    return iv + AES.new(key, AES.MODE_CBC, iv).encrypt(pad(data, 16))

def decrypt(data, key):
    return unpad(AES.new(key, AES.MODE_CBC, data[:16]).decrypt(data[16:]), 16)

def encrypt_req(pt):
    return base64.b64encode(encrypt(pt.encode(), KEY_P)).decode()

def dec_str(ct, key):
    return decrypt(base64.b64decode(ct), key).decode()

def decrypt_resp(ct):
    try:
        raw = json.loads(base64.b64decode(ct))
        if 'dracula' in raw:
            d = raw['dracula']
            kh = dec_str(d['encryptedAesKey'], KEY_O)
            k = bytes.fromhex(kh)
            iv = bytes.fromhex(d['iv'])
            return json.loads(unpad(AES.new(k, AES.MODE_CBC, iv).decrypt(base64.b64decode(d['encryptedText'])), 16))
        return raw
    except Exception as e:
        print(f"  [decrypt] {type(e).__name__}: {e}")
        return None

# Login
s = cf_req.Session(impersonate="chrome124")
s.get(f"{BASE}/auth/login", timeout=20)
H = {"Content-Type": "text/plain", "Referer": f"{BASE}/auth/login", "Origin": BASE}
s.post(f"{BASE}/api/auth/login",
    data=encrypt_req(json.dumps({"username": DF_USER, "password": DF_PASS, "visitorId": None})),
    headers=H, timeout=15)
r2 = s.post(f"{BASE}/api/auth/login-pin",
    data=encrypt_req(json.dumps({"username": DF_USER, "password": DF_PASS, "pin": DF_PIN, "visitorId": None})),
    headers=H, timeout=15)
d2 = decrypt_resp(r2.text)
print(f"Login: {d2.get('success') if d2 else 'FAIL'}")

query = "61984611071"
print(f"\nTestando actions para telefone: {query}")

# Buscar nos JS bundles os action IDs disponíveis
print("\n[1] Buscando action IDs nos JS bundles...")
page_r = s.get(f"{BASE}/app/modulos/investigador-osint", timeout=20)
chunks = re.findall(r'"(/_next/static/chunks/[^"]+\.js)"', page_r.text)
print(f"  {len(chunks)} chunks encontrados")
all_action_ids = set()
for chunk_url in chunks:
    try:
        cr = s.get(f"{BASE}{chunk_url}", timeout=10)
        acts = re.findall(r'["\']((?:60|40|c0)[0-9a-f]{38,42})["\']', cr.text)
        tel_ctx = re.findall(r'.{0,60}(?:telefon|phone|celular|fone).{0,60}', cr.text, re.IGNORECASE)
        if acts:
            all_action_ids.update(acts)
        if tel_ctx:
            print(f"  Chunk {chunk_url[-20:]}: {tel_ctx[:2]}")
    except Exception as e:
        print(f"  Erro chunk {chunk_url[-20:]}: {e}")

print(f"  Action IDs encontrados: {list(all_action_ids)}")

# Testa todos os action IDs + os conhecidos
known = [
    ('cpf', '60cfed776b956422225588773edd905425a2a4d38f'),
    ('documentos', '602f465b6303b9e7917ab450d8681d9b5d2658342d'),
    ('nomeend', '60144c075589c30f46302a88f0edd2cc868bf2e3c3'),
    ('cnpj', '6065bdbf8e8208d4a7caa4c637ba22a4258d3313e7'),
    ('processos', '40c55c199ff135f2e28d88c87cc558dd13dd7e0080'),
]
known_ids = [x[1] for x in known]
to_test = known + [('disc_' + a[:8], a) for a in all_action_ids if a not in known_ids]

print(f"\n[2] Testando {len(to_test)} actions com telefone...")
for action_name, action_id in to_test:
    h2 = {
        "Content-Type": "text/plain;charset=UTF-8",
        "Next-Action": action_id,
        "Next-Router-State-Tree": "%5B%22%22%2C%7B%7D%2Cnull%2Cnull%2Ctrue%5D",
        "Origin": BASE,
        "Referer": f"{BASE}/app/modulos/investigador-osint",
        "Accept": "text/x-component",
    }
    r = s.post(f"{BASE}/app/modulos/investigador-osint",
               data=json.dumps([query]).encode(), headers=h2, timeout=30)
    blocks = re.findall(r'\d+:T[0-9a-fA-F]+,([A-Za-z0-9+/=]{20,})', r.text)
    landing = 'investigadores profissionais' in r.text
    print(f"  {action_name}: size={len(r.text)}, blocks={len(blocks)}, landing={landing}")
    if blocks and not landing:
        data = decrypt_resp(blocks[0])
        if data:
            print(f"    *** FUNCIONOU! Keys: {list(data.keys())[:8]}")
        else:
            print(f"    Block[:80]: {blocks[0][:80]}")
