# Worker image for Azure App Service (Web App for Containers).
# Includes the sshd on port 2222 that App Service's SSH console expects (root password "Docker!" is the
# Azure convention; the port is only reachable through the portal's Kudu tunnel, never from the internet).
FROM python:3.12-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PORT=8080
RUN apt-get update && apt-get install -y --no-install-recommends openssh-server \
    && echo "root:Docker!" | chpasswd \
    && mkdir -p /run/sshd \
    && rm -rf /var/lib/apt/lists/*
COPY deploy/sshd_config /etc/ssh/sshd_config
COPY requirements.txt pyproject.toml ./
RUN pip install --no-cache-dir -r requirements.txt
COPY analysts ./analysts
COPY collectors ./collectors
COPY config ./config
COPY contracts ./contracts
COPY db ./db
COPY executors ./executors
COPY gate ./gate
COPY llm ./llm
COPY orchestrator ./orchestrator
COPY rules ./rules
COPY scripts ./scripts
COPY worker.py deploy/entrypoint.sh ./
RUN chmod +x entrypoint.sh
EXPOSE 8080 2222
CMD ["./entrypoint.sh"]
