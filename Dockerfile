FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Explicit copies keep private exports out of every image layer.
COPY pyproject.toml requirements.txt ./
COPY src/ ./src/
RUN pip install --no-cache-dir ".[dashboard]"

COPY app.py ./
COPY .streamlit/config.toml ./.streamlit/config.toml
COPY demo/ ./demo/
COPY config/demand.json config/forecast.json config/replenishment.json ./config/
COPY scripts/accept_case.py scripts/build_demo.py ./scripts/
COPY docs/CASE.md ./docs/CASE.md
COPY README.md ./README.md

RUN useradd --create-home --uid 10001 vector
USER vector

EXPOSE 8501
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8501/_stcore/health', timeout=3)"

CMD ["python", "-m", "streamlit", "run", "app.py", "--server.address=0.0.0.0", "--server.port=8501", "--server.headless=true"]
