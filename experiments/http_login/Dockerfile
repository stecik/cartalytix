FROM cartalytix-api:latest
USER root
ENV HOME=/tmp
RUN uv pip install --python /app/.venv/bin/python playwright==1.64.0 && \
    /app/.venv/bin/python -m playwright install --with-deps chrome
USER app
