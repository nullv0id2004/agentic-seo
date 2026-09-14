"""The only module that talks to a model. Every call is tool-less and returns strict JSON.

Analysts and the stage 2 verifier receive an LLM handle through their context. They cannot pass
tools, and nothing in this package accepts a tools argument.
"""
from llm.client import LLMClient, LLMResult, LLMValidationError, build_client
from llm.fake import FakeLLM

__all__ = ["LLMClient", "LLMResult", "LLMValidationError", "FakeLLM", "build_client"]
