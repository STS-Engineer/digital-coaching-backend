import json
import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

from docx import Document

from openai_client import client, MODEL


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

- Generic_training.docx

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

### Quiz Control Rules (Critical)

During the final evaluation:
- Ask exactly 10 multiple-choice questions.
- Never ask a question more than once unless the user explicitly asks to retry.
- After question 10 is answered and corrected, you MUST stop the quiz.
- Immediately move to the final training summary and email tone confirmation.
- Never restart the quiz after question 10.
- Never repeat question 10 once it has been answered and corrected.

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

---------------------------------------------------------------------------
## 8. Email Sending Rules
---------------------------------------------------------------------------
- The final training summary email must always be sent to the active user's email provided by the application.
- The assistant MUST never ask the user for the recipient email address.
- The assistant MUST never ask the user to confirm the recipient email address.
- When the final summary email is ready, the assistant may ask for the preferred tone if required by the module.
- The assistant must then call the email tool without requesting any recipient address from the user.
""".strip()


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "sendTrainingEmail",
            "description": "Send the final training summary email in HTML format.",
            "parameters": {
                "type": "object",
                "properties": {
                    "to": {
                        "type": "string",
                        "description": "Recipient email address (auto-filled from the active user if omitted)"
                    },
                    "subject": {
                        "type": "string",
                        "description": "Email subject"
                    },
                    "ui_lang": {
                        "type": "string",
                        "description": "Selected user interface language"
                    },
                    "html_core": {
                        "type": "string",
                        "description": "Full HTML body of the training summary email"
                    }
                },
                "required": ["subject", "ui_lang", "html_core"],
                "additionalProperties": False
            }
        }
    }
]


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


def send_training_email(to: str, subject: str, ui_lang: str, html_core: str) -> dict:
    """
    Envoie l'email HTML via le serveur Outlook AVOCarbon
    """

    SMTP_HOST = os.getenv("SMTP_HOST", "avocarbon-com.mail.protection.outlook.com")
    SMTP_PORT = int(os.getenv("SMTP_PORT", "25"))
    EMAIL_FROM_NAME = os.getenv("EMAIL_FROM_NAME", "Administration STS")
    EMAIL_FROM = os.getenv("EMAIL_FROM", "administration.STS@avocarbon.com")

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = f"{EMAIL_FROM_NAME} <{EMAIL_FROM}>"
    msg["To"] = to
    msg.attach(MIMEText(html_core, "html", "utf-8"))

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=10) as server:
            server.sendmail(
                EMAIL_FROM,
                [to],
                msg.as_string()
            )

        return {
            "ok": True,
            "message": "Email sent successfully",
            "to": to,
            "subject": subject,
            "ui_lang": ui_lang
        }

    except Exception:
        return {
            "ok": False,
            "message": "Email sending failed"
        }


DOC_TEXT = load_docx_text(DOC_PATH)

FINAL_SYSTEM_PROMPT = (
    SYSTEM_PROMPT
    + "\n\n-----------------------------------------------------------------------\n"
    + "## LOADED MODULE CONTENT (DOCX)\n"
    + "-----------------------------------------------------------------------\n"
    + DOC_TEXT
)


def _extract_ui_lang(session: dict) -> str:
    return session.get("ui_lang", "English")


def _extract_active_email(session: dict) -> str:
    return (session.get("user_email") or "").strip()


def _build_active_email_system_message(active_email: str) -> dict:
    if active_email:
        content = (
            f"ACTIVE_USER_EMAIL={active_email}. "
            "If an email recipient is needed, use ACTIVE_USER_EMAIL. "
            "Never ask the user for a recipient email or confirmation. "
            "Do not mention the address unless the user explicitly asks."
        )
    else:
        content = (
            "ACTIVE_USER_EMAIL is not available. "
            "Never ask the user for a recipient email or confirmation. "
            "If an email must be sent, state it will be sent to the active user's email "
            "provided by the application once available."
        )
    return {"role": "system", "content": content}


def run(message: str, session: dict) -> str:
    history = session.setdefault("history", [])
    history.append({"role": "user", "content": message})

    active_email = _extract_active_email(session)
    base_messages = [
        {"role": "system", "content": FINAL_SYSTEM_PROMPT},
        _build_active_email_system_message(active_email),
    ]
    messages = base_messages + history

    try:
        first_response = client.chat.completions.create(
            model=MODEL,
            messages=messages,
            tools=TOOLS,
            tool_choice="auto",
        )

        assistant_message = first_response.choices[0].message

        if not getattr(assistant_message, "tool_calls", None):
            reply = assistant_message.content or ""
            history.append({"role": "assistant", "content": reply})
            return reply

        history.append(
            {
                "role": "assistant",
                "content": assistant_message.content or "",
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": tc.type,
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        },
                    }
                    for tc in assistant_message.tool_calls
                ],
            }
        )

        for tc in assistant_message.tool_calls:
            if tc.function.name == "sendTrainingEmail":
                try:
                    args = json.loads(tc.function.arguments or "{}")
                except json.JSONDecodeError:
                    args = {}

                if not active_email:
                    result = {
                        "ok": False,
                        "message": "Active user email not available",
                    }
                else:
                    result = send_training_email(
                        to=active_email,
                        subject=args.get("subject", "Training Summary"),
                        ui_lang=args.get("ui_lang") or _extract_ui_lang(session),
                        html_core=args.get("html_core", ""),
                    )

                history.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": json.dumps(result, ensure_ascii=False),
                    }
                )

        second_response = client.chat.completions.create(
            model=MODEL,
            messages=base_messages + history,
        )

        final_reply = second_response.choices[0].message.content or ""
        history.append({"role": "assistant", "content": final_reply})
        return final_reply

    except Exception:
        return "I apologize for the technical issue. Please try again or contact support."


def run_stream(message: str, session: dict):
    yield run(message, session)
