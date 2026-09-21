"""Post-generation verification.

The writer is instructed to be current and sourced; this module checks that it
actually was. Anything the model can get wrong in a way that would embarrass
the channel - an undated claim, a stale source, a year reference pointing
backwards, a script that reads like a template - fails here and the topic is
dropped rather than published.

Failing closed is the right default for an unattended pipeline: a skipped
video costs one slot, a wrong or stale video costs channel trust and, under
the inauthentic-content policy, potentially monetisation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from ..config import Config
from ..freshness import extract_generations, history_frame, stale_year_reference
from ..models import Script, utcnow

# Phrases that signal templated, mass-produced narration. Their presence is
# the textual fingerprint of exactly the content YouTube demonetises.
FILLER_PHRASES = (
    "in today's video",
    "in this video we will",
    "let's dive in",
    "let's dive right in",
    "buckle up",
    "without further ado",
    "stay tuned",
    "that's right folks",
    "you won't believe",
    "hit that like button",
    "as an ai",
    "as a language model",
    "i cannot provide",
)

HYPE_WORDS = ("game-changer", "game changer", "revolutionary", "mind-blowing", "insane")


@dataclass
class VerificationResult:
    ok: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def __bool__(self) -> bool:
        return self.ok


class ScriptVerifier:
    def __init__(self, config: Config, known_generations: dict[str, float] | None = None):
        self.config = config
        self.known_generations = dict(known_generations or {})

    def verify(self, script: Script, now: datetime | None = None) -> VerificationResult:
        now = now or utcnow()
        cfg = self.config.script
        fresh = self.config.freshness
        errors: list[str] = []
        warnings: list[str] = []

        text = script.voiceover_text
        lowered = text.lower()

        # --- length -----------------------------------------------------
        words = script.word_count()
        if words < cfg.min_words:
            errors.append(f"script too short: {words} words (min {cfg.min_words})")
        elif words > cfg.max_words:
            warnings.append(f"script long: {words} words (max {cfg.max_words})")

        # --- sourcing ---------------------------------------------------
        sourced = [c for c in script.claims if c.source_url and c.as_of]
        if len(sourced) < cfg.min_claims_with_sources:
            errors.append(
                f"only {len(sourced)} fully-sourced claims "
                f"(need {cfg.min_claims_with_sources})"
            )

        for claim in script.claims:
            if not claim.source_url:
                warnings.append(f"unsourced claim: {claim.text[:70]}")
                continue
            if claim.as_of is None:
                warnings.append(f"claim has no date: {claim.text[:70]}")
                continue
            age_days = (now - claim.as_of).total_seconds() / 86400.0
            if age_days > fresh.max_claim_age_days:
                errors.append(
                    f"claim sourced to {claim.as_of:%Y-%m-%d} "
                    f"({age_days:.0f}d old, limit {fresh.max_claim_age_days:.0f}d): "
                    f"{claim.text[:60]}"
                )
            elif age_days < -1:
                errors.append(f"claim dated in the future: {claim.text[:60]}")

        # --- currency of the prose -------------------------------------
        bad_year = stale_year_reference(text, now, fresh.max_year_reference_lag)
        if bad_year is not None:
            errors.append(f"script references outdated year {bad_year}")

        frame = history_frame(text)
        if frame:
            warnings.append(f"script uses a backward-looking frame: '{frame}'")

        if fresh.reject_superseded_generations:
            for gen in extract_generations(text):
                latest = self.known_generations.get(gen.family)
                if latest is not None and latest > gen.number:
                    errors.append(
                        f"script discusses {gen.label} but {gen.family} "
                        f"is now at {latest:g}"
                    )

        # --- originality ------------------------------------------------
        if cfg.require_original_analysis:
            analysis = script.original_analysis.strip()
            if len(analysis.split()) < 12:
                errors.append(
                    "missing original analysis - this is what separates the video "
                    "from a summary and from mass-produced content"
                )

        # --- template fingerprints --------------------------------------
        for phrase in FILLER_PHRASES:
            if phrase in lowered:
                errors.append(f"templated filler phrase: '{phrase}'")
        hype_hits = [w for w in HYPE_WORDS if w in lowered]
        if len(hype_hits) > 1:
            warnings.append(f"hype language: {', '.join(hype_hits)}")

        # --- title ------------------------------------------------------
        if len(script.title) > 100:
            errors.append(f"title exceeds YouTube's 100-char limit ({len(script.title)})")
        if script.title.isupper() and len(script.title) > 15:
            warnings.append("title is all caps")

        return VerificationResult(ok=not errors, errors=errors, warnings=warnings)
