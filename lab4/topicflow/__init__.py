"""Lab 4: Temporal automation on top of labs 1-3.

  * IngestNewFiles workflow  - a watcher detects finished files in workspace/input and starts it; it calls the
                               lab 1/3 ingestion agent (in the lab 2 harness) that builds L0/L1/L2 in Elasticsearch.
  * TopicReport workflow     - started by a Temporal Schedule; freezes the L0 document list, runs the topic-analysis
                               agent (same create_agent + harness as lab 3) and publishes a report document at L0.

The workflow code only coordinates; LLM, Elasticsearch/MCP and file I/O run in activities (activities.py).
"""
from __future__ import annotations

__version__ = "4.0.0"
