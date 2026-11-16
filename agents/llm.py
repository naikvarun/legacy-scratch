"""Shared LLM-call plumbing for every agent.

``call_agent`` is the single path through which agents talk to an LLM. It loads a
versioned prompt template, stamps a fresh ``prompt_id``, runs prompt | model | JSON parser
as a LangChain chain (so LangFuse traces it through the callbacks in ``config``), and
writes an ``llm_call`` audit entry with the hash of the exact rendered prompt.

LLM_MODE=stub (the default for now) swaps in FakeListChatModel with canned responses, so
the graph runs end-to-end with no API key and the traces still have the real shape.
"""
from __future__ import annotations

import hashlib
import json
import os
import uuid
from typing import Any

from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.output_parsers import JsonOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import RunnableConfig

from audit.logger import AuditLog
from observability.tracing import trace_id_for
from workflow.config import ROOT

SYSTEM = "You are a careful data migration assistant. Respond with JSON only."


def load_prompt(name: str, version: str) -> tuple[ChatPromptTemplate, str]:
    text = (ROOT / "prompts" / f"{name}.{version}.md").read_text(encoding="utf-8")
    return ChatPromptTemplate.from_messages([("system", SYSTEM), ("human", text)]), f"{name}.{version}"


def chat_model(stub_response: Any):
    mode = os.getenv("LLM_MODE", "stub")
    if mode == "stub":
        return FakeListChatModel(responses=[json.dumps(stub_response)])
    raise NotImplementedError(
        f"LLM_MODE={mode!r} is not wired yet; real models arrive with the ai_mapper milestone. "
        "Use LLM_MODE=stub.")


def call_agent(*, run_id: str, agent: str, prompt: str, version: str, variables: dict[str, Any],
               stub_response: Any, config: RunnableConfig) -> tuple[Any, str, str]:
    """Run one structured LLM call. Returns (parsed_json, prompt_id, prompt_version)."""
    template, prompt_version = load_prompt(prompt, version)
    prompt_id = f"prompt-{uuid.uuid4()}"
    rendered = template.format(**variables)
    chain = (template | chat_model(stub_response) | JsonOutputParser()).with_config(
        run_name=agent,
        metadata={"prompt_id": prompt_id, "prompt_version": prompt_version, "run_id": run_id},
        tags=[agent],
    )
    output = chain.invoke(variables, config=config)
    AuditLog().append(
        run_id, "llm_call", f"agent:{agent}",
        {"prompt_version": prompt_version,
         "prompt_sha256": hashlib.sha256(rendered.encode()).hexdigest(),
         "llm_mode": os.getenv("LLM_MODE", "stub"),
         "output": output},
        prompt_id=prompt_id, trace_id=trace_id_for(run_id),
    )
    return output, prompt_id, prompt_version
