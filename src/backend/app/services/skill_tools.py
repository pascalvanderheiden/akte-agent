"""MCP skill implementations as Copilot SDK @define_tool functions.

Each function here corresponds to one skill in skills.yaml.
The SDK calls these when the agent decides to invoke a skill.
"""

import logging
import os
import subprocess
import time
from contextvars import ContextVar

import httpx
from azure.identity.aio import DefaultAzureCredential
from azure.search.documents.aio import SearchClient
from copilot.tools import define_tool
from opentelemetry import trace
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)
tracer = trace.get_tracer(__name__)

# ─── Kratos context vars — set by copilot_agent.run() so tool spans carry the ─
# ─── same kratos attributes as the parent invoke_agent span.               ─────

_ctx_use_case: ContextVar[str] = ContextVar("kratos_use_case", default="")
_ctx_conversation_id: ContextVar[str] = ContextVar("kratos_conversation_id", default="")
_ctx_eval_run_id: ContextVar[str] = ContextVar("kratos_eval_run_id", default="")


def _set_kratos_attrs(span: trace.Span) -> None:
    """Stamp kratos.* attributes on *span* from the active context vars."""
    use_case = _ctx_use_case.get()
    if use_case:
        span.set_attribute("kratos.use_case", use_case)
    conv_id = _ctx_conversation_id.get()
    if conv_id:
        span.set_attribute("kratos.conversation_id", conv_id)
    eval_run_id = _ctx_eval_run_id.get()
    if eval_run_id:
        span.set_attribute("kratos.eval_run_id", eval_run_id)


# ─── Shared singletons — avoid recreating expensive objects on every tool call ─

_credential: DefaultAzureCredential | None = None
_http_client: httpx.AsyncClient | None = None


def _get_credential() -> DefaultAzureCredential:
    global _credential
    if _credential is None:
        _credential = DefaultAzureCredential()
    return _credential


def _get_http_client() -> httpx.AsyncClient:
    global _http_client
    if _http_client is None or _http_client.is_closed:
        _http_client = httpx.AsyncClient(timeout=30.0)
    return _http_client


# ─── Web Search ──────────────────────────────────────────────────────────────


class WebSearchParams(BaseModel):
    query: str = Field(default="", description="The search query to look up on the internet")


@define_tool(description="Real-time internet search for current information and market data")
async def web_search(params: WebSearchParams) -> dict:
    """Search the internet using Foundry web_search_preview tool."""
    with tracer.start_as_current_span("skill.web_search") as span:
        _set_kratos_attrs(span)
        t0 = time.monotonic()
        query = params.query
        logger.info("web_search called: query=%r", query)

        if not query:
            return {"error": "Missing required search query for web_search"}

        foundry_endpoint = os.environ.get("FOUNDRY_ENDPOINT", "")
        model_deployment = os.environ.get("FOUNDRY_MODEL_DEPLOYMENT", "")
        if not foundry_endpoint or not model_deployment:
            return {"error": "FOUNDRY_ENDPOINT or FOUNDRY_MODEL_DEPLOYMENT not configured"}

        # Build the Responses API URL from the Foundry endpoint
        # FOUNDRY_ENDPOINT is like https://<account>.cognitiveservices.azure.com/
        # We need https://<account>.services.ai.azure.com/openai/responses
        account_name = foundry_endpoint.rstrip("/").split("//")[1].split(".")[0]
        responses_url = f"https://{account_name}.services.ai.azure.com/openai/responses?api-version=2025-03-01-preview"

        try:
            credential = _get_credential()
            token = await credential.get_token("https://cognitiveservices.azure.com/.default")
        except Exception as e:
            logger.error("Failed to get auth token for web search: %s", e)
            return {"error": f"Authentication failed: {e}"}

        t1 = time.monotonic()
        try:
            client = _get_http_client()
            response = await client.post(
                responses_url,
                headers={
                    "Authorization": f"Bearer {token.token}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model_deployment,
                    "input": f"Search the web for: {query}. Return only factual search results with sources.",
                    "tools": [{"type": "web_search_preview"}],
                    "tool_choice": {"type": "web_search_preview"},
                },
            )
            response.raise_for_status()
            data = response.json()
        except Exception as e:
            logger.error("Foundry web search call failed: %s", e)
            return {"error": f"Web search call failed: {e}"}

        # Extract text and citations from the Responses API output
        text = ""
        citations = []
        for item in data.get("output", []):
            if item.get("type") == "message":
                for content in item.get("content", []):
                    if content.get("type") == "output_text":
                        text = content.get("text", "")
                        for ann in content.get("annotations", []):
                            if ann.get("type") == "url_citation":
                                citations.append(
                                    {
                                        "title": ann.get("title", ""),
                                        "url": ann.get("url", ""),
                                    }
                                )

        logger.info(
            "web_search returned %d citations for query=%s auth_ms=%.0f search_ms=%.0f",
            len(citations),
            query,
            (t1 - t0) * 1000,
            (time.monotonic() - t1) * 1000,
        )
        return {"text": text, "citations": citations, "query": query}


# ─── RAG Search ──────────────────────────────────────────────────────────────


class RAGSearchParams(BaseModel):
    query: str = Field(description="The query to search in the knowledge base")
    index_name: str = Field(
        default="",
        description="The configured Azure AI Search index name to query.",
    )
    top: int = Field(default=5, description="Number of results to return")


@define_tool(description="Azure AI Search knowledge base for grounded answers from internal documents")
async def rag_search(params: RAGSearchParams) -> dict:
    """Search the Azure AI Search knowledge base."""
    with tracer.start_as_current_span("skill.rag_search") as span:
        _set_kratos_attrs(span)
        ai_search_endpoint = os.environ.get("AZURE_AI_SEARCH_ENDPOINT", "")
        if not ai_search_endpoint:
            return {"error": "AZURE_AI_SEARCH_ENDPOINT not configured"}

        index_name = params.index_name.strip() if params.index_name else os.environ.get("AI_SEARCH_INDEX", "")
        if not index_name:
            return {"error": "Provide index_name or configure AI_SEARCH_INDEX"}
        credential = _get_credential()
        async with SearchClient(
            endpoint=ai_search_endpoint,
            index_name=index_name,
            credential=credential,
        ) as client:
            results = await client.search(
                search_text=params.query,
                top=params.top,
                query_type="semantic",
                semantic_configuration_name="default",
            )
            docs = []
            async for result in results:
                docs.append(
                    {
                        "content": str(result.get("content", ""))[:500],
                        "title": result.get("title", ""),
                        "source": result.get("source", ""),
                        "page": result.get("page_number", ""),
                        "score": result.get("@search.score", 0),
                    }
                )
            return {"results": docs, "query": params.query}


# ─── Code Interpreter ─────────────────────────────────────────────────────────


class CodeInterpreterParams(BaseModel):
    code: str = Field(description="Python code to execute in a sandboxed environment")


@define_tool(description="Sandboxed Python execution for computation, data analysis, and code generation")
async def code_interpreter(params: CodeInterpreterParams) -> dict:
    """Execute Python code in a sandboxed subprocess."""
    with tracer.start_as_current_span("skill.code_interpreter") as span:
        _set_kratos_attrs(span)
        try:
            result = subprocess.run(
                ["python", "-c", params.code],
                capture_output=True,
                text=True,
                timeout=30,
                cwd="/tmp",  # noqa: S108
                check=False,
            )
            return {
                "stdout": result.stdout[:4000],
                "stderr": result.stderr[:1000],
                "returncode": result.returncode,
            }
        except subprocess.TimeoutExpired:
            return {"error": "Code execution timed out (30s limit)"}
        except Exception as e:
            return {"error": str(e)}


# ─── Foundry Agent ────────────────────────────────────────────────────────────


class FoundryAgentParams(BaseModel):
    task: str = Field(description="The task to delegate to the specialized Foundry sub-agent")
    agent_name: str = Field(default="default", description="Name of the Foundry sub-agent to invoke")


@define_tool(description="Delegate complex or specialized tasks to Microsoft Foundry sub-agents")
async def foundry_agent(params: FoundryAgentParams) -> dict:
    """Delegate a task to a Microsoft Foundry specialized sub-agent."""
    with tracer.start_as_current_span("skill.foundry_agent") as span:
        _set_kratos_attrs(span)
        foundry_endpoint = os.environ.get("FOUNDRY_ENDPOINT", "")
        if not foundry_endpoint:
            return {"error": "FOUNDRY_ENDPOINT not configured"}

        credential = _get_credential()
        token = await credential.get_token("https://cognitiveservices.azure.com/.default")

        client = _get_http_client()
        response = await client.post(
            f"{foundry_endpoint}/agents/{params.agent_name}/run",
            headers={"Authorization": f"Bearer {token.token}"},
            json={"task": params.task},
        )
        return response.json()


# ─── Tool registry ────────────────────────────────────────────────────────────

# All tools to register with every SDK session
ALL_TOOLS = [web_search, rag_search, code_interpreter, foundry_agent]

# Map from tool function name → tool object (used by CopilotAgent to filter by enabled skills)
TOOL_MAP = {tool.name: tool for tool in ALL_TOOLS}
