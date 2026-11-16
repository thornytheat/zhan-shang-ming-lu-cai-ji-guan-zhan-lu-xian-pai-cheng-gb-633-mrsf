"""运行清单：run_id、输入摘要、规则版本、参数、耗时，可复算。

run_id = 前缀 + UTC 时间戳 + 4 位随机，人读短、机读唯一；
batch_key = 来源 + 输入摘要前 16 位，同一次输入重跑命中同一个键。
"""
from __future__ import annotations

import datetime as dt
import hashlib
import uuid

from . import config


def new_run_id(prefix: str = "BP") -> str:
    stamp = dt.datetime.now(config.UTC).strftime("%Y%m%d%H%M%S")
    return f"{prefix}-{stamp}-{uuid.uuid4().hex[:4]}"


def digest_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def digest_parts(parts: list[str]) -> str:
    h = hashlib.sha256()
    for part in parts:
        h.update(part.encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()


def batch_key(source: str, input_digest: str) -> str:
    """批次键：来源 + 输入摘要前缀。同一份输入重跑得到同一个键（幂等）。"""
    return f"{source}:{input_digest[:16]}"


def build(*, run_id: str, batch_id: str, batch_key_value: str, settings, source: str,
          input_digest: str, pages: list[dict], params: dict, started_at: str,
          finished_at: str, elapsed_ms: int, stages: list[dict], counters: dict) -> dict:
    return {
        "run_id": run_id,
        "batch_id": batch_id,
        "batch_key": batch_key_value,
        "source": source,
        "rule_version": settings.rule_version,
        "eval_date": settings.eval_date.isoformat(),
        "sample_site_base": settings.sample_site_base,
        "dist_decimals": "距离 Decimal，单位米，最小刻度 0.1",
        "timezone": "入库 UTC，展示 Asia/Shanghai(+08:00)",
        "window_rule": "论坛时间窗左闭右开 [start, end)",
        "input_digest": input_digest,
        "inputs": [
            {"url": p["url"], "status": p["status"], "bytes": p.get("bytes"),
             "sha256": p.get("sha256"), "encoding": p.get("encoding")}
            for p in pages
        ],
        "params": params,
        "started_at": started_at,
        "finished_at": finished_at,
        "elapsed_ms": elapsed_ms,
        "stages": stages,
        "counters": counters,
        "replayable": "同 batch_key 重跑复用已完成的批次，不新增行",
    }
