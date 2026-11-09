/* AcmeCloud support chat: talks to the FastAPI backend in this project. */
(() => {
  "use strict";

  const API = window.ACME_API_BASE || "";   // same origin by default
  const DESKS = {
    billing_agent:    { name: "Billing",      key: "billing" },
    technical_agent:  { name: "Technical",    key: "technical" },
    general_agent:    { name: "General",      key: "general" },
    human_escalation: { name: "Support team", key: "human" },
    guardrail:        { name: "Safety",       key: "guard" },
  };
  const MASKED_WORDS = {
    card_masked: "card number", cnic_masked: "CNIC number", password_masked: "password",
    cvv_masked: "card security code", iban_masked: "bank account number",
  };
  const INTENTS = {
    billing: "a billing question",
    technical: "a technical problem",
    general: "a general question",
    escalate: "a request for a person",
    off_topic: "an off-topic request",
    unsafe: "a request it can't help with",
  };

  const $ = (id) => document.getElementById(id);
  const els = {
    log: $("log"), form: $("composer"), input: $("input"), send: $("send"),
    chatView: $("chat-view"), chatActions: $("chat-actions"), accountName: $("account-name"), signOut: $("sign-out"),
    loginView: $("login-view"), loginForm: $("login-form"), email: $("email"), password: $("password"),
    emailError: $("email-error"), passwordError: $("password-error"), loginError: $("login-error"),
    loginNotice: $("login-notice"), loginSubmit: $("login-submit"),
    demoAccounts: $("demo-accounts"), demoList: $("demo-list"), demoPassword: $("demo-password"),
    signinPanel: $("signin-panel"), signupPanel: $("signup-panel"), toSignupLine: $("to-signup-line"),
    toSignup: $("to-signup"), toSignin: $("to-signin"), signupForm: $("signup-form"),
    suName: $("su-name"), suEmail: $("su-email"), suPassword: $("su-password"), suConfirm: $("su-confirm"),
    suNameError: $("su-name-error"), suEmailError: $("su-email-error"),
    suPasswordError: $("su-password-error"), suConfirmError: $("su-confirm-error"),
    signupError: $("signup-error"), signupSubmit: $("signup-submit"),
    newChat: $("new-chat"), toggle: $("toggle-panel"), closePanel: $("close-panel"),
    panelBody: $("panel-body"),
  };
  const state = { account: null, customerId: null, sessionId: null, busy: false, demoLoaded: false, optionsLoaded: false };
  const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
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

  function currentCustomer() { return state.account; }

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

  function buildMeta(data) {
    const meta = el("div", "msg-meta");
    if (data.ticket_id) {
      const t = el("span", "meta-ticket");
      t.append(ticketIcon(), document.createTextNode(`Ticket ${data.ticket_id} opened`));
      meta.append(t);
    }
    if (data.needs_human) {
      meta.append(el("div", "handoff-note", "Human agents reply Monday to Friday, 9:00 to 18:00 PKT."));
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
    const meta = buildMeta(data);
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

  function showMasked(data) {
    // Show the customer exactly what was sent and saved, and say what was hidden.
    const bubbles = els.log.querySelectorAll(".msg-customer");
    const article = bubbles[bubbles.length - 1];
    if (!article) return;
    article.querySelector(".bubble").textContent = data.message_masked;
    const hidden = (data.guardrails || []).map((g) => MASKED_WORDS[g.kind]).filter(Boolean);
    if (hidden.length) article.append(el("p", "masked-note", `Hidden before sending, for your safety: ${hidden.join(", ")}.`));
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
    if (err.status === 422) return "That message couldn't be sent. Keep it under 4,000 characters.";
    if (err.status === 429 && err.detail) return err.detail;
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
    const label = !desk ? "Unknown" : desk.key === "human" ? "Support team"
      : desk.key === "guard" ? "Safety check" : `${desk.name} desk`;
    const pill = el("span", `desk-pill ${desk ? `desk-${desk.key}` : ""}`, label);
    dd.append(pill);
    dl.append(dd);
    if (typeof data.confidence === "number" && data.intent !== "blocked") {
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
    if (data.intent === "blocked") {
      step("The safety check stopped the message before it reached the AI.");
    } else if (data.intent) {
      step(`The router read the message as ${INTENTS[data.intent] || data.intent}.`);
    }
    if (data.assigned_agent === "guardrail") {
      step("Answered with a standard reply. No agent or tool was used.");
    } else if (data.assigned_agent === "human_escalation") {
      step(`Handed over to the support team${data.ticket_id ? ` with escalated ticket ${data.ticket_id}` : ""}.`);
    } else {
      for (const call of data.tools_used || []) step(describeTool(call), codeLine(call));
      if (desk) step(`The ${desk.name} desk wrote the reply.`);
    }
    frag.append(steps);

    const lookedUp = data.retrieved || data.sources || [];
    if (lookedUp.length) {
      frag.append(el("h3", null, "Help articles looked up"));
      const ul = el("ul", "sources-list");
      for (const src of lookedUp) {
        const parts = src.split(" > ");
        const li = el("li");
        li.append(el("span", "source-doc", parts[0]), document.createTextNode(parts.slice(1).join(" › ") || parts[0]));
        if ((data.sources || []).includes(src)) li.append(el("span", "source-cited", " (cited)"));
        ul.append(li);
      }
      frag.append(ul);
    }

    if (data.guardrails && data.guardrails.length) {
      frag.append(el("h3", null, "Safety checks"));
      const ul = el("ul", "guard-list");
      for (const g of data.guardrails) ul.append(el("li", null, g.message));
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
        const res = await fetch(`${API}/api/v1/sessions/${encodeURIComponent(state.sessionId)}/messages`);
        if (res.status === 401) { showLogin("Your session has ended. Sign in again to continue."); return; }
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
        body: JSON.stringify({ message: text, session_id: state.sessionId || undefined }),
      });
      if (res.status === 401) {
        typing.remove();
        showLogin("Your session has ended. Sign in again and your message will still be in the box.");
        els.input.value = text;
        return;
      }
      let data = null;
      try { data = await res.json(); } catch { /* non-JSON error page */ }
      if (!res.ok) throw Object.assign(new Error("request failed"), { status: res.status, detail: data && data.detail });
      typing.remove();
      state.sessionId = data.session_id;
      store.set(sessionKey(state.customerId), data.session_id);
      if (data.message_masked) showMasked(data);
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

  // ---------- sign-in ----------

  function showChat(account) {
    state.account = account;
    state.customerId = account.id;
    els.accountName.textContent = `${account.name} (${account.plan})`;
    els.loginView.hidden = true;
    els.chatView.hidden = false;
    els.chatActions.hidden = false;
    document.body.classList.remove("is-loading");
    openConversation().then(() => els.input.focus());
  }

  function showLogin(notice) {
    state.account = null;
    state.customerId = null;
    state.sessionId = null;
    els.log.replaceChildren();
    els.chatView.hidden = true;
    els.chatActions.hidden = true;
    els.loginView.hidden = false;
    document.body.classList.remove("is-loading");
    els.loginNotice.hidden = !notice;
    els.loginNotice.textContent = notice || "";
    clearLoginErrors();
    els.password.value = "";
    showSigninPanel();
    loadDemoAccounts();
    loadAuthOptions();
  }

  function showSigninPanel() {
    els.signupPanel.hidden = true;
    els.signinPanel.hidden = false;
    (els.email.value ? els.password : els.email).focus();
  }

  function showSignupPanel() {
    els.signinPanel.hidden = true;
    els.signupPanel.hidden = false;
    clearSignupErrors();
    if (!els.suEmail.value && els.email.value) els.suEmail.value = els.email.value.trim();
    els.suName.focus();
  }

  function clearLoginErrors() {
    for (const [input, msg] of [[els.email, els.emailError], [els.password, els.passwordError]]) {
      input.removeAttribute("aria-invalid");
      msg.hidden = true;
    }
    els.loginError.hidden = true;
  }

  function fieldError(input, msg, text) {
    input.setAttribute("aria-invalid", "true");
    input.setAttribute("aria-describedby", msg.id);
    msg.textContent = text;
    msg.hidden = false;
  }

  function validateLogin() {
    clearLoginErrors();
    const email = els.email.value.trim();
    let ok = true;
    if (!email) { fieldError(els.email, els.emailError, "Enter your email address."); ok = false; }
    else if (!EMAIL_RE.test(email)) { fieldError(els.email, els.emailError, "Enter an email address like name@example.com."); ok = false; }
    if (!els.password.value) { fieldError(els.password, els.passwordError, "Enter your password."); ok = false; }
    if (!ok) (els.email.getAttribute("aria-invalid") ? els.email : els.password).focus();
    return ok;
  }

  async function signIn(email, password) {
    els.loginNotice.hidden = true;           // an old "signed out" or "session ended" note no longer applies
    els.loginError.hidden = true;
    els.loginSubmit.disabled = true;
    els.loginSubmit.textContent = "Signing in";
    try {
      const res = await fetch(`${API}/api/v1/auth/login`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, password }),
      });
      let data = null;
      try { data = await res.json(); } catch { /* ignore */ }
      if (res.ok) { showChat(data.customer); return; }
      els.loginError.textContent = res.status === 401 || res.status === 429
        ? (data && data.detail) || "Email or password is incorrect."
        : "Sign-in failed on the server. Try again in a moment.";
      els.loginError.hidden = false;
      els.password.select();
    } catch {
      els.loginError.textContent = "Can't reach the server. Check that it's running, then try again.";
      els.loginError.hidden = false;
    } finally {
      els.loginSubmit.disabled = false;
      els.loginSubmit.textContent = "Sign in";
    }
  }

  async function loadDemoAccounts() {
    if (state.demoLoaded) return;
    state.demoLoaded = true;
    try {
      const res = await fetch(`${API}/api/v1/demo/accounts`);
      if (!res.ok) return;                 // demo mode is off
      const accounts = await res.json();
      if (!accounts.length) return;
      els.demoPassword.textContent = accounts[0].password;
      for (const a of accounts) {
        const b = el("button", "demo-account");
        b.type = "button";
        b.append(el("span", "demo-account-name", `Sign in as ${a.name}`),
                 el("span", "demo-account-meta", `${a.email}, ${a.plan} plan`));
        b.addEventListener("click", () => {
          els.email.value = a.email;
          els.password.value = a.password;
          clearLoginErrors();
          signIn(a.email, a.password);
        });
        els.demoList.append(b);
      }
      els.demoAccounts.hidden = false;
    } catch { /* demo list is optional */ }
  }

  async function loadAuthOptions() {
    if (state.optionsLoaded) return;
    try {
      const res = await fetch(`${API}/api/v1/auth/options`);
      if (!res.ok) return;
      const options = await res.json();
      state.optionsLoaded = true;
      els.toSignupLine.hidden = !options.registration_enabled;
    } catch { /* sign-up link stays hidden */ }
  }

  // ---------- create account ----------

  const signupFields = () => [
    [els.suName, els.suNameError], [els.suEmail, els.suEmailError],
    [els.suPassword, els.suPasswordError], [els.suConfirm, els.suConfirmError],
  ];

  function clearSignupErrors() {
    for (const [input, msg] of signupFields()) { input.removeAttribute("aria-invalid"); msg.hidden = true; }
    els.signupError.hidden = true;
    els.signupError.replaceChildren();
  }

  function validateSignup() {
    clearSignupErrors();
    const name = els.suName.value.trim(), email = els.suEmail.value.trim(), pw = els.suPassword.value;
    if (!name) fieldError(els.suName, els.suNameError, "Enter your name.");
    if (!email) fieldError(els.suEmail, els.suEmailError, "Enter your email address.");
    else if (!EMAIL_RE.test(email)) fieldError(els.suEmail, els.suEmailError, "Enter an email address like name@example.com.");
    if (pw.length < 8) fieldError(els.suPassword, els.suPasswordError, "Use a password of at least 8 characters.");
    else if (els.suConfirm.value !== pw) fieldError(els.suConfirm, els.suConfirmError, "The two passwords don't match.");
    const firstBad = signupFields().find(([input]) => input.getAttribute("aria-invalid"));
    if (firstBad) { firstBad[0].focus(); return false; }
    return true;
  }

  function signupFailure(text, offerSignIn) {
    els.signupError.replaceChildren(document.createTextNode(text));
    if (offerSignIn) {
      const b = el("button", "link-button", "Go to sign in");
      b.type = "button";
      b.addEventListener("click", () => { els.email.value = els.suEmail.value.trim(); showSigninPanel(); });
      els.signupError.append(" ", b);
    }
    els.signupError.hidden = false;
  }

  async function createAccount() {
    els.signupSubmit.disabled = true;
    els.signupSubmit.textContent = "Creating account";
    try {
      const res = await fetch(`${API}/api/v1/auth/register`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: els.suName.value.trim(), email: els.suEmail.value.trim(),
                               password: els.suPassword.value }),
      });
      let data = null;
      try { data = await res.json(); } catch { /* ignore */ }
      if (res.status === 201) {
        els.signupForm.reset();
        showChat(data.customer);
        return;
      }
      const detail = data && typeof data.detail === "string" ? data.detail : null;
      if (res.status === 422 && detail && /password/i.test(detail)) {
        fieldError(els.suPassword, els.suPasswordError, detail);
        els.suPassword.focus();
      } else if (res.status === 422 && detail && /email/i.test(detail)) {
        fieldError(els.suEmail, els.suEmailError, detail);
        els.suEmail.focus();
      } else if (res.status === 409) {
        signupFailure("An account with this email already exists.", true);
      } else {
        signupFailure(detail || "The account couldn't be created. Try again in a moment.", false);
      }
    } catch {
      signupFailure("Can't reach the server. Check that it's running, then try again.", false);
    } finally {
      els.signupSubmit.disabled = false;
      els.signupSubmit.textContent = "Create account";
    }
  }

  async function signOut() {
    // Forget this browser's copies of the conversation details; the server keeps the chat itself.
    if (state.sessionId) store.del(detailsKey(state.sessionId));
    try { await fetch(`${API}/api/v1/auth/logout`, { method: "POST" }); } catch { /* still sign out locally */ }
    showLogin("You're signed out.");
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

  els.loginForm.addEventListener("submit", (e) => {
    e.preventDefault();
    if (validateLogin()) signIn(els.email.value.trim(), els.password.value);
  });
  for (const input of [els.email, els.password]) {
    input.addEventListener("input", () => {
      if (input.getAttribute("aria-invalid")) clearLoginErrors();
    });
  }
  els.signOut.addEventListener("click", signOut);
  els.toSignup.addEventListener("click", showSignupPanel);
  els.toSignin.addEventListener("click", showSigninPanel);
  els.signupForm.addEventListener("submit", (e) => {
    e.preventDefault();
    if (validateSignup()) createAccount();
  });
  for (const [input] of signupFields()) {
    input.addEventListener("input", () => {
      if (input.getAttribute("aria-invalid")) {
        const pair = signupFields().find(([i]) => i === input);
        input.removeAttribute("aria-invalid");
        pair[1].hidden = true;
      }
    });
  }

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
  fetch(`${API}/api/v1/auth/me`)
    .then((res) => (res.ok ? res.json().then(showChat) : showLogin()))
    .catch(() => showLogin("Can't reach the server. Check that it's running, then reload the page."));
})();
