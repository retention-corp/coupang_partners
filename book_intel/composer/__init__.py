"""OpenClaw-backed content composer for the book vertical.

One public entrypoint: `compose_post(raw, tier)`. Runs the raw material through an
OpenClaw agent session (gateway or embedded) with a tier-appropriate prompt and
returns a dict the publisher consumes: title / lead / body / tags / prompt_variant.

Direct Anthropic SDK use is intentionally avoided — composition routes through the
operator's OpenClaw subscription so billing and session/tool configuration stay in
one place.
"""

from .openclaw import CompositionError, ComposerConfig, compose_post

__all__ = ["CompositionError", "ComposerConfig", "compose_post"]
