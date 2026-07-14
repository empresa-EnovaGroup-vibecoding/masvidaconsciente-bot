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
MAX_IMAGEN = 5 * 1024 * 1024  # 5 MB (límite de imagen de WhatsApp)


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
    # JPEG/PNG que ya caben: tal cual (no re-comprimir lo que ya sirve).
    if ct in ("image/jpeg", "image/jpg", "image/png") and len(contenido) <= MAX_IMAGEN:
        ext = "png" if ct == "image/png" else "jpeg"
        return contenido, f"image/{'png' if ext == 'png' else 'jpeg'}", ext

    # Todo lo demás (HEIC de iPhone, WebP, o un JPEG gigante) → JPEG que quepa.
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

    # Fotos de cámara enormes: bajar el lado mayor a 2048px (de sobra para WhatsApp).
    if max(img.size) > 2048:
        img.thumbnail((2048, 2048))
    for calidad in (90, 80, 70, 60):
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=calidad, optimize=True)
        if buf.tell() <= MAX_IMAGEN:
            return buf.getvalue(), "image/jpeg", "jpeg"
    raise MediaInvalida("La imagen es demasiado pesada incluso comprimida. Usa una foto más pequeña.")


async def normalizar_imagen(contenido: bytes, content_type: str) -> tuple[bytes, str, str]:
    """Deja CUALQUIER imagen como WhatsApp la exige (JPEG/PNG ≤ 5 MB).
    Devuelve (bytes, content_type, ext). Lanza MediaInvalida si no se puede."""
    return await asyncio.to_thread(_convertir_imagen_sync, contenido, content_type)
