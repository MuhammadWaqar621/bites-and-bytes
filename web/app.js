/* ==========================================================================
   Bites & Bytes — front end
   Vanilla JS, no framework. Four jobs:
     1. keep a session id so the server can find this tab's cart
     2. POST messages to /api/chat and render the reply
     3. render the tool trace that came back with it
     4. mirror cart + orders into the right-hand panel
   ========================================================================== */

const API = {
  health: "/api/health",
  menu: "/api/menu",
  tools: "/api/tools",
  cart: (id) => `/api/cart?session_id=${encodeURIComponent(id)}`,
  chat: "/api/chat",
  reset: "/api/reset",
};

const el = (id) => document.getElementById(id);

const chatLog = el("chat-log");
const composer = el("composer");
const input = el("message-input");
const sendBtn = el("send-btn");
const traceList = el("trace-list");
const cartItems = el("cart-items");
const cartSummary = el("cart-summary");
const cartBadge = el("cart-badge");
const orderList = el("order-list");
const menuList = el("menu-list");
const categoryFilters = el("category-filters");
const toolList = el("tool-list");
const statusPill = el("status-pill");

let sessionId = loadSessionId();
let currency = "₹";
let menuData = [];
let activeCategory = "All";
let turnCounter = 0;
let busy = false;

/* ── Session id ───────────────────────────────────────────────────────── */

function loadSessionId() {
  // Persisted so a page refresh keeps the same cart. Wrapped because private
  // windows and blocked-storage settings make localStorage throw, not return null.
  try {
    const saved = localStorage.getItem("bb-session");
    if (saved) return saved;
    const fresh = newId();
    localStorage.setItem("bb-session", fresh);
    return fresh;
  } catch {
    return newId();
  }
}

function newId() {
  return `s-${Math.random().toString(36).slice(2, 10)}${Date.now().toString(36)}`;
}

/* ── Boot ─────────────────────────────────────────────────────────────── */

init();

async function init() {
  wireTabs();
  wireSuggestions();
  wireComposer();
  el("reset-btn").addEventListener("click", resetSession);

  await Promise.all([loadHealth(), loadMenu(), loadTools()]);
  await refreshCart();
  input.focus();
}

async function loadHealth() {
  try {
    const health = await getJSON(API.health);
    el("tool-count").textContent = health.tool_count;
    if (health.configured) {
      setStatus("ok", `connected · ${health.deployment}`);
    } else {
      setStatus("bad", `missing ${health.missing_env.join(", ")}`);
    }
  } catch {
    setStatus("bad", "server unreachable");
  }
}

function setStatus(kind, text) {
  statusPill.textContent = text;
  statusPill.className = `pill pill-${kind}`;
}

/* ── Menu panel ───────────────────────────────────────────────────────── */

async function loadMenu() {
  const data = await getJSON(API.menu);
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
        <span class="tag">${dish.prep_minutes} min</span>
      </div>
    </article>
  `).join("");
}

/* ── Tools panel ──────────────────────────────────────────────────────── */

async function loadTools() {
  const data = await getJSON(API.tools);
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
    </article>
  `).join("");
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
    const data = await postJSON(API.chat, { session_id: sessionId, message: text });
    typing.remove();
    addMessage("bot", data.message, data.trace);
    renderTrace(data.trace);
    renderCart(data.cart, data.orders);
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
  const welcome = chatLog.querySelector(".welcome");
  if (welcome) welcome.remove();
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

function renderTrace(trace) {
  if (!trace || !trace.length) return;
  turnCounter += 1;

  const turn = document.createElement("div");
  turn.className = "trace-turn";
  turn.innerHTML = `<div class="trace-turn-head">Turn ${turnCounter} · ${trace.length} call${trace.length > 1 ? "s" : ""}</div>`;

  trace.forEach((call) => {
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
    turn.appendChild(item);
  });

  const placeholder = traceList.querySelector(".empty");
  if (placeholder) placeholder.remove();
  traceList.prepend(turn);
}

/* ── Cart panel ───────────────────────────────────────────────────────── */

async function refreshCart() {
  const data = await getJSON(API.cart(sessionId));
  renderCart(data.cart, data.orders);
}

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

/* ── Reset ────────────────────────────────────────────────────────────── */

async function resetSession() {
  const data = await postJSON(API.reset, { session_id: sessionId });
  renderCart(data.cart, data.orders);
  traceList.innerHTML = "";
  turnCounter = 0;
  chatLog.innerHTML = `
    <div class="welcome">
      <div class="welcome-icon">🍛</div>
      <h2>Fresh session</h2>
      <p>Cart, coupon, orders and chat history are all cleared.</p>
      <div class="suggestions">
        <button class="suggestion" type="button">Show me the starters</button>
        <button class="suggestion" type="button">Something vegan under ₹300</button>
        <button class="suggestion" type="button">What coupons do you have?</button>
      </div>
    </div>`;
  wireSuggestions();
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

/* ── HTTP helpers ─────────────────────────────────────────────────────── */

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
  // FastAPI puts the message in `detail`; fall back to the raw body.
  try {
    const data = await response.json();
    return data.detail || `Request failed (${response.status})`;
  } catch {
    return `Request failed (${response.status})`;
  }
}

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (char) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[char]));
}
