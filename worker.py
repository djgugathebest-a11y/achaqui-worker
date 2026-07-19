#!/usr/bin/env python3
"""
Achaqui Worker — Fix7 (Server Action direto, sem Playwright)
============================================================
Estratégia: login via API criptografada + consulta via Next.js Server Action
- Sem Playwright, sem WebSocket, sem Socket.IO
- Funciona 100% via HTTP com curl_cffi (impersonando Chrome)
- A Server Action retorna os dados encriptados com o mesmo sistema dracula
"""
import os, json, time, re, base64, hashlib, asyncio
from curl_cffi import requests as cf_req
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad, unpad

# ── Configurações ──────────────────────────────────────────────────────────────
BASE        = "https://detetiveforense.com"
DF_USER     = "edson102"
DF_PASS     = "123456789"
DF_PIN      = "162738"
FK          = os.environ.get("FIREBASE_API_KEY", "AIzaSyAoZYnDTl8WoCG5K3q6hjFQnVFmkAS6PZ8")
FS_BASE     = "https://firestore.googleapis.com/v1/projects/bancadamatriz-9f797/databases/(default)/documents"
NTFY_URL    = "https://ntfy.sh/achaqui-zapia-guga-secret-2025"
RUN_SEC     = int(os.environ.get("RUN_DURATION", "290"))

# ── Chave cripto ───────────────────────────────────────────────────────────────
i_map = {
    "utc":[77,114,88,110],"est":[55,76,98,72],"cst":[107,51,87,113],
    "mst":[82,104,74,53],"pst":[100,65,111,89],"gmt":[70,115,54,106],
    "cet":[113,78,103,52],"eet":[88,109,66,120],"ist":[57,85,116,75],
    "jst":[108,71,56,99],"kst":[80,105,90,55],"nst":[101,68,110,83],
    "hst":[66,119,77,97],"akt":[118,48,102,79],"wst":[106,81,72,114],
    "aet":[51,107,89,69]
}
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
            d = raw['dracula']
            kh = dec_str(d['encryptedAesKey'], KEY_O)
            k = bytes.fromhex(kh); iv = bytes.fromhex(d['iv'])
            return json.loads(unpad(AES.new(k, AES.MODE_CBC, iv).decrypt(base64.b64decode(d['encryptedText'])), 16))
        return raw
    except Exception as e:
        return None

# ── Server Action IDs (descobertos em 16/07/2026) ────────────────────────────
SA_ACTIONS = {
    'cpf':       '60cfed776b956422225588773edd905425a2a4d38f',
    'documentos':'602f465b6303b9e7917ab450d8681d9b5d2658342d',
    'nomeend':   '60144c075589c30f46302a88f0edd2cc868bf2e3c3',
    'cnpj':      '6065bdbf8e8208d4a7caa4c637ba22a4258d3313e7',
    'processos': '40c55c199ff135f2e28d88c87cc558dd13dd7e0080',
}

# ── Mapa productId -> consulta ────────────────────────────────────────────────
MODULE_MAP = {
    'cpf-basico':          {'action': 'cpf',        'field': 'cpf'},
    'cpf-completo':        {'action': 'cpf',        'field': 'cpf'},
    'cpf-pro':             {'action': 'cpf',        'field': 'cpf'},
    'nome-cpf':            {'action': 'cpf',        'field': 'cpf'},
    'parentes':            {'action': 'cpf',        'field': 'cpf'},
    'historico-enderecos': {'action': 'cpf',        'field': 'cpf'},
    'telefones':           {'action': 'cpf',        'field': 'cpf'},
    'foto-redes':          {'action': 'cpf',        'field': 'cpf'},
    'localizar-pessoa':    {'action': 'cpf',        'field': 'cpf'},
    'localizar-celular':   {'action': 'cpf',        'field': 'cpf'},
    'investigar-pessoa':   {'action': 'cpf',        'field': 'cpf'},
    'processos':           {'action': 'processos',  'field': 'cpf'},
    'cnpj-basico':         {'action': 'cnpj',       'field': 'cnpj'},
    'cnpj-completo':       {'action': 'cnpj',       'field': 'cnpj'},
    'nome-endereco':       {'action': 'nomeend',    'field': 'nome'},
    'placa-veiculo':       {'action': 'documentos', 'field': 'placa'},
}

# ── Login ──────────────────────────────────────────────────────────────────────
def api_login():
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
    if d2 and d2.get('success'):
        token = dict(s.cookies).get('accessToken', '')
        print(f"[Login] OK, token: {token[:20]}...")
        return s, token
    print('[Login] Falhou')
    return None, None

# ── Consulta via Server Action ────────────────────────────────────────────────
def consultar(session, product_id, query_data):
    module = MODULE_MAP.get(product_id) or MODULE_MAP.get('cpf-basico')
    action_name = module['action']
    action_id = SA_ACTIONS.get(action_name, SA_ACTIONS['cpf'])
    
    # Limpa query (remove pontos, traços, espaços)
    query_clean = re.sub(r'[\.\-/\s]', '', str(query_data).strip())
    
    print(f"[Consulta] product={product_id} | action={action_name} | query={query_clean}")
    
    payload = json.dumps([query_clean]).encode()
    headers = {
        "Content-Type": "text/plain;charset=UTF-8",
        "Next-Action": action_id,
        "Next-Router-State-Tree": '%5B%22%22%2C%7B%7D%2Cnull%2Cnull%2Ctrue%5D',
        "Origin": BASE,
        "Referer": f"{BASE}/app/modulos/investigador-osint",
        "Accept": "text/x-component",
    }
    
    try:
        r = session.post(
            f"{BASE}/app/modulos/investigador-osint",
            data=payload,
            headers=headers,
            timeout=40
        )
        print(f"[Consulta] HTTP {r.status_code}, size={len(r.text)}")
        
        # Verificação rápida: se a resposta contém marcadores da landing page, sessão expirou
        LANDING_MARKERS = ['investigadores profissionais', 'Acesse sua conta para continuar', 'word word word']
        if any(m in r.text for m in LANDING_MARKERS):
            print("[Consulta] Resposta é landing page — sessão expirada")
            return None
        
        # Extrai e descriptografa — regex aceita tamanho em decimal OU hex (Next.js RSC)
        b64_blocks = re.findall(r'\d+:T[0-9a-fA-F]+,([A-Za-z0-9+/=]{20,})', r.text)
        if not b64_blocks:
            print("[Consulta] Sem blocos de dados na resposta")
            return None
        
        print(f"[Consulta] {len(b64_blocks)} bloco(s) encontrado(s), tamanhos: {[len(b) for b in b64_blocks[:5]]}")
        
        data = None
        for blk in b64_blocks:
            candidate = decrypt_resp(blk)
            if candidate is not None and isinstance(candidate, dict):
                # Valida que é dado real — chaves específicas dos módulos do Detetive Forense
                valid_keys = {'cpf', 'consulta', 'cnpj', 'documentos', 'processos', 'nomeend'}
                if any(k in candidate for k in valid_keys):
                    data = candidate
                    break
                # Também aceita se tem 'success': True (login ok) + alguma chave de dados
                if candidate.get('success') is True and len(candidate) > 2:
                    data = candidate
                    break
                else:
                    print(f"[Consulta] Bloco descartado (keys inválidas): {list(candidate.keys())[:5]}")
        
        if data is None:
            print("[Consulta] Nenhum bloco com dados válidos encontrado")
            return None
        
        print(f"[Consulta] Dados OK! Keys: {list(data.keys())[:5]}")
        return data
        
    except Exception as e:
        print(f"[Consulta] Erro: {e}")
        return None

# ── Formata resultado ──────────────────────────────────────────────────────────
def formatar_resultado(data, product_id, query_data, product_name):
    from datetime import datetime
    agora = datetime.now().strftime("%d/%m/%Y às %H:%M")
    
    linhas = [
        "🔍 RELATÓRIO — ACHAQUI",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"📋 {product_name}",
        f"🔎 Consulta: {query_data}",
        f"📅 {agora}",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        "",
    ]
    
    cad = data.get('consulta', {}).get('cadastral', {})
    
    if cad:
        linhas.append("👤 DADOS PESSOAIS")
        if cad.get('nome'): linhas.append(f"  Nome: {cad['nome']}")
        if cad.get('cpfMask'): linhas.append(f"  CPF: {cad['cpfMask']}")
        if cad.get('dataNasc'): linhas.append(f"  Nascimento: {cad['dataNasc']} ({cad.get('idade','')} anos)")
        if cad.get('naturalidade'): linhas.append(f"  Naturalidade: {cad['naturalidade']}")
        if cad.get('sexo'): linhas.append(f"  Sexo: {'Masculino' if cad['sexo']=='M' else 'Feminino' if cad['sexo']=='F' else cad['sexo']}")
        if cad.get('renda'): linhas.append(f"  Renda estimada: {cad['renda']}")
        if cad.get('escolaridade'): linhas.append(f"  Escolaridade: {cad['escolaridade'].title()}")
        if cad.get('indicativoCriminal'): linhas.append(f"  ⚠️ Indicativo criminal: SIM")
        
        mae = cad.get('mae', {})
        pai = cad.get('pai', {})
        if mae and mae.get('nome'):
            linhas.append(f"  Mãe: {mae['nome']}")
        if pai and pai.get('nome'):
            linhas.append(f"  Pai: {pai['nome']}")
        linhas.append("")
    
    consulta = data.get('consulta', {})
    
    # Endereços
    enderecos = consulta.get('enderecos', [])
    if enderecos:
        linhas.append(f"📍 ENDEREÇOS ({len(enderecos)})")
        for i, end in enumerate(enderecos[:3]):
            parts = []
            if end.get('logradouro'): parts.append(end['logradouro'])
            if end.get('numero'): parts.append(end['numero'])
            if end.get('bairro'): parts.append(end['bairro'])
            if end.get('cidade'): parts.append(end['cidade'])
            if end.get('uf'): parts.append(end['uf'])
            if end.get('cep'): parts.append(f"CEP {end['cep']}")
            if parts: linhas.append(f"  {i+1}. {', '.join(str(p) for p in parts if p)}")
        linhas.append("")
    
    # Telefones
    telefones = consulta.get('telefones', [])
    if telefones:
        linhas.append(f"📞 TELEFONES ({len(telefones)})")
        for tel in telefones[:5]:
            num = tel.get('numero') or tel.get('telefone') or str(tel)
            op = tel.get('operadora', '')
            linhas.append(f"  {num}" + (f" ({op})" if op else ""))
        linhas.append("")
    
    # Emails
    emails = consulta.get('emails', [])
    if emails:
        linhas.append(f"✉️ EMAILS ({len(emails)})")
        for em in emails[:3]:
            linhas.append(f"  {em.get('email', str(em))}")
        linhas.append("")
    
    # Processos
    processos = consulta.get('processos', [])
    if processos:
        linhas.append(f"⚖️ PROCESSOS ({len(processos)})")
        for proc in processos[:3]:
            classe = proc.get('classe', '')
            assunto = proc.get('assunto', '')
            tribunal = proc.get('tribunal', '')
            linhas.append(f"  • {classe}" + (f" — {assunto}" if assunto else "") + (f" ({tribunal})" if tribunal else ""))
        linhas.append("")
    
    # Parentes
    parentes = consulta.get('parentes', [])
    if parentes:
        linhas.append(f"👨‍👩‍👧 PARENTES ({len(parentes)})")
        for par in parentes[:4]:
            nome = par.get('nome', '')
            grau = par.get('grau', '')
            if nome: linhas.append(f"  {nome}" + (f" ({grau})" if grau else ""))
        linhas.append("")
    
    linhas.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    linhas.append("🔐 Achaqui — Consulta confidencial")
    
    return "\n".join(linhas)

# ── Firestore helpers ─────────────────────────────────────────────────────────
import urllib.request as _ur

def fs_get(doc_path):
    url = f"{FS_BASE}/{doc_path}?key={FK}"
    try:
        with _ur.urlopen(_ur.Request(url), timeout=10) as r:
            return json.loads(r.read()).get('fields', {})
    except Exception as e:
        print(f"[FS] GET error: {e}")
        return {}

def fs_patch(doc_path, fields):
    names = ','.join(fields.keys())
    url = f"{FS_BASE}/{doc_path}?key={FK}&updateMask.fieldPaths={'&updateMask.fieldPaths='.join(fields.keys())}"
    body = json.dumps({"fields": fields}).encode()
    req = _ur.Request(url, data=body,
        headers={"Content-Type": "application/json"}, method="PATCH")
    try:
        with _ur.urlopen(req, timeout=10) as r:
            return json.loads(r.read())
    except Exception as e:
        print(f"[FS] PATCH error: {e}")
        return {}

def fs_query():
    url = f"{FS_BASE}:runQuery?key={FK}"
    body = json.dumps({"structuredQuery": {
        "from": [{"collectionId": "orders"}],
        "where": {"fieldFilter": {
            "field": {"fieldPath": "status"},
            "op": "EQUAL",
            "value": {"stringValue": "processing"}
        }},
        "limit": 5
    }}).encode()
    req = _ur.Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with _ur.urlopen(req, timeout=10) as r:
            results = json.loads(r.read())
            pedidos = []
            for item in results:
                doc = item.get('document')
                if doc:
                    fields = doc.get('fields', {})
                    oid = doc['name'].split('/')[-1]
                    pedidos.append({
                        'id': oid,
                        'productId':   fields.get('productId',{}).get('stringValue','cpf-basico'),
                        'productName': fields.get('productName',{}).get('stringValue','Consulta'),
                        'queryData':   fields.get('queryData',{}).get('stringValue',''),
                        'userId':      fields.get('userId',{}).get('stringValue',''),
                    })
            return pedidos
    except Exception as e:
        print(f"[FS] Query error: {e}")
        return []

def ntfy_send(title, msg):
    try:
        req = _ur.Request(NTFY_URL,
            data=msg.encode(),
            headers={"Title": title, "Priority": "high", "Tags": "mag"},
            method="POST")
        _ur.urlopen(req, timeout=5)
    except Exception as e:
        print(f"[NTFY] {e}")

# ── Validação de dados de entrada ─────────────────────────────────────────────
def validar_dado(product_id, query_data):
    """Retorna (ok, motivo) — motivo só preenchido quando inválido."""
    import re
    q = re.sub(r'[\.\-/\s]', '', str(query_data).strip())
    module = MODULE_MAP.get(product_id, {})
    field = module.get('field', 'cpf')

    if field == 'cpf':
        if len(q) != 11 or not q.isdigit() or len(set(q)) == 1:
            return False, f"CPF '{query_data}' inválido (deve ter 11 dígitos numéricos)."
        # Dígitos verificadores
        for i in range(9, 11):
            soma = sum(int(q[j]) * ((i + 1) - j) for j in range(i))
            dig = (soma * 10 % 11) % 10
            if dig != int(q[i]):
                return False, f"CPF '{query_data}' não existe na Receita Federal (dígito verificador inválido)."
        return True, ""

    elif field == 'cnpj':
        if len(q) != 14 or not q.isdigit() or len(set(q)) == 1:
            return False, f"CNPJ '{query_data}' inválido (deve ter 14 dígitos)."
        return True, ""

    elif field == 'placa':
        if not re.match(r'^[A-Za-z]{3}\d[A-Za-z0-9]\d{2}$', q):
            return False, f"Placa '{query_data}' inválida (formato esperado: ABC1234 ou ABC1D23)."
        return True, ""

    elif field == 'nome':
        if len(query_data.strip()) < 3:
            return False, f"Nome '{query_data}' muito curto."
        return True, ""

    # Outros campos — aceita qualquer coisa com ao menos 5 chars
    if len(q) < 5:
        return False, f"Dado '{query_data}' muito curto para consulta."
    return True, ""


def main():
    print("[Achaqui Worker] Fix7 — Server Action direto (sem Playwright)")
    
    session, token = api_login()
    if not session:
        print("[Worker] Login falhou, abortando")
        return
    
    t0 = time.time()
    last_fs = 0
    pedidos_ok = 0
    
    while time.time() - t0 < RUN_SEC:
        now = time.time()
        
        # Poll Firestore a cada 15s
        if now - last_fs >= 15:
            last_fs = now
            pedidos = fs_query()
            print(f"[Worker] {len(pedidos)} pedido(s) pendente(s)")
            
            for pedido in pedidos:
                oid = pedido['id']
                product_id = pedido['productId']
                product_name = pedido['productName']
                query_data = pedido['queryData']
                
                print(f"[Worker] Processando {oid} | {product_id} | {query_data}")
                
                from datetime import datetime, timezone
                ts = datetime.now(timezone.utc).isoformat()

                # Valida o dado antes de consultar
                dado_ok, motivo = validar_dado(product_id, query_data)
                if not dado_ok:
                    print(f"[Worker] Dado inválido: {motivo}")
                    resultado = (
                        "⚠️ DADO INVÁLIDO\n"
                        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                        f"{motivo}\n\n"
                        "Por favor, verifique o dado informado e entre em contato conosco "
                        "para corrigir ou solicitar reembolso de saldo."
                    )
                    fields = {
                        'status':      {'stringValue': 'invalid_data'},
                        'result':      {'stringValue': resultado},
                        'deliveredAt': {'stringValue': ts},
                    }
                    fs_patch(f"orders/{oid}", fields)
                    print(f"[Worker] {oid} -> invalid_data")
                    ntfy_send(f"Achaqui: dado inválido", f"{product_id} | {query_data}\n{motivo}")
                    continue

                # Faz a consulta com retry — renova sessão sempre que necessário
                MAX_TENTATIVAS = 5
                data = None
                for tentativa in range(1, MAX_TENTATIVAS + 1):
                    data = consultar(session, product_id, query_data)
                    if data:
                        break
                    print(f"[Worker] Tentativa {tentativa}/{MAX_TENTATIVAS} falhou — renovando sessão...")
                    session, token = api_login()
                    if not session:
                        print("[Worker] Login falhou, aguardando 10s...")
                        time.sleep(10)
                        session, token = api_login()  # segunda chance
                    time.sleep(3)

                if data:
                    resultado = formatar_resultado(data, product_id, query_data, product_name)
                    # Verificação final: resultado real tem pelo menos Nome ou Endereço
                    # Se contiver texto da landing page → rejeitar
                    landing_markers = ['A melhor plataforma exclusiva', 'investigadores profissionais', 'word word word', 'mmMwWLliI0']
                    resultado_invalido = any(m in resultado for m in landing_markers)
                    # Resultado útil deve ter ao menos uma linha de dado (nome, CPF, endereço)
                    tem_dado = any(x in resultado for x in ['Nome:', 'CPF:', 'Endereço', 'TELEFONE', 'ENDEREÇO', 'Nascimento:', 'Titular:', 'Proprietário:'])
                    
                    if resultado_invalido or not tem_dado:
                        print(f"[Worker] Resultado rejeitado (landing page ou sem dados). Tentando novamente...")
                        data = None  # força retry
                        status = 'processing'
                    else:
                        status = 'done'
                        print(f"[Worker] Consulta OK (tentativa {tentativa}): {resultado[:80]}...")
                else:
                    # Todas as tentativas falharam — mantém processing para próximo run
                    print(f"[Worker] {oid} falhou após {MAX_TENTATIVAS} tentativas — mantendo processing")
                    resultado = ""
                    status = 'processing'
                
                # Salva no Firestore (se processing, não sobrescreve resultado)
                if status == 'done':
                    fields = {
                        'status':      {'stringValue': status},
                        'result':      {'stringValue': resultado},
                        'deliveredAt': {'stringValue': ts},
                    }
                    fs_patch(f"orders/{oid}", fields)
                # Se processing, não faz nada — o próximo run vai tentar de novo
                print(f"[Worker] {oid} -> {status}")
                
                # Notifica
                ntfy_send(f"Achaqui: {product_name}", f"{query_data}\n{status}")
                pedidos_ok += 1
                
                time.sleep(2)
        
        time.sleep(5)
    
    print(f"[Worker] {RUN_SEC}s atingido. Processados: {pedidos_ok}")

if __name__ == '__main__':
    main()
