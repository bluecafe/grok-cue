"""Cue × Grok handoff bridge.

Accepts HMAC-SHA256 v2 signed task reports, records them, and forwards the
first acceptance to Grok through an outbox. It does not run, merge, or deploy
the reported work.
"""

__version__ = "0.2.0"
