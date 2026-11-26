#!/usr/bin/env python3
"""端到端冒烟测试：模拟导播 + 投屏端，验证预取/许可/撤下/断线/重连语义。

用法: python3 smoke_test.py [base_url]   默认 http://localhost:8000
仅依赖标准库。全程约 25 秒（许可 5 秒到期需要真实等待）。
"""
import json
import sys
import time
import urllib.error
import urllib.request

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000"
FAILED = []


def req(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"} if body is not None else {}
    r = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(r, timeout=5) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def check(name, cond, detail=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f"  ({detail})" if detail and not cond else ""))
    if not cond:
        FAILED.append(name)


def wait_elapsed(target, timeout=30):
    deadline = time.time() + timeout
    while time.time() < deadline:
        _, st = req("GET", "/api/state")
        if st["elapsed"] is not None and st["elapsed"] >= target:
            return
        time.sleep(0.1)
    raise TimeoutError(f"elapsed 未到 {target}s")


def sub_status(sid):
    _, st = req("GET", "/api/state")
    return next(s["status"] for s in st["subtitles"] if s["id"] == sid)


print(f"== 目标: {BASE} ==")
req("POST", "/api/reset")

# 1. 录入与校验
check("录入#1 [2,8]", req("POST", "/api/subtitles", {"start": 2, "end": 8, "text": "第一条"})[0] == 201)
check("录入#2 [10,16]", req("POST", "/api/subtitles", {"start": 10, "end": 16, "text": "第二条"})[0] == 201)
check("录入#3 [20,26]", req("POST", "/api/subtitles", {"start": 20, "end": 26, "text": "第三条"})[0] == 201)
check("重叠字幕被拒(409)", req("POST", "/api/subtitles", {"start": 5, "end": 6, "text": "x"})[0] == 409)
check("start>=end 被拒(400)", req("POST", "/api/subtitles", {"start": 8, "end": 6, "text": "x"})[0] == 400)
check("空文本被拒(400)", req("POST", "/api/subtitles", {"start": 30, "end": 32, "text": " "})[0] == 400)

# 2. 开播前预取
_, p = req("GET", "/api/prefetch")
check("开播前预取 started=false", p["started"] is False and p["subtitles"] == [])

# 3. 开播：预取窗口与许可时长
check("开播", req("POST", "/api/broadcast/start")[0] == 200)
check("重复开播被拒(409)", req("POST", "/api/broadcast/start")[0] == 409)
_, p = req("GET", "/api/prefetch")
ids = [s["id"] for s in p["subtitles"]]
check("预取窗口只含未来5秒内字幕(#1)", ids == [1], f"got {ids}")
lic = p["subtitles"][0]["license_expiry"] - p["now"]
check("许可自发出起约5秒", 4.0 < lic <= 5.2, f"{lic:.2f}s")

# 4. 在线撤下当前字幕：确认 → 已撤下
wait_elapsed(3.0)
req("GET", "/api/prefetch")  # 投屏在线，#1 持有许可
_, r = req("POST", "/api/subtitles/1/revoke")
check("撤下受理→撤下中", r["status"] == "revoking", r.get("status"))
_, p = req("GET", "/api/prefetch")
check("受理后立即停发#1许可", 1 not in [s["id"] for s in p["subtitles"]])
check("预取告知投屏清除#1", 1 in p["revoked"])
_, a = req("POST", "/api/subtitles/1/ack")  # 投屏清除后确认
check("收到确认→已撤下", a["status"] == "revoked", a.get("status"))
_, a2 = req("POST", "/api/subtitles/1/ack")
check("晚到的重复确认幂等，不改变状态", a2["status"] == "revoked")

# 5. 重连/续取：#1 永不复播
seen1 = False
for _ in range(3):
    _, p = req("GET", "/api/prefetch")
    seen1 = seen1 or 1 in [s["id"] for s in p["subtitles"]]
    time.sleep(0.4)
check("撤下后续取/重连不再发放#1", not seen1)

# 6. 断线期间撤下：无确认，旧许可到期自动转已撤下
wait_elapsed(10.5)
_, p = req("GET", "/api/prefetch")  # 掉线前最后一次预取
check("#2 在窗口内获得许可", 2 in [s["id"] for s in p["subtitles"]])
_, r = req("POST", "/api/subtitles/2/revoke")
check("撤下#2→撤下中（许可未到期）", r["status"] == "revoking", r.get("status"))
# 模拟投屏掉线：不再预取、不确认
deadline, flipped = time.time() + 8, False
while time.time() < deadline:
    if sub_status(2) == "revoked":
        flipped = True
        break
    time.sleep(0.3)
check("无确认时旧许可到期自动→已撤下", flipped)

# 7. 重连：#2 不复播，其他字幕照常；撤下中字幕经重连清除并确认
_, p = req("GET", "/api/prefetch")  # 重连
ids = [s["id"] for s in p["subtitles"]]
check("重连后#2不复播", 2 not in ids)
check("其他字幕照常发放(#3)", 3 in ids, f"got {ids}")
_, r = req("POST", "/api/subtitles/3/revoke")
check("撤下#3→撤下中", r["status"] == "revoking", r.get("status"))
time.sleep(2)  # 投屏离线 2 秒（#3 许可仍未到期）
_, p = req("GET", "/api/prefetch")  # 再次重连
check("重连预取告知清除#3", 3 in p["revoked"])
check("重连不再发放#3许可", 3 not in [s["id"] for s in p["subtitles"]])
_, a = req("POST", "/api/subtitles/3/ack")
check("确认后#3→已撤下", a["status"] == "revoked", a.get("status"))

# 8. 边界：首尾相接不算重叠；已撤下时段可复用
req("POST", "/api/reset")
req("POST", "/api/subtitles", {"start": 2, "end": 8, "text": "A"})
check("首尾相接不算重叠(201)", req("POST", "/api/subtitles", {"start": 8, "end": 10, "text": "B"})[0] == 201)
req("POST", "/api/subtitles/1/revoke")
check("已撤下时段可复用(201)", req("POST", "/api/subtitles", {"start": 3, "end": 5, "text": "C"})[0] == 201)
req("POST", "/api/reset")

print("== " + ("全部通过" if not FAILED else f"{len(FAILED)} 项失败: {FAILED}") + " ==")
sys.exit(1 if FAILED else 0)
