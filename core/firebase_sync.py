import json
import urllib.request
import urllib.error
import logging
from datetime import datetime

FIREBASE_URL = "https://accdc-b7aa0-default-rtdb.firebaseio.com/"

def verificar_status_servico(timeout=4):
    """Verifica se o serviço Firebase/Network está acessível e operacional em tempo real."""
    if not FIREBASE_URL or "exemplo" in FIREBASE_URL:
        return False, "URL do Firebase não configurado."

    base_url = FIREBASE_URL.rstrip('/')
    url = f"{base_url}/network_logs.json?shallow=true"

    try:
        req = urllib.request.Request(url, method='GET')
        with urllib.request.urlopen(req, timeout=timeout) as response:
            if response.status in (200, 204):
                return True, "Online"
            return False, f"Código: {response.status}"
    except urllib.error.HTTPError as e:
        return False, f"HTTP {e.code}"
    except urllib.error.URLError as e:
        return False, f"Sem rede: {e.reason}"
    except Exception as e:
        return False, str(e)

def verificar_status_survey(timeout=4):
    """Verifica se o servidor STScI DSS (Survey) está acessível."""
    url = "https://archive.stsci.edu/"
    try:
        req = urllib.request.Request(url, method='GET', headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=timeout) as response:
            if response.status == 200:
                return True, "Online"
            return False, f"HTTP {response.status}"
    except Exception as e:
        return False, str(e)

def enviar_registo_network(user_name, country, target, ra, dec, telescope, focal, ccd, pixel_size):
    """Envia um novo registo de pesquisa para o Firebase na aba Network."""
    if not FIREBASE_URL or "exemplo" in FIREBASE_URL:
        logging.warning("[FIREBASE] URL inválido ou não configurado.")
        return False

    base_url = FIREBASE_URL.rstrip('/')
    url = f"{base_url}/network_logs.json"

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    user_tag = f"{user_name}@{country}"

    dados = {
        "user": user_tag,
        "datetime": timestamp,
        "target": target,
        "ra": ra,
        "dec": dec,
        "telescope": telescope,
        "focal": str(focal),
        "ccd": ccd,
        "pixel_size": str(pixel_size)
    }

    try:
        data_bytes = json.dumps(dados).encode('utf-8')
        req = urllib.request.Request(
            url,
            data=data_bytes,
            headers={'Content-Type': 'application/json'},
            method='POST'
        )
        with urllib.request.urlopen(req, timeout=5) as response:
            logging.info(f"[FIREBASE ENVIAR SUCCESS] Código HTTP: {response.status}")
            return response.status in (200, 201)
    except urllib.error.HTTPError as e:
        logging.error(f"[FIREBASE ERRO HTTP ENVIAR] Código: {e.code} | Razão: {e.reason}")
    except urllib.error.URLError as e:
        logging.error(f"[FIREBASE ERRO REDE ENVIAR] {e.reason}")
    except Exception as e:
        logging.error(f"[FIREBASE ERRO GERAL ENVIAR] {e}")
    return False

def carregar_registos_network():
    """Obtém todos os registos partilhados da rede a partir do Firebase e limpa os antigos se passar de 400."""
    if not FIREBASE_URL or "exemplo" in FIREBASE_URL:
        return []

    base_url = FIREBASE_URL.rstrip('/')
    url = f"{base_url}/network_logs.json"

    try:
        req = urllib.request.Request(url, method='GET')
        with urllib.request.urlopen(req, timeout=5) as response:
            if response.status == 200:
                body = response.read().decode('utf-8')
                data = json.loads(body)

                if data and isinstance(data, dict):
                    # Gestão de limite (Pruning): Se o total de entradas chegar a 400, apaga as mais antigas
                    if len(data) >= 400:
                        chaves_ordenadas = sorted(list(data.keys()))
                        chaves_a_apagar = chaves_ordenadas[:len(data) - 200]

                        for chave in chaves_a_apagar:
                            try:
                                del_url = f"{base_url}/network_logs/{chave}.json"
                                del_req = urllib.request.Request(del_url, method='DELETE')
                                urllib.request.urlopen(del_req, timeout=3)
                                data.pop(chave, None)
                            except Exception as ex:
                                logging.error(f"[FIREBASE PRUNE ERROR] Falha ao apagar registo antigo {chave}: {ex}")

                        logging.info(f"[FIREBASE PRUNE] Limpeza efetuada com sucesso: {len(chaves_a_apagar)} entradas antigas removidas da base de dados.")

                    lista = []
                    for key, val in data.items():
                        if isinstance(val, dict):
                            lista.append(val)
                    return lista
    except urllib.error.HTTPError as e:
        logging.error(f"[FIREBASE ERRO HTTP LER] Código: {e.code} | Permissão Negada ou Regra do Firebase bloqueando.")
    except Exception as e:
        logging.error(f"[FIREBASE ERRO LER] {e}")
    return []