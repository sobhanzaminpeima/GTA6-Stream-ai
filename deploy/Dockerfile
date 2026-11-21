# Server deployment (Linux). The desktop default is start_studio.ps1 on Windows.
FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg fonts-dejavu-core && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements-studio.txt .
RUN pip install --no-cache-dir -r requirements-studio.txt "psycopg[binary]>=3.2"
COPY studio ./studio
ENV STUDIO_DATA_DIR=/data
VOLUME ["/data"]
EXPOSE 8765
# Put it behind a reverse proxy with TLS and set STUDIO_API_TOKEN when exposing beyond localhost.
CMD ["python", "-m", "studio", "serve", "--host", "0.0.0.0", "--port", "8765"]
