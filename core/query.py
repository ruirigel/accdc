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

def formatar_coordenadas_astropy(ra_raw, dec_raw):
    """
    Tenta converter RA/Dec em string sexagesimal (ex: 03:33.0, +31:10) num formato SkyCoord estandardizado.
    """
    ra_str = ra_raw.strip()
    dec_str = dec_raw.strip()
    try:
        # Tentar ler como horas e graus
        coord = SkyCoord(f"{ra_str} {dec_str}", unit=(u.hourangle, u.deg))
        ra_fmt = coord.ra.to_string(unit='hour', sep='h ', precision=1) + 's'
        dec_fmt = coord.dec.to_string(unit='deg', sep='d ', precision=1) + 'm'
        return ra_fmt, dec_fmt
    except Exception:
        # Tentar ler como graus decimais/sexagesimais genéricos
        try:
            coord = SkyCoord(f"{ra_str} {dec_str}", unit=(u.deg, u.deg))
            ra_fmt = coord.ra.to_string(unit='hour', sep='h ', precision=1) + 's'
            dec_fmt = coord.dec.to_string(unit='deg', sep='d ', precision=1) + 'm'
            return ra_fmt, dec_fmt
        except Exception:
            return ra_str, dec_str

def procurar_objeto_em_catalogos_dat(nome_alvo, pasta_dados=None):
    """
    Procura o objeto nos ficheiros .dat na pasta de dados.
    Suporta formato Nome,RA,Dec (ex: B1,03:33.0,+31:10).
    """
    if pasta_dados is None:
        pasta_dados = os.path.join(obter_caminho_base(), "data")

    if not os.path.exists(pasta_dados):
        return None

    alvo_limpo = nome_alvo.strip().upper().replace(" ", "")

    for ficheiro in os.listdir(pasta_dados):
        if ficheiro.lower().endswith(".dat"):
            caminho = os.path.join(pasta_dados, ficheiro)
            try:
                with open(caminho, 'r', encoding='utf-8', errors='ignore') as f:
                    for linha in f:
                        linha = linha.strip()
                        if not linha or linha.startswith('#'):
                            continue
                        partes = [p.strip() for p in linha.split(',')]
                        if len(partes) >= 3:
                            obj_nome = partes[0].upper()
                            obj_nome_sem_espaco = obj_nome.replace(" ", "")

                            if obj_nome_sem_espaco == alvo_limpo or obj_nome == nome_alvo.strip().upper():
                                ra_fmt, dec_fmt = formatar_coordenadas_astropy(partes[1], partes[2])
                                return {
                                    "name": partes[0],
                                    "ra": ra_fmt,
                                    "dec": dec_fmt,
                                    "raw_ra": partes[1],
                                    "raw_dec": partes[2],
                                    "catalogo": ficheiro,
                                    "status": f"Sucesso ({ficheiro})"
                                }
            except Exception as e:
                print(f"[ERROR READING DAT] {ficheiro}: {e}")

    return None

def obter_todos_objetos_dat(pasta_dados=None):
    """
    Retorna um dicionário agregando todos os objetos encontrados nos ficheiros .dat da pasta data.
    Organizado por nome do catálogo/ficheiro.
    """
    if pasta_dados is None:
        pasta_dados = os.path.join(obter_caminho_base(), "data")

    catalogos = {}

    if not os.path.exists(pasta_dados):
        return catalogos

    for ficheiro in os.listdir(pasta_dados):
        if ficheiro.lower().endswith(".dat"):
            caminho = os.path.join(pasta_dados, ficheiro)
            cat_name = os.path.splitext(ficheiro)[0].upper()
            itens = []
            try:
                with open(caminho, 'r', encoding='utf-8', errors='ignore') as f:
                    for linha in f:
                        linha = linha.strip()
                        if not linha or linha.startswith('#'):
                            continue
                        partes = [p.strip() for p in linha.split(',')]
                        if len(partes) >= 3:
                            itens.append({
                                "name": partes[0],
                                "ra": partes[1],
                                "dec": partes[2]
                            })
            except Exception as e:
                print(f"[ERROR LOADING DAT] {ficheiro}: {e}")

            if itens:
                catalogos[cat_name] = itens

    return catalogos

def query_target_coordinates(nome_alvo):
    alvo_original = nome_alvo.strip()
    alvo_up = alvo_original.upper()
    print(f"[COORDINATES] Querying coordinates for target: '{alvo_original}'...")

    # 1. Tentar encontrar primeiro nos ficheiros .dat locais
    res_dat = procurar_objeto_em_catalogos_dat(alvo_original)
    if res_dat:
        print(f"[COORDINATES] Target '{alvo_original}' found in catalog '{res_dat.get('catalogo')}': RA={res_dat.get('ra')}, Dec={res_dat.get('dec')}")
        return res_dat

    # 2. Deteção inteligente: se o utilizador colar coordenadas brutas na textbox
    coord_pattern = re.compile(r'([\d\.\:]+[hms]?\s*[\+\-]?[\d\.\:\°\′\″]+)', re.IGNORECASE)
    if coord_pattern.search(alvo_original) and any(char in alvo_original for char in ['+', '-', 'h', '°', ':', ',']):
        try:
            coord = SkyCoord(alvo_original, unit=(u.hourangle, u.deg) if 'h' in alvo_original else u.deg)
            ra_str = coord.ra.to_string(unit='hour', sep='h ', precision=1) + 's'
            dec_str = coord.dec.to_string(unit='deg', sep='d ', precision=1) + 'm'
            print(f"[COORDINATES] Manual coordinates parsed: RA={ra_str}, Dec={dec_str}")
            return {
                "name": alvo_original,
                "ra": ra_str,
                "dec": dec_str,
                "status": "Success (Manual Coordinates)"
            }
        except Exception:
            pass

    # 3. Consulta online Astropy SIMBAD / CDS por nome
    alvo_sem_espacos = alvo_up.replace(" ", "")
    try:
        coord = SkyCoord.from_name(alvo_sem_espacos)
        ra_str = coord.ra.to_string(unit='hour', sep='h ', precision=1) + 's'
        dec_str = coord.dec.to_string(unit='deg', sep='d ', precision=1) + 'm'
        print(f"[COORDINATES] Target '{alvo_original}' resolved via SIMBAD/CDS: RA={ra_str}, Dec={dec_str}")
        return {
            "name": alvo_original,
            "ra": ra_str,
            "dec": dec_str,
            "status": "Success"
        }
    except Exception:
        pass

    # 4. Catálogo local de fallback básico
    if alvo_sem_espacos in CATALOGO_LOCAL:
        item = CATALOGO_LOCAL[alvo_sem_espacos]
        print(f"[COORDINATES] Target '{alvo_original}' found in local offline database: RA={item['ra']}, Dec={item['dec']}")
        return {
            "name": item["name"],
            "ra": item["ra"],
            "dec": item["dec"],
            "status": "Success (Offline)"
        }

    print(f"[COORDINATES WARNING] Target '{alvo_original}' could not be resolved.")
    return {"status": "Object not found."}

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
    Descarrega o fundo do NASA SkyView (DSS) usando o nome do objeto ou as coordenadas brutas dos catálogos .dat.
    """
    caminho_cache, fov_efetivo = obter_caminho_cache(nome_alvo, fov_solicitado)

    if os.path.exists(caminho_cache):
        print(f"[CACHE] Image found locally for '{nome_alvo}' ({fov_efetivo:.1f}'): {caminho_cache}")
        if progress_callback:
            progress_callback(100)
        return caminho_cache, fov_efetivo

    # Se for um objeto do catálogo .dat, enviamos as coordenadas exatas para o SkyView em vez do nome!
    res_dat = procurar_objeto_em_catalogos_dat(nome_alvo)
    if res_dat and "raw_ra" in res_dat and "raw_dec" in res_dat:
        posicao_query = f"{res_dat['raw_ra']} {res_dat['raw_dec']}"
        print(f"[SkyView] Using exact .dat coordinates '{posicao_query}' for target '{nome_alvo}'")
    else:
        posicao_query = nome_alvo

    print(f"[SkyView] Downloading {fov_efetivo:.1f} arcmin background from NASA SkyView for '{posicao_query}'...")

    size_deg = fov_efetivo / 60.0
    url_skyview = "https://skyview.gsfc.nasa.gov/current/cgi/runquery.pl"

    params = {
        "Position": posicao_query,
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
                print("[WARNING SkyView] Server returned error HTML instead of an image.")
    except Exception as e:
        print(f"[WARNING] Exception connecting to NASA SkyView: {e}")

    raise RuntimeError("Could not retrieve image from NASA SkyView.")