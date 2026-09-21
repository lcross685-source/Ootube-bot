"""Script generation via the Claude API."""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime
from typing import Any

from ..config import Config
from ..feeds import parse_date
from ..models import Claim, Script, ScriptSection, Topic, utcnow
from .prompts import build_system_prompt, build_user_prompt

log = logging.getLogger(__name__)


class ScriptGenerationError(RuntimeError):
    pass


def _extract_json(text: str) -> dict[str, Any]:
    """Pull a JSON object out of a model response.

    Models occasionally wrap JSON in a markdown fence or add a sentence before
    it despite instructions, so this tolerates both rather than failing the run.
    """
    text = (text or "").strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    start, end = text.find("{"), text.rfind("}")
    if start >= 0 and end > start:
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError as exc:
            raise ScriptGenerationError(f"unparseable JSON from model: {exc}") from exc
    raise ScriptGenerationError("no JSON object found in model response")


class ScriptWriter:
    """Turns a :class:`Topic` plus its evidence into a :class:`Script`."""

    def __init__(self, config: Config):
        self.config = config
        self._client: Any = None

    # ------------------------------------------------------------------
    def _client_or_raise(self) -> Any:
        if self._client is not None:
            return self._client
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise ScriptGenerationError(
                "ANTHROPIC_API_KEY is not set; cannot generate scripts"
            )
        try:
            import anthropic
        except ImportError as exc:  # pragma: no cover
            raise ScriptGenerationError(
                "anthropic package missing. Install with: pip install 'ootube[script]'"
            ) from exc
        self._client = anthropic.Anthropic(api_key=api_key)
        return self._client

    # ------------------------------------------------------------------
    def write(self, topic: Topic, now: datetime | None = None) -> Script:
        now = now or utcnow()
        niche = self.config.niche(topic.niche)
        cfg = self.config.script

        client = self._client_or_raise()
        response = client.messages.create(
            model=cfg.model,
            max_tokens=cfg.max_tokens,
            temperature=cfg.temperature,
            system=build_system_prompt(niche, now),
            messages=[
                {"role": "user", "content": build_user_prompt(topic, niche, self.config, now)}
            ],
        )
        text = "".join(
            block.text for block in response.content if getattr(block, "type", "") == "text"
        )
        return self.parse(text, topic, model=cfg.model)

    # ------------------------------------------------------------------
    def parse(self, raw: str, topic: Topic, model: str = "") -> Script:
        """Convert a model response into a validated :class:`Script`."""
        data = _extract_json(raw)

        sections = [
            ScriptSection(
                heading=str(s.get("heading", "")).strip(),
                voiceover=str(s.get("voiceover", "")).strip(),
                b_roll_query=str(s.get("b_roll_query", "")).strip(),
                on_screen_text=str(s.get("on_screen_text", "")).strip(),
            )
            for s in data.get("sections", []) or []
            if str(s.get("voiceover", "")).strip()
        ]

        claims = [
            Claim(
                text=str(c.get("text", "")).strip(),
                source_url=str(c.get("source_url", "")).strip(),
                as_of=parse_date(str(c.get("as_of", "")) or None),
            )
            for c in data.get("claims", []) or []
            if str(c.get("text", "")).strip()
        ]

        title = str(data.get("title", "")).strip() or topic.term
        script = Script(
            topic_key=topic.key,
            title=title,
            hook=str(data.get("hook", "")).strip(),
            sections=sections,
            claims=claims,
            description=str(data.get("description", "")).strip(),
            tags=[str(t).strip().lower() for t in (data.get("tags", []) or []) if str(t).strip()],
            original_analysis=str(data.get("original_analysis", "")).strip(),
            thumbnail_text=str(data.get("thumbnail_text", "")).strip(),
            model=model,
        )

        if not script.sections:
            raise ScriptGenerationError("model returned no usable sections")
        if not script.hook:
            raise ScriptGenerationError("model returned no hook")
        return script
