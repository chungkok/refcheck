# Local or institutional deployment of RefCheck.
#   docker build -t refcheck .
#   docker run --rm -p 127.0.0.1:8501:8501 -e REFCHECK_DEPLOYMENT=local refcheck
# Binding to 127.0.0.1 keeps the app reachable from this computer only.
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \
    XDG_CACHE_HOME=/home/refcheck/.cache

RUN useradd --create-home --uid 1000 refcheck
WORKDIR /app
COPY requirements-lock.txt ./
RUN pip install -r requirements-lock.txt
COPY --chown=refcheck:refcheck refcheck ./refcheck
COPY --chown=refcheck:refcheck app.py refcheck_cli.py ./
COPY --chown=refcheck:refcheck .streamlit ./.streamlit

USER refcheck
EXPOSE 8501
HEALTHCHECK CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8501/_stcore/health')"
CMD ["streamlit", "run", "app.py", "--server.address=0.0.0.0", "--server.port=8501", "--server.headless=true"]
