import json
import re
from pathlib import Path

from docx import Document

from openai_client import client, MODEL
from rfq_db import init_rfq_db, rfq_session, list_product_lines, list_products, list_products_grouped_by_line, search_products_by_name

# --- Paths robustes ---
BASE_DIR = Path(__file__).resolve().parent.parent 
DOC_PATH = BASE_DIR / "docs" / "Product_line_exploration.docx" 

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

LANG_MENU = """Please select your preferred language.
1- English
2- Français
3- 中文
4- Español
5- Deutsch
6- हिन्दी
"""

LANG_MAP = {
    "1": "English",
    "2": "Français",
    "3": "中文",
    "4": "Español",
    "5": "Deutsch",
    "6": "हिन्दी",
    "english": "English",
    "français": "Français",
    "francais": "Français",
    "中文": "中文",
    "español": "Español",
    "espanol": "Español",
    "deutsch": "Deutsch",
    "हिन्दी": "हिन्दी",
    "hindi": "हिन्दी",
}

def build_rfq_context_for_chatbot(user_message: str) -> str | None:
    text = (user_message or "").strip()
    if not text:
        return None

    t = text.lower()

    # Déclenchement UNIQUEMENT sur option 3 ou 4
    is_opt3 = t in {"3", "option 3", "3.", "3)"} or t.startswith("3 ")
    is_opt4 = t in {"4", "option 4", "4.", "4)"} or t.startswith("4 ")

    if not (is_opt3 or is_opt4):
        return None

    payload = {}

    try:
        with rfq_session() as db:
            if is_opt3:
                # Option 3: liste des lignes
                payload["productLinesList"] = list_product_lines(db, limit=100)

            if is_opt4:
                # Option 4: liste des produits
                payload["productsByLine"] = list_products_grouped_by_line(db, limit=2000)

    except Exception:
        return None

    if not payload:
        return None

    return "RFQ_DATABASE_CONTEXT:\n" + json.dumps(payload, ensure_ascii=False, indent=2)


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


# --- Charge une seule fois au démarrage ---
DOC_TEXT = load_docx_text(DOC_PATH)

FINAL_SYSTEM_PROMPT = (
    SYSTEM_PROMPT
    + "\n\n-----------------------------------------------------------------------\n"
    + "## LOADED MODULE CONTENT (DOCX)\n"
    + "-----------------------------------------------------------------------\n"
    + DOC_TEXT
)


def run(message: str, session: dict) -> str:
    history = session.setdefault("history", [])
    history.append({"role": "user", "content": message})

    msg = (message or "").strip().lower()

    # 1) Mémoriser le choix quand user tape 3/4
    if msg in {"3", "option 3", "3.", "3)"}:
        session["pending_option"] = "3"
    elif msg in {"4", "option 4", "4.", "4)"}:
        session["pending_option"] = "4"

    # 2) Injecter le contexte DB:
    # - soit quand user tape 3/4
    # - soit si on est déjà dans un flow 3/4 (pending_option)
    db_context = build_rfq_context_for_chatbot(message)

    if not db_context and session.get("pending_option") in {"3", "4"}:
        # Réinjecter la liste correspondant au flow en cours, même si user a écrit "ok"
        db_context = build_rfq_context_for_chatbot(session["pending_option"])

    messages = [{"role": "system", "content": FINAL_SYSTEM_PROMPT}]
    if db_context:
        messages.append({"role": "system", "content": db_context})
    messages += history

    try:
        res = client.chat.completions.create(model=MODEL, messages=messages)
        reply = res.choices[0].message.content or ""
        history.append({"role": "assistant", "content": reply})
        return reply
    except Exception:
        return "I apologize for the technical issue. Please try again or contact support."
