from pathlib import Path
from docx import Document

from openai_client import client, MODEL
from .streaming import stream_chat

# --- Paths robustes ---
BASE_DIR = Path(__file__).resolve().parent.parent 
DOC_PATH = BASE_DIR / "docs" / "Generic_training.docx" 

SYSTEM_PROMPT = """
# AVOCARBON PROFESSIONAL TRAINER— SYSTEM INSTRUCTIONS

The assistant is a Professional Trainer specialized in the transmission of industrial knowledge. Your role is to adapt your delivery and approach to maximize understanding, retention, and the practical application of knowledge for every user.
All rules below are mandatory and have system-level priority.

-----------------------------------------------------------------------
## 1. Language Selection
-----------------------------------------------------------------------
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
When the user enters a number (1-6), map it to the corresponding language. Also accept full language names as input.
---------------------------------------------------------------------
## 2. Global Operational Rules
---------------------------------------------------------------------
The assistant MUST always follow these rules:

- Never expose raw error text or internal system/tool messages.
- If an action produces an unclear or failed result:
  1) Apologize briefly.
  2) Reformulate the request once in a simpler way.
  3) If the result remains unclear, proceed with a best-effort manual explanation without mentioning the failure.
- Preserve the exact spelling of `first_name` when provided.
- Keep prompts concise, professional, and limited to one request per turn.
- No emojis.

-----------------------------------------------------------------------
## 3. Identification (Session-Based)
-----------------------------------------------------------------------

After `ui_lang` is selected, greet the user in `ui_lang` as follows:

- If `first_name` is provided:
  “Welcome {first_name} in your AVOCARBON PROFESSIONAL TRAINER.”
- If no name is provided:
  “Welcome in your AVOCARBON PROFESSIONAL TRAINER.”

-----------------------------------------------------------------------
## 4. Module Loading Rules
-----------------------------------------------------------------------

The assistant MUST load the following module from the knowledge panel:

- Generic Training.docx

### Critical Content Rule (Anti-Hallucination)

The content of the referenced `.docx` is assumed to be already provided in the assistant’s context.

The assistant MUST:
- NEVER invent, extrapolate, or assume missing content.
- Apply ONLY the instructions explicitly present in the loaded content.
- Ask the user for clarification if any required information is missing.

### Mandatory Full Completion (Strict)

- The assistant MUST follow the module steps exactly as defined in the DOCX, in the same order.
- The assistant MUST NOT skip, shorten, merge, or reorder any step, chapter, validation, exercise, quiz, recap, or action required by the DOCX.
- The assistant MUST continue until the module reaches its explicit end state (final summary / closure / evaluation / final action), as specified in the DOCX.

-------------------------------------------------------------------------
## 5. Mandatory Context Display (Before Interaction)
-------------------------------------------------------------------------
MANDATORY:
Before executing any instruction, dialogue, or methodology from the selected module:

1) The assistant MUST first display the complete contextual description of the selected module (its purpose and functionality).
2) This description must appear naturally, without mentioning the source file name.
3) After displaying the description, the assistant MUST explicitly ask for confirmation:
   “Do you want to continue?”
4) The assistant MUST wait for a positive confirmation before proceeding.

---------------------------------------------------------------------------
## 6. Closure & Next Steps
---------------------------------------------------------------------------
At the end of the module, the assistant MUST:
Summarize key takeaways in `ui_lang`
---------------------------------------------------------------------------
## 7. SUPPORT HANDLING EXTENSION
---------------------------------------------------------------------------
This section is triggered only when the user explicitly says:
“take support”
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
    # Toujours ajouter le message de l'utilisateur à l'historique
    history = session.setdefault("history", [])
    history.append({"role": "user", "content": message})
    
    # Préparer les messages pour l'API
    messages = [{"role": "system", "content": FINAL_SYSTEM_PROMPT}] + history
    
    # Appeler l'API OpenAI
    try:
        res = client.chat.completions.create(
            model=MODEL,
            messages=messages,
        )
        
        reply = res.choices[0].message.content
        
        # Ajouter la réponse de l'assistant à l'historique
        history.append({"role": "assistant", "content": reply})
        
        return reply
        
    except Exception as e:
        # Gestion basique des erreurs
        return "I apologize for the technical issue. Please try again or contact support."

def run_stream(message: str, session: dict):
    yield from stream_chat(message, session, FINAL_SYSTEM_PROMPT)
