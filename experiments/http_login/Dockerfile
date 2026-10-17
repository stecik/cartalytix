FROM cartalytix-api:latest
USER root
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/chromium HOME=/tmp
RUN uv pip install --python /app/.venv/bin/python playwright==1.64.0 && \
    /app/.venv/bin/python -m playwright install --with-deps --no-shell chromium && \
    chmod -R a+rX /opt/chromium
USER app
