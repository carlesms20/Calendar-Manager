# --- Etapa 1: build del frontend React con Vite ---
FROM node:20-alpine AS frontend-builder

WORKDIR /build

# Manifests primero para aprovechar cache de layers cuando solo cambia codigo
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm ci

# Codigo del frontend y build
COPY frontend/ ./
RUN npm run build


# --- Etapa 2: runtime Python 3.13 ---
FROM python:3.13-slim

WORKDIR /app

# curl por si queremos healthcheck externo mas adelante. Nada mas hace falta:
# el audio lo procesa Gemini remoto, no necesitamos ffmpeg local.
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Requirements primero para cachear la layer de pip
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Codigo Python en la raiz del repo
COPY *.py ./

# Frontend compilado desde la etapa 1, replicando la ruta que server.py espera
COPY --from=frontend-builder /build/dist ./frontend/dist

# Railway/Fly.io inyectan PORT via env. En local sin PORT arranca en 8000.
EXPOSE 8000

CMD ["python", "main.py"]