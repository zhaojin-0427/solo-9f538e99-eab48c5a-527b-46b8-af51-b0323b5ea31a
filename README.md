# 会场字幕投屏系统

会场投屏掉线场景的字幕导播系统：导播页录入字幕并开播，独立投屏页预取字幕、按许可播出，支持断线续播、撤下字幕与重连。

## 启动

```bash
docker compose up --build
# 导播页  http://localhost:8000/
# 投屏页  http://localhost:8000/screen
```

本地开发（无 Docker）：

```bash
pip install -r app/requirements.txt
python app/app.py          # 或 gunicorn --workers 1 --threads 16 --chdir app app:app
```

> 状态保存在单进程内存中，必须单 worker 运行。

## 规则与协议

- **录入**：导播按开播后的起止秒录入字幕，服务端校验互不重叠（首尾相接允许；已撤下字幕的时段可复用）。录完后点「开播」。
- **预取与许可**：投屏页在线时每秒 `GET /api/prefetch`，服务端返回窗口 `[elapsed, elapsed+5s)` 内的字幕，每条附带 `license_expiry = now + 5s`（许可自发出起 5 秒）。
- **断线续播**：投屏页用缓存字幕按原时段续播；许可到期即丢弃并清屏。网页右上角「模拟掉线/模拟重连」按钮可演示。
- **撤下**：导播 `POST /api/subtitles/<id>/revoke`，受理后立即停发该条许可，状态转「撤下中」；预取响应的 `revoked` 列表通知在线投屏清除该条。
- **确认**：投屏清除后 `POST /api/subtitles/<id>/ack`，服务端收到确认即转「已撤下」；若无确认（投屏掉线），旧许可全部到期后自动转「已撤下」（惰性结算）。
- **不复播**：「撤下中/已撤下」字幕永不再发许可；投屏端用墓碑集合拦截晚到的旧响应；重连只会在许可未到期时收到清除通知，不会复活字幕。重复确认幂等。

## 测试

```bash
# 服务端协议冒烟测试（约 25 秒，模拟导播+投屏全流程）
python3 tests/smoke_test.py http://localhost:8000

# 投屏端缓存/许可逻辑测试
node tests/screen_logic_test.js

# 容器内跑冒烟测试
docker compose --profile test up --build --abort-on-container-exit smoke
```

## 演示断线撤字幕

1. 导播页录入几条字幕（如 2–8s、10–16s、20–26s），点「开播」。
2. 投屏页点「模拟掉线」：缓存字幕继续按原时段播，许可到期（约 5 秒）后清屏。
3. 掉线期间在导播页「撤下」一条当前/待播字幕：状态变「撤下中」，约 5 秒（旧许可到期）后自动变「已撤下」。
4. 投屏页点「模拟重连」：被撤字幕不再出现，其余字幕照常播出。
5. 在线时撤下：投屏约 1 秒内清除该条并确认，导播页随即显示「已撤下」。

## 目录

```
app/app.py                 Flask 后端（状态机 + 许可发放 + 惰性到期结算）
app/templates/director.html 导播页
app/templates/screen.html   投屏页
app/static/screen_logic.js  投屏端缓存/许可纯逻辑（页面与 node 测试共用）
tests/smoke_test.py         服务端端到端冒烟测试
tests/screen_logic_test.js  投屏端逻辑测试
```

## 已知限制

- 状态在内存中，重启即丢失；单进程单 worker，未做水平扩展。
- 未做鉴权与多会场隔离。
- 时钟以服务器为准，客户端用偏移量对齐，未做 NTP 级校准（5 秒许可尺度下足够）。
