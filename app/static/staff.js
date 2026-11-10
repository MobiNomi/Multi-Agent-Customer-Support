/* AcmeCloud support console: approve refunds, answer tickets. Talks to /api/v1/staff/*. */
(() => {
  "use strict";

  const API = window.ACME_API_BASE || "";
  const REFRESH_MS = 10000;
  const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

  const $ = (id) => document.getElementById(id);
  const els = {
    loginView: $("login-view"), consoleView: $("console-view"), consoleActions: $("console-actions"),
    staffName: $("staff-name"), signOut: $("sign-out"),
    loginForm: $("login-form"), email: $("email"), password: $("password"), emailError: $("email-error"),
    passwordError: $("password-error"), loginError: $("login-error"), loginNotice: $("login-notice"),
    loginSubmit: $("login-submit"), demoAccounts: $("demo-accounts"), demoList: $("demo-list"),
    demoPassword: $("demo-password"),
    tabs: [...document.querySelectorAll(".queue-tab")], list: $("queue-list"), foot: $("queue-foot"),
    countActions: $("count-actions"), countTickets: $("count-tickets"), detail: $("detail"),
  };
  const state = { staff: null, tab: "actions", queue: null, selected: null, timer: null, demoLoaded: false };

  // ---------- helpers ----------

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text != null) node.textContent = text;
    return node;
  }

  const money = (n) => (n == null ? "" : n.toLocaleString("en-US", { style: "currency", currency: "USD" }));
  const timeFmt = new Intl.DateTimeFormat(undefined, { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
  const when = (iso) => (iso ? timeFmt.format(new Date(iso)) : "");

  function ago(iso) {
    if (!iso) return "";
    const mins = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 60000));
    if (mins < 1) return "just now";
    if (mins < 60) return `${mins} min ago`;
    const hours = Math.round(mins / 60);
    if (hours < 24) return `${hours} h ago`;
    return when(iso);
  }

  const ACTION_PILLS = {
    awaiting_staff: ["wait", "Waiting for approval"], completed: ["done", "Approved"], rejected: ["stop", "Rejected"],
    failed: ["stop", "Failed"], cancelled: ["off", "Withdrawn by customer"], processing: ["wait", "Processing"],
    awaiting_customer: ["ask", "Not confirmed yet"], expired: ["off", "Expired"],
  };
  const TICKET_PILLS = { Open: "ask", Escalated: "wait", Closed: "off" };
  const PRIORITY_PILLS = { Urgent: "stop", High: "stop", Medium: "off", Low: "off" };

  function pill(tone, text) { return el("span", `pill tone-${tone}`, text); }

  // Assistant replies are written in light markdown; show them as plain text here.
  const plain = (text) => String(text || "").replace(/\*\*([^*]+)\*\*/g, "$1").replace(/`([^`]+)`/g, "$1");

  class SignedOut extends Error {}

  async function api(path, { method = "GET", body } = {}) {
    const res = await fetch(`${API}${path}`, {
      method,
      headers: body ? { "Content-Type": "application/json" } : undefined,
      body: body ? JSON.stringify(body) : undefined,
    });
    if (res.status === 401) {
      showLogin("Your session has ended. Sign in again to continue.");
      throw new SignedOut();
    }
    let data = null;
    try { data = await res.json(); } catch { /* empty or non-JSON */ }
    return { ok: res.ok, status: res.status, data };
  }

  // ---------- sign-in ----------

  function showLogin(notice) {
    stopRefresh();
    state.staff = null;
    state.selected = null;
    els.consoleView.hidden = true;
    els.consoleActions.hidden = true;
    els.loginView.hidden = false;
    document.body.classList.remove("is-loading");
    els.loginNotice.hidden = !notice;
    els.loginNotice.textContent = notice || "";
    els.password.value = "";
    loadDemo();
    (els.email.value ? els.password : els.email).focus();
  }

  function showConsole(staff) {
    state.staff = staff;
    els.staffName.textContent = staff.name;
    els.loginView.hidden = true;
    els.consoleView.hidden = false;
    els.consoleActions.hidden = false;
    document.body.classList.remove("is-loading");
    renderEmptyDetail();
    loadQueue();
    startRefresh();
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

  async function signIn(email, password) {
    els.loginNotice.hidden = true;
    els.loginError.hidden = true;
    els.loginSubmit.disabled = true;
    els.loginSubmit.textContent = "Signing in";
    try {
      const res = await fetch(`${API}/api/v1/staff/login`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ email, password }),
      });
      let data = null;
      try { data = await res.json(); } catch { /* ignore */ }
      if (res.ok) { showConsole(data.staff); return; }
      els.loginError.textContent = [401, 403, 429].includes(res.status) && data && data.detail
        ? data.detail : "Sign-in failed on the server. Try again in a moment.";
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

  async function loadDemo() {
    if (state.demoLoaded) return;
    state.demoLoaded = true;
    try {
      const res = await fetch(`${API}/api/v1/demo/accounts`);
      if (!res.ok) return;
      const staff = (await res.json()).filter((a) => a.role === "staff");
      if (!staff.length) return;
      els.demoPassword.textContent = staff[0].password;
      for (const a of staff) {
        const b = el("button", "demo-account");
        b.type = "button";
        b.append(el("span", "demo-account-name", `Sign in as ${a.name}`), el("span", "demo-account-meta", a.email));
        b.addEventListener("click", () => { els.email.value = a.email; els.password.value = a.password; signIn(a.email, a.password); });
        els.demoList.append(b);
      }
      els.demoAccounts.hidden = false;
    } catch { /* optional */ }
  }

  async function signOut() {
    try { await fetch(`${API}/api/v1/staff/logout`, { method: "POST" }); } catch { /* sign out locally anyway */ }
    showLogin("You're signed out.");
  }

  // ---------- queue ----------

  function startRefresh() {
    stopRefresh();
    state.timer = setInterval(() => { if (document.visibilityState === "visible") loadQueue(); }, REFRESH_MS);
  }

  function stopRefresh() {
    if (state.timer) clearInterval(state.timer);
    state.timer = null;
  }

  async function loadQueue() {
    try {
      const { ok, data } = await api("/api/v1/staff/queue");
      if (!ok) { els.foot.textContent = "Couldn't load the queue. Retrying shortly."; return; }
      state.queue = data;
      els.countActions.textContent = data.actions.length;
      els.countTickets.textContent = data.tickets.length;
      els.countActions.classList.toggle("is-zero", !data.actions.length);
      els.countTickets.classList.toggle("is-zero", !data.tickets.length);
      renderList();
      els.foot.textContent = `Refreshes every ${REFRESH_MS / 1000} seconds. Last checked ${timeFmt.format(new Date())}.`;
    } catch (err) {
      if (!(err instanceof SignedOut)) els.foot.textContent = "Can't reach the server. Retrying shortly.";
    }
  }

  function setTab(tab) {
    state.tab = tab;
    for (const t of els.tabs) t.setAttribute("aria-selected", String(t.dataset.tab === tab));
    els.list.setAttribute("aria-labelledby", `tab-${tab}`);
    renderList();
  }

  function renderList() {
    const q = state.queue;
    if (!q) return;
    const items = state.tab === "tickets" ? q.tickets : state.tab === "recent" ? q.recent : q.actions;
    els.list.replaceChildren();
    if (!items.length) {
      const text = { actions: "No refunds waiting. New ones appear here when a customer confirms one.",
                     tickets: "No open tickets.", recent: "No decisions yet." }[state.tab];
      els.list.append(el("li", "queue-empty", text));
      return;
    }
    for (const item of items) {
      const type = state.tab === "tickets" ? "ticket" : "action";
      const li = el("li");
      const b = el("button", "queue-item");
      b.type = "button";
      if (state.selected && state.selected.type === type && state.selected.id === item.id) b.classList.add("is-selected");
      const top = el("span", "queue-item-top");
      top.append(el("span", "queue-item-name", item.customer_name || item.customer_id));
      top.append(el("span", "queue-item-time", ago(type === "ticket" ? item.created_at
        : (item.resolved_at && state.tab === "recent" ? item.resolved_at : item.confirmed_at || item.created_at))));
      const line = el("span", "queue-item-line", type === "ticket" ? item.description : item.summary);
      const tags = el("span", "queue-item-tags");
      if (type === "ticket") {
        tags.append(pill(TICKET_PILLS[item.status] || "off", item.status), pill(PRIORITY_PILLS[item.priority] || "off", item.priority),
                    el("span", "queue-item-id", item.id));
      } else {
        const [tone, text] = ACTION_PILLS[item.status] || ["off", item.status_text];
        tags.append(pill(tone, text), el("span", "queue-item-id", item.id));
      }
      b.append(top, line, tags);
      b.addEventListener("click", () => select(type, item.id));
      li.append(b);
      els.list.append(li);
    }
  }

  async function select(type, id) {
    state.selected = { type, id };
    renderList();
    els.detail.replaceChildren(el("p", "detail-loading", "Loading…"));
    try {
      const { ok, data } = await api(`/api/v1/staff/${type === "ticket" ? "tickets" : "actions"}/${encodeURIComponent(id)}`);
      if (!ok) { els.detail.replaceChildren(el("p", "detail-loading", (data && data.detail) || "Couldn't load this item.")); return; }
      if (type === "ticket") renderTicket(data); else renderAction(data);
      els.detail.scrollTop = 0;
    } catch (err) {
      if (!(err instanceof SignedOut)) els.detail.replaceChildren(el("p", "detail-loading", "Can't reach the server."));
    }
  }

  // ---------- detail: shared parts ----------

  function renderEmptyDetail() {
    const box = el("div", "detail-empty");
    box.append(el("h1", null, "Nothing selected"),
               el("p", null, "Pick a refund or a ticket on the left. Refunds appear after the customer confirms them in the chat."));
    els.detail.replaceChildren(box);
  }

  function facts(rows) {
    const dl = el("dl", "facts");
    for (const [label, value] of rows) {
      if (value == null || value === "") continue;
      dl.append(el("dt", null, label));
      const dd = el("dd");
      if (value instanceof Node) dd.append(value); else dd.textContent = value;
      dl.append(dd);
    }
    return dl;
  }

  function customerFacts(c) {
    if (!c) return null;
    const wrap = el("span");
    wrap.append(el("strong", null, c.name), document.createTextNode(` · ${c.plan} plan · ${c.email} · ${c.id}`));
    return wrap;
  }

  function transcript(conversation) {
    const section = el("section", "transcript");
    section.append(el("h2", null, "Conversation"));
    if (!conversation || !conversation.length) {
      section.append(el("p", "muted", "This didn't come from a chat, so there is no conversation to show."));
      return section;
    }
    const list = el("ol", "transcript-list");
    for (const m of conversation) {
      const li = el("li", `turn turn-${m.role}`);
      const who = m.role === "customer" ? "Customer" : m.role === "assistant" ? "Assistant"
        : m.author ? `Support team · ${m.author}` : "Account update";
      li.append(el("span", "turn-who", who), el("p", "turn-text", plain(m.content)));
      list.append(li);
    }
    section.append(list);
    return section;
  }

  function header(kindLabel, id, pills, title) {
    const head = el("header", "detail-head");
    const meta = el("div", "detail-meta");
    meta.append(el("span", "detail-kind", kindLabel), el("span", "detail-id", id), ...pills);
    head.append(meta, el("h1", "detail-title", title));
    return head;
  }

  function notice(box, text, tone) {
    box.textContent = text;
    box.className = `detail-notice tone-${tone}`;
    box.hidden = false;
  }

  // ---------- detail: refund requests ----------

  function renderAction(a) {
    const [tone, text] = ACTION_PILLS[a.status] || ["off", a.status_text];
    const frag = document.createDocumentFragment();
    frag.append(header(a.kind_label === "Refund" ? "Refund request" : a.kind_label, a.id, [pill(tone, text)], a.summary));

    const p = a.payment, o = a.order;
    const orderText = o ? `${o.order_id} · ${o.status} · ${money(o.amount)} · ${o.payments.length} payment${o.payments.length === 1 ? "" : "s"}: `
      + o.payments.map((x) => `${x.transaction_id} ${x.status}`).join(", ") : null;
    frag.append(facts([
      ["Customer", customerFacts(a.customer)],
      ["Amount", money(a.amount)],
      ["Payment", p ? `${p.transaction_id} · ${p.status} · ${money(p.amount)} · ${when(p.created_at)}` : null],
      ["Order", orderText],
      ["Reason given", a.reason || "None given"],
      ["Came from", a.parent_summary],
      ["Confirmed by customer", when(a.confirmed_at)],
    ]));

    const box = el("p", "detail-notice");
    box.hidden = true;
    box.setAttribute("role", "status");

    if (a.status === "awaiting_staff") {
      frag.append(decisionPanel(a, box));
    } else if (a.resolved_at) {
      const who = a.resolved_by_name || (a.resolved_by === "customer" ? "the customer" : a.resolved_by);
      const outcome = `${text} by ${who}, ${when(a.resolved_at)}.` + (a.resolution_note ? ` ${a.status === "rejected" ? "Reason" : "Note"}: ${a.resolution_note}` : "")
        + (a.status === "failed" && a.result ? ` Problem: ${a.result}` : "");
      notice(box, outcome, tone);
    }
    frag.append(box, transcript(a.conversation));
    els.detail.replaceChildren(frag);
  }

  function decisionPanel(a, box) {
    const panel = el("section", "decision");
    panel.append(el("h2", null, "Decision"));

    const noteLabel = el("label", null, "Note to the customer (optional)");
    noteLabel.htmlFor = "decision-note";
    const note = el("textarea", "field");
    note.id = "decision-note";
    note.rows = 2;
    note.maxLength = 1000;
    const buttons = el("div", "decision-buttons");
    const approve = el("button", "btn-approve", `Approve refund of ${money(a.amount)}`);
    const reject = el("button", "btn-quiet", "Reject…");
    approve.type = reject.type = "button";
    buttons.append(approve, reject);

    const rejectBox = el("div", "reject-box");
    rejectBox.hidden = true;
    const reasonLabel = el("label", null, "Reason for the customer (required)");
    reasonLabel.htmlFor = "reject-reason";
    const reason = el("textarea", "field");
    reason.id = "reject-reason";
    reason.rows = 3;
    reason.maxLength = 1000;
    const reasonError = el("p", "field-error", "Write a reason. The customer sees it in their chat.");
    reasonError.id = "reject-reason-error";
    reasonError.hidden = true;
    const rejectButtons = el("div", "decision-buttons");
    const sendReject = el("button", "btn-reject", "Send rejection");
    const back = el("button", "btn-quiet", "Back");
    sendReject.type = back.type = "button";
    rejectButtons.append(sendReject, back);
    rejectBox.append(reasonLabel, reason, reasonError, rejectButtons);

    const busy = (on) => { for (const b of panel.querySelectorAll("button, textarea")) b.disabled = on; };

    approve.addEventListener("click", async () => {
      busy(true);
      approve.textContent = "Approving…";
      await decide(a, "approve", { note: note.value.trim() }, box);
      busy(false);
      approve.textContent = `Approve refund of ${money(a.amount)}`;
    });
    reject.addEventListener("click", () => { buttons.hidden = true; noteLabel.hidden = note.hidden = true; rejectBox.hidden = false; reason.focus(); });
    back.addEventListener("click", () => { rejectBox.hidden = true; buttons.hidden = false; noteLabel.hidden = note.hidden = false; });
    reason.addEventListener("input", () => { reason.removeAttribute("aria-invalid"); reasonError.hidden = true; });
    sendReject.addEventListener("click", async () => {
      if (reason.value.trim().length < 3) {
        reason.setAttribute("aria-invalid", "true");
        reason.setAttribute("aria-describedby", reasonError.id);
        reasonError.hidden = false;
        reason.focus();
        return;
      }
      busy(true);
      await decide(a, "reject", { reason: reason.value.trim() }, box);
      busy(false);
    });

    panel.append(noteLabel, note, buttons, rejectBox);
    return panel;
  }

  async function decide(a, verb, body, box) {
    try {
      const { ok, status, data } = await api(`/api/v1/staff/actions/${encodeURIComponent(a.id)}/${verb}`, { method: "POST", body });
      if (ok) {
        await select("action", a.id);          // redraw with the outcome
        const fresh = els.detail.querySelector(".detail-notice");
        const sent = data.delivered_to_chat ? " The customer has been told in their chat." : " This request has no chat, so the customer wasn't messaged.";
        if (fresh) notice(fresh, `${verb === "approve" ? (data.action.status === "completed" ? "Approved and refunded." : "Couldn't be completed: " + (data.action.result || "")) : "Rejected."}${sent}`,
                          data.action.status === "completed" ? "done" : "stop");
        loadQueue();
        return;
      }
      notice(box, (data && data.detail) || "That didn't work. Try again.", status === 409 ? "wait" : "stop");
      if (status === 409) loadQueue();
    } catch (err) {
      if (!(err instanceof SignedOut)) notice(box, "Can't reach the server. Try again.", "stop");
    }
  }

  // ---------- detail: tickets ----------

  function renderTicket(t) {
    const frag = document.createDocumentFragment();
    const firstLine = (t.description || "").split("\n")[0];
    frag.append(header("Ticket", t.id, [pill(TICKET_PILLS[t.status] || "off", t.status),
                                        pill(PRIORITY_PILLS[t.priority] || "off", `${t.priority} priority`)],
                       firstLine.length > 140 ? `${firstLine.slice(0, 140)}…` : firstLine));
    frag.append(facts([
      ["Customer", customerFacts(t.customer)],
      ["Category", t.category],
      ["Opened", when(t.created_at)],
      ["Closed", when(t.closed_at)],
      ["Full description", t.description.length > 140 || t.description.includes("\n") ? t.description : null],
    ]));

    if (t.replies.length) {
      const section = el("section", "replies");
      section.append(el("h2", null, "Replies on this ticket"));
      const list = el("ol", "transcript-list");
      for (const r of t.replies) {
        const li = el("li", "turn turn-support");
        li.append(el("span", "turn-who", `${r.author} · ${when(r.created_at)}`), el("p", "turn-text", r.content));
        list.append(li);
      }
      section.append(list);
      frag.append(section);
    }

    const box = el("p", "detail-notice");
    box.hidden = true;
    box.setAttribute("role", "status");

    if (t.status !== "Closed") {
      const panel = el("section", "decision");
      panel.append(el("h2", null, "Reply"));
      if (!t.has_conversation) panel.append(el("p", "muted", "This ticket didn't come from a chat. Your reply is saved on the ticket, but the customer won't see it in a chat."));
      const label = el("label", "sr-only", "Reply to the customer");
      label.htmlFor = "ticket-reply";
      const text = el("textarea", "field");
      text.id = "ticket-reply";
      text.rows = 4;
      text.maxLength = 4000;
      text.placeholder = "Write to the customer. It appears in their chat as a message from you.";
      const err = el("p", "field-error", "Write a reply first.");
      err.id = "ticket-reply-error";
      err.hidden = true;
      const buttons = el("div", "decision-buttons");
      const send = el("button", "btn-approve", "Send reply");
      const close = el("button", "btn-quiet", "Close ticket");
      send.type = close.type = "button";
      buttons.append(send, close);
      const busy = (on) => { for (const b of panel.querySelectorAll("button, textarea")) b.disabled = on; };
      text.addEventListener("input", () => { text.removeAttribute("aria-invalid"); err.hidden = true; });
      send.addEventListener("click", async () => {
        if (text.value.trim().length < 2) {
          text.setAttribute("aria-invalid", "true");
          text.setAttribute("aria-describedby", err.id);
          err.hidden = false;
          text.focus();
          return;
        }
        busy(true);
        await ticketStep(t, "reply", { message: text.value.trim() }, box);
        busy(false);
      });
      close.addEventListener("click", async () => {
        busy(true);
        await ticketStep(t, "close", { message: text.value.trim() }, box);
        busy(false);
      });
      panel.append(label, text, err, buttons);
      frag.append(panel);
    }
    frag.append(box, transcript(t.conversation));
    els.detail.replaceChildren(frag);
  }

  async function ticketStep(t, verb, body, box) {
    try {
      const { ok, status, data } = await api(`/api/v1/staff/tickets/${encodeURIComponent(t.id)}/${verb}`, { method: "POST", body });
      if (ok) {
        renderTicket(data.ticket);
        const fresh = els.detail.querySelector(".detail-notice");
        const where = data.delivered_to_chat ? " The customer sees it in their chat." : " Saved on the ticket.";
        notice(fresh, `${verb === "reply" ? "Reply sent." : "Ticket closed."}${where}`, "done");
        loadQueue();
        return;
      }
      notice(box, (data && data.detail) || "That didn't work. Try again.", status === 409 ? "wait" : "stop");
    } catch (err) {
      if (!(err instanceof SignedOut)) notice(box, "Can't reach the server. Try again.", "stop");
    }
  }

  // ---------- events ----------

  els.loginForm.addEventListener("submit", (e) => {
    e.preventDefault();
    clearLoginErrors();
    const email = els.email.value.trim();
    let ok = true;
    if (!EMAIL_RE.test(email)) { fieldError(els.email, els.emailError, "Enter an email address like name@acmecloud.example."); ok = false; }
    if (!els.password.value) { fieldError(els.password, els.passwordError, "Enter your password."); ok = false; }
    if (ok) signIn(email, els.password.value);
    else (els.email.getAttribute("aria-invalid") ? els.email : els.password).focus();
  });
  for (const input of [els.email, els.password]) input.addEventListener("input", () => { if (input.getAttribute("aria-invalid")) clearLoginErrors(); });
  els.signOut.addEventListener("click", signOut);
  for (const t of els.tabs) t.addEventListener("click", () => setTab(t.dataset.tab));
  document.addEventListener("visibilitychange", () => { if (document.visibilityState === "visible" && state.staff) loadQueue(); });

  // ---------- start ----------

  fetch(`${API}/api/v1/staff/me`)
    .then((res) => (res.ok ? res.json().then(showConsole) : showLogin()))
    .catch(() => showLogin("Can't reach the server. Check that it's running, then reload the page."));
})();
