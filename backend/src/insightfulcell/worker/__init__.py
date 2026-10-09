"""The worker: runs analysis phases and periodic upkeep from the PostgreSQL job queue (IC-007).

Same package and container image as the API, different command:
``python -m insightfulcell.worker``.
"""
