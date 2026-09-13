"""Astra Data Factory agents plane (E5).

Today: the Spec Reader (S5.1.1), the Profiler (S5.2.1) and the Pattern
Matcher (S5.3.1). Each agent is a bounded worker; every one is scored
against its own gold set by astra_verification.agent_eval (S4.3.4).
"""

from astra_agents.pattern_matcher import Assignment, FamilyMatch, PatternMatcherError
from astra_agents.pattern_matcher import run as run_pattern_matcher
from astra_agents.profiler import FieldProfile, Profile, ProfilerError, RecordProfile
from astra_agents.profiler import run as run_profiler
from astra_agents.spec_reader import AnthropicClient, ConnectionTestResult, DraftSpec, Extraction, LlmClient, Page, SpecReaderError, build_draft, run

__all__ = [
    "AnthropicClient",
    "Assignment",
    "ConnectionTestResult",
    "DraftSpec",
    "Extraction",
    "FamilyMatch",
    "FieldProfile",
    "LlmClient",
    "Page",
    "PatternMatcherError",
    "Profile",
    "ProfilerError",
    "RecordProfile",
    "SpecReaderError",
    "build_draft",
    "run",
    "run_pattern_matcher",
    "run_profiler",
]
