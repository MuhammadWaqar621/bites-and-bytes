# 🍛 Bites & Bytes — an LLM tool-calling restaurant agent

A multi-user conversational ordering assistant for a fictional Indian
restaurant, built to demonstrate **LLM function/tool calling** end to end with
**Azure OpenAI** and **SQL Server**.

The customer signs in, then types plain English. The model decides *which* of
**15 Python functions** to call, *in what order*, and *with what arguments* —
and the app proves it by streaming the whole tool trace into the UI beside the
chat.

> The one rule the whole project is built around:
> **the assistant may never state a price, discount, ETA or order id that did
> not come back from a tool.** That constraint is what separates an agent from
> a chatbot.

Accounts, chats, carts, orders and the full message transcript all live in the
database, so a conversation survives a restart, a refresh and a different
browser — and one customer can never see another's data.

---

## Run it — one command

```bash
python run.py
```

That creates the database, builds the schema, seeds **~47,700 rows**, and
starts the web UI at **<http://127.0.0.1:8000>**. Running it again skips
straight to the server.

First time only:

```bash
python -m venv .venv
.venv\Scripts\Activate.ps1        # Windows;  macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
copy .env.example .env            # macOS/Linux: cp .env.example .env
```

Then fill in `.env` and run `python run.py`.

### Sign in

A seeded demo account already has ~2,000 orders of history, which is what makes
the memory features worth demonstrating:

```
demo@bitesbytes.app  /  demo12345
```

Or click **Create account** and register your own — a fresh account starts with
an empty history, which is worth seeing too.

### `.env`

```ini
# Azure OpenAI
LLM_ENDPOINT_MINI_MODEL=https://your-resource.openai.azure.com/
LLM_ENDPOINT_MINI_MODEL_APIKEY=your-key
MINI_MODEL_NAME=gpt-4o-mini       # your DEPLOYMENT name, not the model name
LLM_API_VERSION=2024-10-21        # >= 2024-06-01 for parallel tool calls

# Database — local SQL Server over Windows auth, no password needed
DATABASE_URL=mssql+pyodbc://@localhost:1433/BitesBytes?driver=ODBC+Driver+18+for+SQL+Server&Trusted_Connection=yes&TrustServerCertificate=yes

LLM_TEMPERATURE=0.2               # lower = follows the tool rules more literally
MAX_TOOL_ITERATIONS=6             # safety valve against infinite tool loops
SESSION_DAYS=14                   # how long a login cookie stays valid
APP_PORT=8000
```

The UI and the menu load fine without Azure credentials — only `/api/chat`
needs the model, and the status pill in the header names the missing variable.

### Tests — no API key, no server, no tokens

```bash
pytest
```

**48 tests** covering the business logic and the agent loop. They run against a
throwaway SQLite file with a scripted fake model, so the riskiest code in the
project is verified for free in about 15 seconds.

---

## The database

| | |
|---|---|
| **Engine** | Microsoft SQL Server 2022 Express |
| **Database** | `BitesBytes` (created automatically on first run) |
| **Auth** | Windows authentication — no password in `.env` |
| **Access** | SQLAlchemy 2.0 ORM + `pyodbc` |

Everything — restaurant catalogue, customer accounts, orders **and the chat
transcript** — lives in that one database, because `chat_messages` →
`conversations` → `users` → `orders` all reference each other.

Switching engines is one line in `.env`; no code changes:

```ini
DATABASE_URL=postgresql+psycopg2://user:pass@localhost/bitesbytes
DATABASE_URL=sqlite:///bitesbytes.db
```

### Schema — 11 tables

```mermaid
erDiagram
    users ||--o{ auth_tokens : "logged-in browsers"
    users ||--o{ conversations : "owns"
    users ||--o{ orders : "places"
    conversations ||--o{ chat_messages : "transcript"
    conversations ||--o{ tool_invocations : "audit trail"
    conversations ||--o{ cart_items : "working cart"
    orders ||--o{ order_items : "lines"
    dishes ||--o{ cart_items : ""
    dishes ||--o{ order_items : "price copied, not joined"

    users {
        int id PK
        string email UK
        string password_hash "PBKDF2-SHA256, salted"
    }
    conversations {
        int id PK
        int user_id FK
        string title
        string coupon_code "cart-level, per chat"
    }
    chat_messages {
        int id PK
        int conversation_id FK
        int seq "ordering within the chat"
        string role "user | assistant | tool"
        text content
        text tool_calls_json "the assistant's requests"
        string tool_call_id "pairs a result to its request"
    }
    tool_invocations {
        int id PK
        int turn
        string name
        text arguments_json
        text result_json
        bool ok
        int duration_ms
    }
    orders {
        int id PK
        string code UK "ORD-1042"
        datetime placed_at "status is DERIVED from this"
        datetime cancelled_at
        decimal total
    }
    dishes {
        int id PK
        string name
        string diet "veg | nonveg | vegan"
        decimal price
        int times_ordered "denormalised popularity"
    }
```

Three design decisions worth explaining at an interview:

1. **`orders` has no `status` column.** Status is derived from `placed_at` and
   `cancelled_at` at read time, so a seeded order from last month reports
   `delivered` and one placed a minute ago reports `confirmed` — with no cron
   job ever touching a row.
2. **`order_items` copies the dish name and price** rather than joining. An
   order is a historical record: if Butter Chicken goes up next month, today's
   receipt must still say what was charged today.
3. **Money is `Numeric(10,2)`, never `float`.** A restaurant bill that is off
   by a hundredth of a rupee looks broken. The repository converts to `float`
   at the boundary so JSON stays clean.

### Seeded data — 47,700 rows

| Table | Rows |
|---|---:|
| `dishes` | 238 |
| `coupons` | 14 |
| `delivery_zones` | 60 |
| `users` | 201 |
| `orders` | 12,000 |
| `order_items` | 35,170 |
| **Total** | **~47,700** |

The generator uses a fixed seed, so the same rows come out every time — a demo
whose "best seller" changes on every rebuild is a demo you cannot write a
README about. Dishes are composed (10 proteins × 8 starter styles, 8 proteins ×
10 gravies, three biryani traditions) rather than hand-typed, which is how a
real Indian menu is built and gives `search_dish` a realistically large
catalogue to filter.

---

## The interface

```
┌───────────────────────────────────────────────────────────────────────────┐
│  B&B  Bites & Bytes    ● connected · gpt-4o-mini   Demo Customer  [Sign out]│
├────────────────┬──────────────────────────────────┬───────────────────────┤
│Chats│Menu│Tools│           the conversation       │ Cart │ Tool trace     │
│                │                                  │                       │
│ + New chat     │  ┌─────────────────────────────┐ │  2 × Garlic Naan      │
│ ▸ Dinner for.. │  │ Added 2 × Garlic Naan…      │ │  1 × Dal Makhani      │
│ ▸ Vegan under..│  └─────────────────────────────┘ │  ─────────────────    │
│ ▸ My usual     │   🔧 add_to_cart  🔧 add_to_cart │  Subtotal    ₹480     │
│                │                                  │                       │
│ 238 dishes     │  [ type a message…        ] [→]  │  ▸ add_to_cart  6ms   │
└────────────────┴──────────────────────────────────┴───────────────────────┘
```

- **Chats** — every conversation you have ever had, loaded from
  `conversations`. Click one and the messages, the tool trace and the cart all
  repaint from the database.
- **Menu / Tools** — the live catalogue, and the exact JSON schema list the
  model receives, rendered from the same source the API sends it.
- **Chat** — every reply is tagged with chips showing which tools produced it.
- **Cart / Tool trace** — the live cart, and an expandable log of every call:
  the arguments the model invented, the JSON it got back, the latency.

---

## Example conversations

Real transcripts from `gpt-4o-mini` against the seeded database.

### 1 — a constraint becomes SQL

```
You:  Something vegan under 300 rupees
      → search_dish(diet="vegan", max_price=300)

Bot:  Here are some vegan dishes under ₹300:
      - Corn Seekh Kebab (₹270)
      - Soya Chaap Tikka (₹260)
      - Corn Tandoori (₹250)
```

The model turned an English sentence into two schema arguments, which became a
`WHERE diet IN (...) AND price <= 300` over 238 rows.

### 2 — parallel tool calls

```
You:  Add a dal makhani and 2 garlic naan
      → add_to_cart(dish_name="Dal Makhani")             ┐ same turn,
      → add_to_cart(dish_name="Garlic Naan", quantity=2) ┘ one response
```

One model response, two `tool_calls`. The loop runs both and returns two `tool`
messages, each carrying its own `tool_call_id`.

### 3 — 12,000 rows of history, answered in one call

```
You:  What did I order last time?
      → find_past_orders(limit=5)

Bot:  Last time you ordered:
      - Lamb Rogan Josh (1 × ₹700)
      - Pudina Paratha (1 × ₹95)
      - Chicken Malai (2 × ₹480)
      Would you like to reorder any of these?
```

Impossible before the database existed. The query is scoped to the signed-in
user in SQL — see the isolation test below.

### 4 — the app remembers you

```
You:  Place the order using my usual details
      → get_my_profile()

Bot:  I have your usual details saved:
      - Name: Demo Customer
      - Phone: 9990320822
      - Address: 83 Nehru Street
      - Pincode: 722797
      - Payment Method: Wallet
      Shall I go ahead and place the order with these?
```

This is what "the app remembers me" actually means: a tool that reads the
customer's own history, plus one line in the system prompt telling the model to
offer the saved details rather than demand them again — and to confirm before
using them.

### 5 — the model asks instead of calling

```
You:  Okay place the order
      → (no tools called)

Bot:  I need a few details to place your order:
      1. Your full name    2. Your 10-digit mobile number
      3. Your full address 4. Your 6-digit pincode
      5. Your payment method
```

**The most valuable example here.** `place_order` has five required parameters,
so the model cannot call it yet — and rather than inventing a name and phone
number, it stops and asks. That behaviour comes from the `required` array in the
schema plus one rule in the system prompt.

### 6 — a failure the model has to explain

```
You:  Apply SAVE20 and give me the bill for 755001
      → apply_coupon(code="SAVE20")     ✗ ok: false
      → calc_total(pincode="755001")    ✓

Bot:  SAVE20 can't be applied — your cart is ₹80 short of the ₹800 minimum.
      Your current bill: subtotal ₹720, delivery ₹29, packaging ₹20,
      tax ₹36 → total ₹805.
```

The ₹80 shortfall was computed in Python and handed over in the tool's `hint`.
The model relayed it; it did not calculate it.

### 7 — the full lifecycle

```
You:  Place the order for Waqar, 9876543210, Flat 4B Nehru Street, 755001, cash on delivery
      → place_order(...)          ✓  ORD-13042
You:  Cancel that order, changed my mind
      → cancel_order(...)         ✓
```

Ask again 12 minutes later and `cancel_order` refuses — the order is out for
delivery by then, and the model must relay the refusal and the support number
rather than override it.

### More to try

| Say this | What it exercises |
|---|---|
| `What do you recommend?` | `popular_dishes` — ranked over 35,170 order lines |
| `Is the kitchen open right now?` | `get_current_time` — models have no clock |
| `Add a panner tika` | fuzzy matching an unambiguous typo |
| `Add a biryani` | ambiguity — the tool asks *which of the 18* |
| `Apply MONSOON25` | an expired coupon, with alternatives offered |
| `Deliver to 110001` | an unserviceable pincode |
| `What's my total?` (empty cart) | a clean refusal, not a crash |

---

## The 15 tools

| # | Tool | Arguments | What it teaches |
|---|---|---|---|
| 1 | `get_menu` | `category?` | read-only, paged — the catalogue is too big to send whole |
| 2 | `search_dish` | `query? diet? max_price? spice?` | optional filters resolved in SQL, `enum` values |
| 3 | `popular_dishes` | `limit? diet?` | aggregate over the whole order history |
| 4 | `add_to_cart` | `dish_name*, quantity?` | a write, plus fuzzy-match failure |
| 5 | `view_cart` | — | the zero-argument case |
| 6 | `remove_from_cart` | `dish_name*, quantity?` | destructive, validates first |
| 7 | `apply_coupon` | `code*` | failures that carry the useful information |
| 8 | `calc_total` | `pincode?` | arithmetic the model must never attempt |
| 9 | `estimate_delivery` | `pincode*` | derived value + "we don't serve you" |
| 10 | `place_order` | `name*, phone*, address*, pincode*, payment*` | **five required args → the model must ask** |
| 11 | `order_status` | `order_id*` | user-scoped lookup |
| 12 | `cancel_order` | `order_id*, reason?` | a business rule it must relay, not override |
| 13 | `find_past_orders` | `limit?` | history — only possible with a database |
| 14 | `get_my_profile` | — | **memory**: the customer's own saved details |
| 15 | `get_current_time` | — | grounding — an LLM has no clock |

`*` = required.

---

## Architecture

```mermaid
flowchart LR
    subgraph browser["🖥️  Browser"]
        UI["index.html · app.js · styles.css<br/><i>login · chats · cart · tool trace</i>"]
    end

    subgraph server["⚙️  FastAPI"]
        direction TB
        AU["auth.py<br/><i>HttpOnly cookie → user</i>"]
        SRV["server.py<br/><i>routes only, no logic</i>"]
        AG["agent.py<br/><b>THE LOOP</b>"]
        TL["tools.py<br/><i>15 plain functions</i>"]
        SC["schemas.py<br/><i>JSON catalogue</i>"]
        RP["repository.py<br/><i>every SQL query</i>"]
        LC["llm_client.py<br/><i>Azure wrapper</i>"]
    end

    DB[("🗄️  SQL Server<br/><b>BitesBytes</b><br/>11 tables · 47.7k rows")]
    AZ["☁️  Azure OpenAI<br/><b>gpt-4o-mini</b>"]

    UI -- "POST /api/chat<br/>+ session cookie" --> AU
    AU --> SRV
    SRV -- "reply + trace + cart" --> UI

    SRV --> AG
    AG -- "messages + tools" --> LC
    LC --> AZ
    AZ -- "tool_calls · or an answer" --> LC
    LC --> AG

    AG -- "dispatch by name" --> TL
    TL -- "ok / error JSON" --> AG
    AG -. "sends, never exposes" .-> SC
    SC -. "described to" .-> AZ

    TL --> RP
    AG -- "persists every message" --> RP
    RP --> DB

    classDef hot fill:#ff9a3c22,stroke:#ff9a3c,stroke-width:2px
    classDef cloud fill:#3ddc9722,stroke:#3ddc97,stroke-width:2px
    class AG,SC hot
    class AZ,DB cloud
```

The two highlighted boxes are the ones that matter. **`agent.py`** is the only
place that knows about the loop, and **`schemas.py`** is the only thing the
model ever sees of your code — it never sees `tools.py` at all.

Dependency direction is strictly one-way:

```
server.py → agent.py → tools.py → repository.py → SQL Server
                 ↓
           llm_client.py → Azure
```

`tools.py` contains no SQL and no LLM calls, which is why the same tool code
runs against SQL Server in production and SQLite in the tests.

### File map

```
tool_learing/
├── run.py                  one command: create + seed + serve
├── app/
│   ├── config.py           the only module that reads os.environ
│   ├── db.py               engine + session factory
│   ├── models.py           11 ORM tables — the whole schema
│   ├── security.py         PBKDF2 password hashing, session tokens
│   ├── auth.py             cookie → user dependency
│   ├── repository.py       every SQL query in the project
│   ├── seed.py             schema bootstrap + 47.7k rows
│   ├── data.py             business constants (tax, fees, lifecycle)
│   ├── llm_client.py       Azure wrapper; SDK errors → readable ones
│   ├── tools.py            the 15 tools — no SQL, no LLM
│   ├── schemas.py          the JSON catalogue the model sees ← the real prompt
│   ├── agent.py            THE LOOP — the file worth reading twice
│   └── server.py           FastAPI routes; owns no business logic
├── web/                    login screen + three-panel app, no framework
└── tests/
    ├── conftest.py         SQLite fixtures, two isolated users
    ├── test_tools.py       business logic + authorisation
    └── test_agent.py       the loop, driven by a scripted fake model
```

### API

| Method | Route | Purpose |
|---|---|---|
| `POST` | `/api/auth/register` · `/login` · `/logout` | accounts |
| `GET` | `/api/auth/me` | current user + lifetime stats |
| `GET` `POST` | `/api/conversations` | list / create a chat |
| `GET` `DELETE` | `/api/conversations/{id}` | full transcript / delete |
| `POST` | `/api/chat` | `{conversation_id, message}` → reply + trace + cart |
| `GET` | `/api/menu` · `/api/tools` · `/api/health` | public reference data |

Interactive docs at `/docs` while the server runs.

---

## How tool calling actually works

### The loop

```
messages = [system, …transcript loaded from the database, user]

repeat up to MAX_TOOL_ITERATIONS times:
    reply = model(messages, tools)

    if reply has no tool_calls:
        return reply.content                 ← done, the model answered

    append reply to messages                 ← the ASK
    for each tool_call:
        result = python_function(**json.loads(call.function.arguments))
        append {"role": "tool",
                "tool_call_id": call.id,
                "content": json.dumps(result)}   ← the ANSWER
    # loop: the model now sees the results and continues
```

All of it lives in [`app/agent.py`](app/agent.py), heavily commented.

Here is one real turn — `"add a dal makhani and 2 garlic naan"` — which takes
**two** round-trips to Azure and runs **two** tools in between:

```mermaid
sequenceDiagram
    autonumber
    actor C as Customer
    participant A as agent.py
    participant M as Azure<br/>gpt-4o-mini
    participant T as tools.py
    participant D as SQL Server

    C->>A: "add a dal makhani<br/>and 2 garlic naan"
    A->>D: INSERT chat_messages (role=user)

    rect rgba(255,154,60,0.10)
    note over A,M: round-trip 1 — the model ASKS
    A->>M: transcript + 15 tool schemas
    M-->>A: finish_reason: "tool_calls"<br/>[call_a add_to_cart, call_b add_to_cart]
    end

    note over A,D: append the assistant message FIRST<br/>(rule 1 — or the next call is a 400)

    rect rgba(61,220,151,0.10)
    note over A,T: your code ANSWERS
    A->>T: add_to_cart(dish_name="Dal Makhani")
    T->>D: INSERT cart_items
    T-->>A: {ok: true, cart_subtotal: 320.0}
    A->>T: add_to_cart(dish_name="Garlic Naan", quantity=2)
    T->>D: INSERT cart_items
    T-->>A: {ok: true, cart_subtotal: 480.0}
    end

    note over A,D: one tool message per call, each with<br/>its own tool_call_id (rule 2)

    rect rgba(255,154,60,0.10)
    note over A,M: round-trip 2 — the model CONCLUDES
    A->>M: same transcript + both tool results
    M-->>A: finish_reason: "stop"<br/>"Added 1 × Dal Makhani and 2 × Garlic Naan."
    end

    A->>D: COMMIT — transcript + tool_invocations
    A-->>C: answer + the trace of both calls
```

The loop exits because the second reply has **no** `tool_calls`. Every message
in that diagram is a row in `chat_messages`, which is why the next turn — or
the next browser — replays exactly the same context.

### Deciding what happens each iteration

```mermaid
flowchart TD
    S(["user message"]) --> SAVE["INSERT the user message"]
    SAVE --> CALL["call the model<br/>with transcript + tools"]
    CALL --> Q{"reply has<br/>tool_calls?"}

    Q -- "no" --> DONE(["save + return the answer"])

    Q -- "yes" --> APP["append the assistant's<br/>tool_calls message"]
    APP --> RUN["for each call:<br/>json.loads(arguments)<br/>dispatch via TOOL_REGISTRY<br/><i>inside a SAVEPOINT</i>"]

    RUN --> OK{"did it<br/>work?"}
    OK -- "yes" --> RES["{ok: true, …}<br/>release savepoint"]
    OK -- "no" --> ERR["{ok: false, error, hint}<br/>roll back savepoint only<br/><i>never raise</i>"]

    RES --> PAIR["append role:tool<br/>+ matching tool_call_id"]
    ERR --> PAIR

    PAIR --> GUARD{"iterations<br/>&lt; 6?"}
    GUARD -- "yes" --> CALL
    GUARD -- "no" --> BAIL(["graceful fallback message"])

    classDef stop fill:#3ddc9722,stroke:#3ddc97,stroke-width:2px
    classDef warn fill:#ff6b6b22,stroke:#ff6b6b,stroke-width:2px
    class DONE stop
    class ERR,BAIL warn
```

The red boxes are the two things beginners leave out: a tool that fails
**returns** instead of raising, and the loop has a hard ceiling so a model that
never stops calling tools cannot hang the request.

The SAVEPOINT detail matters once a database is involved. A tool that blows up
halfway through a write would otherwise poison the transaction for the rest of
the turn — and a plain `rollback()` would throw away the transcript rows
already written. A nested transaction undoes **only that tool**.

### What goes over the wire

**Request** — the conversation plus the catalogue, on *every* call:

```json
{
  "model": "gpt-4o-mini",
  "messages": [
    {"role": "system", "content": "You are the ordering assistant…"},
    {"role": "user",   "content": "Add two garlic naan"}
  ],
  "tools": [
    {"type": "function", "function": {
      "name": "add_to_cart",
      "description": "Put a dish into the customer's cart…",
      "parameters": {
        "type": "object",
        "properties": {
          "dish_name": {"type": "string", "description": "Dish name exactly as…"},
          "quantity":  {"type": "integer", "minimum": 1, "maximum": 20}
        },
        "required": ["dish_name"],
        "additionalProperties": false
      }
    }}
  ],
  "tool_choice": "auto"
}
```

**Response** — `finish_reason: "tool_calls"` means *your turn to work*:

```json
{
  "finish_reason": "tool_calls",
  "message": {
    "role": "assistant",
    "content": null,
    "tool_calls": [{
      "id": "call_8Kq2",
      "type": "function",
      "function": {
        "name": "add_to_cart",
        "arguments": "{\"dish_name\":\"Garlic Naan\",\"quantity\":2}"
      }
    }]
  }
}
```

⚠️ `arguments` is a **JSON string**, not an object — and it was written by a
language model, so it can be malformed. Parse it in a `try`.

**Your follow-up** — the request message *and* the result, in that order:

```json
[
  …,
  {"role": "assistant", "content": null, "tool_calls": [{"id": "call_8Kq2", …}]},
  {"role": "tool", "tool_call_id": "call_8Kq2",
   "content": "{\"ok\":true,\"line_quantity\":2,\"cart_subtotal\":160.0}"}
]
```

### The three rules that break everything if you get them wrong

1. **Append the assistant's request before the results.** A `tool` message with
   no preceding `tool_calls` is a 400.
2. **Match every `tool_call_id`.** Parallel calls arrive together; the id is the
   only thing pairing a result with its request.
3. **Never let a tool raise.** An exception ends the conversation. Return
   `{"ok": false, "error": …, "hint": …}` and the model apologises, corrects
   itself and carries on.

### Failure modes you will actually hit

| Symptom | Cause | Fix in this repo |
|---|---|---|
| `400 … 'tool' message without preceding tool_calls` | the replayed window cut mid-handshake | `_history` walks forward to the first `user` row |
| The model invents a price or an order id | the fact never came from a tool | hard rules 1–2 in `SYSTEM_PROMPT`; tools return facts, not prose |
| The model refuses or confirms without calling anything | it "remembered" a stale result | hard rule 6: *never decide on a tool's behalf* |
| It re-asks for details it already has | over-applying "ask if missing" | rule 3's second half: *"yes" is the confirmation, act on it* |
| The request never terminates | the model keeps calling tools | `MAX_TOOL_ITERATIONS` + a graceful fallback |
| `Incorrect syntax near '1'` | `.is_(True)` renders as `IS 1` | SQL Server has no BOOLEAN — use `== True` |
| Duplicate key on `orders.code` | two code formulas disagreed | one shared `order_code_for(id)` |

Every row in that table is a bug that actually happened while building this,
not a textbook example.

### Why the schema descriptions matter more than the Python

The model **never sees `app/tools.py`**. It sees only
[`app/schemas.py`](app/schemas.py). So the description text is not
documentation, it is *prompt*. Compare:

```python
# weak — the model calls it at the wrong time and adds up numbers itself
"description": "Calculates the total."

# strong — used in this project
"description": "Compute the bill: subtotal, coupon discount, delivery fee, "
               "packaging and 5% tax. You must call this before stating any "
               "total — never add the numbers up yourself. Pass `pincode` so "
               "the delivery fee is included."
```

Three habits that pay for themselves: say **when** to use a tool, use `enum`
for every closed value set, and mark a field `required` only when the tool
genuinely cannot run without it — because each required field is a question the
model must ask the user first.

---

## Security

This is a demo, but the parts that would be inexcusable to get wrong are done
properly:

- **Passwords** are PBKDF2-SHA256, 260,000 rounds, 16 random salt bytes per
  user, compared with `hmac.compare_digest`. Nothing is stored in plaintext and
  nothing is reversible.
- **Sessions** are 32 random bytes in an **HttpOnly** cookie; the database
  stores only the SHA-256 hash, so a dump of `auth_tokens` cannot be replayed
  as a login. JavaScript on the page cannot read the cookie.
- **Authorisation is in SQL, not in the prompt.** Every user-scoped query
  filters by `user_id`. A model that hallucinates a valid order id belonging to
  somebody else gets "not found", never a stranger's address:

  ```python
  def test_one_customer_cannot_read_anothers_order(ctx, other_ctx):
      order_id = place_an_order(ctx)["order"]["order_id"]
      assert tools.order_status(other_ctx, order_id=order_id)["ok"] is False
      assert tools.cancel_order(other_ctx, order_id=order_id)["ok"] is False
      assert tools.find_past_orders(other_ctx)["count"] == 0
  ```

- **Login failures** say "email or password is incorrect" for both cases, so
  the endpoint cannot be used to enumerate which emails have accounts.
- **A missing chat returns 404, not 403** — never confirm that somebody else's
  data exists.

Before deploying anywhere real: set `secure=True` on the cookie (it is off
because localhost is plain HTTP), and put the app behind HTTPS.

---

## Troubleshooting

| Problem | Cause |
|---|---|
| `DATABASE ERROR` on startup | SQL Server is not running, or `DATABASE_URL` is wrong. `python run.py` prints the URL it tried |
| Status pill says **missing …** | that variable is absent or still a placeholder in `.env` |
| `503` when chatting | same — the server started without Azure credentials |
| `Azure returned HTTP 404` | `MINI_MODEL_NAME` is not a deployment on that resource. Use the *deployment* name, not the model name |
| `Azure rejected the API key` | wrong key, or a key from a different resource than the endpoint |
| `429` | Azure rate limit — wait, or raise the deployment's TPM quota |
| Want a clean database | drop `BitesBytes` in SSMS and run `python run.py` again |

---

## Extending it

Adding a sixteenth tool is three edits, always in this order:

1. Write the function in `app/tools.py` (returning the `ok` envelope, taking
   `ctx` first) and add it to `TOOL_REGISTRY`.
2. Add its schema to `TOOL_SCHEMAS` in `app/schemas.py`. **The `name` must
   match the registry key exactly** — that string is the entire contract, and
   `test_every_schema_has_a_matching_registry_entry` fails if they drift.
3. Add a test in `tests/test_tools.py`.

The UI needs no changes: the Tools panel and the trace render whatever the
registry and the schemas contain.

Natural next steps: stream replies with server-sent events, add an admin view
over the 12,000 seeded orders, or add a second agent that handles complaints.

---

## License

MIT — see [LICENSE](LICENSE).
