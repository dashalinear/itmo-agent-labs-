"""Temporal worker: two task queues in one process.

  lab4-ingest  IngestNewFiles + stage/ingest/verify activities   (one activity at a time: one ingestion agent)
  lab4-topics  TopicReport + freeze/topic-agent/publish activities (one activity at a time)
"""
from __future__ import annotations

import asyncio
import os

from temporalio.client import Client
from temporalio.worker import Worker

from .activities import INGEST_ACTIVITIES, TOPIC_ACTIVITIES, Env
from .workflows import IngestNewFiles, TopicReport


async def run_worker(st, env: Env | None = None, client: Client | None = None) -> None:
    client = client or await Client.connect(st.temporal_address, namespace=st.namespace)
    env = env or Env.get()                        # loads the embedding model before polling
    print(f"[worker pid {os.getpid()}] Temporal {st.temporal_address}/{st.namespace}; queues {st.ingest_queue}, "
          f"{st.topics_queue}; task root {env.cfg.root}")
    w1 = Worker(client, task_queue=st.ingest_queue, workflows=[IngestNewFiles], activities=INGEST_ACTIVITIES,
                max_concurrent_activities=1)
    w2 = Worker(client, task_queue=st.topics_queue, workflows=[TopicReport], activities=TOPIC_ACTIVITIES,
                max_concurrent_activities=1)
    await asyncio.gather(w1.run(), w2.run())
