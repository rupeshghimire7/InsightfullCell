"""The analysis engine: statistics, semantics, planning, cleaning and recipes.

Pure code: it imports no web framework, database driver or cloud SDK. It reads local Parquet
with DuckDB, and reaches the outside world only through the ports in ``engine.ports`` (the
language model, the run's records, object storage). Stages in ``insightfulcell.pipeline``
call it; adapters in ``insightfulcell.services`` and ``insightfulcell.llm`` implement the ports.
"""
