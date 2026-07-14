"""Normalización de fotos y videos EN LA PUERTA (al subirlos desde el panel).

🔴 Nació de un caso real (2026-07-14): la dueña subió el video de la Torta keto en
formato QuickTime (.mov, lo que graba un iPhone) y WhatsApp NO acepta ese formato —
el envío iba a fallar SIEMPRE, sin que nadie supiera por qué. La clienta no tiene por
qué saber de formatos: sube LO QUE SEA y el sistema lo deja como WhatsApp lo exige.

Reglas de WhatsApp (Cloud API):
  · VIDEO:  MP4 (H.264 + AAC), máximo 16 MB.
  · IMAGEN: JPEG o PNG, máximo 5 MB.

Doctrina: la conversión pasa UNA vez, al subir (la puerta), no en cada envío. Lo que
queda guardado en R2 ya es enviable. Si un archivo no se puede convertir, se RECHAZA
con un mensaje claro — jamás se guarda algo que después no se pueda enviar (eso es un
"envío fantasma" en cámara lenta).
"""
import asyncio
import io
import json
import logging
import os
import subprocess
import tempfile

logger = logging.getLogger(__name__)

MAX_VIDEO = 16 * 1024 * 1024  # 16 MB (límite de video de WhatsApp)
MAX_IMAGEN = 5 * 1024 * 1024  # 5 MB (límite DURO de imagen de WhatsApp)
# 🔴 EL OBJETIVO REAL no es el límite de 5 MB: es que Meta descargue el enlace de R2 RÁPIDO y no
# lo rechace bajo ráfagas (caso real 2026-07-14: fotos de 1.5 MB fallaban al pedir varias
# seguidas). WhatsApp muestra las fotos pequeñas; 1600px de lado y ~500 KB se ven idénticas en el
# teléfono, cargan al instante y no gatillan el rate-limit de la URL pública de R2.
OBJETIVO_IMAGEN = 500 * 1024   # 500 KB: la meta de compresión (no el límite)
LADO_MAX_IMAGEN = 1600         # px del lado mayor: de sobra para una foto de producto en WhatsApp


class MediaInvalida(Exception):
    """El archivo no se puede dejar en un formato que WhatsApp acepte. El mensaje es
    para la dueña (claro, sin jerga)."""


def _ffprobe(ruta: str) -> dict:
    """Formato y códecs reales del archivo (no la extensión: la extensión miente)."""
    out = subprocess.run(
        [
            "ffprobe", "-v", "error", "-print_format", "json",
            "-show_format", "-show_streams", ruta,
        ],
        capture_output=True, timeout=60,
    )
    if out.returncode != 0:
        raise MediaInvalida("No se pudo leer el video. ¿Seguro que el archivo no está dañado?")
    return json.loads(out.stdout or "{}")


def _video_ya_sirve(info: dict) -> bool:
    """True si YA es MP4 con H.264 (+ AAC si tiene audio): no hay que tocarlo."""
    formato = (info.get("format", {}).get("format_name") or "")
    if "mp4" not in formato:
        return False
    for s in info.get("streams", []):
        if s.get("codec_type") == "video" and s.get("codec_name") != "h264":
            return False
        if s.get("codec_type") == "audio" and s.get("codec_name") != "aac":
            return False
    return True


def _convertir_video_sync(contenido: bytes) -> bytes:
    with tempfile.TemporaryDirectory() as d:
        origen = os.path.join(d, "origen")
        destino = os.path.join(d, "destino.mp4")
        with open(origen, "wb") as f:
            f.write(contenido)

        info = _ffprobe(origen)
        if _video_ya_sirve(info) and len(contenido) <= MAX_VIDEO:
            return contenido  # ya es lo que WhatsApp pide: no se re-comprime (no perder calidad)

        # Dos intentos: calidad normal (hasta 1280px) y, si sigue pesado, 720px más comprimido.
        for escala, crf in (("1280", "26"), ("720", "28")):
            cmd = [
                "ffmpeg", "-y", "-i", origen,
                "-c:v", "libx264", "-preset", "medium", "-crf", crf,
                "-pix_fmt", "yuv420p",
                "-vf", f"scale='min({escala},iw)':-2",
                "-c:a", "aac", "-b:a", "96k",
                "-movflags", "+faststart",
                destino,
            ]
            out = subprocess.run(cmd, capture_output=True, timeout=600)
            if out.returncode != 0:
                logger.error("ffmpeg falló: %s", (out.stderr or b"")[-400:])
                raise MediaInvalida(
                    "No se pudo convertir el video. Intenta grabarlo o exportarlo de nuevo."
                )
            tam = os.path.getsize(destino)
            if tam <= MAX_VIDEO:
                with open(destino, "rb") as f:
                    return f.read()
        raise MediaInvalida(
            "El video es muy largo o muy pesado para WhatsApp (máximo ~16 MB ya convertido). "
            "Recórtalo a un video más corto y súbelo de nuevo."
        )


async def normalizar_video(contenido: bytes) -> tuple[bytes, str, str]:
    """Deja CUALQUIER video como WhatsApp lo exige. Devuelve (bytes, content_type, ext).
    Lanza MediaInvalida (mensaje para la dueña) si no se puede."""
    convertido = await asyncio.to_thread(_convertir_video_sync, contenido)
    return convertido, "video/mp4", "mp4"


def _convertir_imagen_sync(contenido: bytes, content_type: str) -> tuple[bytes, str, str]:
    ct = (content_type or "").split(";")[0].strip().lower()
    # ⚡ SIEMPRE se optimiza para el ENVÍO, no solo si pasa el límite de 5 MB. Un JPEG de 1.5 MB
    # "cabía" en 5 MB y antes se dejaba TAL CUAL — y era justo el que Meta rechazaba bajo ráfagas.
    # Ahora todo pasa por aquí: se baja a 1600px y se comprime hasta ~500 KB. Si el original ya es
    # un JPEG pequeño y liviano, sale casi idéntico; si es pesado, se aligera.
    from PIL import Image
    try:
        from pillow_heif import register_heif_opener  # fotos de iPhone (HEIC/HEIF)
        register_heif_opener()
    except Exception:  # noqa: BLE001 — si no está, los demás formatos igual funcionan
        pass

    try:
        img = Image.open(io.BytesIO(contenido))
        img = img.convert("RGB")  # JPEG no acepta transparencia
    except Exception as e:  # noqa: BLE001
        logger.error("Imagen ilegible (%s): %s", ct, e)
        raise MediaInvalida(
            "No se pudo leer la imagen. Prueba guardarla como JPG y subirla de nuevo."
        ) from e

    # Baja el lado mayor a 1600px (de sobra para una foto de producto en WhatsApp).
    if max(img.size) > LADO_MAX_IMAGEN:
        img.thumbnail((LADO_MAX_IMAGEN, LADO_MAX_IMAGEN))

    # Comprime buscando ~500 KB; si a calidad 60 aún no baja de 5 MB, es que era descomunal.
    ultimo = None
    for calidad in (85, 78, 70, 62, 55):
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=calidad, optimize=True)
        ultimo = buf.getvalue()
        if len(ultimo) <= OBJETIVO_IMAGEN:
            return ultimo, "image/jpeg", "jpeg"
    # No llegó a 500 KB (foto muy detallada): vale igual mientras respete el límite DURO de 5 MB.
    if ultimo is not None and len(ultimo) <= MAX_IMAGEN:
        return ultimo, "image/jpeg", "jpeg"
    raise MediaInvalida("La imagen es demasiado pesada incluso comprimida. Usa una foto más pequeña.")


async def normalizar_imagen(contenido: bytes, content_type: str) -> tuple[bytes, str, str]:
    """Deja CUALQUIER imagen como WhatsApp la exige (JPEG/PNG ≤ 5 MB).
    Devuelve (bytes, content_type, ext). Lanza MediaInvalida si no se puede."""
    return await asyncio.to_thread(_convertir_imagen_sync, contenido, content_type)
