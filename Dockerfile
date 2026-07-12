# Interline — neutral cross-rail agent-payment MCP server (stdio).
# Container image for Glama.ai listing + general use.
#
# Ships NO keys: payment is NON-CUSTODIAL and per-call, using the CALLER's own
# wallet key from the APV0_BUYER_PRIVATE_KEY env var — this image never holds one.
# The server starts KEYLESS and exposes 3 tools (discover_payment_rails,
# pay_for_resource, payment_history); Glama's MCP introspection enumerates them.
FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends gcc \
    && rm -rf /var/lib/apt/lists/*

COPY . /app
RUN pip install --no-cache-dir .

# stdio MCP server entrypoint (no wallet key needed to start / list tools)
ENTRYPOINT ["python", "-m", "mcp_router.server"]
