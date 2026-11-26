/* 投屏端缓存/许可逻辑测试（node tests/screen_logic_test.js） */
"use strict";
const L = require("../app/static/screen_logic.js");
let failed = 0;
function check(name, cond) {
  console.log(`[${cond ? "PASS" : "FAIL"}] ${name}`);
  if (!cond) failed++;
}

// A. 断线后续播 + 许可到期清屏
{
  const p = L.createPlayer();
  L.applyPrefetch(p, { started: true, now: 1000, elapsed: 0, revoked: [],
    subtitles: [{ id: 1, text: "甲", start: 2, end: 8, licenseExpiry: 1005 }] });
  check("断线后许可期内按原时段续播", (L.currentSubtitle(p, 1003) || {}).text === "甲");
  check("时段前不显示", L.currentSubtitle(p, 1001) === null);
  check("许可到期即清屏", L.currentSubtitle(p, 1005.5) === null);
  check("过期条目从缓存移除", p.cache.size === 0);
}

// B. 撤下：立即清除 + 晚到响应不复活 + 其他字幕照常
{
  const p = L.createPlayer();
  const staleResp = { started: true, now: 1000, elapsed: 0, revoked: [], subtitles: [
    { id: 1, text: "甲", start: 2, end: 8, licenseExpiry: 1005 },
    { id: 2, text: "乙", start: 10, end: 16, licenseExpiry: 1005 }] };
  L.applyPrefetch(p, staleResp);
  const acks = L.applyPrefetch(p, { started: true, now: 1001, elapsed: 1, revoked: [1], subtitles: [
    { id: 2, text: "乙", start: 10, end: 16, licenseExpiry: 1020 }] });
  check("撤下后需向服务器确认该条", acks.join(",") === "1");
  check("该条立即从缓存清除", !p.cache.has(1));
  check("撤下后该条不再显示", L.currentSubtitle(p, 1003) === null);
  L.applyPrefetch(p, staleResp); // 晚到的旧响应（仍携带 #1）
  check("晚到响应不能复活已撤下字幕", !p.cache.has(1) && L.currentSubtitle(p, 1003) === null);
  check("其他字幕照常播放", (L.currentSubtitle(p, 1011) || {}).text === "乙");
}

// C. 许可到期后重连：收到撤下通知需确认，且不复播
{
  const p = L.createPlayer();
  L.applyPrefetch(p, { started: true, now: 1000, elapsed: 0, revoked: [],
    subtitles: [{ id: 1, text: "甲", start: 2, end: 8, licenseExpiry: 1005 }] });
  const acks = L.applyPrefetch(p, { started: true, now: 1006, elapsed: 6, subtitles: [], revoked: [1] });
  check("重连收到撤下通知需确认", acks.join(",") === "1");
  check("重连后该条不复播", L.currentSubtitle(p, 1006.5) === null);
}

// D. 未开播
{
  const p = L.createPlayer();
  L.applyPrefetch(p, { started: false, now: 1000, subtitles: [], revoked: [] });
  check("未开播不显示", L.currentSubtitle(p, 1001) === null);
}

console.log(failed ? `== ${failed} 项失败 ==` : "== 全部通过 ==");
process.exit(failed ? 1 : 0);
