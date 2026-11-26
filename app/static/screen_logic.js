/* 投屏端字幕缓存 / 许可逻辑（纯逻辑，页面与 node 测试共用）。
 *
 * 规则：
 *  - 预取响应中的字幕带 license_expiry（服务器时钟），过期即丢弃 → 清屏。
 *  - 响应中的 revoked 列表：立即从缓存移除并加入墓碑集合，永不复播。
 *  - 晚到的旧响应（乱序到达）若仍携带已撤下字幕，墓碑集合会拦截。
 */
(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.ScreenLogic = factory();
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  function createPlayer() {
    return {
      broadcastStart: null, // 开播时刻（服务器时钟 epoch 秒）
      cache: new Map(),     // id -> {id, text, start, end, licenseExpiry}
      tombstones: new Set() // 已撤下字幕 id：晚到响应/重连都不能复活
    };
  }

  /* 应用一次预取响应；返回需要向服务器确认（ack）的字幕 id 列表。 */
  function applyPrefetch(player, resp) {
    if (resp.started) {
      player.broadcastStart = resp.now - resp.elapsed;
    } else {
      player.broadcastStart = null;
    }
    var revoked = resp.revoked || [];
    for (var i = 0; i < revoked.length; i++) {
      var id = revoked[i];
      player.tombstones.add(id);
      player.cache.delete(id); // 立即清除该条（若正在播则随即清屏）
    }
    var subs = resp.subtitles || [];
    for (var j = 0; j < subs.length; j++) {
      var s = subs[j];
      if (player.tombstones.has(s.id)) continue; // 晚到响应不能复活已撤下字幕
      var old = player.cache.get(s.id);
      if (old && old.licenseExpiry > s.licenseExpiry) continue; // 晚到的旧响应不缩短已有许可
      player.cache.set(s.id, s);
    }
    return revoked.slice();
  }

  /* 当前应显示的字幕；serverNow 为服务器时钟的当前时刻（epoch 秒）。
   * 许可过期的条目就地丢弃（断线后许可到期 → 清屏）。 */
  function currentSubtitle(player, serverNow) {
    if (player.broadcastStart === null) return null;
    var elapsed = serverNow - player.broadcastStart;
    var cur = null;
    player.cache.forEach(function (s, id) {
      if (s.licenseExpiry <= serverNow) { player.cache.delete(id); return; }
      if (player.tombstones.has(id)) return;
      if (s.start <= elapsed && elapsed < s.end) cur = s;
    });
    return cur;
  }

  return { createPlayer: createPlayer, applyPrefetch: applyPrefetch, currentSubtitle: currentSubtitle };
});
