"""Token 用量统计：按天累加 prompt/completion token，持久化，后端重启不丢失。

源数据 prompt_tokens / completion_tokens 是推理引擎"启动以来累计值"，
模型重启会归零。这里做增量累加：
  - 正常：delta = current - last
  - 归零（模型重启）：current < last，delta = current（从 0 重新累计的部分）
按天分桶落盘到 ~/.model-deploy-assistant/token_stats.json，后端重启不丢失。
"""

import json
import os
import threading
from datetime import date, timedelta

CONFIG_DIR = os.path.join(os.path.expanduser("~"), ".model-deploy-assistant")
STATS_FILE = os.path.join(CONFIG_DIR, "token_stats.json")
RETENTION_DAYS = 30

_lock = threading.Lock()


def _load() -> dict:
    if not os.path.exists(STATS_FILE):
        return {}
    try:
        with open(STATS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save(data: dict) -> None:
    os.makedirs(CONFIG_DIR, exist_ok=True)
    tmp = STATS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, STATS_FILE)


def _prune_days(days: dict) -> None:
    """只保留最近 RETENTION_DAYS 天，避免文件无限增长。"""
    cutoff = (date.today() - timedelta(days=RETENTION_DAYS)).isoformat()
    for k in [k for k in days if k < cutoff]:
        del days[k]


def record_tokens(target_id: str, metrics: dict) -> None:
    """每次采集后调用，把增量累加进当天桶。

    无 token 字段（如 comfyui 引擎）则跳过。重复调用且值未变化时 delta=0，
    不会重复累加，因此 snapshot 与 ws 通道都可安全调用。
    """
    if not metrics or ("prompt_tokens" not in metrics and "completion_tokens" not in metrics):
        return
    cur_p = int(metrics.get("prompt_tokens", 0) or 0)
    cur_c = int(metrics.get("completion_tokens", 0) or 0)

    with _lock:
        data = _load()
        st = data.setdefault(target_id, {"last_prompt": 0, "last_completion": 0, "days": {}})
        last_p = st.get("last_prompt", 0)
        last_c = st.get("last_completion", 0)
        # 归零检测：current < last 说明模型重启，增量取 current（从 0 重新累计的部分）
        delta_p = cur_p - last_p if cur_p >= last_p else cur_p
        delta_c = cur_c - last_c if cur_c >= last_c else cur_c
        if delta_p < 0:
            delta_p = 0
        if delta_c < 0:
            delta_c = 0

        today = date.today().isoformat()
        days = st.setdefault("days", {})
        day = days.setdefault(today, {"prompt": 0, "completion": 0})
        day["prompt"] += delta_p
        day["completion"] += delta_c

        st["last_prompt"] = cur_p
        st["last_completion"] = cur_c
        _prune_days(days)
        _save(data)


def get_total_stats(target_id: str) -> dict:
    """累计消耗：所有已记录天的 prompt/completion 求和（不受查询窗口限制）。"""
    with _lock:
        data = _load()
    day_map = data.get(target_id, {}).get("days", {})
    total_p = sum(v.get("prompt", 0) for v in day_map.values())
    total_c = sum(v.get("completion", 0) for v in day_map.values())
    return {"prompt": total_p, "completion": total_c, "total": total_p + total_c}


def get_daily_stats(target_id: str, days: int = 14) -> list:
    """返回 [{date, prompt, completion}]，缺失天补 0。

    窗口起点：最早有数据的那天（含今天）；尚无任何数据时回退最近 days 天。
    """
    with _lock:
        data = _load()
    day_map = data.get(target_id, {}).get("days", {})
    today = date.today()
    if day_map:
        start = min(date.fromisoformat(d) for d in day_map)
    else:
        start = today - timedelta(days=days - 1)
    result = []
    cur = start
    while cur <= today:
        d = cur.isoformat()
        v = day_map.get(d, {"prompt": 0, "completion": 0})
        result.append({
            "date": d,
            "prompt": v.get("prompt", 0),
            "completion": v.get("completion", 0),
        })
        cur += timedelta(days=1)
    return result
