"""Astra Data Factory agents plane (E5).

Today: the Spec Reader (S5.1.1). Each agent is a bounded worker; every one is
scored against its own gold set by astra_verification.agent_eval (S4.3.4).
"""

from astra_agents.spec_reader import AnthropicClient, ConnectionTestResult, DraftSpec, Extraction, LlmClient, Page, SpecReaderError, build_draft, run

__all__ = ["AnthropicClient", "ConnectionTestResult", "DraftSpec", "Extraction", "LlmClient", "Page", "SpecReaderError", "build_draft", "run"]
