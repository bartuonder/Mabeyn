/*! Mabeyn chat widget. One script tag, no dependencies:
 *  <script src="https://API/widget.js" data-key="mbk_pub_..." async></script>
 *  or, for signed-in customers, data-session-url="/support/session" (the shop's own server opens
 *  the session with its secret key and returns {"token": "..."}).
 */
(function () {
  "use strict";
  var script = document.currentScript;
  if (!script || window.__mabeynWidget) return;
  window.__mabeynWidget = true;

  var data = script.dataset;
  var cfg = {
    api: (data.api || new URL(script.src, location.href).origin).replace(/\/$/, ""),
    key: data.key || "",
    sessionUrl: data.sessionUrl || "",
    title: data.title || "Support",
    greeting: data.greeting || "Hi! Ask me about orders, delivery, returns or anything else.",
    color: data.color || "#2563eb",
  };
  var STORE = "mabeyn-widget:" + (cfg.sessionUrl || cfg.key);
  var CITE = /\s*\[[^\[\]]+:[^\[\]]+\]/g;
  var POLL_MS = 4000;
  var POLL_LIMIT_MS = 30 * 60 * 1000;

  var state = load();
  var busy = false;
  var pollTimer = null;

  function load() {
    try {
      return JSON.parse(sessionStorage.getItem(STORE)) || {};
    } catch (e) {
      return {};
    }
  }
  function save() {
    try {
      sessionStorage.setItem(STORE, JSON.stringify(state));
    } catch (e) {}
  }

  async function openSession() {
    var response;
    if (cfg.sessionUrl) {
      response = await fetch(cfg.sessionUrl, { method: "POST", credentials: "same-origin" });
    } else {
      response = await fetch(cfg.api + "/sessions", {
        method: "POST",
        headers: { "X-API-Key": cfg.key, "Content-Type": "application/json" },
        body: "{}",
      });
    }
    if (!response.ok) throw new Error("session " + response.status);
    var body = await response.json();
    state.token = body.token;
    state.expiresAt = Date.now() + (body.expires_in || 900) * 1000 - 30000;
    save();
  }

  async function ensureSession() {
    if (!state.token || Date.now() > (state.expiresAt || 0)) {
      state.conversationId = state.conversationId || null;
      await openSession();
    }
  }

  async function call(path, options, retried) {
    await ensureSession();
    var headers = Object.assign({ Authorization: "Bearer " + state.token }, options.headers || {});
    var response = await fetch(cfg.api + path, Object.assign({}, options, { headers: headers }));
    if (response.status === 401 && !retried) {
      state.token = null;
      return call(path, options, true);
    }
    return response;
  }

  // ---- interface -------------------------------------------------------------------------
  var host = document.createElement("div");
  host.style.cssText = "position:fixed;right:20px;bottom:20px;z-index:2147483000;";
  var root = host.attachShadow({ mode: "open" });
  root.innerHTML =
    "<style>" +
    ":host{all:initial}*{box-sizing:border-box;font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif}" +
    ".bubble{width:56px;height:56px;border-radius:50%;border:0;background:" + cfg.color + ";color:#fff;cursor:pointer;box-shadow:0 6px 20px rgba(0,0,0,.25);font-size:24px}" +
    ".panel{position:absolute;right:0;bottom:70px;width:370px;max-width:calc(100vw - 32px);height:540px;max-height:calc(100vh - 110px);background:#fff;color:#111827;border-radius:14px;box-shadow:0 12px 40px rgba(0,0,0,.28);display:none;flex-direction:column;overflow:hidden}" +
    ".panel.open{display:flex}" +
    ".head{background:" + cfg.color + ";color:#fff;padding:12px 14px;display:flex;align-items:center;gap:8px}" +
    ".head h2{margin:0;font-size:15px;font-weight:600;flex:1}" +
    ".head button{background:transparent;border:0;color:#fff;cursor:pointer;font-size:13px;opacity:.9}" +
    ".log{flex:1;overflow-y:auto;padding:14px;display:flex;flex-direction:column;gap:10px;background:#f8fafc}" +
    ".msg{max-width:85%;padding:9px 12px;border-radius:12px;font-size:14px;line-height:1.4;white-space:pre-wrap;word-wrap:break-word}" +
    ".user{align-self:flex-end;background:" + cfg.color + ";color:#fff;border-bottom-right-radius:4px}" +
    ".bot{align-self:flex-start;background:#fff;border:1px solid #e5e7eb;border-bottom-left-radius:4px}" +
    ".note{align-self:center;font-size:12px;color:#6b7280;background:#eef2ff;border-radius:999px;padding:4px 10px;text-align:center}" +
    ".src{margin-top:6px;font-size:11px;color:#6b7280}" +
    ".src span{display:inline-block;background:#f1f5f9;border-radius:6px;padding:2px 6px;margin:2px 4px 0 0}" +
    ".dots::after{content:'...';animation:b 1s steps(4) infinite}@keyframes b{50%{opacity:.3}}" +
    "form{display:flex;gap:8px;padding:10px;border-top:1px solid #e5e7eb;background:#fff}" +
    "input{flex:1;border:1px solid #d1d5db;border-radius:10px;padding:10px 12px;font-size:14px;outline:none}" +
    "input:focus{border-color:" + cfg.color + "}" +
    "form button{border:0;background:" + cfg.color + ";color:#fff;border-radius:10px;padding:0 14px;font-size:14px;cursor:pointer}" +
    "form button:disabled{opacity:.5;cursor:default}" +
    "</style>" +
    '<div class="panel" role="dialog" aria-label="' + cfg.title + '">' +
    '<div class="head"><h2></h2><button type="button" data-new title="Start a new conversation">New chat</button>' +
    '<button type="button" data-close aria-label="Close">✕</button></div>' +
    '<div class="log" role="log" aria-live="polite"></div>' +
    '<form><input type="text" maxlength="2000" placeholder="Type your message" aria-label="Message" autocomplete="off">' +
    '<button type="submit">Send</button></form></div>' +
    '<button class="bubble" type="button" aria-label="Open chat">\u{1F4AC}</button>';

  var panel = root.querySelector(".panel");
  var log = root.querySelector(".log");
  var form = root.querySelector("form");
  var input = root.querySelector("input");
  var sendButton = form.querySelector("button");
  root.querySelector("h2").textContent = cfg.title;

  function add(kind, text) {
    var node = document.createElement("div");
    node.className = "msg " + kind;
    node.textContent = text;
    log.appendChild(node);
    log.scrollTop = log.scrollHeight;
    return node;
  }
  function note(text) {
    var node = document.createElement("div");
    node.className = "note";
    node.textContent = text;
    log.appendChild(node);
    log.scrollTop = log.scrollHeight;
    return node;
  }
  function clean(text) {
    return (text || "").replace(CITE, "").trim();
  }
  // Show only the sources the answer cites, each as "document > the cited heading".
  function sources(node, citations, answer) {
    var cited = (citations || []).filter(function (c) {
      return answer.indexOf("[" + c.cite + "]") !== -1;
    });
    if (!cited.length) return;
    var box = document.createElement("div");
    box.className = "src";
    var seen = {};
    cited.forEach(function (c) {
      var heading = c.cite.slice(c.cite.indexOf(":") + 1);
      var label = c.document_title + (heading && heading !== c.document_title ? " \u203A " + heading : "");
      if (seen[label]) return;
      seen[label] = true;
      var chip = document.createElement("span");
      chip.textContent = label;
      box.appendChild(chip);
    });
    node.appendChild(box);
  }
  function setBusy(value) {
    busy = value;
    sendButton.disabled = value;
    input.disabled = value;
    if (!value) input.focus();
  }

  // ---- streaming -----------------------------------------------------------------------
  function parseEvent(block) {
    var name = "message";
    var payload = "";
    block.split("\n").forEach(function (line) {
      if (line.indexOf("event:") === 0) name = line.slice(6).trim();
      else if (line.indexOf("data:") === 0) payload += line.slice(5).trim();
    });
    try {
      return { name: name, data: payload ? JSON.parse(payload) : {} };
    } catch (e) {
      return { name: name, data: {} };
    }
  }

  async function send(text) {
    setBusy(true);
    add("user", text);
    var bubble = add("bot", "");
    var body = document.createElement("span");
    bubble.appendChild(body);
    bubble.classList.add("dots");
    var shown = "";
    var pending = false;
    var citations = [];
    try {
      var response = await call("/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(
          state.conversationId ? { message: text, conversation_id: state.conversationId } : { message: text }
        ),
      });
      if (response.status === 429) throw new Error("Too many messages. Please wait a moment.");
      if (!response.ok) throw new Error("The assistant is unavailable right now.");
      var reader = response.body.getReader();
      var decoder = new TextDecoder();
      var buffer = "";
      for (;;) {
        var step = await reader.read();
        if (step.done) break;
        buffer += decoder.decode(step.value, { stream: true });
        var parts = buffer.split("\n\n");
        buffer = parts.pop();
        parts.forEach(function (block) {
          var event = parseEvent(block);
          if (event.name === "meta") {
            state.conversationId = event.data.conversation_id;
            save();
          } else if (event.name === "token") {
            shown += event.data.text;
            bubble.classList.remove("dots");
            body.textContent = clean(shown);
          } else if (event.name === "retract") {
            shown = "";
            body.textContent = "";
            bubble.classList.add("dots");
          } else if (event.name === "citations") {
            citations = event.data;
          } else if (event.name === "approval") {
            pending = true;
          } else if (event.name === "escalated") {
            note("A human colleague will continue this conversation.");
          } else if (event.name === "error") {
            throw new Error(event.data.message || "Something went wrong.");
          } else if (event.name === "done") {
            bubble.classList.remove("dots");
            var raw = event.data.answer || shown;
            if (event.data.answer) body.textContent = clean(event.data.answer);
            sources(bubble, citations, raw);
            if (!body.textContent) bubble.remove();
          }
        });
      }
      log.scrollTop = log.scrollHeight;
      if (pending) waitForDecision();
    } catch (error) {
      bubble.classList.remove("dots");
      bubble.remove();
      note(error.message || "Something went wrong.");
    } finally {
      setBusy(false);
    }
  }

  // An approval is decided by a human later; the answer is stored in the conversation.
  function waitForDecision() {
    note("Waiting for a human to review this request.");
    var known = 0;
    var started = Date.now();
    clearTimeout(pollTimer);
    async function poll() {
      if (!state.conversationId || Date.now() - started > POLL_LIMIT_MS) return;
      try {
        var response = await call("/chat/" + state.conversationId, { method: "GET" });
        if (response.ok) {
          var detail = await response.json();
          var replies = detail.messages.filter(function (m) { return m.role === "assistant"; });
          if (!known) known = replies.length;
          else if (replies.length > known) {
            replies.slice(known).forEach(function (m) { add("bot", clean(m.content)); });
            return;
          }
        }
      } catch (e) {}
      pollTimer = setTimeout(poll, POLL_MS);
    }
    poll();
  }

  async function restore() {
    if (!state.conversationId) {
      add("bot", cfg.greeting);
      return;
    }
    try {
      var response = await call("/chat/" + state.conversationId, { method: "GET" });
      if (!response.ok) throw new Error("gone");
      var detail = await response.json();
      detail.messages.forEach(function (m) {
        add(m.role === "user" ? "user" : "bot", clean(m.content));
      });
      if (!detail.messages.length) add("bot", cfg.greeting);
    } catch (e) {
      state.conversationId = null;
      save();
      add("bot", cfg.greeting);
    }
  }

  async function newChat() {
    clearTimeout(pollTimer);
    var id = state.conversationId;
    state.conversationId = null;
    save();
    log.textContent = "";
    add("bot", cfg.greeting);
    if (id) {
      try {
        await call("/chat/" + id + "/close", { method: "POST" });
      } catch (e) {}
    }
  }

  var restored = false;
  function toggle(open) {
    panel.classList.toggle("open", open);
    if (open && !restored) {
      restored = true;
      restore();
    }
    if (open) input.focus();
  }

  root.querySelector(".bubble").addEventListener("click", function () {
    toggle(!panel.classList.contains("open"));
  });
  root.querySelector("[data-close]").addEventListener("click", function () { toggle(false); });
  root.querySelector("[data-new]").addEventListener("click", newChat);
  form.addEventListener("submit", function (event) {
    event.preventDefault();
    var text = input.value.trim();
    if (!text || busy) return;
    input.value = "";
    send(text);
  });

  document.body.appendChild(host);
})();
