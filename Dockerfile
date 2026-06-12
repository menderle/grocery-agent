FROM mcr.microsoft.com/playwright/python:v1.57.0-noble

WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir . "texas-grocery-mcp[browser]"

# State mounts (see docker-compose.yml):
#   /data/agent  -> GROCERY_AGENT_HOME (config, data, audit log)
#   /data/auth   -> shared HEB session (auth.json)
ENV GROCERY_AGENT_HOME=/data/agent \
    AUTH_STATE_PATH=/data/auth/auth.json \
    MCP_HTTP_PORT=8787

EXPOSE 8787
CMD ["grocery-gateway", "--http"]
