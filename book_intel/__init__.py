"""book_intel — enrichment, composition, and feedback loop for the book vertical.

Separate package from book_reco so `book_reco` remains a pure library (stateless
ranking / persona / Coupang bridge) while `book_intel` owns the stateful, I/O-heavy
layers: source scrapers, cached enrichment, OpenClaw composer, publisher adapters,
and the daily signal-rollup that feeds behavior-learned ranking back into
`book_reco.utils.learned_boost`.

Import boundary: book_intel may import from book_reco; the reverse is forbidden.
"""
