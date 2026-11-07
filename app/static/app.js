/* AcmeCloud support chat: talks to the FastAPI backend in this project. */
(() => {
  "use strict";

  const API = window.ACME_API_BASE || "";   // same origin by default
  const DESKS = {
    billing_agent:    { name: "Billing",      key: "billing" },
    technical_agent:  { name: "Technical",    key: "technical" },
    general_agent:    { name: "General",      key: "general" },
    human_escalation: { name: "Support team", key: "human" },
  };
  const INTENTS = {
    billing: "a billing question",
    technical: "a technical problem",
    general: "a general question",
    escalate: "a request for a person",
  };

  const $ = (id) => document.getElementById(id);
  const els = {
    log: $("log"), form: $("composer"), input: $("input"), send: $("send"),
    customer: $("customer"), customerWrap: $("customer-wrap"),
    newChat: $("new-chat"), toggle: $("toggle-panel"), closePanel: $("close-panel"),
    panelBody: $("panel-body"),
  };
  const state = { customers: [], customerId: null, sessionId: null, busy: false };
  const narrow = window.matchMedia("(max-width: 900px)");

  // localStorage can be blocked (private mode, sandboxed frames): never let it break the chat.
  const store = {
    get(k) { try { return localStorage.getItem(k); } catch { return null; } },
    set(k, v) { try { localStorage.setItem(k, v); } catch { /* ignore */ } },
    del(k) { try { localStorage.removeItem(k); } catch { /* ignore */ } },
  };
  const sessionKey = (customerId) => `acme:session:${customerId}`;
  const detailsKey = (sessionId) => `acme:details:${sessionId}`;
  const replyData = new WeakMap();   // article element -> API response, for the details panel

  function loadDetails(sessionId) {
    try { return JSON.parse(store.get(detailsKey(sessionId)) || "[]"); } catch { return []; }
  }
  function saveDetails(sessionId, data) {
    const list = loadDetails(sessionId);
    list.push(data);
    store.set(detailsKey(sessionId), JSON.stringify(list.slice(-50)));
  }

  // ---------- helpers ----------

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text != null) node.textContent = text;
    return node;
  }

  function scrollDown() { els.log.scrollTop = els.log.scrollHeight; }

  function currentCustomer() { return state.customers.find((c) => c.id === state.customerId); }

  function prettySource(source) { return source.split(" > ").join(" › "); }

  // ---------- safe markdown ----------
  // Model output is untrusted: everything is HTML-escaped first, then a few patterns are formatted.

  function escapeHtml(s) {
    return s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }

  function inline(text) {
    return text
      .replace(/`([^`]+)`/g, "<code>$1</code>")
      .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
      // [Refund policy &gt; Annual plans]  ->  citation chip showing the section name
      .replace(/\[([^\[\]\n]+? &gt; [^\[\]\n]+?)\]/g, (_, src) => {
        const parts = src.split(" &gt; ");
        return `<span class="cite" title="${parts.join(" › ")}">${parts[parts.length - 1]}</span>`;
      });
  }

  function renderMarkdown(raw) {
    const lines = escapeHtml(String(raw).trim()).split("\n");
    const out = [];
    let para = [], list = null, table = [];

    const flushPara = () => { if (para.length) { out.push(`<p>${inline(para.join("<br>"))}</p>`); para = []; } };
    const flushList = () => {
      if (list) { out.push(`<${list.type}>${list.items.map((i) => `<li>${inline(i)}</li>`).join("")}</${list.type}>`); list = null; }
    };
    const flushTable = () => {
      if (!table.length) return;
      const rows = table
        .filter((r) => !(/^[|\s:-]+$/.test(r) && r.includes("-")))
        .map((r) => r.replace(/^\||\|$/g, "").split("|").map((c) => inline(c.trim())));
      const [head, ...body] = rows;
      out.push(`<div class="table-wrap"><table><thead><tr>${head.map((c) => `<th>${c}</th>`).join("")}</tr></thead>` +
               `<tbody>${body.map((r) => `<tr>${r.map((c) => `<td>${c}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`);
      table = [];
    };

    for (const line of lines) {
      const t = line.trim();
      let m;
      if (t.startsWith("|")) { flushPara(); flushList(); table.push(t); continue; }
      flushTable();
      if (!t) { flushPara(); flushList(); continue; }
      if ((m = t.match(/^[-*•]\s+(.*)$/))) {
        flushPara();
        if (!list || list.type !== "ul") { flushList(); list = { type: "ul", items: [] }; }
        list.items.push(m[1]); continue;
      }
      if ((m = t.match(/^\d+[.)]\s+(.*)$/))) {
        flushPara();
        if (!list || list.type !== "ol") { flushList(); list = { type: "ol", items: [] }; }
        list.items.push(m[1]); continue;
      }
      if ((m = t.match(/^#{1,6}\s+(.*)$/))) { flushPara(); flushList(); out.push(`<p class="md-heading">${inline(m[1])}</p>`); continue; }
      flushList();
      para.push(t);
    }
    flushPara(); flushList(); flushTable();
    return out.join("");
  }

  // ---------- messages ----------

  function addCustomerMessage(text) {
    const article = el("article", "msg msg-customer");
    article.append(el("div", "bubble", text));
    els.log.append(article);
    scrollDown();
  }

  function ticketIcon() {
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("viewBox", "0 0 16 16");
    svg.setAttribute("aria-hidden", "true");
    svg.innerHTML = '<path d="M2 5.5V4a1 1 0 011-1h10a1 1 0 011 1v1.5a1.5 1.5 0 000 3V12a1 1 0 01-1 1H3a1 1 0 01-1-1V8.5a1.5 1.5 0 000-3z" fill="none" stroke="currentColor" stroke-width="1.3"/><path d="M10 3.5v9" stroke="currentColor" stroke-width="1.3" stroke-dasharray="1.5 1.5"/>';
    return svg;
  }

  function buildMeta(data, hasCitations) {
    const meta = el("div", "msg-meta");
    if (data.ticket_id) {
      const t = el("span", "meta-ticket");
      t.append(ticketIcon(), document.createTextNode(`Ticket ${data.ticket_id} opened`));
      meta.append(t);
    }
    if (data.needs_human) {
      meta.append(el("div", "handoff-note", "Human agents reply Monday to Friday, 9:00 to 18:00 PKT."));
    }
    // Citation chips already show the sources inline; list them only when the reply didn't cite any.
    if (data.sources && data.sources.length && !hasCitations) {
      meta.append(el("div", "meta-sources", `Based on: ${data.sources.map(prettySource).join("; ")}`));
    }
    return meta.childElementCount ? meta : null;
  }

  function addBotMessage(data, { isNew = true } = {}) {
    const desk = DESKS[data.assigned_agent];
    const article = el("article", `msg msg-bot ${desk ? `desk-${desk.key}` : "desk-none"}${isNew ? " is-new" : ""}`);
    if (desk) {
      article.append(el("div", "desk-tab", desk.name));
      article.setAttribute("aria-label", `Reply from the ${desk.name} desk`);
    }
    const bubble = el("div", "bubble");
    const body = el("div", "msg-text");
    body.innerHTML = renderMarkdown(data.response || "");
    bubble.append(body);
    const meta = buildMeta(data, Boolean(body.querySelector(".cite")));
    if (meta) bubble.append(meta);
    article.append(bubble);
    if (data.assigned_agent) {   // replies with routing details can be opened in the panel
      replyData.set(article, data);
      article.classList.add("has-details");
      bubble.tabIndex = 0;
      bubble.setAttribute("aria-label", "Show how this reply was made");
      const open = () => { selectReply(article); setPanel(true); };
      bubble.addEventListener("click", (e) => { if (!window.getSelection().toString()) open(); });
      bubble.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); open(); } });
    }
    els.log.append(article);
    scrollDown();
    return article;
  }

  function selectReply(article) {
    for (const a of els.log.querySelectorAll(".msg-bot.is-selected")) a.classList.remove("is-selected");
    article.classList.add("is-selected");
    const all = [...els.log.querySelectorAll(".msg-bot.has-details")];
    renderPanel(replyData.get(article), all[all.length - 1] === article);
  }

  function addTyping() {
    const article = el("article", "msg msg-bot desk-none typing");
    article.setAttribute("aria-label", "The assistant is writing a reply");
    const bubble = el("div", "bubble");
    for (let i = 0; i < 3; i++) bubble.append(el("span", "dot"));
    article.append(bubble);
    els.log.append(article);
    scrollDown();
    return article;
  }

  function errorMessage(err) {
    if (err.status === 503) return "The assistant isn't connected to its AI model. Add GROQ_API_KEY to the .env file and restart the server.";
    if (err.status === 404) return "This customer doesn't exist. Pick another customer at the top.";
    if (err.status === 422) return "That message couldn't be sent. Keep it under 4,000 characters.";
    if (err.status) return "The server hit an error while answering. Try again; if it keeps happening, check the server log.";
    return "Can't reach the server. Check that it's running, then try again.";
  }

  function addError(err, text) {
    const box = el("div", "msg-error");
    box.setAttribute("role", "alert");
    box.append(el("span", null, errorMessage(err)));
    const retry = el("button", "btn-quiet", "Try again");
    retry.type = "button";
    retry.addEventListener("click", () => { box.remove(); request(text); });
    box.append(retry);
    els.log.append(box);
    scrollDown();
  }

  // ---------- details panel ----------

  function describeTool(call) {
    const a = call.args || {};
    switch (call.name) {
      case "search_knowledge_base": return `Searched help articles for “${a.query || ""}”`;
      case "get_order_payments": return `Checked the payments on ${a.order_id || "an order"}`;
      case "get_customer_details": return "Looked up the customer's account, orders and tickets";
      case "create_support_ticket": return `Opened a ${a.priority ? `${a.priority.toLowerCase()}-priority ` : ""}${a.category || "support"} ticket`;
      default: return `Used ${call.name}`;
    }
  }

  function codeLine(call) {
    // Function name on the first line, one argument per line below it.
    const args = Object.entries(call.args || {}).map(([k, v]) => `  ${k}: ${JSON.stringify(v)}`);
    return [call.name, ...args].join("\n");
  }

  function showPanelMessage(text) {
    els.panelBody.replaceChildren(el("p", "panel-empty", text));
  }

  function renderPanel(data, isLatest = true) {
    const desk = DESKS[data.assigned_agent];
    const frag = document.createDocumentFragment();

    const dl = el("dl", "route");
    dl.append(el("dt", null, "Answered by"));
    const dd = el("dd");
    const label = !desk ? "Unknown" : desk.key === "human" ? "Support team" : `${desk.name} desk`;
    const pill = el("span", `desk-pill ${desk ? `desk-${desk.key}` : ""}`, label);
    dd.append(pill);
    dl.append(dd);
    if (typeof data.confidence === "number") {
      dl.append(el("dt", null, "Router confidence"));
      const conf = el("dd", desk ? `desk-${desk.key}` : "");
      const meter = el("span", "meter");
      const fill = el("span");
      fill.style.width = `${Math.round(data.confidence * 100)}%`;
      meter.append(fill);
      conf.append(meter, document.createTextNode(`${Math.round(data.confidence * 100)}%`));
      dl.append(conf);
    }
    frag.append(dl);

    frag.append(el("h3", null, "Steps"));
    const steps = el("ol", "steps");
    const step = (text, code) => {
      const li = el("li", null, text);
      if (code) li.append(el("span", "step-code", code));
      steps.append(li);
    };
    if (data.intent) step(`The router read the message as ${INTENTS[data.intent] || data.intent}.`);
    if (data.assigned_agent === "human_escalation") {
      step(`Handed over to the support team${data.ticket_id ? ` with escalated ticket ${data.ticket_id}` : ""}.`);
    } else {
      for (const call of data.tools_used || []) step(describeTool(call), codeLine(call));
      if (desk) step(`The ${desk.name} desk wrote the reply.`);
    }
    frag.append(steps);

    if (data.sources && data.sources.length) {
      frag.append(el("h3", null, "Help articles used"));
      const ul = el("ul", "sources-list");
      for (const src of data.sources) {
        const parts = src.split(" > ");
        const li = el("li");
        li.append(el("span", "source-doc", parts[0]), document.createTextNode(parts.slice(1).join(" › ") || parts[0]));
        ul.append(li);
      }
      frag.append(ul);
    }

    frag.append(el("p", "panel-note", isLatest
      ? "Showing the latest reply. Select any earlier reply to see how it was made."
      : "Showing an earlier reply. Select another reply to switch."));
    els.panelBody.replaceChildren(frag);
  }

  function setPanel(open) {
    document.body.classList.toggle("panel-open", open);
    document.body.classList.toggle("panel-closed", !open);
    els.toggle.setAttribute("aria-expanded", String(open));
    els.toggle.textContent = narrow.matches ? "Details" : open ? "Hide details" : "Show details";
  }

  // ---------- conversation ----------

  function suggestions(customer) {
    const list = [];
    if (customer && customer.orders.length) list.push(`I was charged twice for order ${customer.orders[0]}`);
    list.push("Can I get a refund on my yearly plan?", "My API keeps returning error 401",
              "What are your support hours?", "I'd like to talk to a person");
    return list;
  }

  function renderEmptyState() {
    const customer = currentCustomer();
    const first = customer ? customer.name.split(" ")[0] : null;
    const wrap = el("div", "empty");
    wrap.append(el("h1", "empty-title", first ? `Hi ${first}, what can we help with?` : "What can we help with?"));
    wrap.append(el("p", "empty-text",
      "Ask about billing, your orders, your account or the API. If you'd rather talk to a person, just say so."));
    const list = el("div", "suggestions");
    for (const text of suggestions(customer)) {
      const b = el("button", "suggestion", text);
      b.type = "button";
      b.addEventListener("click", () => sendMessage(text));
      list.append(b);
    }
    wrap.append(list);
    els.log.append(wrap);
  }

  async function openConversation() {
    els.log.replaceChildren();
    showPanelMessage("Send a message to see which desk answered, what it checked and which help articles it used.");
    state.sessionId = store.get(sessionKey(state.customerId));
    if (state.sessionId) {
      try {
        const url = `${API}/api/v1/sessions/${encodeURIComponent(state.sessionId)}/messages` +
                    `?customer_id=${encodeURIComponent(state.customerId)}`;
        const res = await fetch(url);
        if (res.ok) {
          const data = await res.json();
          if (data.messages.length) {
            const saved = loadDetails(state.sessionId);
            els.log.append(el("p", "history-note", "Earlier in this conversation"));
            let last = null;
            for (const m of data.messages) {
              if (m.role === "customer") { addCustomerMessage(m.content); continue; }
              const details = saved.find((d) => d.response === m.content);
              const article = addBotMessage(details || { response: m.content }, { isNew: false });
              if (details) last = article;
            }
            if (last) selectReply(last);
            else showPanelMessage("Details for these replies aren't saved in this browser. They appear for new messages.");
            return;
          }
        }
      } catch { /* fall through to a fresh start */ }
    }
    renderEmptyState();
  }

  function setBusy(busy) {
    state.busy = busy;
    els.send.disabled = busy;
  }

  async function request(text) {
    setBusy(true);
    const typing = addTyping();
    try {
      const res = await fetch(`${API}/api/v1/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: text, customer_id: state.customerId, session_id: state.sessionId || undefined }),
      });
      let data = null;
      try { data = await res.json(); } catch { /* non-JSON error page */ }
      if (!res.ok) throw Object.assign(new Error("request failed"), { status: res.status, detail: data && data.detail });
      typing.remove();
      state.sessionId = data.session_id;
      store.set(sessionKey(state.customerId), data.session_id);
      saveDetails(data.session_id, data);
      selectReply(addBotMessage(data));
    } catch (err) {
      typing.remove();
      addError(err, text);
    } finally {
      setBusy(false);
    }
  }

  function sendMessage(raw) {
    const text = raw.trim();
    if (!text || state.busy) return;
    const empty = els.log.querySelector(".empty");
    if (empty) empty.remove();
    addCustomerMessage(text);
    request(text);
  }

  async function loadCustomers() {
    try {
      const res = await fetch(`${API}/api/v1/demo/customers`);
      if (!res.ok) throw new Error(String(res.status));
      state.customers = await res.json();
    } catch {
      state.customers = [];
    }
    if (!state.customers.length) {   // demo mode off: take ?customer=... from the URL
      els.customerWrap.hidden = true;
      state.customerId = new URLSearchParams(location.search).get("customer") || "CUS-1001";
      return;
    }
    for (const c of state.customers) {
      const option = el("option", null, `${c.name} (${c.plan})`);
      option.value = c.id;
      els.customer.append(option);
    }
    const saved = store.get("acme:customer");
    state.customerId = state.customers.some((c) => c.id === saved) ? saved : state.customers[0].id;
    els.customer.value = state.customerId;
  }

  // ---------- events ----------

  els.form.addEventListener("submit", (e) => {
    e.preventDefault();
    if (state.busy) return;
    const text = els.input.value;
    els.input.value = "";
    els.input.style.height = "";
    sendMessage(text);
  });

  els.input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
      e.preventDefault();
      els.form.requestSubmit();
    }
  });

  els.input.addEventListener("input", () => {
    els.input.style.height = "auto";
    els.input.style.height = `${Math.min(els.input.scrollHeight, 160)}px`;
  });

  els.customer.addEventListener("change", () => {
    state.customerId = els.customer.value;
    store.set("acme:customer", state.customerId);
    openConversation();
  });

  els.newChat.addEventListener("click", () => {
    if (state.sessionId) store.del(detailsKey(state.sessionId));
    store.del(sessionKey(state.customerId));
    openConversation();
    els.input.focus();
  });

  narrow.addEventListener("change", () => setPanel(!narrow.matches));
  els.toggle.addEventListener("click", () => setPanel(!document.body.classList.contains("panel-open")));
  els.closePanel.addEventListener("click", () => setPanel(false));
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && narrow.matches && document.body.classList.contains("panel-open")) setPanel(false);
  });

  // ---------- start ----------

  if (narrow.matches) els.input.placeholder = "Ask a question";
  setPanel(!narrow.matches);
  loadCustomers().then(openConversation).then(() => els.input.focus());
})();
