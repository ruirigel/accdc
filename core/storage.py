import os
import json
import sys
from datetime import datetime

def obter_caminho_base():
    """Retorna o diretório base correto, compatível com aplicações compiladas (PyInstaller)."""
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def obter_caminho_dados(nome_ficheiro):
    """Retorna o caminho absoluto para os ficheiros JSON e logs na pasta data/"""
    base_dir = obter_caminho_base()
    pasta_data = os.path.join(base_dir, "data")
    if not os.path.exists(pasta_data):
        os.makedirs(pasta_data)
    return os.path.join(pasta_data, nome_ficheiro)

class TeeStream:
    """Duplica o stdout/stderr para a consola e para o ficheiro de log (app.log) com data e hora."""
    def __init__(self, filename):
        self.terminal = sys.stdout
        self.log = open(filename, "a", encoding="utf-8")
        self.line_start = True

    def write(self, message):
        if self.terminal:
            try:
                self.terminal.write(message)
            except Exception:
                pass
        try:
            if message:
                if self.line_start and message.strip():
                    timestamp = datetime.now().strftime("[%Y-%m-%d %H:%M:%S] ")
                    self.log.write(timestamp)
                self.log.write(message)
                self.line_start = message.endswith('\n')
                self.log.flush()
        except Exception:
            pass

    def flush(self):
        if self.terminal:
            try:
                self.terminal.flush()
            except Exception:
                pass
        try:
            self.log.flush()
        except Exception:
            pass

def setup_logging():
    """Configura a geração automática do ficheiro de logs com data e hora para fins de suporte e depuração."""
    try:
        log_path = obter_caminho_dados("app.log")
        sys.stdout = TeeStream(log_path)
        sys.stderr = TeeStream(log_path)
        print(f"--- [LOG STARTED] {os.path.basename(log_path)} ---")
    except Exception as e:
        print(f"[LOG SETUP ERROR] {e}")

def carregar_instrumentos():
    """Carrega a lista de CCDs e telescópios do disco."""
    caminho = obter_caminho_dados("instruments.json")
    if os.path.exists(caminho):
        try:
            with open(caminho, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            pass
    return {
        "ccds": [{"name": "Custom Camera", "pixel_x": "6.45", "pixel_y": "6.45", "width": "1392", "height": "1040"}],
        "telescopes": [{"name": "Custom Telescope", "focal": "500", "aperture": "80"}]
    }

def guardar_instrumentos(ccds, telescopes):
    """Guarda a lista de CCDs e telescópios no disco."""
    caminho = obter_caminho_dados("instruments.json")
    try:
        dados = {"ccds": ccds, "telescopes": telescopes}
        with open(caminho, 'w', encoding='utf-8') as f:
            json.dump(dados, f, indent=4, ensure_ascii=False)
        return True
    except Exception as e:
        print(f"[ERROR SAVING INSTRUMENTS] {e}")
        return False

def carregar_historico():
    """Carrega o histórico de pesquisas locais do disco."""
    caminho = obter_caminho_dados("history.json")
    if os.path.exists(caminho):
        try:
            with open(caminho, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            pass
    return []

def guardar_historico(historico_lista):
    """Guarda o histórico de pesquisas no disco."""
    caminho = obter_caminho_dados("history.json")
    try:
        with open(caminho, 'w', encoding='utf-8') as f:
            json.dump(historico_lista, f, indent=4, ensure_ascii=False)
        return True
    except Exception as e:
        print(f"[ERROR SAVING HISTORY] {e}")
        return False

def carregar_settings():
    """Carrega as definições do ficheiro settings.json ou devolve padrões."""
    caminho = obter_caminho_dados("settings.json")
    if os.path.exists(caminho):
        try:
            with open(caminho, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            pass
    return {
        "user_name": "rui",
        "user_country": "pt",
        "share_network": True,
        "ascom_host": "127.0.0.1",
        "ascom_port": "11111",
        "ascom_enabled": False
    }

def guardar_settings(settings_dict):
    """Guarda as definições no disco."""
    caminho = obter_caminho_dados("settings.json")
    try:
        with open(caminho, 'w', encoding='utf-8') as f:
            json.dump(settings_dict, f, indent=4, ensure_ascii=False)
        return True
    except Exception as e:
        print(f"[ERROR SAVING SETTINGS] {e}")
        return False