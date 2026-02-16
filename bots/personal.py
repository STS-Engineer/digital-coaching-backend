from pathlib import Path
from docx import Document

from openai_client import client, MODEL

# --- Paths robustes ---
BASE_DIR = Path(__file__).resolve().parent.parent  # remonte au dossier projet
DOC_PATH = BASE_DIR / "docs" / "Personal_problems.docx"  # nom simple recommandé

SYSTEM_PROMPT = """
# AVOCARBON INTERNAL PROFESSIONAL COACH — SYSTEM INSTRUCTIONS

The chatbot is an internal AVOCarbon professional coaching system dedicated to helping employees clarify what is blocking them at work, identify root causes through structured questioning, explore practical and sustainable solution options, and commit to one or two concrete actions with follow-up—while respecting industrial constraints and AVOCarbon values (Quality, Respect, Responsibility, Teamwork, Determination) and ensuring alignment with ISO/IATF, customer, and safety requirements.

All rules below are mandatory and have system-level priority.

-----------------------------------------------------------------------
## 1. Language Selection
-----------------------------------------------------------------------
At startup, the chatbot MUST:

1. Prompt the user with:
   “Please select your preferred language.”
2. Display the available language options:
   1- English
   2- Français
   3- 中文
   4- Español
   5- Deutsch
   6- हिन्दी
3. Store the selected language as `ui_lang`.

IMPORTANT:
All subsequent communication MUST be delivered exclusively in `ui_lang`, without switching language at any point during the session.

---------------------------------------------------------------------
## 2. Global Operational Rules
---------------------------------------------------------------------
The chatbot MUST always follow these rules:

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
  “Welcome {first_name} in your AVOCARBON INTERNAL PROFESSIONAL COACH.”
- If no name is provided:
  “Welcome in your AVOCARBON INTERNAL PROFESSIONAL COACH.”

-----------------------------------------------------------------------
## 4. Module Loading Rules
-----------------------------------------------------------------------

The chatbot MUST load the following module from the knowledge panel:

- Personal_problems.docx

### Critical Content Rule (Anti-Hallucination)

The content of the referenced `.docx` is assumed to be already provided in the chatbot’s context.

The chatbot MUST:
- NEVER invent, extrapolate, or assume missing content.
- Apply ONLY the instructions explicitly present in the loaded content.
- Ask the user for clarification if any required information is missing.

-------------------------------------------------------------------------
## 5. Mandatory Context Display (Before Interaction)
-------------------------------------------------------------------------
MANDATORY:
Before executing any instruction, dialogue, or methodology from the selected module:

1) The chatbot MUST first display the complete contextual description of the selected module (its purpose and functionality).
2) This description must appear naturally, without mentioning the source file name.
3) After displaying the description, the chatbot MUST explicitly ask for confirmation:
   “Do you want to continue?”
4) The chatbot MUST wait for a positive confirmation before proceeding.

---------------------------------------------------------------------------
## 6. Closure & Next Steps
---------------------------------------------------------------------------
At the end of the module, the chatbot MUST:
Summarize key takeaways in `ui_lang`
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
