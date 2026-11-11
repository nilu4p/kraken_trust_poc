"""KRAKEN PoC notification sink — a tiny local stand-in for Slack / Mattermost.

Receives incoming-webhook POSTs (Slack / Mattermost JSON: ``text`` +
``attachments``) on ``/hooks/<channel>`` and shows them in a chat-like page on
``/``. Stdlib only, in-memory, for demonstrations: not a production service.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Deque, Dict
from urllib.parse import parse_qs, urlparse

MAX_BODY = 256 * 1024
MESSAGES: Deque[Dict[str, Any]] = deque(maxlen=1000)
LOCK = threading.Lock()
CHANNEL_RE = re.compile(r"^/hooks/([A-Za-z0-9_-]{1,40})$")

PAGE = """<!doctype html>
<html lang="fr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>KRAKEN · Notifications (PoC)</title>
<style>
:root{--bg:#0b1016;--panel:#111a23;--side:#0d141c;--line:#1f2d3b;--text:#e2e8f0;--muted:#7b8a9a;--accent:#00e5ff}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:14px/1.45 system-ui,-apple-system,Segoe UI,sans-serif;display:flex;height:100vh}
aside{width:270px;background:var(--side);border-right:1px solid var(--line);display:flex;flex-direction:column}
.brand{padding:16px;border-bottom:1px solid var(--line)}.brand h1{font-size:13px;letter-spacing:.2em;text-transform:uppercase;color:var(--accent);margin:0}
.brand p{color:var(--muted);font-size:12px;margin:4px 0 0}.chans{overflow-y:auto;padding:8px}
.chan{display:flex;align-items:center;gap:8px;padding:7px 8px;border-radius:4px;color:var(--muted);cursor:pointer}
.chan:hover{background:#15212d}.chan.active{background:#1b2a38;color:#fff}.chan .name{flex:1;min-width:0}
.chan .name small{display:block;font-size:11px;color:var(--muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.chan.unread .name b{color:#fff}.badge{background:#ff3b3b;color:#fff;border-radius:9px;font-size:11px;padding:0 6px;font-weight:700}
.count{font-size:11px;color:var(--muted)}main{flex:1;display:flex;flex-direction:column;min-width:0}
header{padding:10px 20px;border-bottom:1px solid var(--line);display:flex;gap:12px;align-items:center}
header .t{flex:1;min-width:0}header b{font-size:15px}header small{display:block;color:var(--muted)}
input{background:var(--panel);border:1px solid var(--line);color:var(--text);padding:6px 10px;border-radius:4px;width:220px}
button{background:none;border:1px solid var(--line);color:var(--muted);padding:5px 10px;border-radius:4px;cursor:pointer}
button:hover{color:#fff}#feed{flex:1;overflow-y:auto;padding:12px 20px}.msg{display:flex;gap:10px;padding:10px 0;border-bottom:1px solid #141e28}
.avatar{width:36px;height:36px;border-radius:6px;background:#12303a;display:flex;align-items:center;justify-content:center;font-size:18px;flex:none}
.who{font-weight:700}.when{color:var(--muted);font-size:12px;margin-left:6px}.tag{font-size:11px;color:var(--accent);margin-left:6px}
.text{white-space:pre-wrap;word-break:break-word}.att{margin-top:6px;border-left:4px solid #999;background:var(--panel);padding:10px 14px;border-radius:0 4px 4px 0;max-width:820px}
.pre{color:#cbd5e1;font-size:13px;margin-bottom:4px}.att .title{color:#5cc8ff;font-weight:700;text-decoration:none;font-size:15px}
.fields{display:grid;grid-template-columns:1fr 1fr;gap:8px 20px;margin-top:8px}.field.long{grid-column:1/-1}
.field .k{color:var(--muted);font-size:11px;text-transform:uppercase;letter-spacing:.04em}.field .v{white-space:pre-wrap;word-break:break-word}
.actions{display:flex;flex-wrap:wrap;gap:6px;margin-top:10px}.actions a{border:1px solid #2a5a74;color:#5cc8ff;text-decoration:none;padding:4px 10px;border-radius:4px;font-size:12px}
.actions a:hover{background:#12303a}.foot{color:var(--muted);font-size:11px;margin-top:8px}.empty{color:var(--muted);text-align:center;margin-top:20vh}
</style></head><body>
<aside><div class="brand"><h1>KRAKEN-Trust</h1><p>Serveur de notifications local (PoC)<br>imitation Slack / Mattermost</p></div><div class="chans" id="channels"></div></aside>
<main><header><div class="t"><b id="title"></b><small id="desc"></small></div>
<input id="search" placeholder="Rechercher (réf., navire, autorité…)"><span id="count" class="count"></span><button id="clear">Vider</button></header>
<div id="feed"></div></main>
<script>
const KNOWN = [
  ["alertes", "🚨", "Nouveaux incidents prioritaires et sévérités relevées"],
  ["escalades", "📡", "Saisines des autorités AEM, interventions et clôtures"],
  ["sla", "⏱", "Échéances de triage / résolution dépassées"],
  ["surveillance", "🎯", "Navires de la liste de surveillance (INN) dans la ZEE"],
  ["operations", "🛰", "Scans Sentinel-1, échecs, santé des sources"],
  ["securite", "🛡", "Événements de sécurité de la plateforme"],
];
const ALL = "__all__";
let channel = ALL, messages = [], query = "";
const seen = JSON.parse(sessionStorage.getItem("kraken-seen") || "{}");
const el = (tag, cls, text) => { const n = document.createElement(tag); if (cls) n.className = cls; if (text != null) n.textContent = text; return n; };
const meta = c => KNOWN.find(k => k[0] === c) || [c, "#", "Canal externe"];
const safeUrl = u => /^https?:\\/\\//.test(u || "");
// Slack mrkdwn *bold* with DOM nodes only (payloads are untrusted: never innerHTML).
function mrkdwn(text, cls) {
  const span = el("span", cls || "text");
  String(text || "").split(/(\\*[^*\\n]+\\*)/g).forEach(part => {
    if (/^\\*[^*]+\\*$/.test(part)) span.appendChild(el("strong", null, part.slice(1, -1)));
    else span.appendChild(document.createTextNode(part));
  });
  return span;
}
function haystack(m) { return JSON.stringify(m.payload || {}).toLowerCase(); }
function renderChannels() {
  const names = [...new Set([...KNOWN.map(k => k[0]), ...messages.map(m => m.channel)])];
  const list = document.getElementById("channels"); list.replaceChildren();
  const entries = [[ALL, "≡", "Tous les canaux"], ...names.map(meta)];
  entries.forEach(([name, icon, desc]) => {
    const own = name === ALL ? messages : messages.filter(m => m.channel === name);
    const unread = own.filter(m => m.received_at > (seen[name] || 0)).length;
    const row = el("div", "chan" + (name === channel ? " active" : "") + (unread ? " unread" : ""));
    row.appendChild(el("span", null, icon));
    const label = el("span", "name"); label.appendChild(el("b", null, name === ALL ? "Tous" : "# " + name)); label.appendChild(el("small", null, desc));
    row.appendChild(label);
    if (unread && name !== channel) row.appendChild(el("span", "badge", String(unread)));
    else row.appendChild(el("span", "count", own.length ? String(own.length) : ""));
    row.onclick = () => { channel = name; render(); };
    list.appendChild(row);
  });
}
function renderMessage(m, feed) {
  const p = m.payload || {}; const row = el("div", "msg");
  row.appendChild(el("div", "avatar", meta(m.channel)[1]));
  const body = el("div"); body.style.minWidth = "0"; body.style.flex = "1";
  const head = el("div"); head.appendChild(el("span", "who", p.username || "webhook"));
  const date = new Date(m.received_at * 1000);
  head.appendChild(el("span", "when", date.toLocaleString("fr-FR") + " · " + date.toISOString().slice(11, 16) + "Z"));
  if (channel === ALL) head.appendChild(el("span", "tag", "#" + m.channel));
  body.appendChild(head);
  if (!(p.attachments || []).length && p.text) body.appendChild(mrkdwn(p.text));
  (p.attachments || []).forEach(a => {
    const box = el("div", "att"); box.style.borderLeftColor = /^#[0-9a-fA-F]{6}$/.test(a.color || "") ? a.color : "#999";
    if (a.pretext) box.appendChild(mrkdwn(a.pretext, "pre"));
    if (a.title) {
      const t = el(safeUrl(a.title_link) ? "a" : "div", "title", a.title);
      if (safeUrl(a.title_link)) { t.href = a.title_link; t.target = "_blank"; t.rel = "noreferrer"; }
      box.appendChild(t);
    }
    if (a.text) { const d = el("div"); d.appendChild(mrkdwn(a.text)); box.appendChild(d); }
    if ((a.fields || []).length) {
      const grid = el("div", "fields");
      a.fields.forEach(f => { const c = el("div", "field" + (f.short === false ? " long" : "")); c.appendChild(el("div", "k", f.title)); c.appendChild(el("div", "v", f.value)); grid.appendChild(c); });
      box.appendChild(grid);
    }
    const actions = (a.actions || []).filter(x => safeUrl(x.url));
    if (actions.length) {
      const bar = el("div", "actions");
      actions.forEach(x => { const link = el("a", null, x.text || "Ouvrir"); link.href = x.url; link.target = "_blank"; link.rel = "noreferrer"; bar.appendChild(link); });
      box.appendChild(bar);
    }
    if (a.footer) box.appendChild(el("div", "foot", a.footer));
    body.appendChild(box);
  });
  row.appendChild(body); feed.appendChild(row);
}
function render() {
  const [name, , desc] = channel === ALL ? [ALL, "", "Flux complet de toutes les notifications"] : meta(channel);
  document.getElementById("title").textContent = channel === ALL ? "Tous les canaux" : "# " + name;
  document.getElementById("desc").textContent = desc;
  let shown = channel === ALL ? messages : messages.filter(m => m.channel === channel);
  if (query) shown = shown.filter(m => haystack(m).includes(query));
  document.getElementById("count").textContent = shown.length + " message(s)";
  const feed = document.getElementById("feed"); const atBottom = feed.scrollHeight - feed.scrollTop - feed.clientHeight < 80;
  feed.replaceChildren();
  if (!shown.length) feed.appendChild(el("p", "empty", query ? "Aucun message ne correspond." : "Aucune notification sur ce canal."));
  shown.forEach(m => renderMessage(m, feed));
  if (atBottom) feed.scrollTop = feed.scrollHeight;
  const latest = shown.length ? shown[shown.length - 1].received_at : 0;
  seen[channel] = Math.max(seen[channel] || 0, latest);
  if (channel === ALL) KNOWN.forEach(k => { seen[k[0]] = Math.max(seen[k[0]] || 0, latest); });
  sessionStorage.setItem("kraken-seen", JSON.stringify(seen));
  renderChannels();
}
async function load() { try { messages = await (await fetch("api/messages")).json(); render(); } catch (e) {} }
document.getElementById("search").oninput = e => { query = e.target.value.trim().toLowerCase(); render(); };
document.getElementById("clear").onclick = async () => {
  await fetch("api/clear" + (channel === ALL ? "" : "?channel=" + encodeURIComponent(channel)), { method: "POST" }); load();
};
load(); setInterval(load, 2000);
</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    server_version = "kraken-notif-sink"
    sys_version = ""

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'",
        )
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, data: Any) -> None:
        self._send(status, json.dumps(data, ensure_ascii=False).encode(), "application/json; charset=utf-8")

    def do_GET(self) -> None:  # noqa: N802
        if self.path in ("/", "/index.html"):
            self._send(200, PAGE.encode(), "text/html; charset=utf-8")
        elif self.path == "/api/messages":
            with LOCK:
                self._json(200, list(MESSAGES))
        elif self.path == "/health":
            self._json(200, {"status": "ok"})
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        if self.path.startswith("/api/clear"):
            target = parse_qs(urlparse(self.path).query).get("channel", [None])[0]
            with LOCK:
                kept = [m for m in MESSAGES if target and m["channel"] != target]
                MESSAGES.clear()
                MESSAGES.extend(kept)
            self._json(200, {"status": "cleared", "channel": target or "all"})
            return
        match = CHANNEL_RE.match(self.path)
        if not match:
            self._json(404, {"error": "use /hooks/<channel>"})
            return
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > MAX_BODY:
            self._json(413, {"error": "payload too large or empty"})
            return
        try:
            payload = json.loads(self.rfile.read(length))
            if not isinstance(payload, dict):
                raise ValueError
        except ValueError:
            self._json(400, {"error": "invalid JSON"})
            return
        with LOCK:
            MESSAGES.append({"channel": match.group(1), "received_at": time.time(), "payload": payload})
        self._json(200, {"ok": True})  # Slack answers "ok"; Mattermost 200 is enough

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"[notif-sink] {self.address_string()} {fmt % args}", flush=True)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8090"))
    print(f"[notif-sink] listening on :{port} — webhook URL: http://notif-sink:{port}/hooks/kraken", flush=True)
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()
