# -*- coding: utf-8 -*-
import socket
import urllib.request
import urllib.parse
import json
import re
from astropy.coordinates import SkyCoord
import astropy.units as u

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
    except Exception:
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
    base_url = f"http://{host}:{port}/api/v1/telescope/0"
    print(f"[ASCOM LOG] Dispatching Slew command -> Target RA: {ra_str} | Dec: {dec_str} to {host}:{port}")
    try:
        # Normalizar string de RA (ex: '13h29 52.0s' -> '13h29m52.0s')
        ra_cleaned = ra_str.strip()
        ra_cleaned = re.sub(r'([0-9]+)h([0-9]+)\s+([0-9.]+)[a-zA-Z]*', r'\1h\2m\3s', ra_cleaned)

        # Normalizar string de Dec (ex: '47d11 42.0m' -> '47d11m42.0s')
        dec_cleaned = dec_str.strip()
        dec_cleaned = re.sub(r'([+-]?[0-9]+)d([0-9]+)\s+([0-9.]+)[a-zA-Z]*', r'\1d\2m\3s', dec_cleaned)

        # Converter coordenadas formatadas para valores decimais exigidos pelo protocolo Alpaca
        coord = SkyCoord(ra_cleaned, dec_cleaned, unit=(u.hourangle, u.deg))
        ra_val = coord.ra.hour
        dec_val = coord.dec.deg

        # 1. Desparquear o telescópio usando o método oficial Alpaca /unpark
        try:
            unpark_url = f"{base_url}/unpark"
            unpark_params = {"ClientTransactionID": 1, "ClientID": 1}
            unpark_data = urllib.parse.urlencode(unpark_params).encode('utf-8')
            req_unpark = urllib.request.Request(unpark_url, data=unpark_data, method='PUT')
            with urllib.request.urlopen(req_unpark, timeout=timeout):
                print("[ASCOM LOG] Telescope unparked successfully via /unpark.")
        except Exception as unpark_err:
            print(f"[ASCOM LOG] Info: Unpark command skipped or already unparked: {unpark_err}")

        # 2. Ativar o Tracking (Tracking = True) para evitar o erro de estado de seguimento
        try:
            tracking_url = f"{base_url}/tracking"
            tracking_params = {"Tracking": True, "ClientTransactionID": 2, "ClientID": 1}
            tracking_data = urllib.parse.urlencode(tracking_params).encode('utf-8')
            req_tracking = urllib.request.Request(tracking_url, data=tracking_data, method='PUT')
            with urllib.request.urlopen(req_tracking, timeout=timeout):
                print("[ASCOM LOG] Telescope tracking enabled automatically.")
        except Exception as tracking_err:
            print(f"[ASCOM LOG] Info: Enable tracking skipped or failed: {tracking_err}")

        # 3. Enviar o comando de Slew assíncrono
        url = f"{base_url}/slewtocoordinatesasync"
        params = {
            "RightAscension": ra_val,
            "Declination": dec_val,
            "ClientTransactionID": 3,
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
    except urllib.error.HTTPError as e:
        try:
            error_body = e.read().decode('utf-8')
            err_data = json.loads(error_body)
            err_msg = err_data.get("ErrorMessage", str(e))
            err_num = err_data.get("ErrorNumber", e.code)
            msg = f"ASCOM HTTP Error {err_num}: {err_msg}"
        except Exception:
            msg = f"ASCOM HTTP Error {e.code}: {str(e)}"
        print(f"[ASCOM LOG] ERROR: {msg}")
        return False, msg
    except Exception as e:
        msg = f"ASCOM Connection Error: {str(e)}"
        print(f"[ASCOM LOG] ERROR: {msg}")
        return False, msg