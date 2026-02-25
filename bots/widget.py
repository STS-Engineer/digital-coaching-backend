import os
import re
import smtplib
import ssl
from email.utils import formataddr
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime
from groq_client import client, MODEL

SYSTEM_PROMPT = (
    "You are a product support assistant for the Digital Coaching app. "
    "Your scope is to answer questions about how the app works: chat usage, bots, chat history, troubleshooting, and human support requests (including sending support emails). "
    "If the user asks for human support or reports a technical issue, "
    "ask them ONCE to describe the issue in detail (what they did, what they expected, and any error message). "
    "After they provide the details, do NOT ask again. The system will send it to technical support. "
    "If the user says they have a question about something and asks which bot to use, "
    "guide them to the best of the five available bots based on their topic. "
    "When the user asks which bot to use, recommend the best bot and explain why. "
    "If the request is ambiguous, ask one short clarifying question before recommending. "
    "BOT GUIDE: "
    "Personal Problems Assistant = workplace challenges, wellbeing, blockers, and coaching. "
    "Product and Product Lines Assistant = product strategy, product knowledge, and product line details. "
    "Problem Formalization Assistant = structure a problem, root causes (5 Whys), impacts, and action steps. "
    "Generic Training Assistant = interactive training, lessons, and quizzes. "
    "Write Email Assistant = drafting professional emails with clear structure and tone. "
    "If the user asks for coaching content, explain that this help chat "
    "is for support and suggest using the main assistant chats. "
    "LANGUAGE OVERRIDE: Always respond in the language of the user's latest message. "
    "If the user switches languages, switch immediately. "
    "Ignore the language used in previous turns; follow only the latest user message. "
    "Do not ask the user to select a language. "
    "Be concise and direct. Answer only what the user asked. "
    "Do not add extra information that the user did not mention. "
    "Use only information explicitly present in this prompt or provided by the user. "
    "If information is missing, say you don't know and ask a short clarifying question. "
    "Keep responses professional."
)

SMTP_HOST = "avocarbon-com.mail.protection.outlook.com"
SMTP_PORT = 25
EMAIL_FROM_NAME = "Administration STS"
EMAIL_FROM = "administration.STS@avocarbon.com"
SUPPORT_EMAIL = "ons.ghariani@avocarbon.com"


def detect_language(text: str) -> str:
    """
    Detect language using ONLY the LLM.
    Returns ISO 639-1 2-letter code (e.g., en, fr, ar, es).
    """
    try:
        res = client.chat.completions.create(
            model=MODEL,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Detect the language of the user's message. "
                        "Return ONLY the ISO 639-1 language code (2 letters). "
                        "Examples: en, fr, ar, es, de, it. "
                        "No punctuation. No extra text."
                    ),
                },
                {"role": "user", "content": text or ""},
            ],
            temperature=0,
            max_tokens=5,
        )
        raw = (res.choices[0].message.content or "").strip().lower()
        m = re.search(r"\b[a-z]{2}\b", raw)
        if m:
            return m.group(0)
    except Exception as exc:
        print(f"[LANG DETECT] Failed: {exc}")

    return "en"


def translate_to_lang(text: str, lang: str) -> str:
    """
    Translate using ONLY the LLM. Strictly return target language output.
    """
    if not text or lang in {"en", "fr"}:
        return text

    try:
        res = client.chat.completions.create(
            model=MODEL,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Translate the text into the target language specified by the ISO 639-1 code. "
                        "Return ONLY the translated text in the target language. "
                        "Do not include English. Do not add explanations. Do not add alternatives. "
                        "Do not wrap in quotes."
                    ),
                },
                {"role": "user", "content": f"Target language: {lang}\n\nText:\n{text}"},
            ],
            temperature=0,
            max_tokens=800,
        )
        translated = (res.choices[0].message.content or "").strip()
        return translated or text
    except Exception as exc:
        print(f"[LANG TRANSLATE] Failed: {exc}")
        return text


def localize_template(en_text: str, fr_text: str, user_message: str, lang: str) -> str:
    if lang == "fr":
        return fr_text
    if lang == "en":
        return en_text
    return translate_to_lang(en_text, lang)


def build_subject(details: str, lang: str) -> str:
    """Generate a concise, meaningful email subject that summarizes the issue."""
    if not details or not details.strip():
        return {
            "fr": "Demande de support Digital Coaching",
            "en": "Digital Coaching Support Request",
            "default": "Digital Coaching Support Request",
        }.get(lang, "Digital Coaching Support Request")

    system_prompt = (
        "You are a support ticket summarizer. Create a VERY concise subject line (max 8-10 words) "
        "that captures the core issue. Focus on the main problem, error, or feature mentioned. "
        "Return ONLY the subject line, no quotes, no extra text. "
        "The subject should be in the same language as the user's description."
    )

    lang_instruction = f"The user's language is: {lang}. " if lang not in ["en", "fr"] else ""
    user_prompt = f"{lang_instruction}Summarize this support request into a brief subject line: {details}"

    try:
        res = client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.3,
            max_tokens=30,
        )
        summary = (res.choices[0].message.content or "").strip()
        summary = summary.strip('"\'.,;:')

        if len(summary) > 80:
            summary = summary[:77].rstrip() + "..."

        return summary

    except Exception as exc:
        print(f"[SUBJECT] AI summary failed: {exc}")
        words = details.split()[:6]
        return " ".join(words) + ("..." if len(details.split()) > 6 else "")


def build_html_body(description: str, user_email: str, lang: str) -> str:
    """Build a clean and modern HTML email body with a single blue-accent card."""

    labels = {
        "fr": {
            "title": "Demande de Support Digital Coaching",
            "email": "📧 Email utilisateur :",
            "received": "📅 Requête reçue le :",
            "problem": "❓ Description du problème :",
            "footer": "© 2026 Digital Coaching - Support Technique",
        },
        "en": {
            "title": "Digital Coaching Support Request",
            "email": "📧 User email :",
            "received": "📅 Request received on :",
            "problem": "❓ Problem description :",
            "footer": "© 2026 Digital Coaching - Technical Support",
        },
    }

    label = labels.get(lang, labels["en"])
    current_date = datetime.now().strftime("%Y-%m-%d %H:%M")

    description_escaped = (
        (description or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace("\n", "<br>")
    )

    html = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<style>
    body {{
        font-family: 'Segoe UI', Arial, sans-serif;
        background-color: #f4f6f8;
        margin: 0;
        padding: 30px 15px;
        color: #333;
    }}

    .container {{
        max-width: 600px;
        margin: auto;
        background-color: #ffffff;
        border-radius: 10px;
        box-shadow: 0 4px 15px rgba(0,0,0,0.05);
        padding: 30px;
    }}

    .title {{
        font-size: 22px;
        font-weight: 600;
        color: #000000;
        margin-bottom: 25px;
        text-align: center;
    }}

    .card {{
        display: flex;
        border-radius: 8px;
        overflow: hidden;
        border: 1px solid #e5e7eb;
    }}

    .card-accent {{
        width: 6px;
        background-color: #2563eb;
    }}

    .card-content {{
        padding: 20px;
        flex: 1;
    }}

    .row {{
        margin-bottom: 18px;
    }}

    .label {{
        font-weight: 600;
        color: #111827;
        margin-bottom: 6px;
    }}

    .value {{
        background-color: #f9fafb;
        padding: 10px 12px;
        border-radius: 6px;
        border: 1px solid #e5e7eb;
        font-size: 14px;
    }}

    .footer {{
        margin-top: 30px;
        font-size: 12px;
        color: #6b7280;
        text-align: center;
    }}
</style>
</head>

<body>
    <div class="container">

        <div class="title">
            {label['title']}
        </div>

        <div class="card">
            <div class="card-accent"></div>
            <div class="card-content">

                <div class="row">
                    <div class="label">{label['email']}</div>
                    <div class="value"><strong>{user_email or 'Not provided'}</strong></div>
                </div>

                <div class="row">
                    <div class="label">{label['received']}</div>
                    <div class="value">{current_date}</div>
                </div>

                <div class="row">
                    <div class="label">{label['problem']}</div>
                    <div class="value">
                        {description_escaped}
                    </div>
                </div>

            </div>
        </div>

        <div class="footer">
            {label['footer']}<br>
        </div>

    </div>
</body>
</html>"""
    return html


def build_text_body(description: str, user_email: str, lang: str) -> str:
    """Build a plain text version of the email (fallback)."""
    if lang == "fr":
        lines = [
            "=" * 50,
            "DEMANDE DE SUPPORT DIGITAL COACHING",
            "=" * 50,
            f"Email utilisateur : {user_email or 'inconnu'}",
            f"Date : {datetime.now().strftime('%Y-%m-%d %H:%M')}",
            "-" * 50,
            "DESCRIPTION DU PROBLEME",
            "-" * 50,
            (description or "").strip(),
            "=" * 50,
            "© 2026 Digital Coaching - Support Technique",
        ]
    else:
        lines = [
            "=" * 50,
            "DIGITAL COACHING SUPPORT REQUEST",
            "=" * 50,
            f"User email: {user_email or 'unknown'}",
            f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
            "-" * 50,
            "PROBLEM DESCRIPTION",
            "-" * 50,
            (description or "").strip(),
            "=" * 50,
            "© 2026 Digital Coaching - Technical Support",
        ]
    return "\n".join(lines).strip()


def send_support_email(subject: str, description: str, user_email: str, lang: str) -> bool:
    """Send a beautifully formatted HTML email with plain text fallback."""
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = formataddr((EMAIL_FROM_NAME, EMAIL_FROM))
    msg["To"] = SUPPORT_EMAIL

    if user_email:
        msg["Reply-To"] = user_email
        msg["X-User-Email"] = user_email

    msg["X-Priority"] = "3"
    msg["X-Mailer"] = "Digital Coaching Support System"

    text_part = MIMEText(build_text_body(description, user_email, lang), "plain", "utf-8")
    html_part = MIMEText(build_html_body(description, user_email, lang), "html", "utf-8")

    msg.attach(text_part)
    msg.attach(html_part)

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=10) as server:
            server.ehlo()
            try:
                context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
                context.check_hostname = False
                context.verify_mode = ssl.CERT_NONE
                server.starttls(context=context)
                server.ehlo()
            except Exception:
                pass

            server.send_message(msg)
        return True
    except Exception as exc:
        print(f"[SUPPORT EMAIL] Failed to send: {exc}")
        return False


def polish_description(details: str, lang: str) -> str:
    system = (
        "You are an editor. "
        "Fix grammar, spelling, and punctuation ONLY. "
        "Do NOT add or remove information. "
        "Do NOT paraphrase. "
        "Do NOT ask questions. "
        "Return ONLY the corrected text in the same language."
    )
    user = f"Text:\n{details}"
    try:
        res = client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=0.1,
            max_tokens=600,
        )
        cleaned = (res.choices[0].message.content or "").strip()
        if cleaned:
            return cleaned
    except Exception as exc:
        print(f"[SUPPORT EMAIL] Grammar fix failed: {exc}")

    return " ".join((details or "").strip().split())


def resolve_system_prompt(session: dict) -> str:
    return SYSTEM_PROMPT

def run(message: str, session: dict) -> str:
    history = session.setdefault("history", [])
    history.append({"role": "user", "content": message})

    stage = session.get("stage", "idle")
    user_email = session.get("user_email") or ""
    lang = detect_language(message)

    # If we are waiting for details → send email immediately
    if stage == "support_waiting_details":
        details = message.strip()
        description = polish_description(details, lang)
        subject = build_subject(description, lang)

        ok = send_support_email(
            subject=subject,
            description=description,
            user_email=user_email,
            lang=lang,
        )

        session["stage"] = "idle"

        return (
            f"Thanks. Your request has been sent to technical support ({SUPPORT_EMAIL})."
            if ok
            else f"Sending failed. Please contact {SUPPORT_EMAIL} directly."
        )

    system_prompt = resolve_system_prompt(session)
    messages = [{"role": "system", "content": system_prompt}] + history

    try:
        res = client.chat.completions.create(
            model=MODEL,
            messages=messages,
        )
        reply = (res.choices[0].message.content or "").strip()
        history.append({"role": "assistant", "content": reply})

        if "describe the issue" in reply.lower() or "décrivez le problème" in reply.lower():
            session["stage"] = "support_waiting_details"

        return reply

    except Exception:
        return "Technical issue. Please try again."
