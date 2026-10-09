"""The analysis pipeline: stage contract, checkpoints and the stages themselves.

Pure like ``ingestion``: no web framework, database driver or cloud SDK. A stage gets typed
inputs, does its work, and reaches the outside world only through the ``ArtifactStore`` port;
the worker (``services.run_executor``) persists results and writes checkpoints.
"""
