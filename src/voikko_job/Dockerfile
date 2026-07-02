FROM python:3.11-slim

WORKDIR /app

# Asennetaan libvoikko ja suomen sanakirja (voikko-fi)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libvoikko1 \
    voikko-fi \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py .

CMD ["python", "main.py"]
