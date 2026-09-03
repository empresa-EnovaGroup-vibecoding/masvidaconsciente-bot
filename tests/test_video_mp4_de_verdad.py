"""EL VIDEO QUE WHATSAPP RECHAZA — el QuickTime disfrazado de .mp4 (autopsia 2026-09-03).

EL BUG (con evidencia en vivo): `_video_ya_sirve` comparaba `"mp4" in format_name`, pero
ffprobe reporta para CUALQUIER QuickTime (.mov de iPhone) el format_name compartido
`"mov,mp4,m4a,3gp,3g2,mj2"` — que CONTIENE "mp4". Así, los 5 videos .mov del catálogo
pasaron como "ya sirven": el barrido del 14-jul los re-subió byte a byte con extensión
.mp4 (los 5 Last-Modified en 5 segundos lo prueban), y el PRIMER envío real de un video
(Tortas keto, mensaje 9481→9499 del 3-sep) murió con `131053 Media upload error` — Meta
descargó el archivo (HTTP 200, 0.45 MB) y su procesador rechazó el contenedor QuickTime.

LA CURA: el contenedor REAL lo dice `format.tags.major_brand` (un MP4 ISO trae isom/mp42/…;
un QuickTime trae `qt`), en LISTA BLANCA — lo desconocido se convierte, jamás se deja pasar.
Y los streams `data` de cámara (Pan Keto arrastra 4) también obligan a convertir: el ffmpeg
los descarta con `-map 0:v:0 -map 0:a:0?`.

Y en `convertir_media_vieja.py`: los VIDEOS ya no se saltan por extensión — la extensión
.mp4 es exactamente la mentira que dejó la corrida del 14-jul.
"""
import pytest

from app.services.media_convert import _video_ya_sirve
from scripts.convertir_media_vieja import _se_salta_sin_sondear

# El format_name que ffprobe reporta IGUAL para un MP4 ISO real y para un QuickTime:
# es el demuxer compartido. Por eso jamás puede ser el juez.
_FORMAT_NAME_COMPARTIDO = "mov,mp4,m4a,3gp,3g2,mj2"


def _info(brand, streams):
    """Arma un dict con la forma EXACTA que devuelve ffprobe -print_format json."""
    formato = {"format_name": _FORMAT_NAME_COMPARTIDO}
    if brand is not None:
        formato["tags"] = {"major_brand": brand}
    return {"format": formato, "streams": streams}


_H264 = {"codec_type": "video", "codec_name": "h264"}
_AAC = {"codec_type": "audio", "codec_name": "aac"}


# ── 1) LA REVERSIÓN-ROJA DEL BUG: el QuickTime del iPhone ──
def test_un_quicktime_con_h264_y_aac_NO_sirve():
    """🔴 EL CASO EXACTO de los 5 videos del catálogo: contenedor `qt`, códecs buenos,
    y un format_name que contiene "mp4". El código viejo devolvía True (por la subcadena)
    y por eso el barrido del 14-jul no convirtió nada. Tiene que ser False: se convierte."""
    assert _video_ya_sirve(_info("qt  ", [_H264, _AAC])) is False


def test_un_mp4_iso_de_verdad_SI_sirve():
    """El contraste: MISMO format_name, mismos códecs — lo único distinto es el brand.
    Si esto se pusiera rojo, el conversor re-comprimiría todo lo sano (pérdida de calidad)."""
    assert _video_ya_sirve(_info("isom", [_H264, _AAC])) is True
    assert _video_ya_sirve(_info("mp42", [_H264, _AAC])) is True


def test_sin_major_brand_NO_sirve():
    """Un contenedor que no dice su marca es sospechoso: el lado seguro es convertir."""
    assert _video_ya_sirve(_info(None, [_H264, _AAC])) is False


def test_un_mp4_fragmentado_dash_NO_sirve():
    """`dash` queda fuera de la lista blanca a propósito: mejor normalizarlo."""
    assert _video_ya_sirve(_info("dash", [_H264, _AAC])) is False


# ── 2) Los streams `data` del iPhone (el caso Pan Keto: 4 streams de metadata) ──
def test_streams_data_obligan_a_convertir():
    assert _video_ya_sirve(_info("isom", [
        _H264, _AAC, {"codec_type": "data", "codec_name": "bin_data"},
    ])) is False


# ── 3) Las reglas de códec de siempre se conservan ──
@pytest.mark.parametrize("streams", [
    [{"codec_type": "video", "codec_name": "hevc"}, _AAC],   # H.265 no es H.264
    [_H264, {"codec_type": "audio", "codec_name": "mp3"}],   # mp3 no es AAC
])
def test_codec_equivocado_sigue_convirtiendose(streams):
    assert _video_ya_sirve(_info("isom", streams)) is False


def test_video_sin_audio_si_sirve():
    """El audio es opcional (el ffmpeg usa `0:a:0?`): un MP4 ISO H.264 mudo pasa limpio."""
    assert _video_ya_sirve(_info("isom", [_H264])) is True


# ── 4) El barrido ya no confía en la extensión de los VIDEOS ──
def test_un_video_punto_mp4_SE_SONDEA_igual():
    """🔴 La otra mitad del no-op del 14-jul: el script saltaba todo video `.mp4` sin bajarlo —
    y los 5 rotos eran justamente `.mp4` porque el propio script los renombró. Un video
    JAMÁS se salta por extensión: se baja y ffprobe decide."""
    assert _se_salta_sin_sondear("video", "productos/11/a9fad9.mp4") is False
    assert _se_salta_sin_sondear("video", "productos/3/pan.quicktime") is False


def test_las_imagenes_conservan_su_salto_por_extension():
    """Las 29 fotos se verificaron sanas (autopsia 3-sep) y su puerta valida contenido real
    con Pillow: bajarlas todas en cada corrida sería costo sin defensa nueva."""
    assert _se_salta_sin_sondear("imagen", "productos/11/foto.jpeg") is True
    assert _se_salta_sin_sondear("imagen", "productos/11/foto.png") is True
    assert _se_salta_sin_sondear("imagen", "productos/11/foto.heic") is False
