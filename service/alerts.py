"""Publish Watcher alerts to Pub/Sub and read them back, for event-driven preparation alerts.

Guarded, like the agent layer: without the library, without credentials, or without the topic, `publish` and
`recent` return a status instead of raising - a missing alerting backend must never stop the screening from
serving. On Cloud Run the service account's own credentials are used; the topic and subscription are created
once with gcloud (see the runbook).
"""
from __future__ import annotations

import json
import os

PROJECT = (os.environ.get("GOOGLE_CLOUD_PROJECT") or os.environ.get("BIH_VERTEX_PROJECT")
           or "before-it-hits")
TOPIC = os.environ.get("BIH_ALERTS_TOPIC", "bih-alerts")
SUBSCRIPTION = os.environ.get("BIH_ALERTS_SUB", "bih-alerts-sub")
OFF = os.environ.get("BIH_ALERTS_OFF", "").lower() in ("1", "true", "yes")

_publisher = None


def _publisher_or_raise():
    global _publisher
    if _publisher is None:
        from google.cloud import pubsub_v1
        _publisher = pubsub_v1.PublisherClient()
    return _publisher


def publish(alerts: list[dict]) -> dict:
    """Publish each alert as one Pub/Sub message. Returns how many went out, or why none did."""
    if not alerts:
        return {"published": 0, "status": "no alerts crossed the threshold"}
    if OFF:
        return {"published": 0, "status": "alerting disabled (BIH_ALERTS_OFF)"}
    try:
        pub = _publisher_or_raise()
        path = pub.topic_path(PROJECT, TOPIC)
        n = 0
        for a in alerts:
            future = pub.publish(path, json.dumps(a).encode("utf-8"),
                                 region=str(a.get("region", "")), severity=str(a.get("severity", "")))
            future.result(timeout=20)
            n += 1
        return {"published": n, "topic": TOPIC, "status": "ok"}
    except Exception as exc:              # a Pub/Sub outage is reported, never a 500
        return {"published": 0, "status": f"pub/sub unavailable: {type(exc).__name__}: {exc}"[:200]}


def recent(max_messages: int = 20) -> dict:
    """Pull and acknowledge recent alerts from the subscription. Draining semantics: each alert is returned once."""
    if OFF:
        return {"alerts": [], "status": "alerting disabled (BIH_ALERTS_OFF)"}
    try:
        from google.cloud import pubsub_v1
        sub = pubsub_v1.SubscriberClient()
        path = sub.subscription_path(PROJECT, SUBSCRIPTION)
        resp = sub.pull(request={"subscription": path, "max_messages": max(1, min(int(max_messages), 50))},
                        timeout=15)
        out, ack = [], []
        for m in resp.received_messages:
            ack.append(m.ack_id)
            try:
                out.append(json.loads(m.message.data.decode("utf-8")))
            except Exception:
                pass
        if ack:
            sub.acknowledge(request={"subscription": path, "ack_ids": ack})
        sub.close()
        out.sort(key=lambda a: a.get("issued_at", ""), reverse=True)
        return {"alerts": out, "status": "ok"}
    except Exception as exc:
        return {"alerts": [], "status": f"pub/sub unavailable: {type(exc).__name__}: {exc}"[:200]}
