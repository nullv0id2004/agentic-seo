# Worker image for Azure App Service (Web App for Containers) or any container host.
FROM python:3.12-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PORT=8080
COPY pyproject.toml ./
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
COPY worker.py ./
RUN pip install --no-cache-dir ".[azure]"
EXPOSE 8080
CMD ["python", "worker.py"]
