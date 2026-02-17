import json
import re
from pathlib import Path

from docx import Document

from openai_client import client, MODEL
from rfq_db import init_rfq_db, rfq_session, list_product_lines, get_product_line_by_id, list_products, list_products_grouped_by_line, search_products_by_name

# --- Paths robustes ---
BASE_DIR = Path(__file__).resolve().parent.parent 
DOC_PATH = BASE_DIR / "docs" / "Product_line_exploration.docx" 

# ✅ à appeler UNE seule fois au démarrage de l'app
init_rfq_db()

SYSTEM_PROMPT = """
# DIGITAL COACHING SYSTEM — PRODUCT & PRODUCT LINES ONLY (SYSTEM INSTRUCTIONS)

The assistant is a **modular digital coaching system** designed to operate through structured, professional workflows.
All rules below are **mandatory and have system-level priority**.

========================
1) LANGUAGE SELECTION
========================
At startup, the assistant MUST:

1. Prompt the user with:
   “Please select your preferred language.”
2. Display the available language options:
   - English
   - Français
   - 中文
   - Español
   - Deutsch
   - हिन्दी
3. Store the selected language as `ui_lang`.
4. All subsequent communication MUST be delivered exclusively in `ui_lang`, without switching language at any point during the session.

IMPORTANT: 
When the user enters a number (1-6), map it to the corresponding language. Also accept full language names as input.========================
========================
2) GLOBAL OPERATIONAL RULES
========================
The assistant MUST always follow these rules:

- Never expose raw error text.
- If an action produces an unclear or failed result:
  1) Apologize briefly.
  2) Reformulate the request once in a simpler way.
  3) If the result remains unclear, proceed with a best-effort manual explanation without mentioning the failure.
- Preserve the exact spelling of `first_name` when provided.
- Keep prompts concise, professional, and limited to **one request per turn**.
- No emojis.
===========================
3) IDENTIFICATION (SESSION-BASED)
===========================
Greet the user in `ui_lang` as follows:

- If `first_name` is provided:
  “Welcome {first_name} in your Digital Coach assistant.”
- If no name is provided:
  “Welcome in your digital assistant.”

========================
4) Single Module Mode 
========================
Immediately after the greeting, in the **same turn**, the assistant MUST:

- Announce that this assistant operates for:
  **Product and Product Lines understanding**.
- The assistant MUST NOT display a main menu and MUST NOT offer other topics.
===========================
 5) Module Loading Rules (Fixed)
===========================
The assistant MUST always load the following module:

- **Product and Product Lines understanding** → Product_line_exploration.docx
===========================
 Critical Content Rule (Anti-Hallucination)
===========================
The content of the referenced `.docx` is assumed to be **already provided in the assistant’s context**.

The assistant MUST:

- NEVER invent, extrapolate, or assume missing content.
- Apply ONLY the instructions explicitly present in the loaded content.
- Ask the user for clarification if any required information is missing.

If the module content is not present/available in the current conversation, the assistant MUST ask the user to paste or upload the relevant module content before proceeding.

## 6. Mandatory Context Display (Before Interaction)

MANDATORY

Before executing any instruction, dialogue, or methodology from the module:

1. The assistant MUST first display the **complete contextual description** of the module (its purpose and functionality).
2. This description must appear naturally, without mentioning the source file name.
3. After displaying the description, the assistant MUST explicitly ask for confirmation:
   “Do you want to continue?”
4. The assistant MUST wait for a positive confirmation before proceeding.
========================
7) INTERACTION RULES (WHEN MODULE IS ACTIVE)
========================
When the module is active, the assistant MUST:

- Apply the methodology from the selected module **exactly**.
- Maintain:
  - Neutral, professional tone
  - Structured, step-by-step progression
  - Validation before advancing
  - Active listening and reformulation
  - No emojis
- Keep prompts limited to **one request per turn**.

If the user requests anything outside product/product line understanding, the assistant MUST politely refuse and restate the scope:
“This assistant only covers Product and Product Lines understanding.”
========================
8) CLOSURE & NEXT STEPS
========================
At the end of the module interaction, the assistant MUST:

1. Summarize key takeaways in `ui_lang`.
2. Offer next actions such as:
   - Saving progress
   - Generating an output (product brief, comparison summary, internal Q&A, etc.)
3. Remind the user they can ask a new product/product-line question at any time.
""".strip()

def load_docx_text(path: Path) -> str:
    if not path.exists():
        raise FileNotFoundError(f"DOCX not found at: {path}")
    doc = Document(str(path))
    parts = []
    for p in doc.paragraphs:
        txt = (p.text or "").strip()
        if txt:
            parts.append(txt)
    return "\n".join(parts)

DOC_TEXT = load_docx_text(DOC_PATH)
FINAL_SYSTEM_PROMPT = SYSTEM_PROMPT + "\n\n" + DOC_TEXT

# --- Anti-narration output cleaning (safety net) ---
_FORBIDDEN_LINE_PATTERNS = [
    r"(?im)^\s*i will.*$",
    r"(?im)^\s*please hold.*$",
    r"(?im)^\s*please wait.*$",
    r"(?im)^\s*you selected.*$",
    r"(?im)^\s*i have noted.*$",
    r"(?im)^\s*i am going to.*$",
    r"(?im)^\s*are you ready.*$",
]

def strip_narration(text: str) -> str:
    if not text:
        return text
    out = text
    for pat in _FORBIDDEN_LINE_PATTERNS:
        out = re.sub(pat, "", out)
    lines = [ln.rstrip() for ln in out.splitlines() if ln.strip()]
    return "\n".join(lines).strip()

# -----------------------
# DB context builders
# -----------------------

def build_product_lines_list_context() -> str | None:
    try:
        with rfq_session() as db:
            payload = {"productLinesList": list_product_lines(db, limit=200)}
        return "RFQ_DATABASE_CONTEXT:\n" + json.dumps(payload, ensure_ascii=False, indent=2, default=str)
    except Exception:
        return None

def build_products_grouped_context() -> str | None:
    try:
        with rfq_session() as db:
            payload = {"productsByLine": list_products_grouped_by_line(db, limit=2000)}
        return "RFQ_DATABASE_CONTEXT:\n" + json.dumps(payload, ensure_ascii=False, indent=2, default=str)
    except Exception:
        return None

def build_product_line_detail_context(product_line_id: int) -> str | None:
    try:
        with rfq_session() as db:
            line = get_product_line_by_id(db, int(product_line_id))
            if not line:
                return None
            payload = {"productLineDetail": line}
        return "RFQ_DATABASE_CONTEXT:\n" + json.dumps(payload, ensure_ascii=False, indent=2, default=str)
    except Exception:
        return None

def build_product_detail_context(product_query: str) -> str | None:
    """
    Option 4 step 2: search product by name and inject ALL columns result(s)
    """
    try:
        with rfq_session() as db:
            matches = search_products_by_name(db, product_query, limit=10)
            if not matches:
                return None
            payload = {"productDetails": matches}
        return "RFQ_DATABASE_CONTEXT:\n" + json.dumps(payload, ensure_ascii=False, indent=2, default=str)
    except Exception:
        return None

# -----------------------
# Runner
# -----------------------

def run(message: str, session: dict) -> str:
    history = session.setdefault("history", [])
    history.append({"role": "user", "content": message})

    raw = (message or "").strip()
    msg = raw.lower()
    stage = (session.get("stage") or "select_lang").strip()

    db_context = None

    # OPTION 3: waiting for numeric product_line_id
    if stage == "await_product_line_id":
        if raw.isdigit():
            db_context = build_product_line_detail_context(int(raw))
            if db_context:
                session["stage"] = "in_module"
        if not db_context:
            # stay in same stage if invalid
            session["stage"] = "await_product_line_id"

    # OPTION 4: waiting for product query/name (free text)
    if not db_context and stage == "await_product_query":
        if raw:
            db_context = build_product_detail_context(raw)
            if db_context:
                session["stage"] = "in_module"
            else:
                # stay waiting if no match
                session["stage"] = "await_product_query"

    # Menu triggers (only if not already handled)
    if not db_context:
        is_opt3 = msg in {"3", "option 3", "3.", "3)"} or msg.startswith("3 ")
        is_opt4 = msg in {"4", "option 4", "4.", "4)"} or msg.startswith("4 ")

        if is_opt3:
            session["stage"] = "await_product_line_id"
            db_context = build_product_lines_list_context()

        elif is_opt4:
            session["stage"] = "await_product_query"
            db_context = build_products_grouped_context()

    messages = [{"role": "system", "content": FINAL_SYSTEM_PROMPT}]

    # lock language every turn if you store ui_lang in conversation
    ui_lang = session.get("ui_lang") or "English"
    messages.append({"role": "system", "content": f"UI_LANG={ui_lang}. Respond ONLY in UI_LANG."})

    if db_context:
        messages.append({"role": "system", "content": db_context})
        messages.append({
            "role": "system",
            "content": (
                "RFQ_DB_MODE=ON. Use ONLY RFQ_DATABASE_CONTEXT. "
                "No narration. No confirmations. Answer immediately."
            )
        })

    messages += history

    try:
        res = client.chat.completions.create(model=MODEL, messages=messages)
        reply = res.choices[0].message.content or ""
        reply = strip_narration(reply)
        history.append({"role": "assistant", "content": reply})
        return reply
    except Exception:
        return "I apologize for the technical issue. Please try again or contact support."


def run_stream(message: str, session: dict):
    history = session.setdefault("history", [])
    history.append({"role": "user", "content": message})

    raw = (message or "").strip()
    msg = raw.lower()
    stage = (session.get("stage") or "select_lang").strip()

    db_context = None

    if stage == "await_product_line_id":
        if raw.isdigit():
            db_context = build_product_line_detail_context(int(raw))
            if db_context:
                session["stage"] = "in_module"
        if not db_context:
            session["stage"] = "await_product_line_id"

    if not db_context and stage == "await_product_query":
        if raw:
            db_context = build_product_detail_context(raw)
            if db_context:
                session["stage"] = "in_module"
            else:
                session["stage"] = "await_product_query"

    if not db_context:
        is_opt3 = msg in {"3", "option 3", "3.", "3)"} or msg.startswith("3 ")
        is_opt4 = msg in {"4", "option 4", "4.", "4)"} or msg.startswith("4 ")

        if is_opt3:
            session["stage"] = "await_product_line_id"
            db_context = build_product_lines_list_context()
        elif is_opt4:
            session["stage"] = "await_product_query"
            db_context = build_products_grouped_context()

    messages = [{"role": "system", "content": FINAL_SYSTEM_PROMPT}]
    ui_lang = session.get("ui_lang") or "English"
    messages.append({"role": "system", "content": f"UI_LANG={ui_lang}. Respond ONLY in UI_LANG."})

    if db_context:
        messages.append({"role": "system", "content": db_context})
        messages.append({
            "role": "system",
            "content": (
                "RFQ_DB_MODE=ON. Use ONLY RFQ_DATABASE_CONTEXT. "
                "No narration. No confirmations. Answer immediately."
            )
        })

    messages += history

    parts = []
    try:
        stream = client.chat.completions.create(model=MODEL, messages=messages, stream=True)
        for chunk in stream:
            delta = chunk.choices[0].delta.content if chunk.choices else None
            if not delta:
                continue
            parts.append(delta)
            yield delta
    except Exception:
        error_text = "I apologize for the technical issue. Please try again or contact support."
        parts = [error_text]
        yield error_text

    reply = strip_narration("".join(parts))
    history.append({"role": "assistant", "content": reply})
