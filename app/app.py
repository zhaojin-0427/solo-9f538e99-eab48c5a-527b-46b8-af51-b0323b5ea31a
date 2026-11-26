"""会场字幕投屏系统 —— Flask 后端。

角色：
  /         导播页：录入字幕（开播后的起止秒，互不重叠）、开播、撤下字幕。
  /screen   投屏页：在线时每 1 秒预取未来 5 秒字幕；许可自发出起 5 秒有效。

核心规则：
  * 预取命中窗口 [elapsed, elapsed+5s) 且未撤下的字幕才发放许可（now+5s）。
  * 撤下受理后立即停发该条许可，状态变为"撤下中"。
  * 投屏端清除该条后回确认（ack），或旧许可全部到期，才变为"已撤下"。
  * "撤下中/已撤下"字幕永不再发许可：晚到响应与重连都不能使其复播。

状态保存在单进程内存中，因此必须以单 worker 运行（gunicorn --workers 1）。
"""

import threading
import time

from flask import Flask, jsonify, render_template, request

PREFETCH_WINDOW = 5.0   # 预取窗口：未来 5 秒
LICENSE_TTL = 5.0       # 许可时长：自发出起 5 秒
SCREEN_ONLINE_TTL = 2.5  # 距最近一次预取不足 2.5s 视为投屏在线
MAX_TEXT_LEN = 200

app = Flask(__name__)
lock = threading.Lock()

state = {
    "broadcast_start": None,   # epoch 秒；None = 未开播
    "subtitles": {},           # id -> 字幕记录
    "next_id": 1,
    "events": [],              # 导播页事件日志（最多保留 100 条）
    "last_prefetch_at": None,  # 最近一次预取时刻，用于推断投屏在线
}


def _now():
    return time.time()


def _log(msg):
    state["events"].append({"t": _now(), "msg": msg})
    del state["events"][:-100]


def _sweep(now):
    """把旧许可已全部到期的"撤下中"字幕转为"已撤下"（惰性结算，无需后台线程）。"""
    for sub in state["subtitles"].values():
        if sub["status"] == "revoking" and now >= sub["licensed_until"]:
            sub["status"] = "revoked"
            sub["revoked_at"] = now
            _log(f"字幕 #{sub['id']} 旧许可已全部到期 → 已撤下")


def _public(sub, now, elapsed):
    status = sub["status"]
    return {
        "id": sub["id"],
        "start": sub["start"],
        "end": sub["end"],
        "text": sub["text"],
        "status": status,
        "playing": bool(
            status == "scheduled" and elapsed is not None
            and sub["start"] <= elapsed < sub["end"]
        ),
        "finished": bool(elapsed is not None and sub["end"] <= elapsed),
        "license_remaining": round(max(0.0, sub["licensed_until"] - now), 2),
    }


def _err(msg, code=400):
    return jsonify({"error": msg}), code


# ---------------- 页面 ----------------

@app.get("/")
def director_page():
    return render_template("director.html")


@app.get("/screen")
def screen_page():
    return render_template("screen.html")


# ---------------- 导播接口 ----------------

@app.post("/api/subtitles")
def add_subtitle():
    data = request.get_json(silent=True) or {}
    try:
        start = float(data["start"])
        end = float(data["end"])
        text = str(data["text"]).strip()
    except (KeyError, TypeError, ValueError):
        return _err("参数错误：需要数值 start/end 与文本 text")
    if not (start >= 0 and end > start):
        return _err("需满足 0 ≤ start < end")
    if not text:
        return _err("字幕文本不能为空")
    if len(text) > MAX_TEXT_LEN:
        return _err(f"字幕文本过长（>{MAX_TEXT_LEN} 字）")

    with lock:
        now = _now()
        _sweep(now)
        for sub in state["subtitles"].values():
            if sub["status"] == "revoked":
                continue  # 已撤下的时段允许复用
            if start < sub["end"] and end > sub["start"]:
                return _err(f"与字幕 #{sub['id']}（{sub['start']}–{sub['end']}s）重叠", 409)
        sid = state["next_id"]
        state["next_id"] += 1
        state["subtitles"][sid] = {
            "id": sid,
            "start": start,
            "end": end,
            "text": text,
            "status": "scheduled",
            "licensed_until": 0.0,  # 已发出许可的最晚到期时刻
            "revoke_requested_at": None,
            "revoked_at": None,
        }
        _log(f"录入字幕 #{sid}（{start}–{end}s）：{text}")
        return jsonify({"id": sid}), 201


@app.post("/api/broadcast/start")
def start_broadcast():
    with lock:
        if state["broadcast_start"] is not None:
            return _err("已开播", 409)
        state["broadcast_start"] = _now()
        _log("开播")
        return jsonify({"broadcast_start": state["broadcast_start"]})


@app.post("/api/subtitles/<int:sid>/revoke")
def revoke_subtitle(sid):
    """撤下当前或待播字幕：受理后立即停发该条许可。"""
    with lock:
        now = _now()
        _sweep(now)
        sub = state["subtitles"].get(sid)
        if sub is None:
            return _err("字幕不存在", 404)
        if sub["status"] == "scheduled":
            sub["status"] = "revoking"
            sub["revoke_requested_at"] = now
            _log(f"撤下字幕 #{sid} → 撤下中（停发许可，等待确认或许可到期）")
            _sweep(now)  # 若已无未到期许可（如待播且从未预取），直接转为已撤下
        return jsonify({"id": sid, "status": sub["status"],
                        "license_remaining": round(max(0.0, sub["licensed_until"] - now), 2)})


@app.post("/api/subtitles/<int:sid>/ack")
def ack_subtitle(sid):
    """投屏端清除该条后的确认。晚到的确认幂等，不改变已撤下状态。"""
    with lock:
        now = _now()
        _sweep(now)
        sub = state["subtitles"].get(sid)
        if sub is None:
            return _err("字幕不存在", 404)
        if sub["status"] == "revoking":
            sub["status"] = "revoked"
            sub["revoked_at"] = now
            _log(f"收到字幕 #{sid} 的清除确认 → 已撤下")
        return jsonify({"id": sid, "status": sub["status"]})


@app.post("/api/reset")
def reset():
    with lock:
        state["broadcast_start"] = None
        state["subtitles"] = {}
        state["next_id"] = 1
        state["events"] = []
        state["last_prefetch_at"] = None
        _log("已重置")
        return jsonify({"ok": True})


# ---------------- 投屏接口 ----------------

@app.get("/api/prefetch")
def prefetch():
    """投屏预取：返回窗口内字幕并发放 5 秒许可；同时告知应清除的"撤下中"字幕。"""
    with lock:
        now = _now()
        state["last_prefetch_at"] = now
        _sweep(now)
        bs = state["broadcast_start"]
        if bs is None:
            return jsonify({"started": False, "now": now, "subtitles": [], "revoked": []})
        elapsed = now - bs
        license_expiry = now + LICENSE_TTL
        out = []
        for sub in state["subtitles"].values():
            if sub["status"] != "scheduled":
                continue  # 撤下中/已撤下：立即停发、永不再发
            if sub["end"] > elapsed and sub["start"] <= elapsed + PREFETCH_WINDOW:
                sub["licensed_until"] = max(sub["licensed_until"], license_expiry)
                out.append({
                    "id": sub["id"],
                    "text": sub["text"],
                    "start": sub["start"],
                    "end": sub["end"],
                    "license_expiry": license_expiry,
                })
        revoking = [sub["id"] for sub in state["subtitles"].values()
                    if sub["status"] == "revoking"]
        return jsonify({
            "started": True,
            "now": now,
            "elapsed": elapsed,
            "subtitles": out,
            "revoked": revoking,
        })


# ---------------- 导播页状态轮询 ----------------

@app.get("/api/state")
def get_state():
    with lock:
        now = _now()
        _sweep(now)
        bs = state["broadcast_start"]
        elapsed = None if bs is None else now - bs
        last = state["last_prefetch_at"]
        return jsonify({
            "now": now,
            "started": bs is not None,
            "elapsed": elapsed,
            "screen_online": bool(last is not None and now - last < SCREEN_ONLINE_TTL),
            "subtitles": [_public(s, now, elapsed) for s in state["subtitles"].values()],
            "events": list(reversed(state["events"][-15:])),
        })


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, threaded=True)
