"""Model router (spec section 6). Deterministic rules decide the path — LLMs never
override hard filters/eligibility (spec 2.1). Structured output is validated with
Pydantic before it's trusted (spec 51: 'never invent missing data').

Model note (Aug 2026): don't hard-pin a Gemini version string — Google retires
dated model IDs on a rolling schedule (2.0 Flash line is already gone). Use the
rolling alias 'gemini-flash-latest' (config default) so this doesn't silently
404 when a version is sunset.
"""

import json
import time
from typing import Protocol

import httpx
from pydantic import BaseModel, ValidationError

from backend.core.config import get_settings
from backend.core.monitoring import agent_monitor

settings = get_settings()


class MatchExplanation(BaseModel):
    # Skills the model quoted verbatim from the JD. Carried through so a
    # reviewer can audit an answer without re-reading the posting.
    evidence: str = ""
    matched_skills: list[str] = []
    missing_skills: list[str] = []
    explanation: str = "unknown"
    confidence: float = 0.0


# ---------------------------------------------------------------------------
# Prompting
# ---------------------------------------------------------------------------

# Bound the JD so one pathological posting cannot blow up latency/cost.
_MAX_JD_CHARS = 1200
_MAX_SKILLS = 40

_SYSTEM_CONTRACT = (
    "You are a resume-evidence extractor. Reply with JSON only, no prose, no code fences. "
    "Every claim must be traceable to a literal span in the supplied JD. "
    "If the JD does not state a skill, it is absent - never infer from the job title alone, "
    "never assume seniority, and never invent experience."
)


def _toon_block(job_title: str, description: str, skills: list[str]) -> str:
    """Serialize the request in TOON (Token-Oriented Object Notation).

    ``key::value`` for scalars, ``key[n]`` with a pipe-delimited run for lists.
    For n items this costs roughly one delimiter per item instead of JSON's
    per-item quoting and braces - a real saving when this block ships with
    every job in a ranked batch.
    """
    trimmed = [s.strip() for s in skills[:_MAX_SKILLS] if s and s.strip()]
    # Collapse newlines so the JD cannot forge a fake TOON header.
    desc = " ".join(description[:_MAX_JD_CHARS].split())
    lines = [
        "## REQUEST",
        f"job.title:: {job_title.strip()}",
        f"job.description:: {desc}",
        f"candidate.skills[{len(trimmed)}]:: {'|'.join(trimmed)}",
    ]
    return "\n".join(lines)


def _build_match_prompt(job_title: str, description: str, candidate_skills: list[str]) -> str:
    """CRAFT-structured prompt: Context, Role, Action, Format, Tone.

    CRAFT over a flat instruction dump buys two things that matter here: the
    Role/Context pair suppresses the one failure that matters (hallucinated
    skills, which spec 2.3 forbids), and the explicit Format block with a worked
    example pins the JSON shape for small local models that otherwise drift.
    """
    return f"""{_SYSTEM_CONTRACT}

# ROLE
You assess candidate-to-job fit from resume evidence only.

# CONTEXT
{_toon_block(job_title, description, candidate_skills)}

# ACTION
Work in this order, briefly, before answering:
1. Scan `job.description` for skills. Record only skills you can point at.
2. Split those into `matched` (also in candidate.skills) and `missing`.
3. Re-check every entry against its source span. Drop anything you cannot source.
4. Score confidence from evidence volume, not from how well the title matches.

# FORMAT
Emit exactly one JSON object, no surrounding text:
{{"evidence":"<skills you literally found in the JD, pipe-separated>",
  "matched_skills":["..."],"missing_skills":["..."],
  "explanation":"<1-2 sentences>","confidence":<0.0-1.0>}}

Rules:
- matched_skills MUST be a subset of candidate.skills.
- missing_skills MUST come from job.description, never from general knowledge.
- confidence < 0.5 when the JD is thin or ambiguous.
- Empty list is the correct answer when the JD names no skills.

Worked example:
JD mentions "Kubernetes" and "Go"; candidate has Go but not Kubernetes.
-> evidence: "go|kubernetes", matched_skills: ["Go"],
   missing_skills: ["Kubernetes"], confidence: 0.8

# TONE
Clinical and terse. No hedging adjectives, no encouragement, no career advice."""


class LLMProvider(Protocol):
    async def structured(self, prompt: str, schema: type[BaseModel]) -> BaseModel: ...


class LlamaCppProvider:
    """Local, private, cheap — first choice for high-volume classification (spec 6.2)."""

    def __init__(self, base_url: str, timeout: float = 5.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    async def structured(self, prompt: str, schema: type[BaseModel]) -> BaseModel:
        payload = {
            "model": "local",
            "messages": [
                {"role": "system", "content": "Respond ONLY with JSON matching the required schema. No prose."},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0,
        }
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(f"{self.base_url}/v1/chat/completions", json=payload)
                resp.raise_for_status()
                raw = resp.json()["choices"][0]["message"]["content"]
            return schema.model_validate(json.loads(raw))
        except (httpx.ConnectError, httpx.TimeoutException):
            # Fail fast — don't hang for 30s if llama.cpp isn't running
            raise


class GeminiProvider:
    """Online escalation for harder ambiguity/reasoning (spec 6.2). Cheapest capable model."""

    def __init__(self, api_key: str, model: str):
        self.api_key = api_key
        self.model = model

    async def structured(self, prompt: str, schema: type[BaseModel]) -> BaseModel:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent"
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "responseMimeType": "application/json",
                "responseSchema": schema.model_json_schema(),
            },
        }
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(url, params={"key": self.api_key}, json=payload)
            resp.raise_for_status()
            raw = resp.json()["candidates"][0]["content"]["parts"][0]["text"]
        return schema.model_validate(json.loads(raw))


class NullProvider:
    """No LLM configured. Returns unknown/empty rather than inventing anything (spec 2.3)."""

    async def structured(self, prompt: str, schema: type[BaseModel]) -> BaseModel:
        return schema()


class OpenRouterProvider:
    """OpenRouter - unified API for 100+ models (Claude, GPT, Llama, etc.) via LangChain."""

    def __init__(self, api_key: str, model: str):
        self.api_key = api_key
        self.model = model

    async def structured(self, prompt: str, schema: type[BaseModel]) -> BaseModel:
        try:
            from langchain_openrouter import ChatOpenRouter
        except ImportError:
            raise RuntimeError("langchain-openrouter not installed. Run: pip install langchain-openrouter")

        llm = ChatOpenRouter(
            api_key=self.api_key,
            model=self.model,
            temperature=0,
        )
        structured_llm = llm.with_structured_output(schema)
        result = await structured_llm.ainvoke(prompt)
        return result


class NVIDIAProvider:
    """NVIDIA NIM - optimized inference microservices for LLMs via LangChain."""

    def __init__(self, api_key: str, model: str):
        self.api_key = api_key
        self.model = model

    async def structured(self, prompt: str, schema: type[BaseModel]) -> BaseModel:
        try:
            from langchain_nvidia_ai_endpoints import ChatNVIDIA
        except ImportError:
            raise RuntimeError("langchain-nvidia-ai-endpoints not installed. Run: pip install langchain-nvidia-ai-endpoints")

        llm = ChatNVIDIA(
            api_key=self.api_key,
            model=self.model,
            temperature=0,
        )
        structured_llm = llm.with_structured_output(schema)
        result = await structured_llm.ainvoke(prompt)
        return result


class ModelRouter:
    """Routing policy per spec 6.2: deterministic rules first, then llama.cpp, then Gemini escalation.

    Extended with OpenRouter and NVIDIA providers. When provider_mode is explicitly set
    (not 'auto'), that provider is used directly without fallback.
    """

    def __init__(
        self,
        llama: LLMProvider | None = None,
        gemini: LLMProvider | None = None,
        openrouter: LLMProvider | None = None,
        nvidia: LLMProvider | None = None,
        provider_mode: str = "auto",
    ):
        self._llama = llama
        self._gemini = gemini
        self._openrouter = openrouter
        self._nvidia = nvidia
        self._provider_mode = provider_mode

    def _get_provider(self, name: str) -> LLMProvider | None:
        providers = {
            "llama": self._llama,
            "gemini": self._gemini,
            "openrouter": self._openrouter,
            "nvidia": self._nvidia,
        }
        return providers.get(name)

    async def _call(self, name: str, provider: "LLMProvider", prompt: str) -> MatchExplanation:
        """Call one LLM provider agent, timed + reported to the agent monitor."""
        start = time.monotonic()
        try:
            result = await provider.structured(prompt, MatchExplanation)
            agent_monitor.record(f"llm:{name}", ok=True, latency_ms=(time.monotonic() - start) * 1000)
            return result
        except Exception as exc:
            agent_monitor.record(f"llm:{name}", ok=False, latency_ms=(time.monotonic() - start) * 1000, error=str(exc)[:300])
            raise

    async def explain_match(self, job_title: str, job_description: str, candidate_skills: list[str]) -> MatchExplanation:
        # Built once and reused across every provider attempt, so routing and
        # fallback cannot change what the model is being asked.
        prompt = _build_match_prompt(job_title, job_description, candidate_skills)

        # If explicit provider mode (not auto), use only that provider
        if self._provider_mode != "auto":
            provider = self._get_provider(self._provider_mode)
            if provider:
                try:
                    return await self._call(self._provider_mode, provider, prompt)
                except (httpx.HTTPError, ValidationError, KeyError, json.JSONDecodeError, RuntimeError) as e:
                    return MatchExplanation(explanation=f"LLM error: {type(e).__name__}")
            return MatchExplanation(explanation=f"unknown — provider '{self._provider_mode}' not configured")

        # Auto mode: llama.cpp → NVIDIA → OpenRouter → Gemini (fallback chain)
        # 1. Try local llama.cpp first (private, fast, free)
        if self._llama:
            try:
                result = await self._call("llama", self._llama, prompt)
                if result.confidence >= 0.6:
                    return result
            except (httpx.HTTPError, ValidationError, KeyError, json.JSONDecodeError):
                pass  # fall through to next provider

        # 2. Try NVIDIA NIM (optimized inference, free tier available)
        if self._nvidia:
            try:
                result = await self._call("nvidia", self._nvidia, prompt)
                if result.confidence >= 0.6:
                    return result
            except Exception:
                pass  # fall through

        # 3. Try OpenRouter (100+ models, free tier available)
        if self._openrouter:
            try:
                result = await self._call("openrouter", self._openrouter, prompt)
                if result.confidence >= 0.6:
                    return result
            except Exception:
                pass  # fall through

        # 4. Try Google Gemini (generous free tier)
        if self._gemini:
            try:
                return await self._call("gemini", self._gemini, prompt)
            except (httpx.HTTPError, ValidationError, KeyError, json.JSONDecodeError):
                pass

        return MatchExplanation(explanation="unknown — no LLM provider available or all providers failed")


def get_model_router() -> ModelRouter:
    # Short timeout for llama so we fail fast if server isn't running
    llama = LlamaCppProvider(settings.llama_cpp_base_url, timeout=5.0) if settings.llama_cpp_base_url else None
    gemini = GeminiProvider(settings.gemini_api_key, settings.gemini_model) if settings.gemini_api_key else None
    openrouter = OpenRouterProvider(settings.openrouter_api_key, settings.openrouter_model) if settings.openrouter_api_key else None
    nvidia = NVIDIAProvider(settings.nvidia_api_key, settings.nvidia_model) if settings.nvidia_api_key else None

    return ModelRouter(
        llama=llama,
        gemini=gemini,
        openrouter=openrouter,
        nvidia=nvidia,
        provider_mode=settings.llm_provider_mode,
    )


def get_model_router_for_request(llm_provider: str | None = None, model_name: str | None = None) -> ModelRouter:
    """Create a ModelRouter with per-request provider/model override."""
    provider_mode = llm_provider or settings.llm_provider_mode

    # Use config defaults unless overridden
    openrouter_key = settings.openrouter_api_key
    openrouter_model = model_name if (provider_mode == "openrouter" or model_name) else settings.openrouter_model
    nvidia_key = settings.nvidia_api_key
    nvidia_model = model_name if (provider_mode == "nvidia" or model_name) else settings.nvidia_model

    # Base providers from config
    llama = LlamaCppProvider(settings.llama_cpp_base_url) if settings.llama_cpp_base_url else None
    gemini = GeminiProvider(settings.gemini_api_key, settings.gemini_model) if settings.gemini_api_key else None
    openrouter = OpenRouterProvider(openrouter_key, openrouter_model) if openrouter_key else None
    nvidia = NVIDIAProvider(nvidia_key, nvidia_model) if nvidia_key else None

    return ModelRouter(
        llama=llama,
        gemini=gemini,
        openrouter=openrouter,
        nvidia=nvidia,
        provider_mode=provider_mode,
    )
