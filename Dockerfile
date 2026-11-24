FROM python:3.12-slim-trixie AS base

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    DOCSTATION_DATA=/data
WORKDIR /app
RUN groupadd -g 10001 docstation && useradd -m -u 10001 -g 10001 docstation \
    && install -d -o docstation -g docstation -m 0700 /data
COPY requirements-web.txt ./
RUN python -m pip install --no-cache-dir -r requirements-web.txt

FROM base AS web
RUN python -m pip install --no-cache-dir gunicorn==27.0.0 whitenoise==6.12.0
COPY manage.py ./
COPY config config
COPY documents documents
RUN DOCSTATION_DATA=/tmp/docstation-build DOCSTATION_SECRET_KEY=build-only \
    python manage.py collectstatic --noinput \
    && chmod -R a+rX /app
USER docstation
RUN test -r manage.py && test -r config/settings.py && test -r documents/models.py
CMD ["sh", "-ec", "python manage.py migrate --noinput && exec python manage.py runserver 0.0.0.0:8000 --noreload"]

FROM base AS worker
COPY requirements.txt ./
RUN python -m pip install --no-cache-dir -r requirements.txt
RUN apt-get update && apt-get install -y --no-install-recommends \
    libreoffice-writer-nogui libreoffice-calc-nogui libreoffice-impress-nogui \
    fonts-dejavu-core fonts-crosextra-carlito fonts-crosextra-caladea \
    qpdf ghostscript poppler-utils pandoc tesseract-ocr tesseract-ocr-rus tesseract-ocr-eng \
    && dpkg --compare-versions "$(dpkg-query -W -f='${Version}' ghostscript)" ge '10.05.1~dfsg-1+deb13u3' \
    && rm -rf /var/lib/apt/lists/*
COPY --from=web /app /app
USER docstation
CMD ["python", "manage.py", "worker"]
