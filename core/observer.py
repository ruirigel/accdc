# -*- coding: utf-8 -*-
import re
from astropy.coordinates import EarthLocation, SkyCoord, AltAz
from astropy.time import Time
import astropy.units as u

def parse_ra_dec_robust(ra_val, dec_val):
    """Tenta múltiplos formatos de conversão de coordenadas RA e Dec."""
    ra_s = str(ra_val).strip()
    dec_s = str(dec_val).strip()

    if not ra_s or not dec_s:
        return None

    # 1. Tentar parse padrão de strings com unidades (ex: '05h34m31.9s', '+22d00m52s')
    try:
        return SkyCoord(ra_s, dec_s, frame='icrs')
    except Exception:
        pass

    # 2. Tentar substituição de caracteres especiais (° ′ ″) por espaços ou d/m/s
    ra_clean = re.sub(r'[hmsHMS\:\°\′\″]', ' ', ra_s).strip()
    dec_clean = re.sub(r'[dmsDMS\:\°\′\″]', ' ', dec_s).strip()

    try:
        return SkyCoord(f"{ra_clean} {dec_clean}", unit=(u.hourangle, u.deg), frame='icrs')
    except Exception:
        pass

    try:
        return SkyCoord(f"{ra_clean} {dec_clean}", unit=(u.deg, u.deg), frame='icrs')
    except Exception:
        pass

    # 3. Tentar conversão direta como floats
    try:
        ra_f = float(ra_s)
        dec_f = float(dec_s)
        # Se RA for < 24, assume horas; caso contrário, assume graus
        unit_ra = u.hourangle if ra_f <= 24.0 else u.deg
        return SkyCoord(ra=ra_f * unit_ra, dec=dec_f * u.deg, frame='icrs')
    except Exception:
        pass

    return None

def calcular_objetos_visiveis(catalogos_dat, lat, lon, min_alt=20.0, max_objetos=200):
    """
    Calcula de forma vetorizada quais os objetos dos catálogos locais estão acima
    da altitude mínima na localização e data/hora atuais.
    """
    try:
        print(f"[OBSERVER] A iniciar cálculo para Lat: {lat}, Lon: {lon}, Min Alt: {min_alt}°")

        if not catalogos_dat:
            print("[OBSERVER WARNING] O dicionário catalogos_dat está vazio!")
            return []

        location = EarthLocation(lat=lat * u.deg, lon=lon * u.deg, height=0 * u.m)
        now = Time.now()
        altaz_frame = AltAz(obstime=now, location=location)

        nomes = []
        catalogos = []
        ras_str = []
        decs_str = []
        mags = []  # <--- Lista para armazenar as magnitudes extraídas dos .dat
        ra_deg_list = []
        dec_deg_list = []

        catalogos_ignorados = {"SAO", "WDSD"}
        total_objs = 0
        parsed_count = 0

        for cat_name, objetos in catalogos_dat.items():
            if cat_name in catalogos_ignorados:
                continue

            for obj in objetos:
                total_objs += 1
                ra_str = obj.get("ra", "")
                dec_str = obj.get("dec", "")
                name = obj.get("name", "")

                # Extrair magnitude diretamente do dicionário do objeto lido do .dat
                mag_val = obj.get("mag", obj.get("magnitude", obj.get("v_mag", obj.get("vmag", None))))

                if not ra_str or not dec_str:
                    continue

                c = parse_ra_dec_robust(ra_str, dec_str)
                if c is not None:
                    parsed_count += 1
                    ras_str.append(str(ra_str))
                    decs_str.append(str(dec_str))
                    nomes.append(name)
                    catalogos.append(cat_name)
                    mags.append(mag_val)  # <--- Guardar a magnitude correspondente
                    ra_deg_list.append(c.ra.deg)
                    dec_deg_list.append(c.dec.deg)

        print(f"[OBSERVER] Objetos parseados com sucesso: {parsed_count} de {total_objs}")

        if not ra_deg_list:
            print("[OBSERVER WARNING] Nenhum objeto válido encontrado para processamento.")
            return []

        # Transformação vetorizada em lote
        coords = SkyCoord(ra=ra_deg_list * u.deg, dec=dec_deg_list * u.deg, frame='icrs')
        altaz = coords.transform_to(altaz_frame)
        altitudes = altaz.alt.degree
        azimutes = altaz.az.degree

        resultados = []
        for i in range(len(nomes)):
            alt = altitudes[i]
            if alt >= min_alt:
                if alt >= 60:
                    qualidade = "Prime (Zenith)"
                elif alt >= 40:
                    qualidade = "Good"
                else:
                    qualidade = "Low Horizon"

                resultados.append({
                    "name": nomes[i],
                    "catalog": catalogos[i],
                    "ra": ras_str[i],
                    "dec": decs_str[i],
                    "mag": mags[i],       # <--- Inserido corretamente no dicionário de saída
                    "altitude": f"{alt:.1f}°",
                    "azimuth": f"{azimutes[i]:.1f}°",
                    "quality": qualidade,
                    "alt_val": alt
                })

        print(f"[OBSERVER] Objetos acima de {min_alt}° de altitude: {len(resultados)}")

        # Ordenar por altitude decrescente
        resultados.sort(key=lambda x: x["alt_val"], reverse=True)
        return resultados[:max_objetos]

    except Exception as e:
        print(f"[OBSERVER CALC ERROR] {e}")
        import traceback
        traceback.print_exc()
        return []