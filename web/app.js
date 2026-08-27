/* ==========================================================================
   Bites & Bytes — front end
   Vanilla JS, no framework. Five jobs:
     1. sign in / register, and gate the app behind that
     2. list the user's chats and switch between them
     3. POST messages to /api/chat and render the reply
     4. render the tool trace that came back with it
     5. mirror cart + orders into the right-hand panel

   There is no session id in JavaScript: identity is an HttpOnly cookie the
   browser attaches automatically, which this script cannot read by design.
   ========================================================================== */

const el = (id) => document.getElementById(id);

/* auth screen */
const authScreen = el("auth-screen");
const authForm = el("auth-form");
const authError = el("auth-error");
const authSubmit = el("auth-submit");
const optionalFields = el("optional-fields");

/* app shell */
const appShell = el("app");
const chatLog = el("chat-log");
const chatTitle = el("chat-title");
const composer = el("composer");
const input = el("message-input");
const sendBtn = el("send-btn");
const chatList = el("chat-list");
const traceList = el("trace-list");
const cartItems = el("cart-items");
const cartSummary = el("cart-summary");
const cartBadge = el("cart-badge");
const orderList = el("order-list");
const menuList = el("menu-list");
const categoryFilters = el("category-filters");
const toolList = el("tool-list");
const statusPill = el("status-pill");
const userPill = el("user-pill");

let authMode = "login";
let currency = "₹";
let menuData = [];
let activeCategory = "All";
let conversationId = null;
let busy = false;

const SUGGESTIONS = [
  "What do you recommend?",
  "Something vegan under ₹300",
  "What did I order last time?",
  "Is the kitchen open right now?",
];

/* ── Boot ─────────────────────────────────────────────────────────────── */

init();

async function init() {
  wireAuth();
  wireTabs();
  wireComposer();
  el("logout-btn").addEventListener("click", logout);
  el("new-chat-btn").addEventListener("click", () => startChat());

  let session;
  try {
    session = await getJSON("/api/auth/me");
  } catch {
    showAuth(); // not signed in — normal on a first visit
    return;
  }

  // Separate from the check above on purpose: a chat or menu that fails to
  // load is not the same as "you are not signed in", and must not bounce a
  // logged-in user back to the login card.
  await enterApp(session.user);
}

/* ── Authentication ───────────────────────────────────────────────────── */

function wireAuth() {
  document.querySelectorAll(".auth-tab").forEach((tab) => {
    tab.addEventListener("click", () => {
      authMode = tab.dataset.mode;
      document.querySelectorAll(".auth-tab")
        .forEach((t) => t.classList.toggle("is-active", t === tab));

      // Signing in asks for email + password only; the extra fields appear
      // for sign-up and stay optional there.
      optionalFields.hidden = authMode === "login";
      authForm.password.autocomplete =
        authMode === "login" ? "current-password" : "new-password";
      authSubmit.textContent = authMode === "login" ? "Sign in" : "Create account";
      hide(authError);
    });
  });

  authForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    hide(authError);
    authSubmit.disabled = true;

    const body = {
      email: authForm.email.value.trim(),
      password: authForm.password.value,
    };
    if (authMode === "register") {
      // Send only what was actually filled in — a blank box must stay NULL in
      // the database, not become an empty string the assistant might quote.
      for (const name of ["display_name", "phone", "address", "pincode"]) {
        const value = authForm[name].value.trim();
        if (value) body[name] = value;
      }
    }

    try {
      const data = await postJSON(`/api/auth/${authMode}`, body);
      authForm.reset();
      await enterApp(data.user);
    } catch (error) {
      show(authError, error.message);
    } finally {
      authSubmit.disabled = false;
    }
  });
}

function showAuth() {
  authScreen.hidden = false;
  appShell.hidden = true;
}

async function enterApp(user) {
  userPill.textContent = user.display_name;

  try {
    // Load everything BEFORE revealing the shell, so it never appears as a
    // set of empty panels that fill in a moment later.
    await Promise.all([loadHealth(), loadMenu(), loadTools()]);
    await loadChats({ openFirst: true });
  } finally {
    // Reveal even if a panel failed: a signed-in user should get the app and
    // an error pill, never a blank page.
    authScreen.hidden = true;
    appShell.hidden = false;
    input.focus();
  }
}

async function logout() {
  await postJSON("/api/auth/logout", {});
  conversationId = null;
  chatLog.innerHTML = "";
  chatList.innerHTML = "";
  showAuth();
}

/* ── Reference data ───────────────────────────────────────────────────── */

async function loadHealth() {
  try {
    const health = await getJSON("/api/health");
    el("tool-count").textContent = health.tool_count;
    el("dish-count").textContent = health.dish_count.toLocaleString();
    if (health.configured) setStatus("ok", `connected · ${health.deployment}`);
    else setStatus("bad", `missing ${health.missing_env.join(", ")}`);
  } catch {
    setStatus("bad", "server unreachable");
  }
}

function setStatus(kind, text) {
  statusPill.textContent = text;
  statusPill.className = `pill pill-${kind}`;
}

async function loadMenu() {
  const data = await getJSON("/api/menu");
  currency = data.currency;
  menuData = data.dishes;

  categoryFilters.innerHTML = "";
  ["All", ...data.categories].forEach((name) => {
    const chip = document.createElement("button");
    chip.type = "button";
    chip.className = `chip${name === activeCategory ? " is-active" : ""}`;
    chip.textContent = name;
    chip.addEventListener("click", () => {
      activeCategory = name;
      categoryFilters.querySelectorAll(".chip")
        .forEach((c) => c.classList.toggle("is-active", c.textContent === name));
      renderMenu();
    });
    categoryFilters.appendChild(chip);
  });

  renderMenu();
}

function renderMenu() {
  const dishes = activeCategory === "All"
    ? menuData
    : menuData.filter((d) => d.category === activeCategory);

  menuList.innerHTML = dishes.map((dish) => `
    <article class="dish">
      <div class="dish-head">
        <span class="dish-name">${esc(dish.name)}</span>
        <span class="dish-price">${currency}${dish.price}</span>
      </div>
      <p class="dish-desc">${esc(dish.description)}</p>
      <div class="dish-meta">
        <span class="tag tag-${dish.diet}">${dish.diet}</span>
        <span class="tag">${esc(dish.spice)}</span>
        ${dish.times_ordered ? `<span class="tag">${dish.times_ordered} sold</span>` : ""}
      </div>
    </article>`).join("");
}

async function loadTools() {
  const data = await getJSON("/api/tools");
  toolList.innerHTML = data.tools.map((tool) => `
    <article class="tool-card">
      <div class="tool-name">${esc(tool.name)}()</div>
      <p class="tool-desc">${esc(tool.description)}</p>
      <div>
        ${tool.parameters.length
          ? tool.parameters.map((p) => `
              <span class="param${p.required ? " is-required" : ""}"
                    title="${p.required ? "required" : "optional"}${p.enum ? " · " + p.enum.join(" | ") : ""}">
                ${esc(p.name)}${p.required ? "*" : "?"}
              </span>`).join("")
          : `<span class="param">no parameters</span>`}
      </div>
    </article>`).join("");
}

/* ── Conversations ────────────────────────────────────────────────────── */

async function loadChats({ openFirst = false } = {}) {
  const data = await getJSON("/api/conversations");
  renderChatList(data.conversations);

  if (openFirst) {
    if (data.conversations.length) await openChat(data.conversations[0].id);
    else await startChat();
  }
}

function renderChatList(conversations) {
  if (!conversations.length) {
    chatList.innerHTML = `<div class="empty"><span class="empty-icon">💬</span>No chats yet.</div>`;
    return;
  }

  chatList.innerHTML = "";
  conversations.forEach((chat) => {
    const row = document.createElement("div");
    row.className = `chat-row${chat.id === conversationId ? " is-active" : ""}`;
    row.innerHTML = `
      <button class="chat-open" type="button">
        <span class="chat-name">${esc(chat.title)}</span>
        <span class="chat-meta">${chat.message_count} message${chat.message_count === 1 ? "" : "s"} · ${shortDate(chat.updated_at)}</span>
      </button>
      <button class="chat-delete" type="button" title="Delete chat" aria-label="Delete chat">×</button>`;

    row.querySelector(".chat-open").addEventListener("click", () => openChat(chat.id));
    row.querySelector(".chat-delete").addEventListener("click", async (event) => {
      event.stopPropagation();
      await fetch(`/api/conversations/${chat.id}`, { method: "DELETE" });
      if (chat.id === conversationId) conversationId = null;
      await loadChats({ openFirst: true });
    });
    chatList.appendChild(row);
  });
}

async function startChat() {
  const created = await postJSON("/api/conversations", { title: "New chat" });
  conversationId = created.id;
  chatTitle.textContent = created.title;
  chatLog.innerHTML = welcomeHtml();
  wireSuggestions();
  traceList.innerHTML = "";
  renderCart({ items: [], subtotal: 0, item_count: 0, coupon_code: null }, []);
  await loadChats();
}

/** Repaint a whole conversation from the database — messages, trace and cart. */
async function openChat(id) {
  const data = await getJSON(`/api/conversations/${id}`);
  conversationId = id;
  chatTitle.textContent = data.title;

  if (data.messages.length) {
    chatLog.innerHTML = "";
    data.messages.forEach((m) => addMessage(m.role === "user" ? "user" : "bot", m.content));
  } else {
    chatLog.innerHTML = welcomeHtml();
    wireSuggestions();
  }

  traceList.innerHTML = "";
  renderTrace(data.trace, { grouped: true });
  renderCart(data.cart, data.orders);
  await loadChats();
  scrollToEnd();
}

function welcomeHtml() {
  return `
    <div class="welcome">
      <div class="welcome-icon">🍛</div>
      <h2>What are you hungry for?</h2>
      <p>
        Ask in plain English. The assistant decides which of the
        <strong>15 tools</strong> to call — watch the <em>Tool trace</em>
        panel as it works.
      </p>
      <div class="suggestions">
        ${SUGGESTIONS.map((s) => `<button class="suggestion" type="button">${esc(s)}</button>`).join("")}
      </div>
    </div>`;
}

/* ── Chat ─────────────────────────────────────────────────────────────── */

function wireComposer() {
  composer.addEventListener("submit", (event) => {
    event.preventDefault();
    const text = input.value.trim();
    if (text && !busy) send(text);
  });
}

function wireSuggestions() {
  chatLog.querySelectorAll(".suggestion").forEach((button) => {
    button.addEventListener("click", () => send(button.textContent.trim()));
  });
}

async function send(text) {
  clearWelcome();
  addMessage("user", text);
  input.value = "";
  setBusy(true);

  const typing = showTyping();

  try {
    const data = await postJSON("/api/chat", {
      conversation_id: conversationId,
      message: text,
    });
    typing.remove();
    addMessage("bot", data.message, data.trace);
    renderTrace(data.trace);
    renderCart(data.cart, data.orders);
    chatTitle.textContent = data.title;
    await loadChats();
  } catch (error) {
    typing.remove();
    addMessage("error", error.message);
  } finally {
    setBusy(false);
    input.focus();
  }
}

function setBusy(value) {
  busy = value;
  sendBtn.disabled = value;
  input.disabled = value;
}

function clearWelcome() {
  chatLog.querySelector(".welcome")?.remove();
}

function addMessage(kind, text, trace) {
  const wrap = document.createElement("div");
  wrap.className = `msg msg-${kind === "user" ? "user" : "bot"}${kind === "error" ? " msg-error" : ""}`;

  const bubble = document.createElement("div");
  bubble.className = "bubble";
  bubble.innerHTML = format(text);
  wrap.appendChild(bubble);

  // Show which tools produced this answer, right under the answer itself.
  if (trace && trace.length) {
    const chips = document.createElement("div");
    chips.className = "msg-tools";
    chips.innerHTML = trace.map((call) => `
      <span class="tool-chip${call.ok ? "" : " is-failed"}" title="${call.duration_ms} ms">
        <span class="dot"></span>${esc(call.name)}
      </span>`).join("");
    wrap.appendChild(chips);
  }

  chatLog.appendChild(wrap);
  scrollToEnd();
  return wrap;
}

function showTyping() {
  const wrap = document.createElement("div");
  wrap.className = "msg msg-bot";
  wrap.innerHTML = `<div class="bubble typing"><span></span><span></span><span></span></div>`;
  chatLog.appendChild(wrap);
  scrollToEnd();
  return wrap;
}

function scrollToEnd() {
  chatLog.scrollTop = chatLog.scrollHeight;
}

/* Minimal markdown: bold, inline code, and "- " bullet lists. The model is
   told to keep replies short, so anything heavier would be dead weight. */
function format(text) {
  const lines = esc(text).split("\n");
  let html = "";
  let inList = false;

  for (const line of lines) {
    const bullet = line.match(/^\s*[-*]\s+(.*)$/);
    if (bullet) {
      if (!inList) { html += "<ul>"; inList = true; }
      html += `<li>${inline(bullet[1])}</li>`;
      continue;
    }
    if (inList) { html += "</ul>"; inList = false; }
    if (line.trim()) html += `<div>${inline(line)}</div>`;
  }
  if (inList) html += "</ul>";
  return html || "&nbsp;";
}

function inline(text) {
  return text
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/`([^`]+)`/g, "<code>$1</code>");
}

/* ── Tool trace panel ─────────────────────────────────────────────────── */

function renderTrace(trace, { grouped = false } = {}) {
  if (!trace || !trace.length) return;

  // A reloaded conversation arrives as one flat list carrying turn numbers; a
  // live reply is always a single turn. Group so both render identically.
  const turns = new Map();
  for (const call of trace) {
    const key = grouped ? call.turn ?? 1 : "live";
    if (!turns.has(key)) turns.set(key, []);
    turns.get(key).push(call);
  }

  for (const [key, calls] of turns) {
    const turn = document.createElement("div");
    turn.className = "trace-turn";
    const label = grouped ? `Turn ${key}` : "Latest turn";
    turn.innerHTML = `<div class="trace-turn-head">${label} · ${calls.length} call${calls.length > 1 ? "s" : ""}</div>`;
    calls.forEach((call) => turn.appendChild(traceItem(call)));

    // Reloaded turns read oldest-first; live turns stack newest on top.
    if (grouped) traceList.appendChild(turn);
    else traceList.prepend(turn);
  }
}

function traceItem(call) {
  const item = document.createElement("div");
  item.className = "trace-item";
  item.innerHTML = `
    <button class="trace-head" type="button">
      <span class="trace-status ${call.ok ? "ok" : "fail"}"></span>
      <span class="trace-name">${esc(call.name)}</span>
      <span class="trace-time">${call.duration_ms}ms</span>
      <span class="trace-caret">▶</span>
    </button>
    <div class="trace-body">
      <div class="trace-label">Arguments the model produced</div>
      <pre class="trace-json">${esc(JSON.stringify(call.arguments, null, 2))}</pre>
      <div class="trace-label">Result sent back to the model</div>
      <pre class="trace-json">${esc(JSON.stringify(call.result, null, 2))}</pre>
    </div>`;
  item.querySelector(".trace-head")
    .addEventListener("click", () => item.classList.toggle("is-open"));
  return item;
}

/* ── Cart panel ───────────────────────────────────────────────────────── */

function renderCart(cart, orders) {
  cartBadge.textContent = cart.item_count;

  if (!cart.items.length) {
    cartItems.innerHTML = `<div class="empty"><span class="empty-icon">🧺</span>Your cart is empty. Ask the assistant to add something.</div>`;
    cartSummary.innerHTML = "";
  } else {
    cartItems.innerHTML = cart.items.map((line) => `
      <div class="cart-line">
        <div>
          <div class="cart-line-name">${esc(line.name)}</div>
          <div class="cart-line-sub">${line.quantity} × ${currency}${line.unit_price}</div>
        </div>
        <div class="cart-line-total">${currency}${line.line_total}</div>
      </div>`).join("");

    cartSummary.innerHTML = `
      <div class="summary-row"><span>Subtotal</span><span>${currency}${cart.subtotal}</span></div>
      ${cart.coupon_code
        ? `<div class="summary-row"><span>Coupon</span><span class="coupon">${esc(cart.coupon_code)}</span></div>`
        : ""}
      <div class="summary-row total"><span>Items</span><span>${cart.item_count}</span></div>
      <p class="panel-hint" style="margin-top:12px">
        Ask for the bill in the chat — only <code>calc_total</code> can produce
        the final figure with tax and delivery.
      </p>`;
  }

  orderList.innerHTML = (orders || []).map((order) => `
    <div class="order-card">
      <div class="order-id">${esc(order.order_id)}</div>
      <div class="order-meta">
        ${esc(order.status.replace(/_/g, " "))} · ${currency}${order.total ?? "—"} · ETA ${order.eta_minutes} min
      </div>
    </div>`).join("");
}

/* ── Tabs ─────────────────────────────────────────────────────────────── */

function wireTabs() {
  document.querySelectorAll(".panel").forEach((panel) => {
    panel.querySelectorAll(".tab").forEach((tab) => {
      tab.addEventListener("click", () => {
        panel.querySelectorAll(".tab").forEach((t) => t.classList.toggle("is-active", t === tab));
        panel.querySelectorAll(".tab-body").forEach((body) => {
          body.classList.toggle("is-active", body.dataset.panel === tab.dataset.tab);
        });
      });
    });
  });
}

/* ── Helpers ──────────────────────────────────────────────────────────── */

async function getJSON(url) {
  const response = await fetch(url);
  if (!response.ok) throw new Error(await errorText(response));
  return response.json();
}

async function postJSON(url, body) {
  const response = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!response.ok) throw new Error(await errorText(response));
  return response.json();
}

async function errorText(response) {
  // FastAPI puts the message in `detail` — a string, or a list for validation
  // errors, which is what a rejected email or short password comes back as.
  try {
    const data = await response.json();
    if (typeof data.detail === "string") return data.detail;
    if (Array.isArray(data.detail)) return data.detail[0]?.msg || "Invalid input.";
    return `Request failed (${response.status})`;
  } catch {
    return `Request failed (${response.status})`;
  }
}

function show(node, text) {
  node.textContent = text;
  node.hidden = false;
}

function hide(node) {
  node.hidden = true;
}

function shortDate(iso) {
  const date = new Date(iso);
  const today = new Date();
  const sameDay = date.toDateString() === today.toDateString();
  return sameDay
    ? date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })
    : date.toLocaleDateString([], { day: "numeric", month: "short" });
}

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (char) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[char]));
}
