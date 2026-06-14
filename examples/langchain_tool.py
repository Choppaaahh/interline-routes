"""
AGENT side — wrap "pay for a resource" as a LangChain tool.

Gives an LLM agent the ability to pay an Interline-gated endpoint on its own: the
agent decides it needs a paid resource, calls the `pay_for_resource` tool with a
URL, and Interline's buyer client handles the 402 -> pay -> retry loop under the
hood (with a spend-limit guard).

LangChain is OPTIONAL here. If `langchain_core` is installed, this exposes a real
`@tool`. If not, it falls back to a plain callable with the same signature so the
file still imports + runs as a self-contained illustration.

Repo: https://github.com/Choppaaahh/interline-routes

    pip install langchain-core         # optional, for the real @tool
    python examples/langchain_tool.py  # runs the underlying payment fn directly
"""
from __future__ import annotations

from eth_account import Account

from router import buyer

# Ephemeral key for the illustration. A real agent loads its funded key from env.
_AGENT_KEY = Account.create().key.hex()

# USDC has 6 decimals -> 0.05 USDC budget ceiling for any single tool call.
_MAX_PRICE_ATOMIC = int(0.05 * 10 ** 6)


def _pay_for_resource(url: str) -> str:
    """Pay an Interline-gated endpoint and return the work product (or an error).

    The agent passes a URL; Interline auto-pays within the spend limit and retries.
    """
    res = buyer.pay_and_get(url, private_key=_AGENT_KEY, max_price_atomic=_MAX_PRICE_ATOMIC)
    if res["status"] == 200:
        return f"paid OK; result={res['body']}; receipt={res['receipt']}"
    return f"could not get resource (status {res['status']}): {res['body']}"


# --- expose as a LangChain tool if available, else a plain callable --------------
try:
    from langchain_core.tools import tool  # lazy / optional import

    @tool
    def pay_for_resource(url: str) -> str:
        """Pay for and fetch a paywalled resource at `url`. Returns the work product."""
        return _pay_for_resource(url)

except ImportError:
    # langchain_core not installed — illustrative fallback with the same signature.
    # (Install `langchain-core` to get the real BaseTool an agent can bind.)
    def pay_for_resource(url: str) -> str:
        """Pay for and fetch a paywalled resource at `url`. Returns the work product."""
        return _pay_for_resource(url)


if __name__ == "__main__":
    # Demonstrate the underlying payment function directly (no agent loop needed).
    print(_pay_for_resource("http://127.0.0.1:8402/report"))
