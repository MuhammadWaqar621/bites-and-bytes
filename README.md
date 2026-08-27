# 🍛 Bites & Bytes — an LLM tool-calling restaurant agent

A conversational ordering assistant for a fictional Indian restaurant, built to
demonstrate **LLM function/tool calling** end to end with **Azure OpenAI**.

The customer types plain English. The model decides *which* of **12 Python
functions** to call, *in what order*, and *with what arguments* — and the app
proves it by streaming the whole tool trace into the UI beside the chat.

> The one rule the whole project is built around:
> **the assistant may never state a price, discount, ETA or order id that did
> not come back from a tool.** That constraint is what separates an agent from
> a chatbot.

---

## Quick start

```bash
# 1. clone / open the project
cd tool_learing

# 2. create a virtual environment
python -m venv .venv

# Windows (PowerShell)
.venv\Scripts\Activate.ps1
# macOS / Linux
source .venv/bin/activate

# 3. install
pip install -r requirements.txt

# 4. add your Azure credentials
cp .env.example .env      # Windows: copy .env.example .env
#   then edit .env

# 5. run
python run.py
```

Open **<http://127.0.0.1:8000>**.

### `.env`

```ini
LLM_ENDPOINT_MINI_MODEL=https://your-resource.openai.azure.com/
LLM_ENDPOINT_MINI_MODEL_APIKEY=your-key
MINI_MODEL_NAME=gpt-4o-mini      # your DEPLOYMENT name, not the model name
LLM_API_VERSION=2024-10-21       # >= 2024-06-01 for parallel tool calls

LLM_TEMPERATURE=0.2              # low = follows the tool rules literally
MAX_TOOL_ITERATIONS=6            # safety valve against infinite tool loops
APP_PORT=8000
```

Only `/api/chat` needs credentials — the menu, the tool catalogue and the UI
load fine without them, and the status pill in the header tells you exactly
which variable is missing.

### Tests (no API key, no tokens)

```bash
pip install -r requirements-dev.txt
pytest
```

33 tests: the business logic and the agent loop, both driven without a network
call. The loop tests use a scripted fake model, so the riskiest code in the
project is verified for free.

---

## The interface

```
┌──────────────────────────────────────────────────────────────────────────┐
│  B&B  Bites & Bytes          ● connected · gpt-4o-mini    [Reset session] │
├───────────────┬──────────────────────────────────┬───────────────────────┤
│ Menu │ Tools  │            the conversation      │ Cart │ Tool trace     │
│               │                                  │                       │
│ 21 dishes,    │  ┌─────────────────────────────┐ │  2 × Paneer Tikka     │
│ live prices   │  │ Added 2 × Paneer Tikka…     │ │  1 × Garlic Naan      │
│               │  └─────────────────────────────┘ │  ─────────────────    │
│ the 12 tool   │   🔧 add_to_cart  🔧 add_to_cart │  Subtotal    ₹720     │
│ schemas the   │                                  │                       │
│ model sees    │  [ type a message…        ] [→]  │  ▸ add_to_cart  4ms   │
└───────────────┴──────────────────────────────────┴───────────────────────┘
```

- **Menu / Tools** (left) — the real menu, and the exact JSON catalogue the
  model receives, rendered from the same source the API sends.
- **Chat** (centre) — every reply is tagged with chips showing which tools
  produced it.
- **Cart / Tool trace** (right) — the live cart, and an expandable log of every
  call: the arguments the model invented, the JSON it got back, the latency.

---

## Example conversations

These are real transcripts from `gpt-4o-mini`, with the tools that fired.

### 1 — one constraint, one tool

```
You:  I'm vegetarian with a budget of ₹1500. What do you suggest?
      → search_dish(diet="veg", max_price=1500)

Bot:  Here are some vegetarian dishes within your budget:
      - Paneer Tikka — ₹320
      - Paneer Butter Masala — ₹420
      - Veg Dum Biryani — ₹380
      …
```

The model turned "vegetarian" and "budget of ₹1500" into two schema arguments
on its own. Note that `diet="veg"` also returns vegan dishes — that rule lives
in Python, not in the prompt.

### 2 — parallel tool calls

```
You:  Add two paneer tikka and one garlic naan
      → add_to_cart(dish_name="Paneer Tikka", quantity=2)   ┐ same turn,
      → add_to_cart(dish_name="Garlic Naan", quantity=1)    ┘ one response

Bot:  I've added 2 × Paneer Tikka and 1 × Garlic Naan to your cart.
```

One model response, two `tool_calls`. The loop runs both and returns two
`tool` messages, each carrying its own `tool_call_id`.

### 3 — a failure the model has to explain

```
You:  Apply coupon SAVE20 and give me the final bill for pincode 755001
      → apply_coupon(code="SAVE20")        ✗ ok: false
      → calc_total(pincode="755001")       ✓

Bot:  The coupon SAVE20 cannot be applied because your cart is ₹80 short of
      the required subtotal of ₹800.

      Here's your current bill for pincode 755001:
      - Subtotal:     ₹720.00
      - Delivery Fee: ₹29.00
      - Packaging:    ₹20.00
      - Tax (5%):     ₹36.00
      - Total:        ₹805.00
```

The ₹80 shortfall was computed in Python and handed over in the tool's `hint`
field. The model relayed it; it did not calculate it.

### 4 — the model asks instead of calling

```
You:  Okay place the order
      → (no tools called)

Bot:  I need a few details to place your order:
      1. Your full name
      2. Your 10-digit mobile number
      3. Your full address (flat/house number and street)
      4. Your 6-digit delivery pincode
      5. Your payment method (UPI, card, cash on delivery, or wallet)
```

**This is the most valuable example in the project.** `place_order` has five
required parameters, so the model cannot call it yet — and rather than
inventing a name and phone number, it stops and asks. That behaviour comes from
the `required` array in the schema plus one line in the system prompt.

### 5 — the full lifecycle

```
You:  Place the order for Waqar, 9876543210, Flat 4B Nehru Street, 755001, cash on delivery
      → place_order(...)          ✓  Order ID: ORD-1042

You:  Cancel it, changed my mind
      → cancel_order(order_id="ORD-1042", reason="changed my mind")   ✓

Bot:  Your order has been successfully cancelled. If you had prepaid,
      refunds typically take 3-5 working days.
```

Ask again 12 minutes later and `cancel_order` refuses — the order is out for
delivery by then, and the model has to relay the refusal and the support
number instead of overriding it.

### More to try

| Say this | What it exercises |
|---|---|
| `Is the kitchen open right now?` | `get_current_time` — models have no clock |
| `Something vegan under ₹300` | two filters in one call |
| `Add a panner tika` | fuzzy matching a typo |
| `Add a biryani` | ambiguity — the tool asks *which one* |
| `Apply MONSOON25` | an expired coupon, with alternatives offered |
| `Deliver to 110001` | an unserviceable pincode |
| `How long will it take to 757001?` | prep time + travel time |
| `What's my total?` (empty cart) | a clean refusal, not a crash |

---

## The 12 tools

| # | Tool | Arguments | What it teaches |
|---|------|-----------|-----------------|
| 1 | `get_menu` | `category?` | read-only, one optional filter |
| 2 | `search_dish` | `query? diet? max_price? spice?` | several optional filters, `enum` values |
| 3 | `add_to_cart` | `dish_name*, quantity?` | a write, plus fuzzy-match failure |
| 4 | `view_cart` | — | the zero-argument case |
| 5 | `remove_from_cart` | `dish_name*, quantity?` | destructive, validates first |
| 6 | `apply_coupon` | `code*` | failures that carry the useful information |
| 7 | `calc_total` | `pincode?` | arithmetic the model must never attempt |
| 8 | `estimate_delivery` | `pincode*` | derived value + "we don't serve you" |
| 9 | `place_order` | `name*, phone*, address*, pincode*, payment*` | **five required args → the model must ask** |
| 10 | `order_status` | `order_id*` | an id it can only have learned from tool 9 |
| 11 | `cancel_order` | `order_id*, reason?` | a business rule it must relay, not override |
| 12 | `get_current_time` | — | grounding — an LLM has no clock |

`*` = required.

---

## How tool calling actually works

### The loop

```
messages = [system, …history, user]

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

### What goes over the wire

**Request** — the conversation plus the catalogue, on *every* call:

```json
{
  "model": "gpt-4o-mini",
  "messages": [
    {"role": "system", "content": "You are the ordering assistant…"},
    {"role": "user",   "content": "Add two paneer tikka"}
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
        "arguments": "{\"dish_name\":\"Paneer Tikka\",\"quantity\":2}"
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
   "content": "{\"ok\":true,\"line_quantity\":2,\"cart_subtotal\":640.0}"}
]
```

### The three rules that break everything if you get them wrong

1. **Append the assistant's request before the results.** A `tool` message with
   no preceding `tool_calls` is a 400.
2. **Match every `tool_call_id`.** Parallel calls arrive together; the id is the
   only thing pairing a result with its request.
3. **Never let a tool raise.** An exception ends the conversation. Return
   `{"ok": false, "error": …, "hint": …}` and the model apologises, corrects
   itself and carries on — which is what makes the assistant feel resilient.

### Four failure modes you will hit

| Symptom | Cause | Fix in this repo |
|---|---|---|
| `400 … 'tool' message without preceding tool_calls` | trimmed history cut mid-handshake | `OrderingAgent._history` walks forward to the first `user` message |
| The model invents a price or an order id | the fact never came from a tool | hard rules 1–2 in `SYSTEM_PROMPT` + tools return facts, not prose |
| The model refuses/confirms without calling anything | it "remembered" a stale result | hard rule 6: *never decide on a tool's behalf* |
| The request never terminates | the model keeps calling tools | `MAX_TOOL_ITERATIONS` + a graceful fallback message |

Rules 6 and the confirmation clause in rule 3 are not theoretical — both were
added after watching `gpt-4o-mini` get them wrong during testing.

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

## Project layout

```
tool_learing/
├── run.py                  entry point — prints config, starts uvicorn
├── app/
│   ├── config.py           the only module that reads os.environ
│   ├── llm_client.py       Azure wrapper; turns SDK errors into readable ones
│   ├── data.py             menu, coupons, tax, delivery zones — the "database"
│   ├── store.py            sessions, cart, orders (swap for Redis to persist)
│   ├── tools.py            the 12 functions — plain Python, zero LLM imports
│   ├── schemas.py          the JSON catalogue the model sees  ← the real prompt
│   ├── agent.py            THE LOOP — the file worth reading twice
│   └── server.py           FastAPI routes; owns no business logic
├── web/
│   ├── index.html          three-panel layout
│   ├── styles.css          one dark theme, no framework
│   └── app.js              vanilla JS — chat, tool trace, cart
└── tests/
    ├── test_tools.py       business logic, no network
    └── test_agent.py       the loop, driven by a scripted fake model
```

The dependency direction is one-way: `server → agent → tools → data/store`.
Nothing in `tools.py` imports the LLM, which is why the whole business layer is
testable in under a second.

### API

| Method | Route | Purpose |
|---|---|---|
| `GET` | `/api/health` | is Azure configured, which vars are missing |
| `GET` | `/api/menu` | dishes, categories, coupons |
| `GET` | `/api/tools` | the catalogue, derived from `TOOL_SCHEMAS` |
| `GET` | `/api/cart?session_id=` | current cart and orders |
| `POST` | `/api/chat` | `{session_id, message}` → reply + tool trace + cart |
| `POST` | `/api/reset` | wipe one session |

Interactive docs at `/docs` while the server is running.

---

## Troubleshooting

| Problem | Cause |
|---|---|
| Status pill says **missing …** | that variable is absent or still a placeholder in `.env` |
| `503` when chatting | same — the server started without credentials |
| `Azure returned HTTP 404` | `MINI_MODEL_NAME` is not a deployment on that resource. Use the *deployment* name from Azure AI Foundry, not the model name |
| `Azure rejected the API key` | wrong key, or the key belongs to a different resource than the endpoint |
| `429` | Azure rate limit — wait, or raise the deployment's TPM quota |
| Tool trace panel is empty | the model answered from history without calling anything. Reset the session and be more specific |

---

## Extending it

Adding a thirteenth tool is three edits, always in this order:

1. Write the function in `app/tools.py` (returning the `ok` envelope) and add
   it to `TOOL_REGISTRY`.
2. Add its schema to `TOOL_SCHEMAS` in `app/schemas.py`. **The `name` must match
   the registry key exactly** — that string is the entire contract.
3. Add a test in `tests/test_tools.py`.

The UI needs no changes: the Tools panel and the trace both render whatever the
registry and the schemas contain.

Other natural next steps: swap `SessionStore` for Redis, stream replies with
server-sent events, or add a second agent that handles complaints.

---

## License

MIT — see [LICENSE](LICENSE).
