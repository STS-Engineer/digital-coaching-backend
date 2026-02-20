import os
import re
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr

from groq_client import client, MODEL

SYSTEM_PROMPT = (
    "You are a product support assistant for the Digital Coaching app. "
    "Your scope is to answer questions about how the app works: chat usage, bots, chat history, troubleshooting, and human support requests (including sending support emails). "
    "If the user reports a technical problem or asks for human support, "
    "ask them to describe the issue in detail and tell them you will send it "
    "to technical support at ons.ghariani@avocarbon.com. "
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
    "Keep responses concise, actionable, and professional."
)

SMTP_HOST = "avocarbon-com.mail.protection.outlook.com"
SMTP_PORT = 25
EMAIL_FROM_NAME = "Administration STS"
EMAIL_FROM = "administration.STS@avocarbon.com"
SUPPORT_EMAIL = "ons.ghariani@avocarbon.com"

print(
    "[SMTP CONFIG]",
    {
        "host": SMTP_HOST,
        "port": SMTP_PORT,
        "from": EMAIL_FROM,
        "fromName": EMAIL_FROM_NAME,
    },
)


def wants_human_support(text: str) -> bool:
    t = (text or "").lower()
    keywords = [
        "support humain",
        "support technique",
        "assistance humaine",
        "aide humaine",
        "contacter le support",
        "contact support",
        "human support",
        "technical support",
        "helpdesk",
        "ticket",
        "bug",
        "erreur",
        "error",
        "not working",
        "ne fonctionne pas",
        "ça marche pas",
        "probleme technique",
        "problème technique",
    ]
    return any(k in t for k in keywords)


def detect_language(text: str) -> str:
    t = (text or "").lower()
    if any(ch in t for ch in "àâäçéèêëîïôöùûüÿœ"):
        return "fr"

    tokens = re.findall(r"[a-zA-Z']+", t)
    fr_words = {
        "bonjour",
        "salut",
        "merci",
        "probleme",
        "problème",
        "aide",
        "erreur",
        "connexion",
        "mot",
        "passe",
        "utiliser",
        "fonctionne",
        "marche",
        "question",
        "besoin",
        "je",
        "tu",
        "vous",
        "nous",
        "le",
        "la",
        "les",
        "des",
        "dans",
        "pour",
        "avec",
    }
    en_words = {
        "i",
        "you",
        "your",
        "please",
        "help",
        "support",
        "problem",
        "error",
        "login",
        "password",
        "not",
        "working",
        "app",
        "question",
        "need",
        "issue",
        "bot",
        "use",
    }

    fr_score = sum(1 for tok in tokens if tok in fr_words)
    en_score = sum(1 for tok in tokens if tok in en_words)

    if fr_score > en_score:
        return "fr"
    if en_score > fr_score:
        return "en"
    if fr_score == 0 and en_score == 0:
        return "other"
    return "other"


EN_MARKERS = {
    "hello",
    "hi",
    "please",
    "thanks",
    "thank",
    "welcome",
    "how",
    "what",
    "why",
    "can",
    "could",
    "would",
    "help",
    "support",
    "issue",
    "problem",
    "error",
    "login",
    "password",
    "app",
}


def strip_english_translation(text: str, lang: str) -> str:
    if not text or lang == "en":
        return text

    def should_strip(segment: str) -> bool:
        lower = segment.lower()
        if any(marker in lower for marker in EN_MARKERS):
            return True
        ascii_ratio = sum(1 for c in segment if c.isascii()) / max(1, len(segment))
        if ascii_ratio > 0.9 and re.search(r"[a-zA-Z]{3,}", segment):
            return True
        return False

    def remove_groups(pattern: str, value: str) -> str:
        def repl(match):
            seg = match.group(1)
            return "" if should_strip(seg) else match.group(0)

        return re.sub(pattern, repl, value)

    cleaned = text
    cleaned = remove_groups(r"\(([^()]*)\)", cleaned)
    cleaned = remove_groups(r"\"([^\"]*)\"", cleaned)
    cleaned = remove_groups(r"'([^']*)'", cleaned)
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip()
    return cleaned


def get_previous_user_message(history: list[dict]) -> str:
    for msg in reversed(history[:-1]):
        if msg.get("role") == "user":
            return msg.get("content") or ""
    return ""


def localize_template(en_text: str, fr_text: str, user_message: str, lang: str) -> str:
    if lang == "fr":
        return fr_text
    if lang == "en":
        return en_text
    if not user_message:
        return en_text

    system = (
        "Translate the assistant message into the same language as the user's message. "
        "Return ONLY the translated text, no extra commentary."
    )
    user = f"User message:\n{user_message}\n\nAssistant message:\n{en_text}"
    try:
        res = client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=0.2,
        )
        translated = (res.choices[0].message.content or "").strip()
        return strip_english_translation(translated or en_text, lang)
    except Exception as exc:
        print(f"[SUPPORT EMAIL] Localization failed: {exc}")
        return en_text


def build_subject(details: str, lang: str) -> str:
    clean = " ".join((details or "").strip().split())
    if not clean:
        return "Demande de support" if lang == "fr" else "Support request"
    summary = clean
    if len(summary) > 80:
        summary = summary[:77].rstrip() + "..."
    prefix = "Demande de support" if lang == "fr" else "Support request"
    return f"{prefix}: {summary}"


def build_body_from_description(description: str, user_email: str, lang: str) -> str:
    if lang == "fr":
        lines = [
            "Demande de support Digital Coaching",
            f"Email utilisateur: {user_email or 'inconnu'}",
            "",
            "Description du probleme:",
            description.strip(),
        ]
    else:
        lines = [
            "Digital Coaching Support Request",
            f"User email: {user_email or 'unknown'}",
            "",
            "Problem description:",
            description.strip(),
        ]
    return "\n".join(lines).strip()


def parse_subject_description(text: str) -> tuple[str, str]:
    subject = ""
    description_lines: list[str] = []
    for line in (text or "").splitlines():
        raw = line.strip()
        if not raw:
            continue
        lower = raw.lower()
        if lower.startswith("subject:"):
            subject = raw.split(":", 1)[1].strip()
            continue
        if lower.startswith("description:"):
            desc = raw.split(":", 1)[1].strip()
            if desc:
                description_lines.append(desc)
            continue
        if not subject:
            subject = raw
            continue
        description_lines.append(raw)
    description = " ".join(description_lines).strip()
    return subject, description


def generate_email_content(details: str, lang: str) -> tuple[str, str]:
    system = (
        "You format internal support emails. "
        "Write in the user's language. "
        "Paraphrase the user's message; do not copy sentences verbatim. "
        "Output two lines only: "
        "Subject: ... (max 80 chars) "
        "Description: ... (2-5 sentences)."
    )
    user = f"Issue:\n{details}"
    try:
        res = client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=0.2,
        )
        raw = res.choices[0].message.content or ""
        subject, description = parse_subject_description(raw)
        subject = " ".join(subject.split())
        description = " ".join(description.split())
        if subject:
            if len(subject) > 80:
                subject = subject[:77].rstrip() + "..."
        if subject and description:
            return subject, description
    except Exception as exc:
        print(f"[SUPPORT EMAIL] Paraphrase failed: {exc}")

    # Fallback: minimal cleanup
    clean = " ".join((details or "").strip().split())
    subject = build_subject(clean, lang)
    description = clean or ("Probleme signale par l'utilisateur." if lang == "fr" else "User reported a problem.")
    return subject, description


def send_support_email(subject: str, body: str, reply_to: str | None = None) -> bool:
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = formataddr((EMAIL_FROM_NAME, EMAIL_FROM))
    msg["To"] = SUPPORT_EMAIL
    if reply_to:
        msg["Reply-To"] = reply_to
    msg.set_content(body)

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


def resolve_system_prompt(session: dict) -> str:
    return SYSTEM_PROMPT


def run(message: str, session: dict) -> str:
    history = session.setdefault("history", [])
    history.append({"role": "user", "content": message})

    stage = (session.get("stage") or "").strip().lower()
    user_email = session.get("user_email") or ""
    lang = detect_language(message)
    fr = lang == "fr"
    ask_details = localize_template(
        "Sure. Please describe the problem you need human support for "
        "(what you did, what you expected, and any error message). "
        "I will send it to technical support.",
        "D'accord. Decrivez le probleme pour lequel vous avez besoin d'un support humain "
        "(ce que vous avez fait, ce que vous attendiez, et tout message d'erreur). "
        "Je vais l'envoyer au support technique.",
        message,
        lang,
    )
    details_empty = localize_template(
        "Please describe the issue so I can send it to support.",
        "Merci de decrire le probleme pour que je puisse transmettre au support.",
        message,
        lang,
    )
    confirm_sent = localize_template(
        f"Thanks. Your request has been sent to technical support ({SUPPORT_EMAIL}).",
        f"Merci. Votre demande a ete transmise au support technique ({SUPPORT_EMAIL}).",
        message,
        lang,
    )
    send_failed = localize_template(
        f"Sorry, sending to support failed. Please contact {SUPPORT_EMAIL} directly.",
        f"Desole, l'envoi au support a echoue. Veuillez contacter {SUPPORT_EMAIL} directement.",
        message,
        lang,
    )

    if stage in {"support_waiting_details", "support_waiting_subject"}:
        details = (message or "").strip()
        if not details:
            return details_empty

        subject, description = generate_email_content(details, lang)
        body = build_body_from_description(description, user_email, lang)

        ok = send_support_email(
            subject=subject,
            body=body,
            reply_to=user_email or None,
        )
        session["stage"] = "idle"
        session.pop("support_request_text", None)

        if fr:
            response = (
                "Merci. Je vais envoyer votre demande au support technique.\n\n"
                f"Objet: {subject}\n"
                f"Contenu:\n{body}"
            )
        else:
            response = (
                "Thanks. I will send your request to technical support now.\n\n"
                f"Subject: {subject}\n"
                f"Body:\n{body}"
            )
        return response if ok else f"{response}\n\n{send_failed}"

    if wants_human_support(message):
        session["stage"] = "support_waiting_details"
        session["support_request_text"] = message
        return ask_details

    system_prompt = resolve_system_prompt(session)
    messages = [{"role": "system", "content": system_prompt}] + history

    try:
        res = client.chat.completions.create(
            model=MODEL,
            messages=messages,
        )
        reply = res.choices[0].message.content or ""
        reply = strip_english_translation(reply, lang)
        history.append({"role": "assistant", "content": reply})
        return reply
    except Exception:
        return "I apologize for the technical issue. Please try again or contact support."
