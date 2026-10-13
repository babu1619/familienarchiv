# Festes, offizielles Python-Image (gibt es für aarch64 und amd64).
# Das von Home Assistant übergebene BUILD_FROM wird bewusst nicht verwendet:
# dessen Basis-Image enthält kein Python.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN python3 -m pip install --no-cache-dir -r /app/requirements.txt

COPY archiv_core /app/archiv_core
COPY dienst /app/dienst

EXPOSE 8099
CMD ["python3", "-m", "dienst"]
