def calculate_image_scale(focal_mm, pixel_um):
    """Calcula a escala de imagem em arcsec/px"""
    if focal_mm <= 0:
        return 0.0
    return (pixel_um / focal_mm) * 206.265

def calculate_fov(focal_mm, pixel_um, width_px, height_px):
    """Calcula o Campo de Visão (FOV) em arcmin e graus"""
    scale_arcsec_px = calculate_image_scale(focal_mm, pixel_um)
    width_arcmin = (scale_arcsec_px * width_px) / 60.0
    height_arcmin = (scale_arcsec_px * height_px) / 60.0
    return {
        "width_arcmin": width_arcmin,
        "height_arcmin": height_arcmin,
        "width_deg": width_arcmin / 60.0,
        "height_deg": height_arcmin / 60.0
    }

def calculate_focal_ratio(focal_mm, aperture_mm):
    """Calcula a razão focal (f/#) do telescópio"""
    if aperture_mm <= 0:
        return 0.0
    return focal_mm / aperture_mm