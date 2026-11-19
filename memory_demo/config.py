"""Build the LLM, the data connection and the memory store from environment variables.

BACKEND=sqlite      local files: SQLITE_DATA (BGL table), SQLITE_MEMORY (memory tables)
BACKEND=databricks  DATABRICKS_WAREHOUSE_ID, DEMO_CATALOG, DEMO_SCHEMA; auth from the Databricks SDK config
LLM_PROVIDER=azure       AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_API_KEY, OPENAI_API_VERSION, AZURE_OPENAI_DEPLOYMENT
LLM_PROVIDER=databricks  DATABRICKS_LLM_ENDPOINT (model serving endpoint name)

Locally these can live in memory-demo/.env (git-ignored); variables already set in the shell win.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv
from langchain_core.language_models import BaseChatModel

from .bgl import COLUMNS, TABLE, TABLE_COMMENT
from .org import Directory
from .service import MemoryService
from .store import CONNECT_LIMITS, DatabricksExecutor, MemoryStore, SqliteExecutor

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")
DEMO_TODAY = os.getenv("DEMO_TODAY", "2005-12-01")  # step 1; "a month later" is the month after


def env(name: str, default: str | None = None) -> str:
    """A setting; an empty value counts as not set, so a placeholder left blank fails with its name."""
    value = os.getenv(name) or default
    if value is None:
        raise RuntimeError(f"environment variable {name} is not set")
    return value


@lru_cache
def llm() -> BaseChatModel:
    provider = env("LLM_PROVIDER", "azure")
    # Reasoning models (GPT-5 family, Claude Sonnet 5) reject a temperature, so it is sent only when set.
    extra = {"temperature": float(t)} if (t := os.getenv("LLM_TEMPERATURE")) else {}
    if effort := os.getenv("LLM_REASONING_EFFORT"):  # low / medium / high, reasoning models only
        extra["reasoning_effort"] = effort
    # Without a limit the OpenAI client waits 600 s per call and retries twice, so one dropped request froze a turn.
    extra["timeout"] = float(os.getenv("LLM_TIMEOUT", "90"))
    extra["max_retries"] = 1
    if provider == "azure":
        from langchain_openai import AzureChatOpenAI

        # AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_API_KEY and OPENAI_API_VERSION are read from the environment.
        return AzureChatOpenAI(azure_deployment=env("AZURE_OPENAI_DEPLOYMENT"), max_tokens=4000, **extra)
    if provider == "databricks":
        from databricks_langchain import ChatDatabricks

        return ChatDatabricks(endpoint=env("DATABRICKS_LLM_ENDPOINT"), max_tokens=4000, **extra)
    raise RuntimeError(f"unknown LLM_PROVIDER {provider!r}")


def table_info() -> str:
    """Schema as the agent sees it, with our descriptions as comments (works the same on both backends)."""
    cols = ",\n".join(f"  {n} {t} -- {c}" for n, t, c in COLUMNS)
    return f"CREATE TABLE {TABLE} (\n{cols}\n)\n-- {TABLE_COMMENT}"


@lru_cache
def data_db():
    """The demo's BGL table as a LangChain SQLDatabase (the memory app never needs it)."""
    from langchain_community.utilities import SQLDatabase

    backend = env("BACKEND", "sqlite")
    if backend == "sqlite":
        path = env("SQLITE_DATA", str(ROOT / "data" / "bgl.db"))
        return SQLDatabase.from_uri(f"sqlite:///{path}", include_tables=[TABLE], custom_table_info={TABLE: table_info()})
    if backend == "databricks":
        from databricks import sql
        from databricks.sdk.core import Config
        from sqlalchemy import create_engine

        cfg = Config()

        def connect():  # a new connection per pool slot; OAuth tokens are refreshed by the SDK
            return sql.connect(
                server_hostname=cfg.host.removeprefix("https://"),
                http_path=f"/sql/1.0/warehouses/{env('DATABRICKS_WAREHOUSE_ID')}",
                credentials_provider=lambda: cfg.authenticate,
                catalog=env("DEMO_CATALOG"),
                schema=env("DEMO_SCHEMA"),
                **CONNECT_LIMITS,
            )

        # The dialect reads catalog and schema from the URL (for SHOW TABLES etc.); `creator` opens the connections.
        # Pre-ping and recycle drop connections whose warehouse session expired while the app sat idle.
        engine = create_engine(f"databricks://?catalog={env('DEMO_CATALOG')}&schema={env('DEMO_SCHEMA')}",
                               creator=connect, pool_pre_ping=True, pool_recycle=600)
        return SQLDatabase(engine, schema=env("DEMO_SCHEMA"), include_tables=[TABLE],
                           custom_table_info={TABLE: table_info()})
    raise RuntimeError(f"unknown BACKEND {backend!r}")


@lru_cache
def memory_store() -> MemoryStore:
    if env("BACKEND", "sqlite") == "databricks":
        # The memory tables may live apart from the demo's data (MEMORY_CATALOG / MEMORY_SCHEMA): a pilot's memory
        # must not share tables with a demo that can be reset.
        ex = DatabricksExecutor(env("DATABRICKS_WAREHOUSE_ID"), env("MEMORY_CATALOG", os.getenv("DEMO_CATALOG")),
                                env("MEMORY_SCHEMA", os.getenv("DEMO_SCHEMA")),
                                size=int(env("MEMORY_DB_CONNECTIONS", "4")))
    else:
        ex = SqliteExecutor(env("SQLITE_MEMORY", str(ROOT / "data" / "memory.db")))
    return MemoryStore(ex)


@lru_cache
def org() -> Directory:
    """The org directory: a table next to the memory tables, re-read every ORG_REFRESH_S seconds (default 300), so
    people and teams added by the HR / Entra ID sync appear on their own. In the demo it is filled from ORG_CSV when
    empty and a reset puts that seed back; ORG_SEED=0 turns both off (a service on a real directory only reads it)."""
    seed = None if env("ORG_SEED", "1") == "0" else env("ORG_CSV", str(ROOT / "seed" / "org.csv"))
    return Directory(memory_store().ex, seed, ttl=float(env("ORG_REFRESH_S", "300")))


def mem0_client(embedder, chat_model, dims: int, path: Path):
    """Mem0 with LangChain models (so it reuses our Azure / Databricks setup) and a local Qdrant at `path`."""
    os.environ.setdefault("MEM0_TELEMETRY", "False")  # Mem0 sends usage telemetry unless told not to
    from mem0 import Memory

    return Memory.from_config({
        "embedder": {"provider": "langchain", "config": {"model": embedder}},
        "llm": {"provider": "langchain", "config": {"model": chat_model}},  # unused: we write with infer=False
        "vector_store": {"provider": "qdrant", "config": {"collection_name": "terms", "path": str(path),
                                                          "embedding_model_dims": dims, "on_disk": True}},
        "history_db_path": str(path / "history.db"),
    })


def embeddings():
    """(embedder, dimensions). Databricks embeddings when an endpoint is configured (or the LLM runs on Databricks),
    else Azure OpenAI."""
    if os.getenv("DATABRICKS_EMBEDDING_ENDPOINT") or env("LLM_PROVIDER", "azure") == "databricks":
        from databricks_langchain import DatabricksEmbeddings

        return (DatabricksEmbeddings(endpoint=env("DATABRICKS_EMBEDDING_ENDPOINT", "databricks-gte-large-en")),
                int(env("EMBEDDING_DIMS", "1024")))
    from langchain_openai import AzureOpenAIEmbeddings

    return AzureOpenAIEmbeddings(azure_deployment=env("AZURE_OPENAI_EMBEDDING_DEPLOYMENT")), int(env("EMBEDDING_DIMS", "1536"))


@lru_cache
def term_index():
    """Mem0 index of term names for the "Find terms with Mem0" switch. Built on first use, in a temp folder that is
    wiped at start-up: it is rebuilt from the tables, which stay the source of truth."""
    import shutil
    import tempfile

    from .semantic import TermIndex

    path = Path(os.getenv("MEM0_INDEX_PATH", str(Path(tempfile.gettempdir()) / "shs_memory_term_index")))
    shutil.rmtree(path, ignore_errors=True)
    path.mkdir(parents=True, exist_ok=True)
    embedder, dims = embeddings()
    return TermIndex(mem0_client(embedder, llm(), dims, path), embedder)


def memory_service(with_llm: bool = True) -> MemoryService:
    return MemoryService(memory_store(), org(), llm() if with_llm else None,
                         joke_filter=env("JOKE_FILTER", "1") == "1",
                         term_index=term_index,  # built only when the Mem0 switch is first used
                         extraction=env("MEMORY_EXTRACTION", "mem0"))
