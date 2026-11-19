# -*- coding: utf-8 -*-
import sys
import os
import uuid
import math
import warnings
from datetime import datetime, time, timedelta
from PIL import Image as PILImage, ImageDraw, ImageFont
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QTabWidget,
    QVBoxLayout, QLabel, QGridLayout, QLineEdit, QPushButton, QGroupBox,
    QHBoxLayout, QComboBox, QCheckBox, QScrollArea, QTableWidget,
    QTableWidgetItem, QHeaderView, QMessageBox, QSizePolicy, QMenu, QProgressBar, QCompleter,
    QSplitter, QDialog, QAbstractItemView, QDoubleSpinBox, QSlider, QToolButton, QFileDialog
)
from PySide6.QtCore import Qt, QThread, Signal, QTimer, QLocale, QRegularExpression
from PySide6.QtGui import (
    QIcon, QPixmap, QImage, QStandardItemModel, QRegularExpressionValidator,
    QColor, QPainter, QPen, QFont
)

from astropy.coordinates import get_sun, EarthLocation, AltAz, SkyCoord
from astropy.time import Time
import astropy.units as u
from astropy.coordinates.baseframe import NonRotationTransformationWarning

# Ignorar o warning de transformação GCRS/ICRS no Astropy
warnings.simplefilter('ignore', NonRotationTransformationWarning)

# Matplotlib para o Gráfico de Elevação
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure

from core.calculator import calculate_image_scale, calculate_fov, calculate_focal_ratio
from core.query import (
    query_target_coordinates, fetch_target_image,
    obter_todos_objetos_dat
)
from core.storage import (
    carregar_instrumentos, guardar_instrumentos,
    carregar_historico, guardar_historico,
    carregar_favoritos, guardar_favoritos,
    carregar_settings, guardar_settings, setup_logging,
    obter_caminho_asset
)
from core.firebase_sync import (
    enviar_registo_network, carregar_registos_network,
    verificar_status_servico, verificar_status_survey
)
from core.ascom import testar_conexao_ascom, enviar_slew_ascom
from core.indi import testar_conexao_indi, enviar_slew_indi
from core.observer import calcular_objetos_visiveis
from core.version import VERSION

APP_VERSION = VERSION
__version__ = VERSION

FOV_PALETTE = [
    (0, 255, 255),    # Ciano
    (255, 215, 0),    # Amarelo/Dourado
    (255, 0, 255),    # Magenta
    (50, 205, 50),    # Verde Lima
    (255, 127, 80),   # Laranja Coral
    (30, 144, 255),   # Azul Elétrico
    (238, 130, 238)   # Violeta
]


class DraggableLabel(QLabel):
    zoom_in_requested = Signal()
    zoom_out_requested = Signal()

    def __init__(self, scroll_area, main_window=None, parent=None):
        super().__init__(parent)
        self.scroll_area = scroll_area
        self.main_window = main_window
        self.dragging = False
        self.dragging_fov = False
        self.drag_start_global_pos = None
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.drag_start_global_pos = event.globalPosition().toPoint()
            if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                self.dragging_fov = True
                self.setCursor(Qt.CursorShape.SizeAllCursor)
            else:
                self.dragging = True
                self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self.drag_start_global_pos is not None:
            cur_global_pos = event.globalPosition().toPoint()
            delta = cur_global_pos - self.drag_start_global_pos

            if self.dragging_fov and self.main_window:
                zoom = self.main_window.zoom_factor
                if zoom > 0:
                    base_w, base_h = self.main_window.get_transformed_base_dimensions()
                    bg_fov = self.main_window.current_bg_fov
                    pixels_per_arcmin_x = base_w / bg_fov
                    pixels_per_arcmin_y = base_h / bg_fov

                    effective_pp_x = pixels_per_arcmin_x * zoom
                    effective_pp_y = pixels_per_arcmin_y * zoom

                    if effective_pp_x > 0 and effective_pp_y > 0:
                        delta_arcmin_x = delta.x() / effective_pp_x
                        delta_arcmin_y = delta.y() / effective_pp_y
                        self.main_window.fov_offset_x += delta_arcmin_x
                        self.main_window.fov_offset_y += delta_arcmin_y
                        self.main_window.renderizar_fov_rapido()

                self.drag_start_global_pos = cur_global_pos
                event.accept()
            elif self.dragging and self.scroll_area:
                h_bar = self.scroll_area.horizontalScrollBar()
                v_bar = self.scroll_area.verticalScrollBar()
                h_bar.setValue(h_bar.value() - delta.x())
                v_bar.setValue(v_bar.value() - delta.y())
                self.drag_start_global_pos = cur_global_pos
                event.accept()
            else:
                super().mouseMoveEvent(event)
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            if self.dragging_fov and self.main_window:
                self.main_window.update_image_transformations()
            self.dragging = False
            self.dragging_fov = False
            self.drag_start_global_pos = None
            self.setCursor(Qt.CursorShape.OpenHandCursor)
            event.accept()
        else:
            super().mouseReleaseEvent(event)

    def wheelEvent(self, event):
        delta = event.angleDelta().y()
        if delta > 0:
            self.zoom_in_requested.emit()
            event.accept()
        elif delta < 0:
            self.zoom_out_requested.emit()
            event.accept()
        else:
            super().wheelEvent(event)


class ElevationMplCanvas(FigureCanvasQTAgg):
    def __init__(self, parent=None, width=6, height=4, dpi=100):
        fig = Figure(figsize=(width, height), dpi=dpi, facecolor='#1a1a1a')
        self.axes = fig.add_subplot(111)
        self.axes.set_facecolor('#1a1a1a')
        super().__init__(fig)
        self.setParent(parent)
        self.setStyleSheet("background-color: transparent;")


class ElevationWorker(QThread):
    finished = Signal(object, object, object, str)

    def __init__(self, lat, lon, ra_str, dec_str):
        super().__init__()
        self.lat = lat
        self.lon = lon
        self.ra_str = ra_str
        self.dec_str = dec_str

    def run(self):
        try:
            self.setPriority(QThread.Priority.LowPriority)
            now = Time.now()
            base_date = now.datetime.date()
            start_dt = datetime.combine(base_date, time(18, 0))

            times_dt = [start_dt + timedelta(hours=i * 0.5) for i in range(25)]
            times = Time(times_dt)

            loc = EarthLocation(lat=self.lat*u.deg, lon=self.lon*u.deg)
            altaz_frame = AltAz(obstime=times, location=loc)
            c = SkyCoord(self.ra_str, self.dec_str, unit=(u.hourangle, u.deg))

            altazs = c.transform_to(altaz_frame)
            altitudes = altazs.alt.deg.tolist()

            sun_altazs = get_sun(times).transform_to(altaz_frame)
            sun_altitudes = sun_altazs.alt.deg.tolist()
            time_labels = [t.datetime.strftime("%H:%M") for t in times]

            self.finished.emit(time_labels, altitudes, sun_altitudes, "")
        except Exception as e:
            print(f"[ELEVATION WORKER ERROR] {e}")
            self.finished.emit(None, None, None, str(e))


class ElevationDialog(QDialog):
    def __init__(self, target_name, ra_str, dec_str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Elevation Curve - {target_name}")
        self.resize(720, 480)
        self.setStyleSheet("background-color: #222222; color: white;")

        layout = QVBoxLayout(self)

        self.canvas = ElevationMplCanvas(self, width=6, height=4, dpi=100)
        layout.addWidget(self.canvas)

        self.lbl_loading = QLabel("Calculating elevation curve in background...")
        self.lbl_loading.setStyleSheet("color: #aaaaaa; font-style: italic;")
        layout.addWidget(self.lbl_loading)

        btn_layout = QHBoxLayout()
        btn_close = QPushButton("Close")
        btn_close.setStyleSheet("background-color: #333333; color: white; padding: 6px 16px; font-weight: bold;")
        btn_close.clicked.connect(self.accept)
        btn_layout.addStretch()
        btn_layout.addWidget(btn_close)
        layout.addLayout(btn_layout)

        self.target_name = target_name
        self.worker = None
        QTimer.singleShot(50, lambda: self.iniciar_calculo(ra_str, dec_str))

    def iniciar_calculo(self, ra_str, dec_str):
        settings = carregar_settings()
        lat = float(settings.get("user_lat", 38.64))
        lon = float(settings.get("user_lon", -9.10))

        self.worker = ElevationWorker(lat, lon, ra_str, dec_str)
        self.worker.finished.connect(self.on_calculated)
        self.worker.start()

    def on_calculated(self, time_labels, altitudes, sun_altitudes, error_msg):
        self.lbl_loading.setVisible(False)
        if error_msg or time_labels is None:
            print(f"[ELEVATION ERROR] {error_msg}")
            return
        self.gerar_grafico(time_labels, altitudes, sun_altitudes)

    def gerar_grafico(self, time_labels, altitudes, sun_altitudes):
        try:
            ax = self.canvas.axes
            ax.clear()
            ax.set_facecolor('#1a1a1a')

            in_dark = False
            dark_start_idx = None

            for i, sun_alt in enumerate(sun_altitudes):
                is_dark_point = sun_alt < -18.0
                if is_dark_point and not in_dark:
                    in_dark = True
                    dark_start_idx = i
                elif not is_dark_point and in_dark:
                    in_dark = False
                    ax.axvspan(dark_start_idx, i, color='#15354a', alpha=0.4, label='Dark Period (Sun < -18°)')

            if in_dark and dark_start_idx is not None:
                ax.axvspan(dark_start_idx, len(time_labels) - 1, color='#15354a', alpha=0.4, label='Dark Period (Sun < -18°)')

            ax.plot(time_labels, altitudes, color='#00ffff', linewidth=2.5, marker='o', markersize=4, label='Altitude (°)')
            ax.axhline(y=20, color='#ff5555', linestyle='--', linewidth=1.2, label='Min Alt Limit (20°)')
            ax.axhline(y=0, color='#888888', linestyle='-', linewidth=1.0, label='Horizon (0°)')

            ax.set_title(f"Altitude vs Time for {self.target_name}", color='white', fontsize=12, pad=10)
            ax.set_xlabel("Local Time (Hours)", color='white', fontsize=10)
            ax.set_ylabel("Altitude (Degrees)", color='white', fontsize=10)

            ax.tick_params(colors='white', labelsize=8)
            ax.grid(True, color='#333333', linestyle=':', alpha=0.7)
            ax.legend(facecolor='#222222', edgecolor='#444444', labelcolor='white', fontsize=9)

            self.canvas.figure.autofmt_xdate(rotation=45)
            self.canvas.draw()
        except Exception as e:
            print(f"[ELEVATION PLOT ERROR] {e}")

    def closeEvent(self, event):
        if self.worker and self.worker.isRunning():
            self.worker.finished.disconnect()
            self.worker.quit()
            self.worker.wait(200)
        event.accept()


class ImageWorker(QThread):
    finished = Signal(str, float, dict)
    progress = Signal(int, str)

    def __init__(self, nome_alvo, fov_solicitado=None):
        super().__init__()
        self.nome_alvo = nome_alvo
        self.fov_solicitado = fov_solicitado

    def run(self):
        try:
            self.setPriority(QThread.Priority.LowPriority)
            self.progress.emit(-1, "Querying target coordinates...")
            query_res = query_target_coordinates(self.nome_alvo)
            if not query_res.get("status", "").startswith("Sucesso") and not query_res.get("status", "").startswith("Success"):
                self.finished.emit(f"ERROR: {query_res.get('status', 'Target not found.')}", 60.0, query_res)
                return

            self.progress.emit(-1, "Fetching catalog background image...")
            caminho_img, fov_efetivo = fetch_target_image(
                self.nome_alvo,
                fov_solicitado=self.fov_solicitado,
                progress_callback=lambda p: self.progress.emit(p, f"Downloading image... {p}%")
            )
            self.finished.emit(caminho_img if caminho_img else "ERROR: Invalid image path.", fov_efetivo, query_res)
        except Exception as e:
            mensagem_erro = f"ERROR: {str(e)}"
            print(f"[TERMINAL LOG] {mensagem_erro}")
            self.finished.emit(mensagem_erro, 60.0, {"status": mensagem_erro})


class FOVRenderWorker(QThread):
    finished = Signal(object, float)

    def __init__(self, raw_image, bg_fov, setups_list, rot_deg, flip_h, flip_v, zoom_factor, fov_offset_x=0.0, fov_offset_y=0.0):
        super().__init__()
        self.raw_image = raw_image
        self.bg_fov = bg_fov
        self.setups_list = setups_list
        self.rot_deg = rot_deg
        self.flip_h = flip_h
        self.flip_v = flip_v
        self.zoom_factor = zoom_factor
        self.fov_offset_x = fov_offset_x
        self.fov_offset_y = fov_offset_y
        self._is_cancelled = False

    def cancel(self):
        self._is_cancelled = True

    def run(self):
        try:
            self.setPriority(QThread.Priority.LowPriority)
            if self.raw_image is None or self._is_cancelled:
                self.finished.emit(None, 1.0)
                return

            img = self.raw_image.copy().convert("RGB")
            if self._is_cancelled:
                self.finished.emit(None, 1.0)
                return

            if self.flip_h:
                img = img.transpose(PILImage.Transpose.FLIP_LEFT_RIGHT)
            if self.flip_v:
                img = img.transpose(PILImage.Transpose.FLIP_TOP_BOTTOM)

            if self._is_cancelled:
                self.finished.emit(None, 1.0)
                return

            bg_w, bg_h = img.size
            pixels_per_arcmin_x = bg_w / self.bg_fov
            pixels_per_arcmin_y = bg_h / self.bg_fov

            max_fov_w_arcmin = self.bg_fov
            max_fov_h_arcmin = self.bg_fov

            computed_setups = []
            for idx, st in enumerate(self.setups_list):
                fov = calculate_fov(st["focal"], st["pixel"], st["width_px"], st["height_px"])
                fw = fov["width_arcmin"]
                fh = fov["height_arcmin"]

                rad = math.radians(self.rot_deg)
                rot_w = abs(fw * math.cos(rad)) + abs(fh * math.sin(rad))
                rot_h = abs(fw * math.sin(rad)) + abs(fh * math.cos(rad))

                if rot_w > max_fov_w_arcmin:
                    max_fov_w_arcmin = rot_w
                if rot_h > max_fov_h_arcmin:
                    max_fov_h_arcmin = rot_h

                tel_name = st.get("telescope", "").strip()
                ccd_name = st.get("ccd", "").strip()

                if tel_name and ccd_name:
                    clean_label = f"{tel_name} + {ccd_name}"
                elif tel_name:
                    clean_label = tel_name
                elif ccd_name:
                    clean_label = ccd_name
                else:
                    clean_label = st.get("label", f"Setup #{idx+1}")

                color = FOV_PALETTE[idx % len(FOV_PALETTE)]
                computed_setups.append({
                    "rect_w": fw * pixels_per_arcmin_x,
                    "rect_h": fh * pixels_per_arcmin_y,
                    "label": clean_label,
                    "color": color
                })

            needed_fov_w = max(self.bg_fov, max_fov_w_arcmin * 1.15)
            needed_fov_h = max(self.bg_fov, max_fov_h_arcmin * 1.15)

            canvas_w = int(needed_fov_w * pixels_per_arcmin_x)
            canvas_h = int(needed_fov_h * pixels_per_arcmin_y)

            MAX_CANVAS_DIM = 8000
            if canvas_w > MAX_CANVAS_DIM or canvas_h > MAX_CANVAS_DIM:
                factor_scale = MAX_CANVAS_DIM / max(canvas_w, canvas_h)
                canvas_w = int(canvas_w * factor_scale)
                canvas_h = int(canvas_h * factor_scale)
                bg_w = int(bg_w * factor_scale)
                bg_h = int(bg_h * factor_scale)
                img = img.resize((bg_w, bg_h), PILImage.Resampling.BOX)
                pixels_per_arcmin_x *= factor_scale
                pixels_per_arcmin_y *= factor_scale
                for cs in computed_setups:
                    cs["rect_w"] *= factor_scale
                    cs["rect_h"] *= factor_scale

            if self._is_cancelled:
                self.finished.emit(None, 1.0)
                return

            canvas = PILImage.new("RGBA", (canvas_w, canvas_h), (20, 20, 20, 255))
            paste_x = (canvas_w - bg_w) // 2
            paste_y = (canvas_h - bg_h) // 2
            canvas.paste(img, (paste_x, paste_y))

            center_x = (canvas_w / 2.0) + (self.fov_offset_x * pixels_per_arcmin_x)
            center_y = (canvas_h / 2.0) + (self.fov_offset_y * pixels_per_arcmin_y)

            draw = ImageDraw.Draw(canvas)
            line_width = max(2, int(canvas_w / 400))

            font_size = max(14, int(canvas_w / 45))
            font = None
            font_candidates = [
                "arialbd.ttf", "Arial Bold", "DejaVuSans-Bold.ttf", "segoebu.ttf",
                "LiberationSans-Bold.ttf", "FreeSansBold.ttf"
            ]
            for f_name in font_candidates:
                try:
                    font = ImageFont.truetype(f_name, font_size)
                    break
                except Exception:
                    continue
            if font is None:
                try:
                    font = ImageFont.load_default()
                except Exception:
                    font = None

            draw.rectangle([paste_x, paste_y, paste_x + bg_w, paste_y + bg_h], outline=(70, 70, 70), width=1)

            overlay = PILImage.new("RGBA", (canvas_w, canvas_h), (0, 0, 0, 0))
            overlay_draw = ImageDraw.Draw(overlay)

            for cs in computed_setups:
                w = cs["rect_w"]
                h = cs["rect_h"]
                color = cs["color"]
                lbl_text = cs["label"]

                left = center_x - (w / 2.0)
                top = center_y - (h / 2.0)
                right = center_x + (w / 2.0)
                bottom = center_y + (h / 2.0)

                overlay_draw.rectangle([left, top, right, bottom], outline=color, width=line_width)

                if lbl_text and font:
                    try:
                        bbox = font.getbbox(lbl_text)
                        txt_h = bbox[3] - bbox[1]
                    except Exception:
                        txt_h = font_size

                    txt_x = left
                    txt_y = top - txt_h - 4.0
                    overlay_draw.text((txt_x, txt_y), lbl_text, fill=color + (255,), font=font)

            rotated_overlay = overlay.rotate(-self.rot_deg, center=(center_x, center_y), resample=PILImage.Resampling.BICUBIC)
            canvas.paste(rotated_overlay, (0, 0), rotated_overlay)

            cross_size = 12
            draw.line([center_x - cross_size, center_y, center_x + cross_size, center_y], fill=(0, 255, 255), width=1)
            draw.line([center_x, center_y - cross_size, center_x, center_y + cross_size], fill=(0, 255, 255), width=1)

            if self._is_cancelled:
                self.finished.emit(None, 1.0)
                return

            new_width = max(int(canvas.width * self.zoom_factor), 10)
            new_height = max(int(canvas.height * self.zoom_factor), 10)
            resized_img = canvas.resize((new_width, new_height), PILImage.Resampling.BOX)

            data = resized_img.convert("RGB").tobytes("raw", "RGB")
            qimage = QImage(data, resized_img.width, resized_img.height, resized_img.width * 3, QImage.Format.Format_RGB888)
            qimage_copy = qimage.copy()

            if not self._is_cancelled:
                self.finished.emit(qimage_copy, self.zoom_factor)
            else:
                self.finished.emit(None, 1.0)
        except Exception as e:
            print(f"[FOV RENDER WORKER ERROR] {e}")
            self.finished.emit(None, 1.0)


class NetworkStatusWorker(QThread):
    status_checked = Signal(bool, str, bool, str, bool, str, bool, str)

    def run(self):
        try:
            self.setPriority(QThread.Priority.LowPriority)
            settings = carregar_settings()
            fb_ok, fb_msg = verificar_status_servico(timeout=4)
            survey_ok, survey_msg = verificar_status_survey(timeout=4)

            ascom_ok, ascom_msg = testar_conexao_ascom(
                host=settings.get("ascom_host", "127.0.0.1"),
                port=settings.get("ascom_port", "11111"),
                timeout=3
            ) if settings.get("ascom_enabled", False) else (False, "ASCOM Disabled")

            indi_ok, indi_msg = testar_conexao_indi(
                host=settings.get("indi_host", "127.0.0.1"),
                port=settings.get("indi_port", "7624"),
                timeout=3
            ) if settings.get("indi_enabled", False) else (False, "INDI Disabled")

            self.status_checked.emit(fb_ok, fb_msg, survey_ok, survey_msg, ascom_ok, ascom_msg, indi_ok, indi_msg)
        except Exception as e:
            self.status_checked.emit(False, str(e), False, str(e), False, str(e), False, str(e))


class NetworkLoadWorker(QThread):
    finished = Signal(list)

    def run(self):
        try:
            self.setPriority(QThread.Priority.LowPriority)
            registos = carregar_registos_network()
            self.finished.emit(registos)
        except Exception as e:
            print(f"[NETWORK LOAD ERROR] {e}")
            self.finished.emit([])


class NightPlannerWorker(QThread):
    finished = Signal(list)

    def __init__(self, catalogos_dat, lat, lon, min_alt):
        super().__init__()
        self.catalogos_dat = catalogos_dat
        self.lat = lat
        self.lon = lon
        self.min_alt = min_alt

    def run(self):
        try:
            self.setPriority(QThread.Priority.LowPriority)
            res = calcular_objetos_visiveis(self.catalogos_dat, self.lat, self.lon, self.min_alt)

            try:
                now = Time.now()
                loc = EarthLocation(lat=self.lat*u.deg, lon=self.lon*u.deg)

                from astropy.coordinates import get_body
                moon_pos = get_body('moon', now, loc)

                for obj in res:
                    ra_str = obj.get("ra", "0h 0m 0s")
                    dec_str = obj.get("dec", "0° 0' 0\"")
                    try:
                        c = SkyCoord(ra_str, dec_str, unit=(u.hourangle, u.deg))

                        sep = c.separation(moon_pos).deg
                        obj["moon_sep"] = f"{sep:.1f}°"

                        lst = now.sidereal_time('mean', longitude=loc.lon)
                        ha_hours = (lst - c.ra).hour
                        transit_diff = (24.0 - ha_hours) % 24.0
                        transit_time = now + (transit_diff * u.hour)
                        obj["transit_time"] = transit_time.datetime.strftime("%H:%M")

                        transit_altaz = c.transform_to(AltAz(obstime=transit_time, location=loc))
                        obj["transit_alt"] = transit_altaz.alt.deg

                    except Exception:
                        obj["moon_sep"] = "--"
                        obj["transit_time"] = "--:--"
                        obj["transit_alt"] = 90.0
            except Exception as e:
                print(f"[PLANNER EXTRA CALC ERROR] {e}")
                for obj in res:
                    obj["moon_sep"] = "--"
                    obj["transit_time"] = "--:--"
                    obj["transit_alt"] = 90.0

            self.finished.emit(res)
        except Exception as e:
            print(f"[NIGHT PLANNER WORKER ERROR] {e}")
            self.finished.emit([])


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"Accdc - Astronomy CCD Calculator (v{APP_VERSION})")
        logo_path = obter_caminho_asset("logo.png")
        if os.path.exists(logo_path):
            self.setWindowIcon(QIcon(logo_path))

        self.resize(1300, 700)
        self.setMinimumSize(800, 550)

        settings = carregar_settings()
        if not settings.get("user_name"):
            unique_id = uuid.uuid4().hex[:8]
            default_user = f"guest_{unique_id}"

            locale_name = QLocale.system().name()
            default_country = locale_name.split('_')[-1].lower() if '_' in locale_name else "pt"

            settings["user_name"] = default_user
            settings["user_country"] = default_country
            guardar_settings(settings)

        self.active_tasks = {}
        self.is_rendering = False
        self.render_pending = False
        self.raw_planner_objects = []

        self.active_comparison_setups = []
        self._fit_pending = False
        self._bg_cache_key = None
        self._bg_cache_pixmap = None

        self.catalogos_locais_dat = obter_todos_objetos_dat()

        status_bar = self.statusBar()
        status_bar.setStyleSheet("""
            QStatusBar {
                font-size: 12px;
                padding: 2px 12px;
            }
            QStatusBar::item {
                border: none;
            }
        """)
        status_bar.showMessage("Ready")

        self.lbl_progress_text = QLabel("")
        self.lbl_progress_text.setStyleSheet("color: #cccccc; font-size: 11px; padding-right: 6px;")
        self.lbl_progress_text.setVisible(False)
        status_bar.addPermanentWidget(self.lbl_progress_text)

        self.download_progress_bar = QProgressBar()
        self.download_progress_bar.setMinimum(0)
        self.download_progress_bar.setMaximum(100)
        self.download_progress_bar.setValue(0)
        self.download_progress_bar.setTextVisible(False)
        self.download_progress_bar.setFixedWidth(180)
        self.download_progress_bar.setFixedHeight(14)

        self.download_progress_bar.setStyleSheet(
            "QProgressBar {"
            "    border: 1px solid #3a3a4a;"
            "    border-radius: 4px;"
            "    background: transparent;"
            "    color: #ffffff;"
            "    font-size: 10px;"
            "    font-weight: normal;"
            "    text-align: center;"
            "}"
            "QProgressBar::chunk {"
            "    background-color: #2b7dd4;"
            "    border-radius: 3px;"
            "}"
        )
        self.download_progress_bar.setVisible(False)
        status_bar.addPermanentWidget(self.download_progress_bar)

        self.lbl_network_status = QLabel("Network checking...")
        self.lbl_network_status.setCursor(Qt.CursorShape.PointingHandCursor)
        self.lbl_network_status.setToolTip("Click to refresh connection status")
        self.lbl_network_status.mousePressEvent = self.ao_clicar_network_status
        self.lbl_network_status.setStyleSheet("color: #cccccc; font-size: 11px;")
        status_bar.addPermanentWidget(self.lbl_network_status)

        self.tabs = QTabWidget()
        self.setCentralWidget(self.tabs)

        self.current_raw_image = None
        self.cached_base_canvas = None
        self.current_bg_fov = 60.0
        self.zoom_factor = 1.0
        self.fov_offset_x = 0.0
        self.fov_offset_y = 0.0

        self.worker = None
        self.fov_worker = None
        self.net_load_worker = None
        self.planner_worker = None
        self.night_planner_loaded = False

        self.render_timer = QTimer(self)
        self.render_timer.setSingleShot(True)
        self.render_timer.setInterval(60)
        self.render_timer.timeout.connect(self._disparar_renderizacao_fov)
        self._pending_zoom_ratio = None

        self.init_panel_tab()
        self.init_catalogs_tab()
        self.init_night_planner_tab()
        self.init_historic_tab()
        self.init_network_tab()
        self.init_instruments_tab()
        self.init_settings_tab()
        self.init_about_tab()

        self.tabs.currentChanged.connect(self.ao_mudar_aba)
        self.atualizar_combos_instrumentos()

        last_tel = settings.get("last_telescope", "")
        last_ccd = settings.get("last_ccd", "")

        if last_tel:
            for i in range(self.combo_panel_telescope.count()):
                if self.combo_panel_telescope.itemText(i) == last_tel:
                    self.combo_panel_telescope.setCurrentIndex(i)
                    break

        if last_ccd:
            for i in range(self.combo_panel_ccd.count()):
                if self.combo_panel_ccd.itemText(i) == last_ccd:
                    self.combo_panel_ccd.setCurrentIndex(i)
                    break

        self.net_worker = None
        self.net_timer = QTimer(self)
        self.net_timer.setInterval(10000)
        self.net_timer.timeout.connect(self.verificar_rede_tempo_real)
        self.net_timer.start()

        self.planner_live_timer = QTimer(self)
        self.planner_live_timer.setInterval(1800000)
        self.planner_live_timer.timeout.connect(self.verificar_atualizacao_periodica_planner)
        self.planner_live_timer.start()

        self.verificar_rede_tempo_real()

    def registrar_tarefa(self, task_id, mensagem):
        self.active_tasks[task_id] = mensagem
        self.atualizar_barra_status_ui()

    def remover_tarefa(self, task_id):
        if task_id in self.active_tasks:
            del self.active_tasks[task_id]
        self.atualizar_barra_status_ui()

    def atualizar_barra_status_ui(self):
        if not self.active_tasks:
            self.download_progress_bar.setVisible(False)
            self.lbl_progress_text.setVisible(False)
        else:
            ultima_mensagem = list(self.active_tasks.values())[-1]
            self.lbl_progress_text.setText(ultima_mensagem)
            self.download_progress_bar.setMaximum(0)
            self.download_progress_bar.setVisible(True)
            self.lbl_progress_text.setVisible(True)

    def iniciar_tarefa(self, mensagem="Processing..."):
        self.registrar_tarefa("generic_task", mensagem)

    def atualizar_progresso_valores(self, percent, mensagem):
        if percent == -1:
            self.download_progress_bar.setMaximum(0)
        else:
            self.download_progress_bar.setMaximum(100)
            self.download_progress_bar.setValue(percent)

        if mensagem:
            self.lbl_progress_text.setText(mensagem)

        self.download_progress_bar.setVisible(True)
        self.lbl_progress_text.setVisible(True)

    def terminar_tarefa(self):
        self.remover_tarefa("generic_task")

    def ao_mudar_aba(self, index):
        nome_aba = self.tabs.tabText(index)

        if nome_aba == "Network":
            self.verificar_rede_tempo_real()
            self.atualizar_tabela_network()
        elif nome_aba == "Night Planner":
            if not self.night_planner_loaded:
                self.recalcular_night_planner()

        widget_atual = self.tabs.currentWidget()
        if widget_atual:
            widget_atual.setFocus()

    def verificar_atualizacao_periodica_planner(self):
        current_tab_text = self.tabs.tabText(self.tabs.currentIndex())
        if current_tab_text == "Night Planner":
            if self.planner_worker is None or not self.planner_worker.isRunning():
                self.recalcular_night_planner()

    def ao_clicar_network_status(self, _event):
        self.lbl_network_status.setText("Network checking...")
        self.verificar_rede_tempo_real()

    def verificar_rede_tempo_real(self):
        if self.net_worker is not None and self.net_worker.isRunning():
            return
        self.net_worker = NetworkStatusWorker()
        self.net_worker.status_checked.connect(self.ao_atualizar_status_network)
        self.net_worker.start()

    def ao_atualizar_status_network(self, fb_ok, fb_msg, survey_ok, survey_msg, ascom_ok, ascom_msg, indi_ok, indi_msg):
        fb_str = "on" if fb_ok else "off"
        survey_str = "on" if survey_ok else "off"
        ascom_str = "on" if ascom_ok else "off"
        indi_str = "on" if indi_ok else "off"

        text = f"Network {fb_str} | survey {survey_str} | ascom {ascom_str} | indi {indi_str}"
        self.lbl_network_status.setText(text)
        self.lbl_network_status.setStyleSheet("color: #cccccc; font-size: 11px;")
        self.lbl_network_status.setToolTip(
            f"Status:\n• Firebase: {fb_msg}\n• Survey: {survey_msg}\n• ASCOM: {ascom_msg}\n• INDI: {indi_msg}\n(Click to refresh)"
        )

    def closeEvent(self, event):
        if hasattr(self, 'net_timer') and self.net_timer.isActive():
            self.net_timer.stop()
        if hasattr(self, 'planner_live_timer') and self.planner_live_timer.isActive():
            self.planner_live_timer.stop()
        if hasattr(self, 'render_timer') and self.render_timer.isActive():
            self.render_timer.stop()
        if self.net_worker and self.net_worker.isRunning():
            self.net_worker.wait(1000)
        if self.net_load_worker and self.net_load_worker.isRunning():
            self.net_load_worker.wait(1000)
        if self.planner_worker and self.planner_worker.isRunning():
            self.planner_worker.wait(1000)
        if self.worker and self.worker.isRunning():
            self.worker.wait(1000)
        if self.fov_worker and self.fov_worker.isRunning():
            self.fov_worker.cancel()
            self.fov_worker.wait(1000)
        event.accept()

    def init_panel_tab(self):
        tab = QWidget()
        layout = QHBoxLayout(tab)

        left_layout = QVBoxLayout()

        telescope_group = QGroupBox("Telescope & Optics")
        telescope_grid = QGridLayout()

        telescope_grid.addWidget(QLabel("Select Telescope:"), 0, 0)
        self.combo_panel_telescope = QComboBox()
        self.combo_panel_telescope.currentIndexChanged.connect(self.ao_selecionar_telescopio_panel)
        telescope_grid.addWidget(self.combo_panel_telescope, 0, 1)

        telescope_grid.addWidget(QLabel("Focal Length (mm):"), 1, 0)
        self.input_focal = QLineEdit("500")
        self.input_focal.textChanged.connect(self.ao_alterar_parametro_optico)
        telescope_grid.addWidget(self.input_focal, 1, 1)

        telescope_grid.addWidget(QLabel("Aperture (mm):"), 2, 0)
        self.input_aperture = QLineEdit("80")
        telescope_grid.addWidget(self.input_aperture, 2, 1)

        telescope_group.setLayout(telescope_grid)
        left_layout.addWidget(telescope_group)

        camera_group = QGroupBox("CCD / Sensor")
        camera_grid = QGridLayout()

        camera_grid.addWidget(QLabel("Select CCD:"), 0, 0)
        self.combo_panel_ccd = QComboBox()
        self.combo_panel_ccd.currentIndexChanged.connect(self.ao_selecionar_ccd_panel)
        camera_grid.addWidget(self.combo_panel_ccd, 0, 1)

        camera_grid.addWidget(QLabel("Pixel Size (µm):"), 1, 0)
        self.input_pixel = QLineEdit("6.45")
        self.input_pixel.textChanged.connect(self.ao_alterar_parametro_optico)
        camera_grid.addWidget(self.input_pixel, 1, 1)

        camera_grid.addWidget(QLabel("Width (px):"), 2, 0)
        self.input_width_px = QLineEdit("1392")
        self.input_width_px.textChanged.connect(self.ao_alterar_parametro_optico)
        camera_grid.addWidget(self.input_width_px, 2, 1)

        camera_grid.addWidget(QLabel("Height (px):"), 3, 0)
        self.input_height_px = QLineEdit("1040")
        self.input_height_px.textChanged.connect(self.ao_alterar_parametro_optico)
        camera_grid.addWidget(self.input_height_px, 3, 1)

        camera_group.setLayout(camera_grid)
        left_layout.addWidget(camera_group)

        target_group = QGroupBox("Celestial Target")
        target_grid = QGridLayout()
        target_grid.addWidget(QLabel("Target (e.g., M51):"), 0, 0)
        self.input_target = QLineEdit("M51")

        validator = QRegularExpressionValidator(QRegularExpression(r"[A-Za-z0-9\+\-\.\:\s°′″hms]*"))
        self.input_target.setValidator(validator)
        self.input_target.textChanged.connect(lambda text: self.input_target.setText(text.upper()))

        lista_sugestoes = []
        for cat_key, itens in self.catalogos_locais_dat.items():
            for item in itens:
                lista_sugestoes.append(item["name"])

        lista_sugestoes.extend([f"M{i}" for i in range(1, 111)])
        lista_sugestoes.extend([f"NGC{i}" for i in range(1, 1000)])
        lista_sugestoes.extend([f"IC{i}" for i in range(1, 1000)])
        estrelas_e_especiais = [
            "Sirius", "Vega", "Betelgeuse", "Rigel", "Polaris", "Antares", "Arcturus",
            "Procyon", "Achernar", "Capella", "Altair", "Aldebaran", "Spica", "Pollux",
            "Fomalhaut", "Deneb", "Regulus", "Adhara", "Castor", "Gacrux", "Shaula"
        ]
        lista_sugestoes.extend(estrelas_e_especiais)

        completer = QCompleter(sorted(list(set(lista_sugestoes))), self)
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        completer.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
        completer.setFilterMode(Qt.MatchFlag.MatchStartsWith)
        self.input_target.setCompleter(completer)

        target_grid.addWidget(self.input_target, 0, 1)

        target_grid.addWidget(QLabel("Coordinates (RA/Dec):"), 1, 0)
        self.lbl_coords = QLabel("--")
        target_grid.addWidget(self.lbl_coords, 1, 1)

        target_group.setLayout(target_grid)
        left_layout.addWidget(target_group)

        results_group = QGroupBox("Optical Results")
        results_grid = QGridLayout()

        results_grid.addWidget(QLabel("Focal Ratio (f/#):"), 0, 0)
        self.lbl_f_ratio = QLabel("--")
        results_grid.addWidget(self.lbl_f_ratio, 0, 1)

        results_grid.addWidget(QLabel("Image Scale:"), 1, 0)
        self.lbl_scale_result = QLabel("-- arcsec/px")
        results_grid.addWidget(self.lbl_scale_result, 1, 1)

        results_grid.addWidget(QLabel("FOV (Width x Height):"), 2, 0)
        self.lbl_fov_result = QLabel("-- arcmin")
        results_grid.addWidget(self.lbl_fov_result, 2, 1)

        results_group.setLayout(results_grid)
        left_layout.addWidget(results_group)

        action_layout = QHBoxLayout()

        # Botão único Calculate & Query Target com submenu à direita (MenuButtonPopup)
        self.btn_preview = QToolButton()
        self.btn_preview.setText("Calculate & Query Target")
        self.btn_preview.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self.btn_preview.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)
        self.btn_preview.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.btn_preview.setStyleSheet("""
            QToolButton {
                font-weight: bold; 
                padding: 8px 12px; 
                background-color: #2b5b84; 
                color: white;
                border: none;
                border-radius: 4px;
            }
            QToolButton::menu-button {
                width: 20px;
                border-left: 1px solid #1e3f5a;
            }
            QToolButton:hover {
                background-color: #356b9c;
            }
        """)
        self.btn_preview.clicked.connect(self.ao_clicar_botao_calcular)

        menu_preview = QMenu(self)
        menu_preview.addAction("Download Object (Exact Size, No FOV)", self.baixar_imagem_exata_sem_fov)
        menu_preview.addAction("Download Object (With FOV - Default Size)", self.baixar_imagem_com_fov)
        self.btn_preview.setMenu(menu_preview)

        self.btn_slew = QPushButton("🔭 Slew (Telescope)")
        self.btn_slew.setToolTip("Send target coordinates to active ASCOM or INDI mount")
        self.btn_slew.setStyleSheet("font-weight: bold; padding: 8px; background-color: #4a2b84; color: white;")
        self.btn_slew.clicked.connect(self.executar_slew_telescopio)

        action_layout.addWidget(self.btn_preview, 3)
        action_layout.addWidget(self.btn_slew, 1)
        left_layout.addLayout(action_layout)

        left_layout.addStretch()
        layout.addLayout(left_layout, 1)

        right_layout = QVBoxLayout()
        preview_group = QGroupBox("Field of View (Dynamic FOV Overlay) - Hold [Shift] + Drag to frame")
        preview_layout = QVBoxLayout()

        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(False)
        self.scroll_area.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.scroll_area.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.scroll_area.setStyleSheet("background-color: #1a1a1a; border: 1px solid #333333;")

        self.lbl_canvas = DraggableLabel(self.scroll_area, main_window=self)
        self.lbl_canvas.setText("Target preview will appear here")
        self.lbl_canvas.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_canvas.setStyleSheet("background-color: #1a1a1a; color: #888888;")
        self.lbl_canvas.adjustSize()
        self.lbl_canvas.zoom_in_requested.connect(self.zoom_in)
        self.lbl_canvas.zoom_out_requested.connect(self.zoom_out)
        self.scroll_area.setWidget(self.lbl_canvas)

        preview_layout.addWidget(self.scroll_area)

        zoom_layout = QHBoxLayout()
        self.btn_zoom_out = QPushButton("🔍 -")
        self.btn_zoom_out.setFixedWidth(45)
        self.btn_zoom_out.clicked.connect(self.zoom_out)

        self.lbl_zoom_percent = QLabel("100%")
        self.lbl_zoom_percent.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_zoom_percent.setStyleSheet("font-weight: bold; color: #ffffff;")
        self.lbl_zoom_percent.setFixedWidth(65)

        self.btn_zoom_in = QPushButton("🔍 +")
        self.btn_zoom_in.setFixedWidth(45)
        self.btn_zoom_in.clicked.connect(self.zoom_in)

        self.btn_zoom_fit = QPushButton("Fit to Window")
        self.btn_zoom_fit.clicked.connect(self.zoom_fit)

        self.btn_center_fov = QPushButton("Center FOV")
        self.btn_center_fov.setToolTip("Reset FOV framing offset to center")
        self.btn_center_fov.clicked.connect(self.reset_fov_offset)

        zoom_layout.addWidget(QLabel("Zoom:"))
        zoom_layout.addWidget(self.btn_zoom_out)
        zoom_layout.addWidget(self.lbl_zoom_percent)
        zoom_layout.addWidget(self.btn_zoom_in)
        zoom_layout.addStretch()
        zoom_layout.addWidget(self.btn_center_fov)
        zoom_layout.addWidget(self.btn_zoom_fit)

        preview_layout.addLayout(zoom_layout)

        orientation_group = QGroupBox("Camera / FOV Rotation (Rotator Control)")
        orientation_grid = QGridLayout()

        orientation_grid.addWidget(QLabel("FOV Rotation Angle:"), 0, 0)

        rot_layout = QHBoxLayout()
        self.slider_rot = QSlider(Qt.Orientation.Horizontal)
        self.slider_rot.setRange(0, 360)
        self.slider_rot.setValue(0)

        self.spin_rot = QDoubleSpinBox()
        self.spin_rot.setRange(0.0, 360.0)
        self.spin_rot.setSingleStep(1.0)
        self.spin_rot.setDecimals(1)
        self.spin_rot.setSuffix("°")
        self.spin_rot.setFixedWidth(100)

        rot_layout.addWidget(self.slider_rot, 1)
        rot_layout.addWidget(self.spin_rot, 0)
        orientation_grid.addLayout(rot_layout, 0, 1)

        self.slider_rot.valueChanged.connect(self._on_slider_rot_changed)
        self.slider_rot.sliderReleased.connect(self.update_image_transformations)
        self.spin_rot.valueChanged.connect(self._on_spin_rot_changed)

        self.chk_flip_h = QCheckBox("Flip Horizontal (Flip H)")
        self.chk_flip_h.stateChanged.connect(self.update_image_transformations)
        orientation_grid.addWidget(self.chk_flip_h, 1, 0)

        self.chk_flip_v = QCheckBox("Flip Vertical (Flip V)")
        self.chk_flip_v.stateChanged.connect(self.update_image_transformations)
        orientation_grid.addWidget(self.chk_flip_v, 1, 1)

        orientation_group.setLayout(orientation_grid)
        preview_layout.addWidget(orientation_group)

        preview_group.setLayout(preview_layout)
        right_layout.addWidget(preview_group)

        layout.addLayout(right_layout, 2)

        self.tabs.addTab(tab, "Panel")

    def baixar_imagem_exata_sem_fov(self):
        target_name = self.input_target.text().strip()
        if not target_name:
            QMessageBox.warning(self, "Warning", "Please specify a target name first.")
            return

        safe_name = "".join(c if c.isalnum() or c in ('_', '-') else '_' for c in target_name)
        default_filename = f"{safe_name}_exact_size.png"

        filepath, _ = QFileDialog.getSaveFileName(self, "Save Exact Image (Survey FOV)", default_filename, "PNG Images (*.png);;All Files (*.*)")
        if not filepath:
            return

        try:
            focal = float(self.input_focal.text())
            pixel = float(self.input_pixel.text())
            width_px = float(self.input_width_px.text())
            height_px = float(self.input_height_px.text())

            fov = calculate_fov(focal, pixel, width_px, height_px)
            fw = fov["width_arcmin"]
            fh = fov["height_arcmin"]
            sensor_fov_max = max(fw, fh)

            self.iniciar_tarefa("Fetching exact size image from survey...")

            # Vai diretamente ao survey buscar a imagem cobrindo o FOV do sensor
            caminho_img, fov_efetivo = fetch_target_image(target_name, fov_solicitado=sensor_fov_max)
            self.terminar_tarefa()

            if caminho_img and os.path.exists(caminho_img) and not caminho_img.startswith("ERROR:"):
                img = PILImage.open(caminho_img).convert("RGB")
                bg_w, bg_h = img.size

                pixels_per_arcmin_x = bg_w / fov_efetivo
                pixels_per_arcmin_y = bg_h / fov_efetivo

                crop_w = fw * pixels_per_arcmin_x
                crop_h = fh * pixels_per_arcmin_y

                center_x = bg_w / 2.0 + (self.fov_offset_x * pixels_per_arcmin_x)
                center_y = bg_h / 2.0 + (self.fov_offset_y * pixels_per_arcmin_y)

                left = center_x - (crop_w / 2.0)
                top = center_y - (crop_h / 2.0)
                right = center_x + (crop_w / 2.0)
                bottom = center_y + (crop_h / 2.0)

                # Crop da região correspondente exatamente ao FOV da câmara no survey
                cropped = img.crop((left, top, right, bottom))

                # Redimensionar exatamente para as dimensões de píxeis da câmara (ex: 1392 x 1040)
                final_img = cropped.resize((int(width_px), int(height_px)), PILImage.Resampling.LANCZOS)

                if self.chk_flip_h.isChecked():
                    final_img = final_img.transpose(PILImage.Transpose.FLIP_LEFT_RIGHT)
                if self.chk_flip_v.isChecked():
                    final_img = final_img.transpose(PILImage.Transpose.FLIP_TOP_BOTTOM)

                rot_deg = self.rot_value()
                if rot_deg != 0.0:
                    final_img = final_img.rotate(-rot_deg, resample=PILImage.Resampling.BICUBIC, expand=True)

                final_img.save(filepath)
                QMessageBox.information(self, "Success", f"Exact sensor size image successfully downloaded from survey and saved to:\n{filepath}")
            else:
                QMessageBox.critical(self, "Error", "Failed to fetch image from survey for exact size.")
        except Exception as e:
            self.terminar_tarefa()
            QMessageBox.critical(self, "Error", f"Failed to download exact image:\n{e}")

    def baixar_imagem_com_fov(self):
        if self.current_raw_image is None:
            QMessageBox.warning(self, "Warning", "No target image loaded yet.")
            return

        target_name = self.input_target.text().strip() or "target"
        safe_name = "".join(c if c.isalnum() or c in ('_', '-') else '_' for c in target_name)
        default_filename = f"{safe_name}_fov_overlay.png"

        filepath, _ = QFileDialog.getSaveFileName(self, "Save Image with FOV", default_filename, "PNG Images (*.png);;All Files (*.*)")
        if filepath:
            try:
                img = self.current_raw_image.copy().convert("RGB")
                if self.chk_flip_h.isChecked():
                    img = img.transpose(PILImage.Transpose.FLIP_LEFT_RIGHT)
                if self.chk_flip_v.isChecked():
                    img = img.transpose(PILImage.Transpose.FLIP_TOP_BOTTOM)

                bg_w, bg_h = img.size
                pixels_per_arcmin_x = bg_w / self.current_bg_fov
                pixels_per_arcmin_y = bg_h / self.current_bg_fov

                setups = self.obter_setups_para_renderizacao()
                rot_deg = self.rot_value()
                rad = math.radians(rot_deg)

                max_fov_w_arcmin = self.current_bg_fov
                max_fov_h_arcmin = self.current_bg_fov

                computed_setups = []
                for idx, st in enumerate(setups):
                    fov = calculate_fov(st["focal"], st["pixel"], st["width_px"], st["height_px"])
                    fw, fh = fov["width_arcmin"], fov["height_arcmin"]
                    rot_w = abs(fw * math.cos(rad)) + abs(fh * math.sin(rad))
                    rot_h = abs(fw * math.sin(rad)) + abs(fh * math.cos(rad))
                    max_fov_w_arcmin = max(max_fov_w_arcmin, rot_w)
                    max_fov_h_arcmin = max(max_fov_h_arcmin, rot_h)

                    tel_name = st.get("telescope", "").strip()
                    ccd_name = st.get("ccd", "").strip()
                    clean_label = f"{tel_name} + {ccd_name}" if (tel_name and ccd_name) else (tel_name or ccd_name or f"Setup #{idx+1}")
                    color = FOV_PALETTE[idx % len(FOV_PALETTE)]
                    computed_setups.append({
                        "rect_w": fw * pixels_per_arcmin_x,
                        "rect_h": fh * pixels_per_arcmin_y,
                        "label": clean_label,
                        "color": color
                    })

                needed_fov_w = max(self.current_bg_fov, max_fov_w_arcmin * 1.15)
                needed_fov_h = max(self.current_bg_fov, max_fov_h_arcmin * 1.15)
                canvas_w = int(needed_fov_w * pixels_per_arcmin_x)
                canvas_h = int(needed_fov_h * pixels_per_arcmin_y)

                canvas = PILImage.new("RGBA", (canvas_w, canvas_h), (20, 20, 20, 255))
                paste_x = (canvas_w - bg_w) // 2
                paste_y = (canvas_h - bg_h) // 2
                canvas.paste(img, (paste_x, paste_y))

                center_x = (canvas_w / 2.0) + (self.fov_offset_x * pixels_per_arcmin_x)
                center_y = (canvas_h / 2.0) + (self.fov_offset_y * pixels_per_arcmin_y)

                draw = ImageDraw.Draw(canvas)
                line_width = max(2, int(canvas_w / 400))
                font_size = max(14, int(canvas_w / 45))
                font = None
                for f_name in ["arialbd.ttf", "Arial Bold", "DejaVuSans-Bold.ttf", "segoebu.ttf", "LiberationSans-Bold.ttf", "FreeSansBold.ttf"]:
                    try:
                        font = ImageFont.truetype(f_name, font_size)
                        break
                    except Exception:
                        continue
                if font is None:
                    try:
                        font = ImageFont.load_default()
                    except Exception:
                        font = None

                draw.rectangle([paste_x, paste_y, paste_x + bg_w, paste_y + bg_h], outline=(70, 70, 70), width=1)

                overlay = PILImage.new("RGBA", (canvas_w, canvas_h), (0, 0, 0, 0))
                overlay_draw = ImageDraw.Draw(overlay)

                for cs in computed_setups:
                    w = cs["rect_w"]
                    h = cs["rect_h"]
                    color = cs["color"]
                    lbl_text = cs["label"]
                    left = center_x - (w / 2.0)
                    top = center_y - (h / 2.0)
                    right = center_x + (w / 2.0)
                    bottom = center_y + (h / 2.0)
                    overlay_draw.rectangle([left, top, right, bottom], outline=color, width=line_width)
                    if lbl_text and font:
                        try:
                            bbox = font.getbbox(lbl_text)
                            txt_h = bbox[3] - bbox[1]
                        except Exception:
                            txt_h = font_size
                        overlay_draw.text((left, top - txt_h - 4.0), lbl_text, fill=color + (255,), font=font)

                rotated_overlay = overlay.rotate(-rot_deg, center=(center_x, center_y), resample=PILImage.Resampling.BICUBIC)
                canvas.paste(rotated_overlay, (0, 0), rotated_overlay)

                cross_size = 12
                draw.line([center_x - cross_size, center_y, center_x + cross_size, center_y], fill=(0, 255, 255), width=1)
                draw.line([center_x, center_y - cross_size, center_x, center_y + cross_size], fill=(0, 255, 255), width=1)

                canvas.convert("RGB").save(filepath)
                QMessageBox.information(self, "Success", f"Image with FOV successfully saved to:\n{filepath}")
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Failed to save image:\n{e}")

    def _on_slider_rot_changed(self, val):
        self.spin_rot.blockSignals(True)
        self.spin_rot.setValue(float(val))
        self.spin_rot.blockSignals(False)
        self.renderizar_fov_rapido()

    def _on_spin_rot_changed(self, val):
        self.slider_rot.blockSignals(True)
        self.slider_rot.setValue(int(val))
        self.slider_rot.blockSignals(False)
        self.renderizar_fov_rapido()
        self.render_timer.setInterval(150)
        self.update_image_transformations()

    def rot_value(self):
        return self.spin_rot.value()

    def set_rot_value(self, val):
        self.spin_rot.setValue(val)
        self.slider_rot.setValue(int(val))

    def reset_fov_offset(self):
        self.fov_offset_x = 0.0
        self.fov_offset_y = 0.0
        self.update_image_transformations()

    def atualizar_combos_instrumentos(self):
        dados = carregar_instrumentos()

        self.combo_panel_telescope.blockSignals(True)
        self.combo_panel_ccd.blockSignals(True)

        self.combo_panel_telescope.clear()
        for tel in dados.get("telescopes", []):
            nome = tel.get("name", "Unnamed")
            focal = tel.get("focal", "0")
            aperture = tel.get("aperture", "0")
            self.combo_panel_telescope.addItem(nome, {"focal": focal, "aperture": aperture})

        self.combo_panel_ccd.clear()
        for ccd in dados.get("ccds", []):
            nome = ccd.get("name", "Unnamed")
            pixel_x = ccd.get("pixel_x", "0")
            width = ccd.get("width", "0")
            height = ccd.get("height", "0")
            self.combo_panel_ccd.addItem(nome, {"pixel_x": pixel_x, "width": width, "height": height})

        self.combo_panel_telescope.blockSignals(False)
        self.combo_panel_ccd.blockSignals(False)

        if self.combo_panel_telescope.count() > 0:
            self.ao_selecionar_telescopio_panel(0)
        if self.combo_panel_ccd.count() > 0:
            self.ao_selecionar_ccd_panel(0)

    def ao_selecionar_telescopio_panel(self, index):
        self.active_comparison_setups = []
        dados = self.combo_panel_telescope.itemData(index)
        if dados:
            self.input_focal.setText(str(dados.get("focal", "500")))
            self.input_aperture.setText(str(dados.get("aperture", "80")))

    def ao_selecionar_ccd_panel(self, index):
        self.active_comparison_setups = []
        dados = self.combo_panel_ccd.itemData(index)
        if dados:
            self.input_pixel.setText(str(dados.get("pixel_x", "6.45")))
            self.input_width_px.setText(str(dados.get("width", "1392")))
            self.input_height_px.setText(str(dados.get("height", "1040")))

        if self.current_raw_image is not None:
            self.zoom_fit()

    def ao_alterar_parametro_optico(self):
        try:
            focal = float(self.input_focal.text())
            aperture = float(self.input_aperture.text())
            pixel = float(self.input_pixel.text())
            width_px = float(self.input_width_px.text())
            height_px = float(self.input_height_px.text())

            f_ratio = calculate_focal_ratio(focal, aperture)
            scale = calculate_image_scale(focal, pixel)
            fov = calculate_fov(focal, pixel, width_px, height_px)

            self.lbl_f_ratio.setText(f"f/{f_ratio:.1f}")
            self.lbl_scale_result.setText(f"{scale:.3f} arcsec/px")
            self.lbl_fov_result.setText(f"{fov['width_arcmin']:.2f}' × {fov['height_arcmin']:.2f}'")

            if self.current_raw_image is not None:
                self.update_image_transformations()
        except ValueError:
            pass

    def ao_clicar_botao_calcular(self):
        self.active_comparison_setups = []
        self.perform_calculations()

    def perform_calculations(self):
        try:
            settings = carregar_settings()
            settings["last_telescope"] = self.combo_panel_telescope.currentText()
            settings["last_ccd"] = self.combo_panel_ccd.currentText()
            guardar_settings(settings)

            focal = float(self.input_focal.text())
            aperture = float(self.input_aperture.text())
            pixel = float(self.input_pixel.text())
            width_px = float(self.input_width_px.text())
            height_px = float(self.input_height_px.text())

            f_ratio = calculate_focal_ratio(focal, aperture)
            scale = calculate_image_scale(focal, pixel)
            fov = calculate_fov(focal, pixel, width_px, height_px)

            self.lbl_f_ratio.setText(f"f/{f_ratio:.1f}")
            self.lbl_scale_result.setText(f"{scale:.3f} arcsec/px")
            self.lbl_fov_result.setText(f"{fov['width_arcmin']:.2f}' × {fov['height_arcmin']:.2f}'")

            max_sensor_fov = max(fov["width_arcmin"], fov["height_arcmin"])

            if self.active_comparison_setups:
                for st in self.active_comparison_setups:
                    cfov = calculate_fov(st["focal"], st["pixel"], st["width_px"], st["height_px"])
                    max_sensor_fov = max(max_sensor_fov, cfov["width_arcmin"], cfov["height_arcmin"])

            target_name = self.input_target.text().strip()
            if target_name:
                self.lbl_coords.setText("Querying coordinates...")
                self.lbl_canvas.setText("Loading background image in background thread...")
                self.lbl_canvas.adjustSize()
                self.btn_preview.setEnabled(False)

                self.registrar_tarefa("query_target", "Querying target coordinates...")

                if self.worker and self.worker.isRunning():
                    self.worker.wait(500)

                self.worker = ImageWorker(target_name, fov_solicitado=max_sensor_fov)
                self.worker.finished.connect(self.on_image_downloaded)
                self.worker.progress.connect(self.on_download_progress)
                self.worker.start()

        except ValueError:
            self.lbl_scale_result.setText("Parameter error!")
            self.lbl_fov_result.setText("Parameter error!")

    def atualizar_tabela_network(self):
        if self.net_load_worker is not None and self.net_load_worker.isRunning():
            return

        self.registrar_tarefa("net_load", "Loading Network records from Firebase...")

        self.net_load_worker = NetworkLoadWorker()
        self.net_load_worker.finished.connect(self.on_network_data_loaded)
        self.net_load_worker.start()

    def on_network_data_loaded(self, registos_rede):
        self.remover_tarefa("net_load")
        self.table_network.setRowCount(0)

        for item in reversed(registos_rede):
            row_idx = self.table_network.rowCount()
            self.table_network.insertRow(row_idx)
            self.table_network.setItem(row_idx, 0, QTableWidgetItem(item.get("user", "")))
            self.table_network.setItem(row_idx, 1, QTableWidgetItem(item.get("datetime", "")))
            self.table_network.setItem(row_idx, 2, QTableWidgetItem(item.get("target", "")))
            self.table_network.setItem(row_idx, 3, QTableWidgetItem(item.get("ra", "")))
            self.table_network.setItem(row_idx, 4, QTableWidgetItem(item.get("dec", "")))
            self.table_network.setItem(row_idx, 5, QTableWidgetItem(item.get("telescope", "")))
            self.table_network.setItem(row_idx, 6, QTableWidgetItem(str(item.get("focal", ""))))
            self.table_network.setItem(row_idx, 7, QTableWidgetItem(str(item.get("ccd", ""))))
            self.table_network.setItem(row_idx, 8, QTableWidgetItem(str(item.get("pixel_size", ""))))

    def executar_slew_telescopio(self):
        settings = carregar_settings()
        ascom_on = settings.get("ascom_enabled", False)
        indi_on = settings.get("indi_enabled", False)

        if not ascom_on and not indi_on:
            QMessageBox.warning(self, "Hardware Control Disabled", "Neither ASCOM nor INDI connection is enabled in Settings.")
            return

        coords = self.lbl_coords.text()
        if "--" in coords or "Querying" in coords:
            QMessageBox.information(self, "Notice", "Please calculate a valid target first.")
            return

        parts = coords.split("|")
        ra_val = parts[0].strip() if len(parts) > 0 else ""
        dec_val = parts[1].strip() if len(parts) > 1 else ""

        mensagens = []
        if ascom_on:
            host = settings.get("ascom_host", "127.0.0.1")
            port = settings.get("ascom_port", "11111")
            ok, msg = enviar_slew_ascom(host, port, ra_val, dec_val)
            mensagens.append(f"ASCOM: {msg}")

        if indi_on:
            host = settings.get("indi_host", "127.0.0.1")
            port = settings.get("indi_port", "7624")
            ok, msg = enviar_slew_indi(host, port, ra_val, dec_val)
            mensagens.append(f"INDI: {msg}")

        QMessageBox.information(self, "Telescope Slew Result", "\n\n".join(mensagens))

    def on_download_progress(self, percent, message=""):
        if percent == -1:
            self.atualizar_progresso_valores(-1, message if message else "Processing...")
        elif percent == 100:
            self.atualizar_progresso_valores(100, "Image ready (Cache/Network)")
            QTimer.singleShot(1200, self.terminar_tarefa)
        else:
            self.atualizar_progresso_valores(percent, message if message else f"Downloading... {percent}%")

    def on_image_downloaded(self, caminho_img, fov_efetivo, query_res):
        self.btn_preview.setEnabled(True)
        self.remover_tarefa("query_target")

        if query_res and (query_res.get("status", "").startswith("Sucesso") or query_res.get("status", "").startswith("Success")):
            ra = query_res["ra"]
            dec = query_res["dec"]
            target_name = self.input_target.text().strip()
            self.lbl_coords.setText(f"{ra} | {dec}")

            if not self.active_comparison_setups:
                self.adicionar_ao_historico(target_name, ra, dec, self.input_focal.text())

            settings = carregar_settings()
            if settings.get("share_network", True):
                user_name = settings.get("user_name", "rui")
                country = settings.get("user_country", "pt")
                telescope_nome = self.combo_panel_telescope.currentText()
                aperture_val = self.input_aperture.text().strip()
                telescope_str = f"{telescope_nome} ({aperture_val}mm)" if aperture_val else telescope_nome
                ccd_nome = self.combo_panel_ccd.currentText()
                focal = float(self.input_focal.text())
                pixel = float(self.input_pixel.text())

                enviar_registo_network(
                    user_name=user_name,
                    country=country,
                    target=target_name,
                    ra=ra,
                    dec=dec,
                    telescope=telescope_str,
                    focal=focal,
                    ccd=ccd_nome,
                    pixel_size=pixel
                )
                self.atualizar_tabela_network()
        else:
            status_msg = query_res.get("status", "Target not found.") if query_res else "Unknown error."
            self.lbl_coords.setText(status_msg)
            self.lbl_canvas.setText(status_msg)
            self.lbl_canvas.adjustSize()
            return

        if caminho_img and os.path.exists(caminho_img) and not caminho_img.startswith("ERROR:"):
            try:
                self.current_raw_image = PILImage.open(caminho_img)
                self._bg_cache_key = None
                self._bg_cache_pixmap = None
                self.current_bg_fov = fov_efetivo
                self.zoom_factor = 1.0
                self.fov_offset_x = 0.0
                self.fov_offset_y = 0.0

                self.cached_base_canvas = self.current_raw_image.copy().convert("RGB")

                self.set_rot_value(0.0)

                self.chk_flip_h.blockSignals(True)
                self.chk_flip_h.setChecked(False)
                self.chk_flip_h.blockSignals(False)

                self.chk_flip_v.blockSignals(True)
                self.chk_flip_v.setChecked(False)
                self.chk_flip_v.blockSignals(False)

                self.zoom_fit()
            except Exception as e:
                self.lbl_canvas.setText(f"Error opening image: {e}")
                self.lbl_canvas.adjustSize()
        else:
            erro_msg = caminho_img if caminho_img else "Unknown failure."
            print(f"[TERMINAL LOG - FAILURE] {erro_msg}")
            self.lbl_canvas.setText(f"Unable to load image:\n{erro_msg}")
            self.lbl_canvas.adjustSize()

    def get_transformed_base_dimensions(self):
        if self.current_raw_image is None:
            return 1, 1
        return self.current_raw_image.width, self.current_raw_image.height

    def calculate_fit_factor(self):
        if self.current_raw_image is None:
            return 1.0

        base_w, base_h = self.get_transformed_base_dimensions()

        try:
            focal = float(self.input_focal.text())
            pixel = float(self.input_pixel.text())
            width_px = float(self.input_width_px.text())
            height_px = float(self.input_height_px.text())

            fov = calculate_fov(focal, pixel, width_px, height_px)
            fov_w_arcmin = fov["width_arcmin"]
            fov_h_arcmin = fov["height_arcmin"]

            if self.active_comparison_setups:
                for st in self.active_comparison_setups:
                    cfov = calculate_fov(st["focal"], st["pixel"], st["width_px"], st["height_px"])
                    if cfov["width_arcmin"] > fov_w_arcmin:
                        fov_w_arcmin = cfov["width_arcmin"]
                    if cfov["height_arcmin"] > fov_h_arcmin:
                        fov_h_arcmin = cfov["height_arcmin"]

            pixels_per_arcmin_x = base_w / self.current_bg_fov
            pixels_per_arcmin_y = base_h / self.current_bg_fov

            needed_fov_w = max(self.current_bg_fov, fov_w_arcmin * 1.15)
            needed_fov_h = max(self.current_bg_fov, fov_h_arcmin * 1.15)

            total_canvas_w = int(needed_fov_w * pixels_per_arcmin_x)
            total_canvas_h = int(needed_fov_h * pixels_per_arcmin_y)
        except Exception:
            total_canvas_w = base_w
            total_canvas_h = base_h

        vp = self.scroll_area.viewport().size()
        vp_w = vp.width()
        vp_h = vp.height()
        if vp_w <= 10 or vp_h <= 10:
            vp_w = max(self.scroll_area.width() - 4, 600)
            vp_h = max(self.scroll_area.height() - 4, 400)

        target_w = max(vp_w - 16, 50)
        target_h = max(vp_h - 16, 50)

        return min(target_w / total_canvas_w, target_h / total_canvas_h)

    def get_zoom_limits(self):
        base_w, base_h = self.get_transformed_base_dimensions()
        bw = float(base_w)
        bh = float(base_h)
        max_dim = bw if bw > bh else bh
        min_dim = bh if bw > bh else bw
        if min_dim < 1.0:
            min_dim = 1.0
        min_factor = max(0.005, min(0.25, 60.0 / max_dim))
        max_factor = min(40.0, max(5.0, 10000.0 / min_dim))
        return min_factor, max_factor

    def center_scroll_bars(self):
        h_bar = self.scroll_area.horizontalScrollBar()
        v_bar = self.scroll_area.verticalScrollBar()
        h_bar.setValue((h_bar.maximum() + h_bar.minimum()) // 2)
        v_bar.setValue((v_bar.maximum() + v_bar.minimum()) // 2)

    def zoom_fit(self):
        if self.current_raw_image is not None:
            self._fit_pending = True
            self.zoom_factor = self.calculate_fit_factor()
            self.update_image_transformations()

    def zoom_in(self):
        if self.current_raw_image is not None:
            min_factor, max_factor = self.get_zoom_limits()
            new_zoom = min(self.zoom_factor * 1.25, max_factor)
            if new_zoom != self.zoom_factor:
                old_zoom = self.zoom_factor
                self.zoom_factor = new_zoom
                self.update_image_transformations(zoom_ratio=new_zoom / old_zoom)

    def zoom_out(self):
        if self.current_raw_image is not None:
            min_factor, max_factor = self.get_zoom_limits()
            new_zoom = max(self.zoom_factor / 1.25, min_factor)
            if new_zoom != self.zoom_factor:
                old_zoom = self.zoom_factor
                self.zoom_factor = new_zoom
                self.update_image_transformations(zoom_ratio=new_zoom / old_zoom)

    def update_image_transformations(self, zoom_ratio=None):
        if self.current_raw_image is None:
            return

        self.lbl_zoom_percent.setText(f"{int(self.zoom_factor * 100)}%")
        self._pending_zoom_ratio = zoom_ratio

        if self.is_rendering:
            self.render_pending = True
            if self.fov_worker and self.fov_worker.isRunning():
                self.fov_worker.cancel()
            return

        self.render_timer.start()

    def _obter_fundo_em_cache(self, canvas_w, canvas_h, bg_w, bg_h, paste_x, paste_y):
        flip_h = self.chk_flip_h.isChecked()
        flip_v = self.chk_flip_v.isChecked()
        cache_key = (id(self.current_raw_image), canvas_w, canvas_h, flip_h, flip_v, round(self.zoom_factor, 4))

        if self._bg_cache_key == cache_key and self._bg_cache_pixmap is not None:
            return self._bg_cache_pixmap

        img = self.current_raw_image.copy().convert("RGB")
        if flip_h:
            img = img.transpose(PILImage.Transpose.FLIP_LEFT_RIGHT)
        if flip_v:
            img = img.transpose(PILImage.Transpose.FLIP_TOP_BOTTOM)

        canvas = PILImage.new("RGB", (canvas_w, canvas_h), (20, 20, 20))
        canvas.paste(img, (paste_x, paste_y))

        new_w = max(int(canvas_w * self.zoom_factor), 10)
        new_h = max(int(canvas_h * self.zoom_factor), 10)
        resized = canvas.resize((new_w, new_h), PILImage.Resampling.BILINEAR)

        data = resized.tobytes("raw", "RGB")
        qimage = QImage(data, resized.width, resized.height, resized.width * 3, QImage.Format.Format_RGB888)
        pixmap = QPixmap.fromImage(qimage)

        self._bg_cache_key = cache_key
        self._bg_cache_pixmap = pixmap
        return pixmap

    def renderizar_fov_rapido(self):
        if self.current_raw_image is None:
            return
        try:
            setups = self.obter_setups_para_renderizacao()
            bg_w, bg_h = self.current_raw_image.size
            pixels_per_arcmin_x = bg_w / self.current_bg_fov
            pixels_per_arcmin_y = bg_h / self.current_bg_fov

            rot_deg = self.rot_value()
            rad_rot = math.radians(rot_deg)

            max_fov_w_arcmin = self.current_bg_fov
            max_fov_h_arcmin = self.current_bg_fov

            for st in setups:
                fov = calculate_fov(st["focal"], st["pixel"], st["width_px"], st["height_px"])
                fw, fh = fov["width_arcmin"], fov["height_arcmin"]
                rot_w = abs(fw * math.cos(rad_rot)) + abs(fh * math.sin(rad_rot))
                rot_h = abs(fw * math.sin(rad_rot)) + abs(fh * math.cos(rad_rot))
                max_fov_w_arcmin = max(max_fov_w_arcmin, rot_w)
                max_fov_h_arcmin = max(max_fov_h_arcmin, rot_h)

            needed_fov_w = max(self.current_bg_fov, max_fov_w_arcmin * 1.15)
            needed_fov_h = max(self.current_bg_fov, max_fov_h_arcmin * 1.15)
            canvas_w = int(needed_fov_w * pixels_per_arcmin_x)
            canvas_h = int(needed_fov_h * pixels_per_arcmin_y)

            paste_x = (canvas_w - bg_w) // 2
            paste_y = (canvas_h - bg_h) // 2

            base_pixmap = self._obter_fundo_em_cache(canvas_w, canvas_h, bg_w, bg_h, paste_x, paste_y)
            frame_pixmap = base_pixmap.copy()

            painter = QPainter(frame_pixmap)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)

            z = self.zoom_factor
            center_x = ((canvas_w / 2.0) + (self.fov_offset_x * pixels_per_arcmin_x)) * z
            center_y = ((canvas_h / 2.0) + (self.fov_offset_y * pixels_per_arcmin_y)) * z

            pen_cross = QPen(QColor(0, 255, 255), 1)
            painter.setPen(pen_cross)
            painter.drawLine(int(center_x - 10), int(center_y), int(center_x + 10), int(center_y))
            painter.drawLine(int(center_x), int(center_y - 10), int(center_x), int(center_y + 10))

            painter.translate(center_x, center_y)
            painter.rotate(rot_deg)

            font = QFont("Arial", max(9, int(11 * min(z, 1.5))), QFont.Weight.Bold)
            painter.setFont(font)

            for idx, st in enumerate(setups):
                fov = calculate_fov(st["focal"], st["pixel"], st["width_px"], st["height_px"])
                w = fov["width_arcmin"] * pixels_per_arcmin_x * z
                h = fov["height_arcmin"] * pixels_per_arcmin_y * z

                r, g, b = FOV_PALETTE[idx % len(FOV_PALETTE)]
                color = QColor(r, g, b)
                painter.setPen(QPen(color, 2))

                left = -w / 2.0
                top = -h / 2.0
                painter.drawRect(int(left), int(top), int(w), int(h))

                tel_name = st.get("telescope", "").strip()
                ccd_name = st.get("ccd", "").strip()
                clean_label = f"{tel_name} + {ccd_name}" if (tel_name and ccd_name) else (tel_name or ccd_name or f"Setup #{idx+1}")
                painter.drawText(int(left), int(top - 5), clean_label)

            painter.end()

            self.lbl_canvas.setPixmap(frame_pixmap)
            self.lbl_canvas.resize(frame_pixmap.size())
        except Exception as e:
            print(f"[FAST RENDER ERROR] {e}")

    def obter_setups_para_renderizacao(self):
        if self.active_comparison_setups:
            return self.active_comparison_setups

        try:
            focal = float(self.input_focal.text())
            pixel = float(self.input_pixel.text())
            width_px = float(self.input_width_px.text())
            height_px = float(self.input_height_px.text())
        except Exception:
            focal, pixel, width_px, height_px = 500.0, 6.45, 1392.0, 1040.0

        telescope_nome = self.combo_panel_telescope.currentText()
        ccd_nome = self.combo_panel_ccd.currentText()

        return [{
            "focal": focal,
            "pixel": pixel,
            "width_px": width_px,
            "height_px": height_px,
            "telescope": telescope_nome,
            "ccd": ccd_nome,
            "label": f"{telescope_nome} + {ccd_nome}" if telescope_nome and ccd_nome else "Current Setup"
        }]

    def _disparar_renderizacao_fov(self):
        if self.current_raw_image is None or self.is_rendering:
            return

        self.is_rendering = True
        self.render_pending = False

        setups_list = self.obter_setups_para_renderizacao()

        rot_deg = self.rot_value()
        flip_h = self.chk_flip_h.isChecked()
        flip_v = self.chk_flip_v.isChecked()

        zoom_ratio = self._pending_zoom_ratio
        self._pending_zoom_ratio = None

        self.registrar_tarefa("render_fov", "Rendering FOV and transformations...")

        self.fov_worker = FOVRenderWorker(
            self.current_raw_image,
            self.current_bg_fov,
            setups_list,
            rot_deg, flip_h, flip_v,
            self.zoom_factor,
            self.fov_offset_x,
            self.fov_offset_y
        )
        self.fov_worker._saved_zoom_ratio = zoom_ratio
        self.fov_worker.finished.connect(self.on_fov_rendered)
        self.fov_worker.start()

    def on_fov_rendered(self, qimage, zoom_factor):
        self.remover_tarefa("render_fov")
        self.is_rendering = False

        if qimage is not None and not qimage.isNull():
            pixmap = QPixmap.fromImage(qimage)
            self.lbl_canvas.setPixmap(pixmap)
            self.lbl_canvas.resize(pixmap.size())

            if self._fit_pending:
                self._fit_pending = False
                self.center_scroll_bars()
                QTimer.singleShot(20, self.center_scroll_bars)
                QTimer.singleShot(50, self.center_scroll_bars)

            zoom_ratio = getattr(self.fov_worker, '_saved_zoom_ratio', None)
            if zoom_ratio is not None and zoom_ratio > 0:
                h_bar = self.scroll_area.horizontalScrollBar()
                v_bar = self.scroll_area.verticalScrollBar()
                vp_w = self.scroll_area.viewport().width()
                vp_h = self.scroll_area.viewport().height()
                new_h = int((h_bar.value() + vp_w / 2.0) * zoom_ratio - vp_w / 2.0)
                new_v = int((v_bar.value() + vp_h / 2.0) * zoom_ratio - vp_h / 2.0)
                h_bar.setValue(new_h)
                v_bar.setValue(new_v)

        if self.render_pending:
            self.render_timer.start()

    def init_catalogs_tab(self):
        tab = QWidget()
        main_layout = QVBoxLayout(tab)
        grid_catalogs = QGridLayout()

        mapeamento_ficheiros = [
            ("BARNARD", "Barnard"),
            ("BSL", "Bright Stars"),
            ("CALDWELL", "Caldwell"),
            ("GPO", "Galactic Planetary Objects"),
            ("IC", "IC"),
            ("LBN", "Lynds Bright Nebulae"),
            ("LDN", "Lynds Dark Nebulae"),
            ("MESSIER", "Messier"),
            ("NGC", "NGC"),
            ("SAO", "SAO"),
            ("SCHIIR", "Sharpless Catalogue of HII Regions"),
            ("WDSD", "Washington Double Stars")
        ]

        for idx, (chave_dat, nome_exibicao) in enumerate(mapeamento_ficheiros):
            linha = idx // 2
            coluna = idx % 2
            grupo_box = QVBoxLayout()
            combo = QComboBox()

            itens_combo = [nome_exibicao]
            if chave_dat in self.catalogos_locais_dat:
                for obj in self.catalogos_locais_dat[chave_dat]:
                    itens_combo.append(obj["name"])
            else:
                itens_combo.append("Select an object...")

            combo.addItems(itens_combo)

            model = combo.model()
            if isinstance(model, QStandardItemModel):
                first_item = model.item(0)
                if first_item:
                    first_item.setEnabled(False)
                    first_item.setForeground(Qt.GlobalColor.gray)

            combo.currentIndexChanged.connect(lambda index, c=nome_exibicao, cb=combo: self.selecionar_alvo_catalogo(c, cb, index))
            grupo_box.addWidget(combo)
            grid_catalogs.addLayout(grupo_box, linha, coluna)

        main_layout.addLayout(grid_catalogs)
        main_layout.addStretch()

        note_group = QGroupBox()
        note_group.setMaximumHeight(75)
        note_group.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        note_layout = QVBoxLayout(note_group)
        note_layout.setContentsMargins(9, 5, 9, 5)
        note_label = QLabel(
            "Note:\nAstronomical catalog is a list of celestial bodies, usually including their main characteristics. "
            "When selecting an object from these catalogs, query is executed by exact coordinates loaded from local .dat files."
        )
        note_label.setWordWrap(True)
        note_label.setStyleSheet("color: #aaaaaa; font-style: italic;")
        note_layout.addWidget(note_label)
        main_layout.addWidget(note_group)

        self.tabs.addTab(tab, "Catalogs")

    def selecionar_alvo_catalogo(self, catalogo, combo, index):
        if index <= 0:
            return
        valor = combo.currentText()
        if "Select" in valor:
            return

        self.active_comparison_setups = []
        alvo_id = valor.split(" ")[0]
        self.input_target.setText(alvo_id)
        self.tabs.setCurrentIndex(0)

        combo.blockSignals(True)
        combo.setCurrentIndex(0)
        combo.blockSignals(False)

        print(f"[TERMINAL LOG] Catalog '{catalogo}' selected: {alvo_id} -> Redirected to Panel")

    def init_night_planner_tab(self):
        tab = QWidget()
        main_layout = QVBoxLayout(tab)

        top_bar = QHBoxLayout()
        self.lbl_planner_location = QLabel("Location: Loading...")
        top_bar.addWidget(self.lbl_planner_location)
        top_bar.addSpacing(15)

        top_bar.addWidget(QLabel("Catalog Filter:"))
        self.combo_planner_catalog_filter = QComboBox()
        self.combo_planner_catalog_filter.addItems([
            "All Catalogs", "Messier", "NGC", "IC", "Caldwell", "Barnard", "LBN/LDN", "Bright Stars"
        ])
        self.combo_planner_catalog_filter.currentIndexChanged.connect(self.aplicar_filtro_catalogos_planner)
        top_bar.addWidget(self.combo_planner_catalog_filter)

        top_bar.addWidget(QLabel("Min Alt (°):"))
        self.input_planner_min_alt = QLineEdit("20")
        self.input_planner_min_alt.setFixedWidth(40)
        top_bar.addWidget(self.input_planner_min_alt)

        top_bar.addWidget(QLabel("Max Alt (°):"))
        self.input_planner_max_alt = QLineEdit("90")
        self.input_planner_max_alt.setFixedWidth(40)
        top_bar.addWidget(self.input_planner_max_alt)

        self.chk_planner_only_dark = QCheckBox("Only Dark Period")
        self.chk_planner_only_dark.setChecked(True)
        self.chk_planner_only_dark.setToolTip("Dim objects that do not transit during dark night hours (Sun below horizon)")
        top_bar.addWidget(self.chk_planner_only_dark)

        top_bar.addWidget(QLabel("Min Mag:"))
        self.input_planner_min_mag = QLineEdit("")
        self.input_planner_min_mag.setFixedWidth(45)
        self.input_planner_min_mag.setPlaceholderText("Min")
        top_bar.addWidget(self.input_planner_min_mag)

        top_bar.addWidget(QLabel("Max Mag:"))
        self.input_planner_max_mag = QLineEdit("")
        self.input_planner_max_mag.setFixedWidth(45)
        self.input_planner_max_mag.setPlaceholderText("Max")
        top_bar.addWidget(self.input_planner_max_mag)

        self.btn_refresh_planner = QPushButton("🔄 Refresh Sky")
        self.btn_refresh_planner.setStyleSheet("font-weight: bold; padding: 6px 12px; background-color: #2b5b84; color: white;")
        self.btn_refresh_planner.clicked.connect(self.recalcular_night_planner)
        top_bar.addWidget(self.btn_refresh_planner)

        top_bar.addStretch()
        main_layout.addLayout(top_bar)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.addWidget(QLabel("<b>Visible Objects in Sky (Click header to sort)</b>"))

        planner_headers = [
            "Object / Target", "Catalog", "Mag", "Altitude", "Azimuth", "Quality", "Moon Sep.", "Peak Alt (Transit)", "RA", "Dec"
        ]

        self.table_planner = QTableWidget(0, 10)
        self.table_planner.setHorizontalHeaderLabels(planner_headers)
        self.table_planner.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table_planner.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table_planner.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table_planner.setSortingEnabled(True)
        self.table_planner.verticalHeader().setVisible(False)
        self.table_planner.doubleClicked.connect(lambda index: self.carregar_alvo_planner_para_painel(index.row(), self.table_planner))

        self.table_planner.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table_planner.customContextMenuRequested.connect(self.mostrar_menu_planner_visiveis)

        left_layout.addWidget(self.table_planner)
        splitter.addWidget(left_widget)

        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.addWidget(QLabel("<b>Night Favorites / Session Plan</b>"))

        self.table_favorites = QTableWidget(0, 10)
        self.table_favorites.setHorizontalHeaderLabels(planner_headers)
        self.table_favorites.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table_favorites.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table_favorites.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table_favorites.setSortingEnabled(True)
        self.table_favorites.verticalHeader().setVisible(False)
        self.table_favorites.doubleClicked.connect(lambda index: self.carregar_alvo_planner_para_painel(index.row(), self.table_favorites))

        self.table_favorites.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table_favorites.customContextMenuRequested.connect(self.mostrar_menu_planner_favoritos)

        right_layout.addWidget(self.table_favorites)
        splitter.addWidget(right_widget)

        splitter.setSizes([600, 600])
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 1)
        main_layout.addWidget(splitter)

        self.carregar_favoritos_tabela()

        note_group = QGroupBox()
        note_group.setMaximumHeight(75)
        note_group.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        note_layout = QVBoxLayout(note_group)
        note_layout.setContentsMargins(9, 5, 9, 5)
        note_label = QLabel(
            "Note:\nLeft table shows live celestial objects. Peak Alt (Transit) indicates peak culmination time. "
            "Right-click any object to open its Elevation Curve or load it to Panel."
        )
        note_label.setWordWrap(True)
        note_label.setStyleSheet("color: #aaaaaa; font-style: italic;")
        note_layout.addWidget(note_label)
        main_layout.addWidget(note_group)

        self.tabs.addTab(tab, "Night Planner")

    def recalcular_night_planner(self):
        if self.planner_worker is not None and self.planner_worker.isRunning():
            return

        settings = carregar_settings()
        lat = float(settings.get("user_lat", 38.64))
        lon = float(settings.get("user_lon", -9.10))

        try:
            min_alt = float(self.input_planner_min_alt.text().strip())
        except ValueError:
            min_alt = 20.0

        self.lbl_planner_location.setText(f"Location: Lat {lat:.2f}°, Lon {lon:.2f}°")
        self.registrar_tarefa("night_planner", "Calculating visible sky objects...")

        self.planner_worker = NightPlannerWorker(self.catalogos_locais_dat, lat, lon, min_alt)
        self.planner_worker.finished.connect(self.on_night_planner_calculated)
        self.planner_worker.start()

    def on_night_planner_calculated(self, lista_objetos):
        self.remover_tarefa("night_planner")
        self.night_planner_loaded = True

        mag_lookup = {}
        for cat_key, itens in self.catalogos_locais_dat.items():
            for item in itens:
                name = str(item.get("name", "")).strip().upper()
                mag = item.get("mag", item.get("magnitude", item.get("v_mag", None)))
                if mag is not None and str(mag).strip() not in ("", "None", "--"):
                    mag_lookup[(cat_key.upper(), name)] = mag
                    mag_lookup[name] = mag

        for obj in lista_objetos:
            current_mag = obj.get("mag", obj.get("magnitude", obj.get("v_mag", None)))
            if current_mag is None or str(current_mag).strip() in ("", "--", "None"):
                cat = str(obj.get("catalog", "")).strip().upper()
                name = str(obj.get("name", "")).strip().upper()
                found_mag = mag_lookup.get((cat, name), mag_lookup.get(name, None))
                if found_mag is not None:
                    obj["mag"] = found_mag

        self.raw_planner_objects = lista_objetos
        self.aplicar_filtro_catalogos_planner()

    def aplicar_filtro_catalogos_planner(self):
        cat_selecionado = self.combo_planner_catalog_filter.currentText()
        only_dark = self.chk_planner_only_dark.isChecked()

        self.table_planner.setSortingEnabled(False)
        self.table_planner.setRowCount(0)

        try:
            min_alt_limit = float(self.input_planner_min_alt.text().strip())
        except ValueError:
            min_alt_limit = 20.0

        try:
            max_alt_limit = float(self.input_planner_max_alt.text().strip())
        except ValueError:
            max_alt_limit = 90.0

        min_mag_str = self.input_planner_min_mag.text().strip()
        max_mag_str = self.input_planner_max_mag.text().strip()

        min_mag = float(min_mag_str) if min_mag_str else None
        max_mag = float(max_mag_str) if max_mag_str else None

        for obj in self.raw_planner_objects:
            catalog_name = obj.get("catalog", "").upper()

            if cat_selecionado != "All Catalogs":
                if cat_selecionado == "Messier" and "MESSIER" not in catalog_name:
                    continue
                elif cat_selecionado == "NGC" and "NGC" not in catalog_name:
                    continue
                elif cat_selecionado == "IC" and "IC" not in catalog_name:
                    continue
                elif cat_selecionado == "Caldwell" and "CALDWELL" not in catalog_name:
                    continue
                elif cat_selecionado == "Barnard" and "BARNARD" not in catalog_name:
                    continue
                elif cat_selecionado == "LBN/LDN" and not ("LBN" in catalog_name or "LDN" in catalog_name):
                    continue
                elif cat_selecionado == "Bright Stars" and "BSL" not in catalog_name:
                    continue

            raw_mag = obj.get("mag", obj.get("magnitude", obj.get("v_mag", obj.get("vmag", obj.get("m", obj.get("mag_v", None))))))
            mag_val = None
            if raw_mag is not None and str(raw_mag).strip() not in ("", "--", "None"):
                try:
                    mag_val = float(str(raw_mag).replace(",", "."))
                except (ValueError, TypeError):
                    pass

            if min_mag is not None and mag_val is not None and mag_val < min_mag:
                continue
            if max_mag is not None and mag_val is not None and mag_val > max_mag:
                continue

            alt_str = obj.get("altitude", "0°").replace("°", "").strip()
            try:
                alt_val = float(alt_str)
            except ValueError:
                alt_val = 0.0

            fora_de_limites = (alt_val < min_alt_limit) or (alt_val > max_alt_limit)

            fora_de_escuro = False
            if only_dark:
                try:
                    transit_time_str = obj.get("transit_time", "00:00")
                    if transit_time_str != "--:--":
                        now = Time.now()
                        settings = carregar_settings()
                        lat = float(settings.get("user_lat", 38.64))
                        lon = float(settings.get("user_lon", -9.10))
                        loc = EarthLocation(lat=lat*u.deg, lon=lon*u.deg)

                        t_parts = transit_time_str.split(":")
                        t_dt = now.datetime.replace(hour=int(t_parts[0]), minute=int(t_parts[1]), second=0, microsecond=0)
                        t_astropy = Time(t_dt)

                        sun_alt_transit = get_sun(t_astropy).transform_to(AltAz(obstime=t_astropy, location=loc)).alt.deg
                        if sun_alt_transit > -18.0:
                            fora_de_escuro = True
                except Exception:
                    pass

            deve_ficar_cinzento = fora_de_limites or fora_de_escuro

            mag_str = f"{mag_val:.1f}" if mag_val is not None else "--"

            row_idx = self.table_planner.rowCount()
            self.table_planner.insertRow(row_idx)

            item_name = QTableWidgetItem(obj.get("name", ""))
            item_cat = QTableWidgetItem(obj.get("catalog", ""))
            item_mag = QTableWidgetItem(mag_str)
            item_alt = QTableWidgetItem(obj.get("altitude", ""))
            item_az = QTableWidgetItem(obj.get("azimuth", ""))
            item_qual = QTableWidgetItem(obj.get("quality", ""))
            item_moon = QTableWidgetItem(obj.get("moon_sep", "--"))
            item_transit = QTableWidgetItem(obj.get("transit_time", "--:--"))
            item_ra = QTableWidgetItem(obj.get("ra", ""))
            item_dec = QTableWidgetItem(obj.get("dec", ""))

            if deve_ficar_cinzento:
                dark_gray = QColor(120, 120, 120)
                for it in [item_name, item_cat, item_mag, item_alt, item_az, item_qual, item_moon, item_transit, item_ra, item_dec]:
                    it.setForeground(dark_gray)

            self.table_planner.setItem(row_idx, 0, item_name)
            self.table_planner.setItem(row_idx, 1, item_cat)
            self.table_planner.setItem(row_idx, 2, item_mag)
            self.table_planner.setItem(row_idx, 3, item_alt)
            self.table_planner.setItem(row_idx, 4, item_az)
            self.table_planner.setItem(row_idx, 5, item_qual)
            self.table_planner.setItem(row_idx, 6, item_moon)
            self.table_planner.setItem(row_idx, 7, item_transit)
            self.table_planner.setItem(row_idx, 8, item_ra)
            self.table_planner.setItem(row_idx, 9, item_dec)

        self.table_planner.setSortingEnabled(True)
        self.carregar_favoritos_tabela()

    def carregar_alvo_planner_para_painel(self, row, table):
        if row < 0:
            return
        self.active_comparison_setups = []
        target_item = table.item(row, 0)
        if target_item:
            target_name = target_item.text().split(" ")[0]
            self.input_target.setText(target_name)
            self.tabs.setCurrentIndex(0)
            self.perform_calculations()

    def mostrar_menu_planner_visiveis(self, pos):
        row = self.table_planner.rowAt(pos.y())
        if row < 0:
            return

        menu = QMenu(self)
        acao_fav = menu.addAction("⭐ Add to Night Favorites")
        acao_fav.triggered.connect(lambda: self.adicionar_aos_favoritos_planner(row))

        acao_elev = menu.addAction("📈 Show Elevation Curve")
        acao_elev.triggered.connect(lambda: self.abrir_janela_elevacao(row, self.table_planner))

        acao_painel = menu.addAction("🔍 Load to Panel")
        acao_painel.triggered.connect(lambda: self.carregar_alvo_planner_para_painel(row, self.table_planner))

        menu.exec(self.table_planner.viewport().mapToGlobal(pos))

    def mostrar_menu_planner_favoritos(self, pos):
        row = self.table_favorites.rowAt(pos.y())
        if row < 0:
            return

        menu = QMenu(self)
        acao_rem = menu.addAction("🗑️ Remove from Favorites")
        acao_rem.triggered.connect(lambda: self.remover_dos_favoritos_planner(row))

        acao_elev = menu.addAction("📈 Show Elevation Curve")
        acao_elev.triggered.connect(lambda: self.abrir_janela_elevacao(row, self.table_favorites))

        acao_painel = menu.addAction("🔍 Load to Panel")
        acao_painel.triggered.connect(lambda: self.carregar_alvo_planner_para_painel(row, self.table_favorites))

        menu.exec(self.table_favorites.viewport().mapToGlobal(pos))

    def abrir_janela_elevacao(self, row, table):
        if row < 0:
            return
        target_name = table.item(row, 0).text() if table.item(row, 0) else "Target"
        ra_str = table.item(row, 8).text() if table.item(row, 8) else "0h 0m 0s"
        dec_str = table.item(row, 9).text() if table.item(row, 9) else "0° 0' 0\""

        dialog = ElevationDialog(target_name, ra_str, dec_str, self)
        dialog.exec()

    def adicionar_aos_favoritos_planner(self, row):
        if row < 0:
            return

        obj_data = {
            "name": self.table_planner.item(row, 0).text() if self.table_planner.item(row, 0) else "",
            "catalog": self.table_planner.item(row, 1).text() if self.table_planner.item(row, 1) else "",
            "mag": self.table_planner.item(row, 2).text() if self.table_planner.item(row, 2) else "--",
            "altitude": self.table_planner.item(row, 3).text() if self.table_planner.item(row, 3) else "",
            "azimuth": self.table_planner.item(row, 4).text() if self.table_planner.item(row, 4) else "",
            "quality": self.table_planner.item(row, 5).text() if self.table_planner.item(row, 5) else "",
            "moon_sep": self.table_planner.item(row, 6).text() if self.table_planner.item(row, 6) else "--",
            "transit_time": self.table_planner.item(row, 7).text() if self.table_planner.item(row, 7) else "--:--",
            "ra": self.table_planner.item(row, 8).text() if self.table_planner.item(row, 8) else "",
            "dec": self.table_planner.item(row, 9).text() if self.table_planner.item(row, 9) else ""
        }

        favs = carregar_favoritos()
        if not any(f.get("name") == obj_data["name"] and f.get("catalog") == obj_data["catalog"] for f in favs):
            favs.append(obj_data)
            guardar_favoritos(favs)
            self.carregar_favoritos_tabela()

    def remover_dos_favoritos_planner(self, row):
        if row < 0:
            return
        favs = carregar_favoritos()
        if 0 <= row < len(favs):
            favs.pop(row)
            guardar_favoritos(favs)
            self.carregar_favoritos_tabela()

    def carregar_favoritos_tabela(self):
        self.table_favorites.setSortingEnabled(False)
        self.table_favorites.setRowCount(0)
        favs = carregar_favoritos()

        try:
            min_alt_limit = float(self.input_planner_min_alt.text().strip())
        except ValueError:
            min_alt_limit = 20.0

        try:
            max_alt_limit = float(self.input_planner_max_alt.text().strip())
        except ValueError:
            max_alt_limit = 90.0

        only_dark = self.chk_planner_only_dark.isChecked()

        for obj in favs:
            row_idx = self.table_favorites.rowCount()
            self.table_favorites.insertRow(row_idx)

            item_name = QTableWidgetItem(obj.get("name", ""))
            item_cat = QTableWidgetItem(obj.get("catalog", ""))
            item_mag = QTableWidgetItem(obj.get("mag", "--"))
            item_alt = QTableWidgetItem(obj.get("altitude", ""))
            item_az = QTableWidgetItem(obj.get("azimuth", ""))
            item_qual = QTableWidgetItem(obj.get("quality", ""))
            item_moon = QTableWidgetItem(obj.get("moon_sep", "--"))
            item_transit = QTableWidgetItem(obj.get("transit_time", "--:--"))
            item_ra = QTableWidgetItem(obj.get("ra", ""))
            item_dec = QTableWidgetItem(obj.get("dec", ""))

            alt_str = obj.get("altitude", "0°").replace("°", "").strip()
            try:
                alt_val = float(alt_str)
            except ValueError:
                alt_val = 0.0

            fora_de_limites = (alt_val < min_alt_limit) or (alt_val > max_alt_limit)

            fora_de_escuro = False
            if only_dark:
                try:
                    transit_time_str = obj.get("transit_time", "00:00")
                    if transit_time_str != "--:--":
                        now = Time.now()
                        settings = carregar_settings()
                        lat = float(settings.get("user_lat", 38.64))
                        lon = float(settings.get("user_lon", -9.10))
                        loc = EarthLocation(lat=lat*u.deg, lon=lon*u.deg)

                        t_parts = transit_time_str.split(":")
                        t_dt = now.datetime.replace(hour=int(t_parts[0]), minute=int(t_parts[1]), second=0, microsecond=0)
                        t_astropy = Time(t_dt)

                        sun_alt_transit = get_sun(t_astropy).transform_to(AltAz(obstime=t_astropy, location=loc)).alt.deg
                        if sun_alt_transit > -18.0:
                            fora_de_escuro = True
                except Exception:
                    pass

            deve_ficar_cinzento = fora_de_limites or fora_de_escuro

            if deve_ficar_cinzento:
                dark_gray = QColor(120, 120, 120)
                for it in [item_name, item_cat, item_mag, item_alt, item_az, item_qual, item_moon, item_transit, item_ra, item_dec]:
                    it.setForeground(dark_gray)

            self.table_favorites.setItem(row_idx, 0, item_name)
            self.table_favorites.setItem(row_idx, 1, item_cat)
            self.table_favorites.setItem(row_idx, 2, item_mag)
            self.table_favorites.setItem(row_idx, 3, item_alt)
            self.table_favorites.setItem(row_idx, 4, item_az)
            self.table_favorites.setItem(row_idx, 5, item_qual)
            self.table_favorites.setItem(row_idx, 6, item_moon)
            self.table_favorites.setItem(row_idx, 7, item_transit)
            self.table_favorites.setItem(row_idx, 8, item_ra)
            self.table_favorites.setItem(row_idx, 9, item_dec)

        self.table_favorites.setSortingEnabled(True)

    def init_historic_tab(self):
        tab = QWidget()
        main_layout = QVBoxLayout(tab)

        self.table_historic = QTableWidget(0, 9)
        self.table_historic.setHorizontalHeaderLabels([
            "Date/Time", "Target", "RA", "Dec", "Telescope", "Focal Len.", "CCD", "Pixel Size X", "Pixel Size Y"
        ])
        self.table_historic.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table_historic.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table_historic.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table_historic.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table_historic.verticalHeader().setVisible(False)

        self.table_historic.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table_historic.customContextMenuRequested.connect(lambda pos: self.mostrar_menu_contexto(pos, self.table_historic))
        self.table_historic.doubleClicked.connect(lambda index: self.carregar_dados_para_painel(self.table_historic, index.row()))

        main_layout.addWidget(self.table_historic)

        historico_salvo = carregar_historico()
        for item in reversed(historico_salvo):
            row_idx = self.table_historic.rowCount()
            self.table_historic.insertRow(row_idx)
            self.table_historic.setItem(row_idx, 0, QTableWidgetItem(item.get("datetime", "")))
            self.table_historic.setItem(row_idx, 1, QTableWidgetItem(item.get("target", "")))
            self.table_historic.setItem(row_idx, 2, QTableWidgetItem(item.get("ra", "")))
            self.table_historic.setItem(row_idx, 3, QTableWidgetItem(item.get("dec", "")))
            self.table_historic.setItem(row_idx, 4, QTableWidgetItem(item.get("telescope", "")))
            self.table_historic.setItem(row_idx, 5, QTableWidgetItem(item.get("focal", "")))
            self.table_historic.setItem(row_idx, 6, QTableWidgetItem(item.get("ccd", "")))
            self.table_historic.setItem(row_idx, 7, QTableWidgetItem(item.get("pixel_x", "")))
            self.table_historic.setItem(row_idx, 8, QTableWidgetItem(item.get("pixel_y", "")))

        note_group = QGroupBox()
        note_group.setMaximumHeight(75)
        note_group.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        note_layout = QVBoxLayout(note_group)
        note_layout.setContentsMargins(9, 5, 9, 5)
        note_label = QLabel(
            "Note:\nSelect multiple rows using [Ctrl] or [Shift] and right-click to compare FOV overlays simultaneously in Panel."
        )
        note_label.setWordWrap(True)
        note_label.setStyleSheet("color: #aaaaaa; font-style: italic;")
        note_layout.addWidget(note_label)
        main_layout.addWidget(note_group)

        self.tabs.addTab(tab, "Historic")

    def init_network_tab(self):
        tab = QWidget()
        main_layout = QVBoxLayout(tab)

        self.table_network = QTableWidget(0, 9)
        self.table_network.setHorizontalHeaderLabels([
            "User", "Date/Time", "Target", "RA", "Dec", "Telescope", "Focal Len.", "CCD", "Pixel Size"
        ])
        self.table_network.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table_network.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table_network.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table_network.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table_network.verticalHeader().setVisible(False)

        self.table_network.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table_network.customContextMenuRequested.connect(lambda pos: self.mostrar_menu_contexto(pos, self.table_network))
        self.table_network.doubleClicked.connect(lambda index: self.carregar_dados_para_painel(self.table_network, index.row()))

        main_layout.addWidget(self.table_network)
        self.atualizar_tabela_network()

        note_group = QGroupBox()
        note_group.setMaximumHeight(75)
        note_group.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        note_layout = QVBoxLayout(note_group)
        note_layout.setContentsMargins(9, 5, 9, 5)
        note_label = QLabel(
            "Note:\nHere you will see all searches in the network in real time. Select multiple setups to compare FOVs in Panel."
        )
        note_label.setWordWrap(True)
        note_label.setStyleSheet("color: #aaaaaa; font-style: italic;")
        note_layout.addWidget(note_label)
        main_layout.addWidget(note_group)

        self.tabs.addTab(tab, "Network")

    def mostrar_menu_contexto(self, pos, table):
        selected_rows = table.selectionModel().selectedRows()
        if not selected_rows:
            return

        menu = QMenu(self)

        if len(selected_rows) > 1:
            acao_comparar = menu.addAction("⚖️ Compare Selected Setups (Multi-FOV)")
            acao_comparar.triggered.connect(lambda: self.comparar_setups_multi_selecionados(table))
        else:
            row = selected_rows[0].row()
            acao_carregar = menu.addAction("🔍 Load parameters to Panel")
            acao_carregar.triggered.connect(lambda: self.carregar_dados_para_painel(table, row))

            if table == self.table_network:
                acao_exportar = menu.addAction("📥 Export Setup to Instruments")
                acao_exportar.triggered.connect(lambda: self.exportar_setup_network(row))

        menu.exec(table.viewport().mapToGlobal(pos))

    def comparar_setups_multi_selecionados(self, table):
        selected_rows = table.selectionModel().selectedRows()
        if not selected_rows:
            return

        instrumentos = carregar_instrumentos()
        ccds_dict = {c.get("name", "").strip().lower(): c for c in instrumentos.get("ccds", [])}

        setups = []
        target_comum = None

        for idx in selected_rows:
            row = idx.row()
            if table == self.table_network:
                target = table.item(row, 2).text() if table.item(row, 2) else ""
                telescope_nome = table.item(row, 5).text() if table.item(row, 5) else ""
                focal_str = table.item(row, 6).text() if table.item(row, 6) else "500"
                ccd_nome = table.item(row, 7).text() if table.item(row, 7) else ""
                pixel_str = table.item(row, 8).text() if table.item(row, 8) else "6.45"
            else:
                target = table.item(row, 1).text() if table.item(row, 1) else ""
                telescope_nome = table.item(row, 4).text() if table.item(row, 4) else ""
                focal_str = table.item(row, 5).text() if table.item(row, 5) else "500"
                ccd_nome = table.item(row, 6).text() if table.item(row, 6) else ""
                pixel_str = table.item(row, 7).text() if table.item(row, 7) else "6.45"

            if target_comum is None and target:
                target_comum = target

            try:
                focal = float(focal_str)
            except ValueError:
                focal = 500.0

            try:
                pixel = float(pixel_str)
            except ValueError:
                pixel = 6.45

            w_px, h_px = 1392.0, 1040.0
            ccd_key = ccd_nome.strip().lower()
            if ccd_key in ccds_dict:
                try:
                    w_px = float(ccds_dict[ccd_key].get("width", 1392))
                    h_px = float(ccds_dict[ccd_key].get("height", 1040))
                except ValueError:
                    pass

            setups.append({
                "focal": focal,
                "pixel": pixel,
                "width_px": w_px,
                "height_px": h_px,
                "telescope": telescope_nome,
                "ccd": ccd_nome,
                "label": f"{telescope_nome} + {ccd_nome}" if telescope_nome and ccd_nome else "Setup"
            })

        if setups:
            self.active_comparison_setups = setups
            if target_comum:
                self.input_target.setText(target_comum)

            self.tabs.setCurrentIndex(0)
            self.perform_calculations()

    def exportar_setup_network(self, row):
        if row < 0:
            return
        telescope_name = self.table_network.item(row, 5).text() if self.table_network.item(row, 5) else "Network Telescope"
        focal_len = self.table_network.item(row, 6).text() if self.table_network.item(row, 6) else "500"
        ccd_name = self.table_network.item(row, 7).text() if self.table_network.item(row, 7) else "Network CCD"
        pixel_size = self.table_network.item(row, 8).text() if self.table_network.item(row, 8) else "6.45"

        dados = carregar_instrumentos()
        ccds = dados.get("ccds", [])
        telescopes = dados.get("telescopes", [])

        if not any(c.get("name") == ccd_name for c in ccds):
            ccds.append({
                "name": ccd_name,
                "pixel_x": pixel_size,
                "pixel_y": pixel_size,
                "width": "2048",
                "height": "2048"
            })

        if not any(t.get("name") == telescope_name for t in telescopes):
            telescopes.append({
                "name": telescope_name,
                "focal": focal_len,
                "aperture": "80"
            })

        sucesso = guardar_instrumentos(ccds, telescopes)
        if sucesso:
            self.atualizar_combos_instrumentos()
            QMessageBox.information(self, "Export Setup", f"Setup '{ccd_name}' and '{telescope_name}' successfully exported to Instruments tab!")
        else:
            QMessageBox.warning(self, "Error", "Could not save setup to instruments.")

    def carregar_dados_para_painel(self, table, row):
        if row < 0:
            return

        self.active_comparison_setups = []

        if table == self.table_network:
            target = table.item(row, 2).text() if table.item(row, 2) else ""
            telescope_name = table.item(row, 5).text() if table.item(row, 5) else ""
            focal_len = table.item(row, 6).text() if table.item(row, 6) else ""
            ccd_name = table.item(row, 7).text() if table.item(row, 7) else ""
            pixel_size = table.item(row, 8).text() if table.item(row, 8) else ""
        else:
            target = table.item(row, 1).text() if table.item(row, 1) else ""
            telescope_name = table.item(row, 4).text() if table.item(row, 4) else ""
            focal_len = table.item(row, 5).text() if table.item(row, 5) else ""
            ccd_name = table.item(row, 6).text() if table.item(row, 6) else ""
            pixel_size = table.item(row, 7).text() if table.item(row, 7) else ""

        self.input_target.setText(target)

        for i in range(self.combo_panel_telescope.count()):
            if telescope_name.lower() in self.combo_panel_telescope.itemText(i).lower():
                self.combo_panel_telescope.setCurrentIndex(i)
                break
        else:
            self.input_focal.setText(focal_len)

        for i in range(self.combo_panel_ccd.count()):
            if ccd_name.lower() in self.combo_panel_ccd.itemText(i).lower():
                self.combo_panel_ccd.setCurrentIndex(i)
                break
        else:
            self.input_pixel.setText(pixel_size)

        self.tabs.setCurrentIndex(0)
        self.perform_calculations()
        print(f"[TERMINAL LOG] Data loaded to panel: Target={target}, Focal={focal_len}mm, CCD={ccd_name}")

    def adicionar_ao_historico(self, target, ra, dec, focal):
        self.table_historic.insertRow(0)

        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")

        telescope_nome = self.combo_panel_telescope.currentText()
        aperture_val = self.input_aperture.text().strip()
        telescopio_str = f"{telescope_nome} ({aperture_val}mm)" if aperture_val else telescope_nome

        ccd_nome = self.combo_panel_ccd.currentText()
        pixel_val = self.input_pixel.text()

        dados = [timestamp, target, ra, dec, telescopio_str, focal, ccd_nome, pixel_val, pixel_val]
        for col_idx, val in enumerate(dados):
            self.table_historic.setItem(0, col_idx, QTableWidgetItem(val))

        lista_completa = []
        for row in range(self.table_historic.rowCount()):
            def _get_hist_text(c):
                it = self.table_historic.item(row, c)
                return it.text() if it is not None else ""
            lista_completa.append({
                "datetime": _get_hist_text(0),
                "target": _get_hist_text(1),
                "ra": _get_hist_text(2),
                "dec": _get_hist_text(3),
                "telescope": _get_hist_text(4),
                "focal": _get_hist_text(5),
                "ccd": _get_hist_text(6),
                "pixel_x": _get_hist_text(7),
                "pixel_y": _get_hist_text(8),
            })
        guardar_historico(lista_completa)

    def init_instruments_tab(self):
        tab = QWidget()
        main_layout = QVBoxLayout(tab)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        ccd_widget = QWidget()
        ccd_layout = QVBoxLayout(ccd_widget)
        ccd_layout.setContentsMargins(0, 0, 0, 0)

        ccd_toolbar = QHBoxLayout()
        self.btn_add_ccd = QPushButton("➕")
        self.btn_del_ccd = QPushButton("➖")
        self.btn_up_ccd = QPushButton("⬆")
        self.btn_down_ccd = QPushButton("⬇")
        self.btn_save_ccd = QPushButton("💾")

        for b, tooltip in [(self.btn_add_ccd, "Add CCD"), (self.btn_del_ccd, "Remove CCD"),
                           (self.btn_up_ccd, "Move Up"), (self.btn_down_ccd, "Move Down"), (self.btn_save_ccd, "Save CCDs")]:
            b.setToolTip(tooltip)
            b.setFixedWidth(40)
            ccd_toolbar.addWidget(b)
        ccd_toolbar.addStretch()
        ccd_layout.addLayout(ccd_toolbar)

        self.table_ccd = QTableWidget(0, 5)
        self.table_ccd.setHorizontalHeaderLabels(["CCD list", "Pixel X (µm)", "Pixel Y (µm)", "Width (px)", "Height (px)"])
        self.table_ccd.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table_ccd.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table_ccd.verticalHeader().setVisible(False)
        ccd_layout.addWidget(self.table_ccd)
        splitter.addWidget(ccd_widget)

        tel_widget = QWidget()
        tel_layout = QVBoxLayout(tel_widget)
        tel_layout.setContentsMargins(0, 0, 0, 0)

        tel_toolbar = QHBoxLayout()
        self.btn_add_tel = QPushButton("➕")
        self.btn_del_tel = QPushButton("➖")
        self.btn_up_tel = QPushButton("⬆")
        self.btn_down_tel = QPushButton("⬇")
        self.btn_save_tel = QPushButton("💾")

        for b, tooltip in [(self.btn_add_tel, "Add Telescope"), (self.btn_del_tel, "Remove Telescope"),
                           (self.btn_up_tel, "Move Up"), (self.btn_down_tel, "Move Down"), (self.btn_save_tel, "Save Telescopes")]:
            b.setToolTip(tooltip)
            b.setFixedWidth(40)
            tel_toolbar.addWidget(b)
        tel_toolbar.addStretch()
        tel_layout.addLayout(tel_toolbar)

        self.table_tel = QTableWidget(0, 3)
        self.table_tel.setHorizontalHeaderLabels(["Telescope list", "Focal length (mm)", "Aperture (mm)"])
        self.table_tel.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table_tel.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table_tel.verticalHeader().setVisible(False)
        tel_layout.addWidget(self.table_tel)
        splitter.addWidget(tel_widget)

        splitter.setSizes([600, 600])
        main_layout.addWidget(splitter)

        dados_salvos = carregar_instrumentos()

        for ccd in dados_salvos.get("ccds", []):
            row_idx = self.table_ccd.rowCount()
            self.table_ccd.insertRow(row_idx)
            self.table_ccd.setItem(row_idx, 0, QTableWidgetItem(ccd.get("name", "")))
            self.table_ccd.setItem(row_idx, 1, QTableWidgetItem(ccd.get("pixel_x", "")))
            self.table_ccd.setItem(row_idx, 2, QTableWidgetItem(ccd.get("pixel_y", "")))
            self.table_ccd.setItem(row_idx, 3, QTableWidgetItem(ccd.get("width", "")))
            self.table_ccd.setItem(row_idx, 4, QTableWidgetItem(ccd.get("height", "")))

        for tel in dados_salvos.get("telescopes", []):
            row_idx = self.table_tel.rowCount()
            self.table_tel.insertRow(row_idx)
            self.table_tel.setItem(row_idx, 0, QTableWidgetItem(tel.get("name", "")))
            self.table_tel.setItem(row_idx, 1, QTableWidgetItem(tel.get("focal", "")))
            self.table_tel.setItem(row_idx, 2, QTableWidgetItem(tel.get("aperture", "")))

        self.btn_add_ccd.clicked.connect(lambda: self.add_inline_row(self.table_ccd))
        self.btn_del_ccd.clicked.connect(lambda: self.delete_equipment_row(self.table_ccd))
        self.btn_save_ccd.clicked.connect(self.salvar_dados_atuais)
        self.btn_up_ccd.clicked.connect(lambda: self.move_row(self.table_ccd, -1))
        self.btn_down_ccd.clicked.connect(lambda: self.move_row(self.table_ccd, 1))

        self.btn_add_tel.clicked.connect(lambda: self.add_inline_row(self.table_tel))
        self.btn_del_tel.clicked.connect(lambda: self.delete_equipment_row(self.table_tel))
        self.btn_save_tel.clicked.connect(self.salvar_dados_atuais)
        self.btn_up_tel.clicked.connect(lambda: self.move_row(self.table_tel, -1))
        self.btn_down_tel.clicked.connect(lambda: self.move_row(self.table_tel, 1))

        note_group = QGroupBox()
        note_group.setMaximumHeight(75)
        note_group.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        note_layout = QVBoxLayout(note_group)
        note_layout.setContentsMargins(9, 5, 9, 5)
        note_label = QLabel("Note:\nHere you can manage your astronomical setup. Click '➕' to add a row, double-click to edit, and click '💾' to save changes permanently.")
        note_label.setWordWrap(True)
        note_label.setStyleSheet("color: #aaaaaa; font-style: italic;")
        note_layout.addWidget(note_label)
        main_layout.addWidget(note_group)

        self.tabs.addTab(tab, "Instruments")

    @staticmethod
    def add_inline_row(table):
        row_idx = table.rowCount()
        table.insertRow(row_idx)
        for col in range(table.columnCount()):
            table.setItem(row_idx, col, QTableWidgetItem(""))
        table.setCurrentCell(row_idx, 0)
        item = table.item(row_idx, 0)
        if item:
            table.editItem(item)

    @staticmethod
    def delete_equipment_row(table):
        selected_rows = table.selectionModel().selectedRows()
        if selected_rows:
            for index in sorted(selected_rows, key=lambda idx: idx.row(), reverse=True):
                table.removeRow(index.row())
        else:
            row = table.currentRow()
            if row >= 0:
                table.removeRow(row)

    @staticmethod
    def move_row(table, direction):
        current_row = table.currentRow()
        if current_row < 0:
            return
        target_row = current_row + direction
        if target_row < 0 or target_row >= table.rowCount():
            return

        for col in range(table.columnCount()):
            current_item = table.takeItem(current_row, col)
            target_item = table.takeItem(target_row, col)
            table.setItem(target_row, col, current_item)
            table.setItem(current_row, col, target_item)

        table.selectRow(target_row)

    def salvar_dados_atuais(self):
        ccds = []
        for row in range(self.table_ccd.rowCount()):
            it0 = self.table_ccd.item(row, 0)
            it1 = self.table_ccd.item(row, 1)
            it2 = self.table_ccd.item(row, 2)
            it3 = self.table_ccd.item(row, 3)
            it4 = self.table_ccd.item(row, 4)
            name = it0.text() if it0 is not None else ""
            px = it1.text() if it1 is not None else ""
            py = it2.text() if it2 is not None else ""
            w = it3.text() if it3 is not None else ""
            h = it4.text() if it4 is not None else ""
            ccds.append({"name": name, "pixel_x": px, "pixel_y": py, "width": w, "height": h})

        telescopes = []
        for row in range(self.table_tel.rowCount()):
            it0 = self.table_tel.item(row, 0)
            it1 = self.table_tel.item(row, 1)
            it2 = self.table_tel.item(row, 2)
            name = it0.text() if it0 is not None else ""
            focal = it1.text() if it1 is not None else ""
            aperture = it2.text() if it2 is not None else ""
            telescopes.append({"name": name, "focal": focal, "aperture": aperture})

        sucesso = guardar_instrumentos(ccds, telescopes)
        if sucesso:
            self.atualizar_combos_instrumentos()
            print("[TERMINAL LOG] Instruments saved and synchronized with Panel tab.")

    def init_settings_tab(self):
        tab = QWidget()
        main_layout = QVBoxLayout(tab)
        settings = carregar_settings()

        user_group = QGroupBox("User Profile & Location")
        user_layout = QGridLayout(user_group)

        user_layout.addWidget(QLabel("Username:"), 0, 0)
        self.input_username = QLineEdit(settings.get("user_name", "rui"))
        user_layout.addWidget(self.input_username, 0, 1)

        user_layout.addWidget(QLabel("Country:"), 1, 0)
        self.combo_country = QComboBox()
        self.combo_country.setEditable(False)

        paises = [
            ("AF", "Afghanistan"), ("AL", "Albania"), ("DZ", "Algeria"), ("AD", "Andorra"), ("AO", "Angola"),
            ("AG", "Antigua and Barbuda"), ("AR", "Argentina"), ("AM", "Armenia"), ("AU", "Australia"), ("AT", "Austria"),
            ("AZ", "Azerbaijan"), ("BS", "Bahamas"), ("BH", "Bahrain"), ("BD", "Bangladesh"), ("BB", "Barbados"),
            ("BY", "Belarus"), ("BE", "Belgium"), ("BZ", "Belize"), ("BJ", "Benin"), ("BT", "Bhutan"),
            ("BO", "Bolivia"), ("BA", "Bosnia and Herzegovina"), ("BW", "Botswana"), ("BR", "Brazil"), ("BN", "Brunei"),
            ("BG", "Bulgaria"), ("BF", "Burkina Faso"), ("BI", "Burundi"), ("CV", "Cabo Verde"), ("KH", "Cambodia"),
            ("CM", "Cameroon"), ("CA", "Canada"), ("CF", "Central African Republic"), ("TD", "Chad"), ("CL", "Chile"),
            ("CN", "China"), ("CO", "Colombia"), ("KM", "Comoros"), ("CG", "Congo"), ("CD", "Congo (Democratic Republic)"),
            ("CR", "Costa Rica"), ("CI", "Cote d'Ivoire"), ("HR", "Croatia"), ("CU", "Cuba"), ("CY", "Cyprus"),
            ("CZ", "Czechia"), ("DK", "Denmark"), ("DJ", "Djibouti"), ("DM", "Dominica"), ("DO", "Dominican Republic"),
            ("EC", "Ecuador"), ("EG", "Egypt"), ("SV", "El Salvador"), ("GQ", "Equatorial Guinea"), ("ER", "Eritrea"),
            ("EE", "Estonia"), ("SZ", "Eswatini"), ("ET", "Ethiopia"), ("FJ", "Fiji"), ("FI", "Finland"),
            ("FR", "France"), ("GA", "Gabon"), ("GM", "Gambia"), ("GE", "Georgia"), ("DE", "Germany"),
            ("GH", "Ghana"), ("GR", "Greece"), ("GD", "Grenada"), ("GT", "Guatemala"), ("GN", "Guinea"),
            ("GW", "Guinea-Bissau"), ("GY", "Guyana"), ("HT", "Haiti"), ("HN", "Honduras"), ("HU", "Hungary"),
            ("IS", "Iceland"), ("IN", "India"), ("ID", "Indonesia"), ("IR", "Iran"), ("IQ", "Iraq"),
            ("IE", "Ireland"), ("IL", "Israel"), ("IT", "Italy"), ("JM", "Jamaica"), ("JP", "Japan"),
            ("JO", "Jordan"), ("KZ", "Kazakhstan"), ("KE", "Kenya"), ("KI", "Kiribati"), ("KP", "Korea (North)"),
            ("KR", "Korea (South)"), ("KW", "Kuwait"), ("KG", "Kyrgyzstan"), ("LA", "Laos"), ("LV", "Latvia"),
            ("LB", "Lebanon"), ("LS", "Lesotho"), ("LR", "Liberia"), ("LY", "Libya"), ("LI", "Liechtenstein"),
            ("LT", "Lithuania"), ("LU", "Luxembourg"), ("MG", "Madagascar"), ("MW", "Malawi"), ("MY", "Malaysia"),
            ("MV", "Maldives"), ("ML", "Mali"), ("MT", "Malta"), ("MH", "Marshall Islands"), ("MR", "Mauritania"),
            ("MU", "Mauritius"), ("MX", "Mexico"), ("FM", "Micronesia"), ("MD", "Moldova"), ("MC", "Monaco"),
            ("MN", "Mongolia"), ("ME", "Montenegro"), ("MA", "Morocco"), ("MZ", "Mozambique"), ("MM", "Myanmar"),
            ("NA", "Namibia"), ("NR", "Nauru"), ("NP", "Nepal"), ("NL", "Netherlands"), ("NZ", "New Zealand"),
            ("NI", "Nicaragua"), ("NE", "Niger"), ("NG", "Nigeria"), ("MK", "North Macedonia"), ("NO", "Norway"),
            ("OM", "Oman"), ("PK", "Pakistan"), ("PW", "Palau"), ("PS", "Palestine"), ("PA", "Panama"),
            ("PG", "Papua New Guinea"), ("PY", "Paraguay"), ("PE", "Peru"), ("PH", "Philippines"), ("PL", "Poland"),
            ("PT", "Portugal"), ("QA", "Qatar"), ("RO", "Romania"), ("RU", "Russia"), ("RW", "Rwanda"),
            ("KN", "Saint Kitts and Nevis"), ("LC", "Saint Lucia"), ("VC", "Saint Vincent and the Grenadines"), ("WS", "Samoa"), ("SM", "San Marino"),
            ("ST", "Sao Tome and Principe"), ("SA", "Saudi Arabia"), ("SN", "Senegal"), ("RS", "Serbia"), ("SC", "Seychelles"),
            ("SL", "Sierra Leone"), ("SG", "Singapore"), ("SK", "Slovakia"), ("SI", "Slovenia"), ("SB", "Solomon Islands"),
            ("SO", "Somalia"), ("ZA", "South Africa"), ("SS", "South Sudan"), ("ES", "Spain"), ("LK", "Sri Lanka"),
            ("SD", "Sudan"), ("SR", "Suriname"), ("SE", "Sweden"), ("CH", "Switzerland"), ("SY", "Syria"),
            ("TW", "Taiwan"), ("TJ", "Tajikistan"), ("TZ", "Tanzania"), ("TH", "Thailand"), ("TL", "Timor-Leste"),
            ("TG", "Togo"), ("TO", "Tonga"), ("TT", "Trinidad and Tobago"), ("TN", "Tunisia"), ("TR", "Turkey"),
            ("TM", "Turkmenistan"), ("TV", "Tuvalu"), ("UG", "Uganda"), ("UA", "Ukraine"), ("AE", "United Arab Emirates"),
            ("GB", "United Kingdom"), ("US", "United States"), ("UY", "Uruguay"), ("UZ", "Uzbekistan"), ("VU", "Vanuatu"),
            ("VA", "Vatican City"), ("VE", "Venezuela"), ("VN", "Vietnam"), ("YE", "Yemen"), ("ZM", "Zambia"),
            ("ZW", "Zimbabwe")
        ]

        saved_country = settings.get("user_country", "pt").lower()
        sel_index = 0
        for idx, (code, name) in enumerate(paises):
            self.combo_country.addItem(f"{code} - {name}", code.lower())
            if code.lower() == saved_country:
                sel_index = idx
        self.combo_country.setCurrentIndex(sel_index)
        user_layout.addWidget(self.combo_country, 1, 1)

        user_layout.addWidget(QLabel("Latitude (°N):"), 2, 0)
        self.input_lat = QLineEdit(str(settings.get("user_lat", 38.64)))
        user_layout.addWidget(self.input_lat, 2, 1)

        user_layout.addWidget(QLabel("Longitude (°E/W):"), 3, 0)
        self.input_lon = QLineEdit(str(settings.get("user_lon", -9.10)))
        user_layout.addWidget(self.input_lon, 3, 1)

        self.chk_share_network = QCheckBox("Allow sharing my searches in the Network")
        self.chk_share_network.setChecked(settings.get("share_network", True))
        user_layout.addWidget(self.chk_share_network, 4, 0, 1, 2)
        main_layout.addWidget(user_group)

        ascom_group = QGroupBox("Telescope Connection & Control (ASCOM Alpaca)")
        ascom_layout = QGridLayout(ascom_group)

        self.chk_ascom_enabled = QCheckBox("Enable ASCOM / Alpaca Connection")
        self.chk_ascom_enabled.setChecked(settings.get("ascom_enabled", False))
        self.chk_ascom_enabled.toggled.connect(self.on_ascom_toggled)
        ascom_layout.addWidget(self.chk_ascom_enabled, 0, 0, 1, 2)

        ascom_layout.addWidget(QLabel("IP Address / Host:"), 1, 0)
        self.input_ascom_host = QLineEdit(settings.get("ascom_host", "127.0.0.1"))
        ascom_layout.addWidget(self.input_ascom_host, 1, 1)

        ascom_layout.addWidget(QLabel("Port:"), 2, 0)
        self.input_ascom_port = QLineEdit(settings.get("ascom_port", "11111"))
        ascom_layout.addWidget(self.input_ascom_port, 2, 1)
        main_layout.addWidget(ascom_group)

        indi_group = QGroupBox("Telescope Connection & Control (INDI Protocol)")
        indi_layout = QGridLayout(indi_group)

        self.chk_indi_enabled = QCheckBox("Enable INDI Server Connection")
        self.chk_indi_enabled.setChecked(settings.get("indi_enabled", False))
        self.chk_indi_enabled.toggled.connect(self.on_indi_toggled)
        indi_layout.addWidget(self.chk_indi_enabled, 0, 0, 1, 2)

        indi_layout.addWidget(QLabel("IP Address / Host:"), 1, 0)
        self.input_indi_host = QLineEdit(settings.get("indi_host", "127.0.0.1"))
        indi_layout.addWidget(self.input_indi_host, 1, 1)

        indi_layout.addWidget(QLabel("Port:"), 2, 0)
        self.input_indi_port = QLineEdit(settings.get("indi_port", "7624"))
        indi_layout.addWidget(self.input_indi_port, 2, 1)
        main_layout.addWidget(indi_group)

        btn_save_settings = QPushButton("💾 Save Settings")
        btn_save_settings.setStyleSheet("font-weight: bold; padding: 8px; background-color: #2b5b84; color: white;")
        btn_save_settings.clicked.connect(self.salvar_configuracoes_settings)
        main_layout.addWidget(btn_save_settings)
        main_layout.addStretch()

        note_group = QGroupBox()
        note_group.setMaximumHeight(75)
        note_group.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        note_layout = QVBoxLayout(note_group)
        note_layout.setContentsMargins(9, 5, 9, 5)
        note_label = QLabel(
            "Note:\nConfigure your observatory location (Latitude/Longitude) to accurately compute target visibility in the Night Planner tab."
        )
        note_label.setWordWrap(True)
        note_label.setStyleSheet("color: #aaaaaa; font-style: italic;")
        note_layout.addWidget(note_label)
        main_layout.addWidget(note_group)

        self.tabs.addTab(tab, "Settings")

    def init_about_tab(self):
        tab = QWidget()
        main_layout = QVBoxLayout(tab)

        about_group = QGroupBox("About Accdc (Astronomy CCD Calculator)")
        about_layout = QVBoxLayout(about_group)

        header_layout = QHBoxLayout()
        logo_path = obter_caminho_asset("logo.png")
        if os.path.exists(logo_path):
            lbl_logo = QLabel()
            pixmap = QPixmap(logo_path).scaled(
                72, 72,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation
            )
            lbl_logo.setPixmap(pixmap)
            lbl_logo.setFixedSize(72, 72)
            lbl_logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
            header_layout.addWidget(lbl_logo)
            header_layout.addSpacing(16)

        title_layout = QVBoxLayout()
        lbl_titulo = QLabel("<b>Accdc - Astronomy CCD Calculator</b>")
        lbl_titulo.setStyleSheet("font-size: 17px; color: #ffffff;")

        lbl_versao = QLabel(f"Version: {APP_VERSION}")
        lbl_versao.setStyleSheet("color: #4da6ff; font-weight: bold; font-size: 13px;")

        title_layout.addWidget(lbl_titulo)
        title_layout.addWidget(lbl_versao)
        title_layout.addStretch()

        header_layout.addLayout(title_layout)
        header_layout.addStretch()

        about_layout.addLayout(header_layout)

        lbl_descricao = QLabel(
            "<b>Accdc</b> is an advanced tool for optical calculation, Field of View (FOV) simulation, "
            "astronomical instrument management, night target planning, and hardware control via ASCOM / INDI protocols, "
            "designed to support amateur and professional astronomers in planning astrophotography sessions."
        )
        lbl_descricao.setWordWrap(True)
        lbl_descricao.setStyleSheet("color: #dddddd; margin-top: 12px; line-height: 1.4;")

        lbl_tech = QLabel(
            "<b>Technologies Used:</b><br>"
            "• Python / PySide6 (Graphical Interface)<br>"
            "• Astropy & Pillow (Optical Processing, Coordinates & Night Planner)<br>"
            "• JSON (Local Data & History Persistence)<br>"
            "• ASCOM / Alpaca & INDI Protocols (Telescope Hardware Integration)"
        )
        lbl_tech.setWordWrap(True)
        lbl_tech.setStyleSheet("color: #cccccc; margin-top: 15px;")

        about_layout.addWidget(lbl_descricao)
        about_layout.addWidget(lbl_tech)
        about_layout.addStretch()

        main_layout.addWidget(about_group)
        main_layout.addStretch()

        note_group = QGroupBox()
        note_group.setMaximumHeight(75)
        note_group.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        note_layout = QVBoxLayout(note_group)
        note_layout.setContentsMargins(9, 5, 9, 5)
        note_label = QLabel(
            "Note:\nDesigned for high-precision field calculations and astronomical observation coordination."
        )
        note_label.setWordWrap(True)
        note_label.setStyleSheet("color: #aaaaaa; font-style: italic;")
        note_layout.addWidget(note_label)
        main_layout.addWidget(note_group)

        self.tabs.addTab(tab, "About")

    def on_ascom_toggled(self, checked):
        if checked and self.chk_indi_enabled.isChecked():
            self.chk_indi_enabled.blockSignals(True)
            self.chk_indi_enabled.setChecked(False)
            self.chk_indi_enabled.blockSignals(False)

    def on_indi_toggled(self, checked):
        if checked and self.chk_ascom_enabled.isChecked():
            self.chk_ascom_enabled.blockSignals(True)
            self.chk_ascom_enabled.setChecked(False)
            self.chk_ascom_enabled.blockSignals(False)

    def salvar_configuracoes_settings(self):
        settings = carregar_settings()
        try:
            lat = float(self.input_lat.text().strip())
            lon = float(self.input_lon.text().strip())
        except ValueError:
            lat, lon = 38.64, -9.10

        novas_settings = {
            "user_name": self.input_username.text().strip(),
            "user_country": self.combo_country.currentData(),
            "user_lat": lat,
            "user_lon": lon,
            "share_network": self.chk_share_network.isChecked(),
            "ascom_host": self.input_ascom_host.text().strip(),
            "ascom_port": self.input_ascom_port.text().strip(),
            "ascom_enabled": self.chk_ascom_enabled.isChecked(),
            "indi_host": self.input_indi_host.text().strip(),
            "indi_port": self.input_indi_port.text().strip(),
            "indi_enabled": self.chk_indi_enabled.isChecked(),
            "last_telescope": settings.get("last_telescope", ""),
            "last_ccd": settings.get("last_ccd", "")
        }
        sucesso = guardar_settings(novas_settings)
        if sucesso:
            self.verificar_rede_tempo_real()
            self.night_planner_loaded = False
            QMessageBox.information(self, "Settings", "Configuration successfully saved to disk (`data/settings.json`)!")
        else:
            QMessageBox.warning(self, "Error", "Could not save configuration.")

if __name__ == "__main__":
    setup_logging()
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("ruirigel.accdc.calculator")
    except Exception:
        pass

    app = QApplication(sys.argv)
    logo_path = obter_caminho_asset("logo.png")
    if os.path.exists(logo_path):
        app.setWindowIcon(QIcon(logo_path))

    window = MainWindow()
    window.show()
    sys.exit(app.exec())