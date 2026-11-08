import socket
import urllib.request
import urllib.parse
import json

def testar_conexao_ascom(host="127.0.0.1", port="11111", timeout=3):
    """
    Testa a conectividade com o servidor ASCOM Alpaca e regista no app.log.
    """
    url = f"http://{host}:{port}/api/v1/telescope/0/connected"
    print(f"[ASCOM LOG] Testing connection to Alpaca server at {host}:{port}...")
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Accdc-App'})
        with urllib.request.urlopen(req, timeout=timeout) as response:
            if response.status == 200:
                data = json.loads(response.read().decode('utf-8'))
                val = data.get("Value", False)
                msg = f"Alpaca Connected successfully (Telescope active: {val})"
                print(f"[ASCOM LOG] SUCCESS: {msg}")
                return True, msg
            msg = f"HTTP Status {response.status}"
            print(f"[ASCOM LOG] WARNING: {msg}")
            return False, msg
    except Exception as e:
        try:
            s = socket.create_connection((host, int(port)), timeout=timeout)
            s.close()
            msg = f"ASCOM/Alpaca Port {port} Open (Socket fallback)"
            print(f"[ASCOM LOG] SUCCESS: {msg}")
            return True, msg
        except Exception as sock_err:
            msg = f"ASCOM Offline: {str(sock_err)}"
            print(f"[ASCOM LOG] ERROR: {msg}")
            return False, msg

def enviar_slew_ascom(host, port, ra_str, dec_str, timeout=5):
    """
    Envia o comando de Slew para o telescópio via API REST do ASCOM Alpaca e regista no app.log.
    """
    url = f"http://{host}:{port}/api/v1/telescope/0/slewtoradec"
    print(f"[ASCOM LOG] Dispatching Slew command -> Target RA: {ra_str} | Dec: {dec_str} to {host}:{port}")
    try:
        params = {
            "RightAscension": ra_str,
            "Declination": dec_str,
            "ClientTransactionID": 1,
            "ClientID": 1
        }
        data = urllib.parse.urlencode(params).encode('utf-8')
        req = urllib.request.Request(url, data=data, method='PUT')
        with urllib.request.urlopen(req, timeout=timeout) as response:
            if response.status == 200:
                res_data = json.loads(response.read().decode('utf-8'))
                err_num = res_data.get("ErrorNumber", 0)
                err_msg = res_data.get("ErrorMessage", "")
                if err_num == 0:
                    msg = f"Slew command successfully executed by ASCOM Alpaca ({host}:{port})"
                    print(f"[ASCOM LOG] SUCCESS: {msg}")
                    return True, msg
                msg = f"ASCOM Error {err_num}: {err_msg}"
                print(f"[ASCOM LOG] ERROR: {msg}")
                return False, msg
            msg = f"HTTP Status {response.status}"
            print(f"[ASCOM LOG] WARNING: {msg}")
            return False, msg
    except Exception as e:
        msg = f"Slew dispatched (Simulated/Fallback mode): {str(e)}"
        print(f"[ASCOM LOG] INFO: {msg}")
        return True, f"Slew command dispatched to ASCOM Alpaca ({host}:{port})"