FROM postgres:17.12-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PATH="/opt/erp/bin:${PATH}"
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends python3 python3-venv \
    && rm -rf /var/lib/apt/lists/* && python3 -m venv /opt/erp
RUN groupadd --gid 10001 erp \
    && useradd --uid 10001 --gid erp --no-create-home erp \
    && install -d -o erp -g erp -m 0700 /srv/erp/media \
    && install -d -o erp -g erp -m 0755 /srv/erp/static
COPY requirements.txt /app/
COPY wheelhouse /tmp/wheelhouse
RUN pip install --no-cache-dir --no-index --find-links /tmp/wheelhouse -r requirements.txt \
    && pip install --no-cache-dir gunicorn==27.0.1 \
    && rm -rf /tmp/wheelhouse
COPY . /app
RUN test -f frontend/dist/index.html && chmod 0755 /app/deploy/entrypoint.sh
USER 10001:10001
STOPSIGNAL SIGTERM
ENTRYPOINT ["/app/deploy/entrypoint.sh"]
CMD ["gunicorn", "-c", "/app/deploy/gunicorn.conf.py", "backbone.wsgi:application"]
