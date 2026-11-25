FROM python:3.11-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY server.py .

ENV PORT=8080
ENV AZURE_CLIENT_ID=""
# No secrets in image. Runtime: workload identity → Key Vault / Cosmos AAD.

EXPOSE 8080
CMD ["python", "server.py"]
