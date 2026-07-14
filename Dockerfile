FROM python:3.12-slim

WORKDIR /app

# ffmpeg: convierte CUALQUIER video/imagen que suba la dueña al formato que WhatsApp
# exige (MP4 H.264 / JPEG). Sin esto, un .mov de iPhone se guardaba tal cual y el envío
# por WhatsApp fallaba SIEMPRE (caso real: el video de la Torta keto, 2026-07-14).
RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
