import os
import json
import sys
import getpass
import locale
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

def obter_caminho_asset(nome_ficheiro):
    """Retorna o caminho absoluto para ficheiros na pasta assets/ (compatível com dev e PyInstaller)."""
    if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
        caminho_bundle = os.path.join(sys._MEIPASS, "assets", nome_ficheiro)
        if os.path.exists(caminho_bundle):
            return caminho_bundle
    base_dir = obter_caminho_base()
    caminho = os.path.join(base_dir, "assets", nome_ficheiro)
    if os.path.exists(caminho):
        return caminho
    caminho_alt = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", nome_ficheiro)
    if os.path.exists(caminho_alt):
        return caminho_alt
    return os.path.join("assets", nome_ficheiro)

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
        except Exception as e:
            print(f"[ERROR LOADING INSTRUMENTS] {e}")
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
        except Exception as e:
            print(f"[ERROR LOADING HISTORY] {e}")
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
    """Carrega as definições do ficheiro settings.json ou devolve padrões dinâmicos baseados no sistema."""
    caminho = obter_caminho_dados("settings.json")

    # Deteção dinâmica do nome de utilizador do sistema operativo
    try:
        default_user = getpass.getuser()
    except Exception:
        default_user = "astronomer"

    # Deteção dinâmica do país do sistema operativo (ex: 'pt_PT' -> 'pt')
    try:
        loc = locale.getdefaultlocale()[0]
        default_country = loc.split('_')[-1].lower() if loc and '_' in loc else "pt"
    except Exception:
        default_country = "pt"

    defaults = {
        "user_name": default_user,
        "user_country": default_country,
        "share_network": True,
        "ascom_host": "127.0.0.1",
        "ascom_port": "11111",
        "ascom_enabled": False,
        "indi_host": "127.0.0.1",
        "indi_port": "7624",
        "indi_enabled": False,
        "last_telescope": "",
        "last_ccd": ""
    }

    if os.path.exists(caminho):
        try:
            with open(caminho, 'r', encoding='utf-8') as f:
                dados = json.load(f)
                # Assegura que todas as chaves obrigatórias existem sem apagar valores guardados
                for chave, valor_padrao in defaults.items():
                    dados.setdefault(chave, valor_padrao)
                return dados
        except Exception as e:
            print(f"[ERROR LOADING SETTINGS] {e} - Could not parse settings.json, falling back to defaults.")

    return defaults

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