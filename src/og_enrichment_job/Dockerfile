FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# shared-hakemisto kopioidaan tähän deploy-ajurin toimesta
COPY shared/ /app/shared/
COPY main.py .

CMD ["python", "main.py"]
