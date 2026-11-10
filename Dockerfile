FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PORT=8080
WORKDIR /app

# PyTorch version CPU (image bien plus légère que la version CUDA)
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu
COPY requirements-api.txt .
RUN pip install --no-cache-dir -r requirements-api.txt \
    && python -m spacy download en_core_web_sm

COPY src/__init__.py src/__init__.py
COPY src/api src/api
COPY src/anonymization src/anonymization
COPY src/preprocessing src/preprocessing
COPY models/lstm.pt models/lstm_vocab.json models/

# Utilisateur non root
RUN useradd --create-home app && chown -R app /app
USER app

EXPOSE 8080
CMD exec uvicorn src.api.main:app --host 0.0.0.0 --port ${PORT} --workers 1
