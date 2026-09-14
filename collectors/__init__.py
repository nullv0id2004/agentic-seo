"""Collection plane. Deterministic. Has network. No LLM, no judgement. Writes only raw_* tables.

Each module exposes collect(project, run_id, params) -> CollectorResult and is registered in
collectors.registry. On failure a collector writes a collection_gaps row and returns partial.
It never estimates.
"""
