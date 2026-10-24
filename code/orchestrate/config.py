"""Single place for every knob. Everything comes from env vars (.env supported)."""
from __future__ import annotations
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")
load_dotenv()


@dataclass(frozen=True)
class Settings:
    provider: str = os.getenv("LLM_PROVIDER", "groq")
    model: str = os.getenv("LLM_MODEL", "llama-3.3-70b-versatile")
    vision_provider: str = os.getenv("VISION_PROVIDER", "") or os.getenv("LLM_PROVIDER", "groq")
    vision_model: str = os.getenv("VISION_MODEL", "") or os.getenv("LLM_MODEL", "llama-3.3-70b-versatile")
    temperature: float = float(os.getenv("LLM_TEMPERATURE", "0"))
    seed: int = int(os.getenv("LLM_SEED", "42"))
    rpm: int = int(os.getenv("LLM_RPM", "25"))
    # tokens-per-minute budget (0 = off). Free tiers are usually bound by tokens, not requests: Groq's gpt-oss-120b console
    # showed ~30 requests/min but only ~8K tokens/min, and a 3K-token call hits that after three requests.
    tpm: int = int(os.getenv("LLM_TPM", "0"))
    est_out_tokens: int = int(os.getenv("LLM_EST_OUT", "1200"))  # assumed output size when budgeting a call before it is made
    reasoning_effort: str = os.getenv("LLM_REASONING_EFFORT", "")  # low|medium|high for reasoning models (gpt-oss); "" = provider default
    cache: bool = os.getenv("LLM_CACHE", "1") == "1"
    cache_dir: Path = Path(os.getenv("LLM_CACHE_DIR", str(ROOT / ".cache")))
    data_dir: Path = Path(os.getenv("DATA_DIR", str(ROOT / "data")))


def get_settings() -> Settings:
    return Settings()
