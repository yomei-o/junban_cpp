// 順番待ちのページ（take / ticket / staff）共通の小道具
"use strict";

const $ = (id) => document.getElementById(id);
const PARAMS = new URLSearchParams(location.search);

// index.html と同じ場所にあるログインのトークン（店員の画面で使う）
const TOKEN_KEY = "access_token";
function loginToken() {
  try { return localStorage.getItem(TOKEN_KEY) || ""; } catch (e) { return ""; }
}

// お客さんが最後に取った整理券（店ごと）。受付ページに戻ってきたときに案内する
function savedTicket(tenant) {
  try { return localStorage.getItem("junban_ticket/" + tenant) || ""; } catch (e) { return ""; }
}
function saveTicket(tenant, token) {
  try { localStorage.setItem("junban_ticket/" + tenant, token); } catch (e) {}
}

// サーバが止まっていた・落ちた（502 など）ときも例外にせず fail の形で返す
async function api(path, body, withLogin) {
  const h = { "Content-Type": "application/json" };
  const t = withLogin ? loginToken() : "";
  if (t) h.Authorization = "Bearer " + t;
  try {
    const res = await fetch("/api/junban/v0/" + path, { method: "POST", headers: h, body: JSON.stringify(body || {}) });
    if (!res.ok) return { status: "fail", message: "http " + res.status };
    return await res.json();
  } catch (e) {
    return { status: "fail", message: "network" };
  }
}

const MESSAGES = {
  "not accepting": "いまは受付を止めています",
  "unknown tenant": "お店が見つかりません（URL を確かめてください）",
  "invalid people": "人数は 1〜99 人で選んでください",
  "unknown seat": "席の種類が見つかりません。選び直してください",
  "unknown ticket": "整理券が見つかりません",
  "cannot cancel": "この整理券はもう取り消せません",
  "no one waiting": "この列で待っている人はいません",
  "not logged in": "ログインしていません",
  "tenant member only": "このお店のメンバーではありません",
  "tenant admin only": "お店の管理者だけができます",
  "seat already exists": "同じ名前の席があります",
  "invalid seat name": "席の名前を入れてください（64 バイトまで）",
  "invalid status": "その状態にはできません",
  "busy": "混み合っています。もう一度押してください",
  "network": "通信できません。電波の良いところでもう一度",
};
function msgOf(r) {
  const m = r && r.message;
  if (MESSAGES[m]) return MESSAGES[m];
  if (m && m.startsWith("http ")) return "サーバが起動中か混み合っています。少し待ってもう一度（" + m + "）";
  return m || "うまくいきませんでした";
}

function showMsg(id, text, kind) {
  const e = $(id);
  e.textContent = text || "";
  e.className = "msg" + (kind ? " " + kind : "");
}

function el(tag, attrs, ...children) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (k === "class") e.className = v;
    else if (k === "onclick") e.onclick = v;
    else if (k === "text") e.textContent = v;
    else e.setAttribute(k, v);
  }
  for (const c of children) if (c != null) e.append(c);
  return e;
}

// 中身が変わったときだけ描き直す（ボタンを押そうとした瞬間に消えないように）
function render(box, key, build) {
  if (box.dataset.key === key) return;
  box.dataset.key = key;
  box.innerHTML = "";
  build();
}

// サーバの時刻は naive な UTC の "YYYY-MM-DD HH:MM:SS"
function parseUtc(s) {
  return s ? new Date(s.replace(" ", "T") + "Z") : null;
}
function hhmm(s) {
  const d = parseUtc(s);
  return d ? d.toLocaleTimeString("ja-JP", { hour: "2-digit", minute: "2-digit" }) : "";
}
function minutesAgo(s) {
  const d = parseUtc(s);
  return d ? Math.max(0, Math.floor((Date.now() - d.getTime()) / 60000)) : 0;
}

// f を ms ごとに呼ぶ。前の回が終わっていなければ飛ばす（遅いサーバで回が重なって詰まらないように）。
// hiddenMs を渡すとタブが裏にある間はその間隔、渡さなければ裏では呼ばない
function poll(f, ms, hiddenMs) {
  let busy = false, last = 0;
  const tick = async () => {
    const wait = document.hidden ? hiddenMs : ms;
    if (busy || !wait || Date.now() - last < wait - 50) return;
    busy = true;
    try { await f(); } catch (e) {} finally { busy = false; last = Date.now(); }
  };
  setInterval(tick, 1000);
  document.addEventListener("visibilitychange", () => { if (!document.hidden) { last = 0; tick(); } });
}
