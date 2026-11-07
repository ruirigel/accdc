from astropy.coordinates import SkyCoord
from astropy import units as u
import requests
import io
import os
import sys
import re
from PIL import Image

CATALOGO_LOCAL = {
    "M31": {"name": "M31 / Galáxia de Andrómeda", "ra": "00h 42m 44.3s", "dec": "+41° 16′ 09″"},
    "M42": {"name": "M42 / Nebulosa de Órion", "ra": "05h 35m 17.3s", "dec": "-05° 23′ 28″"},
    "M45": {"name": "M45 / Plêiades", "ra": "03h 47m 24.0s", "dec": "+24° 07′ 00″"},
    "NGC224": {"name": "NGC224 (M31)", "ra": "00h 42m 44.3s", "dec": "+41° 16′ 09″"},
    "M51": {"name": "M51 / Galáxia do Redemoinho", "ra": "13h 29m 52.7s", "dec": "+47° 11′ 43″"},
}

def obter_caminho_base():
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def query_target_coordinates(nome_alvo):
    alvo_original = nome_alvo.strip()
    alvo_up = alvo_original.upper()

    # Deteção inteligente: se o utilizador colar coordenadas brutas na textbox
    coord_pattern = re.compile(r'([\d\.\:]+[hms]?\s*[\+\-]?[\d\.\:\°\′\″]+)', re.IGNORECASE)
    if coord_pattern.search(alvo_original) and any(char in alvo_original for char in ['+', '-', 'h', '°', ':', ',']):
        try:
            coord = SkyCoord(alvo_original, unit=(u.hourangle, u.deg) if 'h' in alvo_original else u.deg)
            ra_str = coord.ra.to_string(unit='hour', sep='h ', precision=1) + 's'
            dec_str = coord.dec.to_string(unit='deg', sep='d ', precision=1) + 'm'
            return {
                "name": alvo_original,
                "ra": ra_str,
                "dec": dec_str,
                "status": "Sucesso (Coordenadas Manuais)"
            }
        except Exception:
            pass

    # Normalizar para catálogo removendo espaços internos (ex: "NGC 224" -> "NGC224", "M 1" -> "M1")
    alvo_sem_espacos = alvo_up.replace(" ", "")

    try:
        coord = SkyCoord.from_name(alvo_sem_espacos)
        ra_str = coord.ra.to_string(unit='hour', sep='h ', precision=1) + 's'
        dec_str = coord.dec.to_string(unit='deg', sep='d ', precision=1) + 'm'
        return {
            "name": alvo_original,
            "ra": ra_str,
            "dec": dec_str,
            "status": "Sucesso"
        }
    except Exception:
        pass

    if alvo_sem_espacos in CATALOGO_LOCAL:
        item = CATALOGO_LOCAL[alvo_sem_espacos]
        return {
            "name": item["name"],
            "ra": item["ra"],
            "dec": item["dec"],
            "status": "Sucesso (Offline)"
        }

    return {"status": "Objeto não encontrado."}

def obter_caminho_cache(nome_alvo, fov_calculado=None):
    """Retorna o caminho absoluto para a imagem em cache adaptado ao FOV dinâmico solicitado."""
    base_dir = obter_caminho_base()
    pasta_cache = os.path.join(base_dir, "data", "cache")
    if not os.path.exists(pasta_cache):
        os.makedirs(pasta_cache)

    alvo_limpo = "".join(c for c in nome_alvo.upper() if c.isalnum() or c in ('_', '-'))

    if fov_calculado and fov_calculado > 0:
        fov_efetivo = fov_calculado * 1.3
    else:
        fov_efetivo = 60.0

    return os.path.join(pasta_cache, f"{alvo_limpo}_{int(fov_efetivo)}m_skyview.jpg"), fov_efetivo

def fetch_target_image(nome_alvo, fov_solicitado=None, progress_callback=None):
    """
    Descarrega o fundo do NASA SkyView (DSS) com dimensionamento dinâmico correspondente ao FOV do utilizador.
    """
    caminho_cache, fov_efetivo = obter_caminho_cache(nome_alvo, fov_solicitado)

    if os.path.exists(caminho_cache):
        print(f"[CACHE] Imagem encontrada localmente para '{nome_alvo}' ({fov_efetivo:.1f}'): {caminho_cache}")
        if progress_callback:
            progress_callback(100)
        return caminho_cache, fov_efetivo

    print(f"[SkyView] A descarregar fundo de {fov_efetivo:.1f} arcmin do NASA SkyView para '{nome_alvo}'...")

    size_deg = fov_efetivo / 60.0
    url_skyview = "https://skyview.gsfc.nasa.gov/current/cgi/runquery.pl"

    params = {
        "Position": nome_alvo,
        "survey": "DSS",
        "coordinates": "J2000",
        "projection": "Tan",
        "pixels": "1200",
        "size": f"{size_deg:.4f}",
        "float": "on",
        "scaling": "Linear",
        "resolver": "SIMBAD-NED",
        "lut": "colortables/b-w-linear.bin",
        "Return": "JPG"
    }

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Connection": "keep-alive"
    }

    try:
        response = requests.get(url_skyview, params=params, headers=headers, stream=True, timeout=45)

        if response.status_code == 200:
            content_length = response.headers.get('content-length')
            total_size = int(content_length) if content_length and content_length.isdigit() else None

            if progress_callback:
                if total_size:
                    progress_callback(0)
                else:
                    progress_callback(-1)

            buffer = io.BytesIO()
            downloaded = 0
            for chunk in response.iter_content(chunk_size=16384):
                if chunk:
                    buffer.write(chunk)
                    downloaded += len(chunk)
                    if progress_callback and total_size and total_size > 0:
                        percent = min(int((downloaded / total_size) * 100), 99)
                        progress_callback(percent)

            conteudo = buffer.getvalue()
            if len(conteudo) > 1000 and b"<html" not in conteudo.lower() and b"<!doctype" not in conteudo.lower():
                image = Image.open(io.BytesIO(conteudo)).convert("RGB")
                image.save(caminho_cache, "JPEG", quality=95)

                if progress_callback:
                    progress_callback(100)
                return caminho_cache, fov_efetivo
            else:
                print("[AVISO SkyView] O servidor devolveu HTML de erro em vez de imagem.")
    except Exception as e:
        print(f"[AVISO] Exceção ao conectar com NASA SkyView: {e}")

    raise RuntimeError("Não foi possível obter a imagem do NASA SkyView.")