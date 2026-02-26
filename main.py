from datetime import datetime, timezone, timedelta
import os
import json
import re
import secrets
import hashlib
import smtplib
import ssl
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.utils import formataddr
from urllib.parse import quote

from fastapi import FastAPI, Request, Depends, HTTPException, Response
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import func

from auth import COOKIE_NAME, REFRESH_COOKIE_NAME, SECRET_KEY, decode_token, create_access_token, generate_refresh_token, hash_refresh_token, hash_password, verify_password
from bots import BOTS
from db import (
    ChatbotUser,
    PasswordResetToken,
    RefreshToken,
    get_db,
    get_chatbot_session,
    Conversation,
    Message,
)

from fastapi.middleware.cors import CORSMiddleware
app = FastAPI()

# Configuration CORS 
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://digital-coaching.azurewebsites.net"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def utcnow():
    return datetime.now(timezone.utc)

# ==============================
# Password reset config
# ==============================

RESET_TOKEN_TTL_HOURS = int(os.getenv("RESET_TOKEN_TTL_HOURS", "1"))

REFRESH_TOKEN_TTL_DAYS = int(os.getenv("REFRESH_TOKEN_TTL_DAYS", "7"))
REFRESH_COOKIE_SECURE = (os.getenv("REFRESH_COOKIE_SECURE") or "true").strip().lower() in {"1", "true", "yes"}
REFRESH_COOKIE_SAMESITE = (os.getenv("REFRESH_COOKIE_SAMESITE") or "none").strip().lower()
REFRESH_COOKIE_MAX_AGE = REFRESH_TOKEN_TTL_DAYS * 24 * 60 * 60

FRONTEND_RESET_URL = (os.getenv("RESET_PASSWORD_URL") or "https://digital-coaching.azurewebsites.net/reset-password").strip()

SMTP_HOST = os.getenv("SMTP_HOST", "avocarbon-com.mail.protection.outlook.com").strip()
SMTP_PORT = int(os.getenv("SMTP_PORT", "25"))
EMAIL_FROM = os.getenv("EMAIL_FROM", "administration.STS@avocarbon.com").strip()
EMAIL_FROM_NAME = os.getenv("EMAIL_FROM_NAME", "Administration STS").strip()
SMTP_USE_TLS = (os.getenv("SMTP_USE_TLS") or "false").strip().lower() in {"1", "true", "yes"}


# ==============================
# Token helpers
# ==============================

def hash_reset_token(token: str) -> str:
    secret = str(SECRET_KEY or "")
    raw = f"{token}:{secret}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def build_reset_link(token: str, email: str | None = None) -> str:
    base = (FRONTEND_RESET_URL or "").strip()
    if not base:
        raise ValueError("RESET_PASSWORD_URL is empty")

    joiner = "&" if "?" in base else "?"
    if email:
        return f"{base}{joiner}token={quote(token)}&email={quote(email)}"
    return f"{base}{joiner}token={quote(token)}"


def set_refresh_cookie(resp: Response, token: str, expires_at: datetime):
    resp.set_cookie(
        key=REFRESH_COOKIE_NAME,
        value=token,
        httponly=True,
        secure=REFRESH_COOKIE_SECURE,
        samesite=REFRESH_COOKIE_SAMESITE,
        max_age=REFRESH_COOKIE_MAX_AGE,
        expires=expires_at,
        path="/",
    )


def clear_refresh_cookie(resp: Response):
    resp.delete_cookie(REFRESH_COOKIE_NAME, path="/")


def create_refresh_token_record(db: Session, email: str, user_id) -> tuple[str, datetime]:
    refresh_token = generate_refresh_token()
    expires_at = utcnow() + timedelta(days=REFRESH_TOKEN_TTL_DAYS)
    token_hash = hash_refresh_token(refresh_token)
    db.add(RefreshToken(user_id=user_id, email=email, token_hash=token_hash, expires_at=expires_at))
    db.commit()
    return refresh_token, expires_at


# ==============================
# HTML Email Template (same style as your screenshot)
# ==============================

def _escape_html(s: str) -> str:
    return (
        (s or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )


def build_reset_html_body(reset_link: str, expires_hours: int, user_email: str | None = None) -> str:
    # Outlook-friendly: mostly tables + inline styles
    reset_link_escaped = _escape_html(reset_link)
    email_escaped = _escape_html(user_email or "-")
    received_on = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M")

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Password Reset</title>
</head>
<body style="margin:0;padding:0;background:#f3f5f7;">
  <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="background:#f3f5f7;">
    <tr>
      <td align="center" style="padding:32px 16px;">

        <!-- Outer card -->
        <table role="presentation" width="680" cellspacing="0" cellpadding="0" border="0"
               style="max-width:680px;width:100%;background:#ffffff;border-radius:16px;border:1px solid #e9edf3;">
          <tr>
            <td style="padding:28px 24px 18px 24px;">

              <!-- Title -->
              <div style="font-family:Arial,Helvetica,sans-serif;font-size:24px;font-weight:700;color:#111827;text-align:center;">
                Digital Coaching Password Reset
              </div>

              <div style="height:18px;"></div>

              <!-- Inner panel (with left blue bar like screenshot) -->
              <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0"
                     style="border:1px solid #e6eaf0;border-radius:12px;overflow:hidden;">
                <tr>
                  <!-- blue bar -->
                  <td width="8" style="background:#2563eb;">&nbsp;</td>

                  <!-- content -->
                  <td style="padding:18px 18px 10px 18px;background:#ffffff;">

                    <div style="height:16px;"></div>

                    <!-- Row -->
                    <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0">
                      <tr>
                        <td style="font-family:Arial,Helvetica,sans-serif;font-size:14px;color:#111827;font-weight:700;">
                          ❗&nbsp;&nbsp;Action required :
                        </td>
                      </tr>
                      <tr>
                        <td style="padding-top:10px;">
                          <div style="font-family:Arial,Helvetica,sans-serif;font-size:14px;color:#374151;line-height:1.6;
                                      background:#ffffff;border:1px solid #e5e7eb;border-radius:8px;
                                      padding:12px 12px;">
                            We received a request to reset your password. Use the button below to set a new password.
                            <br><br>
                            This link expires in <strong>{expires_hours} hour</strong>.
                          </div>
                        </td>
                      </tr>
                    </table>

                    <div style="height:14px;"></div>

                    <!-- Button -->
                    <table role="presentation" cellspacing="0" cellpadding="0" border="0" align="left">
                      <tr>
                        <td style="border-radius:8px;background:#2563eb;">
                          <a href="{reset_link_escaped}"
                             style="display:inline-block;padding:12px 18px;font-family:Arial,Helvetica,sans-serif;
                                    font-size:14px;font-weight:700;color:#ffffff;text-decoration:none;border-radius:8px;">
                            Reset Password
                          </a>
                        </td>
                      </tr>
                    </table>

                    <div style="height:18px;clear:both;"></div>

                    <div style="font-family:Arial,Helvetica,sans-serif;font-size:12px;color:#6b7280;line-height:1.6;">
                      If you did not request this, you can safely ignore this email.
                    </div>

                  </td>
                </tr>
              </table>

              <!-- Footer -->
              <div style="font-family:Arial,Helvetica,sans-serif;font-size:12px;color:#9ca3af;text-align:center;margin-top:18px;">
                © 2026 Digital Coaching 
              </div>

            </td>
          </tr>
        </table>

      </td>
    </tr>
  </table>
</body>
</html>"""


# ==============================
# Send email
# ==============================

def send_reset_email(to_email: str, reset_link: str) -> None:
    subject = "Reset your Digital Coaching password"

    text_body = (
        "Hello,\n\n"
        "We received a request to reset your password.\n"
        f"Click this link to reset it:\n{reset_link}\n\n"
        f"This link expires in {RESET_TOKEN_TTL_HOURS} hours.\n\n"
        "If you did not request this, please ignore this email.\n\n"
        "Digital Coaching Support"
    )

    html_body = build_reset_html_body(reset_link, RESET_TOKEN_TTL_HOURS, user_email=to_email)

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = formataddr((EMAIL_FROM_NAME, EMAIL_FROM))
    msg["To"] = to_email

    msg.attach(MIMEText(text_body, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=20) as server:
        if SMTP_USE_TLS:
            context = ssl.create_default_context()
            server.starttls(context=context)
        server.sendmail(EMAIL_FROM, [to_email], msg.as_string())


def is_meaningful_message(text: str) -> bool:
    t = " ".join((text or "").strip().split())
    if not t:
        return False
    if len(t) < 12:
        return False
    words = t.split(" ")
    if len(words) < 2:
        return False
    alpha_count = sum(1 for c in t if c.isalpha())
    if alpha_count < 6:
        return False
    if len(words) == 1 and t.lower() in {"hi", "hello", "salut", "hey", "yo", "ok", "okay", "test", "bonjour"}:
        return False
    return True

def summarize_title(text: str, max_words: int = 4) -> str:
    t = " ".join((text or "").strip().split())
    if not t:
        return "New chat"
    
    text_lower = t.lower()
    
    # Découper en mots
    words = re.findall(r'\b[a-zà-ÿ]{3,}\b', text_lower)
    
    # Stopwords basiques
    stopwords = {
        "avec", "dans", "pour", "mon", "ma", "mes", "ton", "ta", "tes", "son", "sa", "ses",
        "un", "une", "le", "la", "les", "des", "du", "de", "au", "aux", "sur", "sous", 
        "par", "entre", "pendant", "depuis", "chez", "vers", "à",
        "je", "tu", "il", "elle", "nous", "vous", "ils", "elles", "ce", "cette", "ces",
        "et", "ou", "mais", "donc", "car", "ni", "or", "que", "qui", "quoi", "où", 
        "est", "sont", "suis", "es", "sommes", "êtes", "ai", "as", "a", "avons", "avez", "ont",
        "très", "peu", "plus", "moins", "aussi", "alors", "puis", "ainsi",
    }
    
    # Filtrer les stopwords
    meaningful_words = [w for w in words if w not in stopwords]
    
    # Si pas assez de mots significatifs
    if len(meaningful_words) < 2:
        # Prendre les premiers mots
        first_words = words[:max_words]
        if not first_words:
            return "New chat"
        title = " ".join(first_words).capitalize()
        return title
    
    # Prendre les 2-4 premiers mots significatifs
    selected_words = meaningful_words[:max_words]
    
    # Former le titre
    title = " ".join(selected_words).capitalize()
    
    return title

def make_title(first_user_message: str) -> str:
    t = " ".join((first_user_message or "").strip().split())
    if not is_meaningful_message(t):
        return "New chat"
    
    title = summarize_title(t)
    
    # Limiter à 4 mots maximum
    words = title.split()
    if len(words) > 4:
        title = " ".join(words[:4])
    
    return title

def normalize_username(raw: str) -> str:
    base = re.sub(r"[^a-zA-Z0-9_]", "", (raw or "").strip().lower())
    if not base:
        base = "user"
    return base[:50]

def unique_username(db: Session, base: str) -> str:
    base = normalize_username(base)
    if not base:
        base = "user"
    exists = db.query(ChatbotUser.id).filter(ChatbotUser.username == base).first()
    if not exists:
        return base

    suffix = 2
    while True:
        candidate = f"{base}{suffix}"
        if len(candidate) > 100:
            candidate = candidate[: 100 - len(str(suffix))] + str(suffix)
        exists = db.query(ChatbotUser.id).filter(ChatbotUser.username == candidate).first()
        if not exists:
            return candidate
        suffix += 1


def sse_event(data: dict, event: str | None = None) -> str:
    payload = f"data: {json.dumps(data, ensure_ascii=False)}\n\n"
    if event:
        payload = f"event: {event}\n" + payload
    return payload

def unique_title(db: Session, email: str, bot_id: str, base: str, exclude_id: int | None = None) -> str:
    base = (base or "New chat").strip()
    if not base:
        base = "New chat"

    q = db.query(Conversation.title).filter(
        Conversation.email == email,
        Conversation.bot_id == bot_id,
        Conversation.is_deleted == False,
    )
    if exclude_id is not None:
        q = q.filter(Conversation.id != exclude_id)

    rows = q.all()
    existing = {r[0] for r in rows if r and r[0]}

    if base not in existing:
        return base

    used_nums = set()
    prefix = base + " ("
    for t in existing:
        if t.startswith(prefix) and t.endswith(")"):
            num = t[len(prefix):-1]
            if num.isdigit():
                used_nums.add(int(num))

    n = 2
    while n in used_nums:
        n += 1
    return f"{base} ({n})"

def list_conversations(db: Session, email: str, bot_id: str):
    return (
        db.query(Conversation)
        .filter(
            Conversation.email == email,
            Conversation.bot_id == bot_id,
            Conversation.is_deleted == False,
        )
        .order_by(func.coalesce(Conversation.updated_at, Conversation.created_at).desc(), Conversation.id.desc())
        .limit(50)
        .all()
    )

def build_history_items(convs):
    items = []
    for c in convs:
        ts = c.updated_at or c.created_at or utcnow()
        items.append(
            {
                "chat_id": c.id,
                "title": summarize_title(c.title or "New chat"),
                "updated_at": ts.isoformat(),
            }
        )
    return items

def chat_cookie_name(bot_id: str) -> str:
    return f"chat_id_{bot_id}"

NO_PERSIST_BOT_IDS = {"widget"}

def is_ephemeral_bot(bot_id: str) -> bool:
    return bot_id in NO_PERSIST_BOT_IDS

EPHEMERAL_TTL_SECONDS = 60 * 60
EPHEMERAL_MAX_MESSAGES = 60
EPHEMERAL_SESSIONS = {}

def get_ephemeral_session(email: str, bot_id: str, bot_mode):
    key = f"{bot_id}:{email or 'anonymous'}"
    now = utcnow()
    entry = EPHEMERAL_SESSIONS.get(key)
    if entry:
        age = (now - entry["updated_at"]).total_seconds()
        if age > EPHEMERAL_TTL_SECONDS:
            entry = None

    if not entry:
        entry = {
            "session": {
                "history": [],
                "ui_lang": None,
                "stage": "idle",
                "bot_mode": bot_mode,
                "user_email": email,
            },
            "updated_at": now,
        }
        EPHEMERAL_SESSIONS[key] = entry
    else:
        entry["session"]["bot_mode"] = bot_mode
        entry["session"]["user_email"] = email
        entry["updated_at"] = now

    history = entry["session"].get("history") or []
    if len(history) > EPHEMERAL_MAX_MESSAGES:
        entry["session"]["history"] = history[-EPHEMERAL_MAX_MESSAGES:]

    return entry["session"]

def get_chatbot_user_id(email: str):
    if not email:
        return None
    chat_db = get_chatbot_session()
    try:
        row = chat_db.query(ChatbotUser.id).filter(ChatbotUser.email == email).first()
        return row[0] if row else None
    except Exception as e:
        print(f"Chatbot user lookup error: {e}")
        return None
    finally:
        chat_db.close()

def create_conversation(db: Session, email: str, bot_id: str, user_id=None) -> Conversation:
    now = utcnow()
    conv = Conversation(
        user_id=user_id,
        email=email,
        bot_id=bot_id,
        title="New chat",
        stage="select_lang",
        ui_lang=None,
        created_at=now,
        updated_at=now,
    )
    db.add(conv)
    db.commit()
    db.refresh(conv)
    return conv

class SignupPayload(BaseModel):
    full_name: str
    email: str
    password: str
    confirm_password: str

class LoginPayload(BaseModel):
    email: str
    password: str

class ForgotPasswordPayload(BaseModel):
    email: str

class ResetPasswordPayload(BaseModel):
    token: str
    password: str
    confirm_password: str
    email: str | None = None

class RenameChatPayload(BaseModel):
    title: str

class EditMessagePayload(BaseModel):
    content: str
    regenerate: bool = False
    bot_mode: str | None = None


def get_bearer_token(request: Request) -> str | None:
    auth_header = request.headers.get("authorization")
    if not auth_header:
        return None
    parts = auth_header.split()
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1]
    return None

def require_user(request: Request) -> str:
    token = get_bearer_token(request) or request.cookies.get(COOKIE_NAME)
    email = decode_token(token) if token else None
    if not email:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return email

@app.get("/")
def root():
    return {"status": "ok"}

@app.post("/api/auth/signup")
async def signup(payload: SignupPayload, db: Session = Depends(get_db)):
    try:
        full_name = (payload.full_name or "").strip()
        email = (payload.email or "").strip().lower()
        password = payload.password or ""
        confirm = payload.confirm_password or ""

        print(f"Signup attempt for: {email}")

        if len(full_name) < 2:
            raise HTTPException(status_code=400, detail="full_name too short")
        if "@" not in email:
            raise HTTPException(status_code=400, detail="invalid email")
        if not email.endswith("@avocarbon.com"):
            raise HTTPException(status_code=400, detail="email must be @avocarbon.com")
        if len(password) < 8:
            raise HTTPException(status_code=400, detail="password too short")
        if password != confirm:
            raise HTTPException(status_code=400, detail="password_mismatch")

        # Create user in chatbot_users DB first
        chat_db = get_chatbot_session()
        try:
            exists = chat_db.query(ChatbotUser.id).filter(ChatbotUser.email == email).first()
            if exists:
                print(f"Email already exists (chatbot_users): {email}")
                raise HTTPException(status_code=409, detail="email already exists")

            base_username = email.split("@")[0] if "@" in email else email
            if not base_username:
                base_username = full_name
            username = unique_username(chat_db, base_username)

            password_hash = hash_password(password)
            chat_user = ChatbotUser(
                email=email,
                username=username,
                password_hash=password_hash,
                full_name=full_name,
            )
            chat_db.add(chat_user)
            chat_db.commit()
            chat_db.refresh(chat_user)
            chat_user_id = chat_user.id
            chat_user_full_name = chat_user.full_name
        except HTTPException:
            chat_db.rollback()
            raise
        except Exception as e:
            chat_db.rollback()
            print(f"Signup chatbot_users error: {e}")
            import traceback
            traceback.print_exc()
            raise HTTPException(status_code=500, detail="server_error")
        finally:
            chat_db.close()

        access_token = create_access_token(email)
        try:
            refresh_token, refresh_expires = create_refresh_token_record(db, email, chat_user_id)
        except Exception as e:
            db.rollback()
            print(f"Refresh token error: {e}")
            raise HTTPException(status_code=500, detail="server_error")

        response = JSONResponse(
            {
                "ok": True,
                "token": access_token,
                "user": {
                    "id": str(chat_user_id),
                    "email": email,
                    "full_name": chat_user_full_name,
                },
            }
        )

        set_refresh_cookie(response, refresh_token, refresh_expires)
        return response

    except HTTPException:
        raise
    except Exception as e:
        print(f"Signup error: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail="server_error")

@app.post("/api/auth/login")
async def login(payload: LoginPayload, db: Session = Depends(get_db)):
    try:
        email = (payload.email or "").strip().lower()
        password = payload.password or ""

        print(f"Login attempt for: {email}")

        chat_db = get_chatbot_session()
        try:
            chat_user = chat_db.query(ChatbotUser).filter(ChatbotUser.email == email).first()
            if not chat_user:
                raise HTTPException(status_code=401, detail="invalid credentials")

            if not verify_password(password, chat_user.password_hash):
                raise HTTPException(status_code=401, detail="invalid credentials")

            chat_user_id = chat_user.id
            chat_user_full_name = chat_user.full_name
            chat_user_password_hash = chat_user.password_hash

            chat_user.last_login = datetime.utcnow()
            chat_db.commit()
        except HTTPException:
            chat_db.rollback()
            raise
        except Exception as e:
            chat_db.rollback()
            print(f"Login chatbot_users error: {e}")
            raise HTTPException(status_code=500, detail="server_error")
        finally:
            chat_db.close()

        access_token = create_access_token(email)
        try:
            refresh_token, refresh_expires = create_refresh_token_record(db, email, chat_user_id)
        except Exception as e:
            db.rollback()
            print(f"Refresh token error: {e}")
            raise HTTPException(status_code=500, detail="server_error")

        resp = JSONResponse(
            {
                "ok": True,
                "token": access_token,
                "user": {
                    "id": str(chat_user_id),
                    "email": email,
                    "full_name": chat_user_full_name,
                },
            }
        )
        set_refresh_cookie(resp, refresh_token, refresh_expires)
        return resp

    except HTTPException:
        raise
    except Exception as e:
        print(f"Login error: {e}")
        raise HTTPException(status_code=500, detail="server_error")

@app.post("/api/auth/refresh")
def refresh_token(request: Request, db: Session = Depends(get_db)):
    token = request.cookies.get(REFRESH_COOKIE_NAME)
    if not token:
        raise HTTPException(status_code=401, detail="refresh_token_missing")

    token_hash = hash_refresh_token(token)
    now = utcnow()
    record = (
        db.query(RefreshToken)
        .filter(RefreshToken.token_hash == token_hash)
        .first()
    )

    if not record or record.revoked_at is not None or record.expires_at <= now:
        raise HTTPException(status_code=401, detail="invalid_refresh_token")

    # Rotate refresh token
    record.revoked_at = now
    new_refresh = generate_refresh_token()
    new_hash = hash_refresh_token(new_refresh)
    new_expires = now + timedelta(days=REFRESH_TOKEN_TTL_DAYS)
    db.add(RefreshToken(user_id=record.user_id, email=record.email, token_hash=new_hash, expires_at=new_expires))
    db.commit()

    access_token = create_access_token(record.email)
    resp = JSONResponse({"ok": True, "token": access_token})
    set_refresh_cookie(resp, new_refresh, new_expires)
    return resp


@app.post("/api/auth/forgot-password")

def forgot_password(payload: ForgotPasswordPayload, db: Session = Depends(get_db)):
    email = (payload.email or "").strip().lower()

    if not email:
        raise HTTPException(status_code=400, detail="Email is required")
    if "@" not in email:
        raise HTTPException(status_code=400, detail="Invalid email")

    chat_db = get_chatbot_session()
    try:
        user = chat_db.query(ChatbotUser).filter(ChatbotUser.email == email).first()
        if not user:
            raise HTTPException(status_code=404, detail="User does not exist")

        now = utcnow()
        # Invalidate previous tokens (main DB)
        db.query(PasswordResetToken).filter(
            PasswordResetToken.email == email,
            PasswordResetToken.used_at == None,
        ).update({PasswordResetToken.used_at: now}, synchronize_session=False)

        token = secrets.token_urlsafe(32)
        token_hash = hash_reset_token(token)
        expires_at = now + timedelta(hours=RESET_TOKEN_TTL_HOURS)

        record = PasswordResetToken(
            user_id=user.id,
            email=email,
            token_hash=token_hash,
            expires_at=expires_at,
        )
        db.add(record)
        db.commit()

        try:
            reset_link = build_reset_link(token=token, email=email)
        except ValueError:
            raise HTTPException(status_code=500, detail="Reset link configuration missing")
        try:
            send_reset_email(email, reset_link)
        except Exception as e:
            print(f"Reset email send failed: {e}")
            raise HTTPException(status_code=500, detail="Failed to send reset email")

        return {"ok": True}
    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        print(f"Forgot password error: {e}")
        raise HTTPException(status_code=500, detail="server_error")
    finally:
        chat_db.close()

@app.post("/api/auth/reset-password")
def reset_password(payload: ResetPasswordPayload, db: Session = Depends(get_db)):
    token = (payload.token or "").strip()
    password = payload.password or ""
    confirm = payload.confirm_password or ""
    email = (payload.email or "").strip().lower() if payload.email else None

    if not token:
        raise HTTPException(status_code=400, detail="token required")
    if len(password) < 8:
        raise HTTPException(status_code=400, detail="password too short")
    if password != confirm:
        raise HTTPException(status_code=400, detail="password_mismatch")

    token_hash = hash_reset_token(token)
    chat_db = get_chatbot_session()
    try:
        now = utcnow()
        record = db.query(PasswordResetToken).filter(
            PasswordResetToken.token_hash == token_hash,
            PasswordResetToken.used_at == None,
            PasswordResetToken.expires_at > now,
        ).first()
        if not record:
            raise HTTPException(status_code=400, detail="invalid_or_expired_token")

        user = chat_db.query(ChatbotUser).filter(ChatbotUser.id == record.user_id).first()
        if not user:
            raise HTTPException(status_code=404, detail="user_not_found")
        if email and user.email.lower() != email:
            raise HTTPException(status_code=400, detail="email_mismatch")

        user.password_hash = hash_password(password)
        record.used_at = now
        chat_db.commit()
        db.commit()

        return {"ok": True}
    except HTTPException:
        raise
    except Exception as e:
        chat_db.rollback()
        db.rollback()
        print(f"Reset password error: {e}")
        raise HTTPException(status_code=500, detail="server_error")
    finally:
        chat_db.close()

@app.post("/auth/logout")
def logout(request: Request, db: Session = Depends(get_db)):
    token = request.cookies.get(REFRESH_COOKIE_NAME)
    if token:
        token_hash = hash_refresh_token(token)
        now = utcnow()
        try:
            db.query(RefreshToken).filter(
                RefreshToken.token_hash == token_hash,
                RefreshToken.revoked_at == None,
            ).update({RefreshToken.revoked_at: now}, synchronize_session=False)
            db.commit()
        except Exception as e:
            db.rollback()
            print(f"Logout refresh token revoke error: {e}")

    resp = JSONResponse({"ok": True})
    resp.delete_cookie(COOKIE_NAME, path="/")
    clear_refresh_cookie(resp)
    return resp



@app.post("/api/chat")
async def chat_api(payload: dict, request: Request, db: Session = Depends(get_db)):
    email = require_user(request)

    bot_id = payload.get("bot_id")
    message = (payload.get("message") or "").strip()
    chat_id = payload.get("chat_id")
    bot_mode = payload.get("bot_mode")
    
    if not bot_id or not message:
        raise HTTPException(status_code=400, detail="bot_id and message required")
    if bot_id not in BOTS:
        raise HTTPException(status_code=400, detail="Unknown bot_id")

    if is_ephemeral_bot(bot_id):
        session = get_ephemeral_session(email=email, bot_id=bot_id, bot_mode=bot_mode)
        reply = BOTS[bot_id]["runner"](message, session)
        now = utcnow()
        resp = JSONResponse(
            {
                "bot_id": bot_id,
                "reply": reply,
                "chat_id": None,
                "title": "Help chat",
                "updated_at": now.isoformat(),
            }
        )
        return resp

    conv = None
    
    # 1) Si un chat_id est fourni, vérifier s'il existe
    if chat_id is not None:
        try:
            payload_id = int(chat_id)
            conv = (
                db.query(Conversation)
                .filter(
                    Conversation.id == payload_id,
                    Conversation.email == email,
                    Conversation.bot_id == bot_id,
                    Conversation.is_deleted == False,
                )
                .first()
            )
        except (ValueError, TypeError):
            pass  # chat_id invalide, on va créer une nouvelle conversation
    
    # 2) Si aucune conversation trouvée, EN CRÉER UNE NOUVELLE
    if not conv:
        print(f"[DEBUG] Création nouvelle conversation pour le message: {message[:50]}...")
        user_id = get_chatbot_user_id(email)
        conv = create_conversation(db=db, email=email, bot_id=bot_id, user_id=user_id)
        print(f"[DEBUG] Nouvelle conversation créée: {conv.id}")
    else:
        print(f"[DEBUG] Utilisation conversation existante: {conv.id}")
        if conv.user_id is None:
            user_id = get_chatbot_user_id(email)
            if user_id:
                conv.user_id = user_id

    # Sauvegarder le premier message utilisateur
    now = utcnow()
    user_message = Message(conversation_id=conv.id, role="user", content=message, created_at=now)
    db.add(user_message)
    
    # Charger l'historique (sera vide pour une nouvelle conversation)
    msgs = (
        db.query(Message)
        .filter(Message.conversation_id == conv.id)
        .order_by(Message.created_at.asc())
        .limit(60)
        .all()
    )
    history = [{"role": m.role, "content": m.content} for m in msgs]

    # Construire session
    session = {
        "history": history,
        "ui_lang": conv.ui_lang,
        "stage": conv.stage,
        "bot_mode": bot_mode,
        "user_email": email,
    }

    # Appel bot
    reply = BOTS[bot_id]["runner"](message, session)

    # Sauvegarder la réponse du bot
    assistant_message = Message(conversation_id=conv.id, role="assistant", content=reply, created_at=now)
    db.add(assistant_message)

    # Persister stage/lang après le runner
    conv.ui_lang = session.get("ui_lang")
    conv.stage = session.get("stage") or conv.stage
    conv.updated_at = now

    # Titre auto au 1er message "utile"
    if conv.title == "New chat":
        base = make_title(message)
        if base != "New chat":
            conv.title = unique_title(db=db, email=email, bot_id=bot_id, base=base, exclude_id=conv.id)

    db.commit()
    
    print(f"[DEBUG] Conversation finalisée: {conv.id}, Titre: {conv.title}")

    title = summarize_title(conv.title or "New chat")
    updated_at = (conv.updated_at or utcnow()).isoformat()
    
    # Mettre à jour le cookie avec l'ID de la conversation
    resp = JSONResponse({
        "bot_id": bot_id, 
        "reply": reply, 
        "chat_id": conv.id,  # Retourner l'ID de la conversation
        "title": title, 
        "updated_at": updated_at
    })
    
    return resp



@app.get("/api/history/{bot_id}")
def history_list(bot_id: str, request: Request, db: Session = Depends(get_db)):
    email = require_user(request)

    if is_ephemeral_bot(bot_id):
        return {"items": []}

    convs = list_conversations(db=db, email=email, bot_id=bot_id)
    return {"items": build_history_items(convs)}


@app.post("/api/history/{bot_id}/new")
def history_new(bot_id: str, request: Request, db: Session = Depends(get_db)):
    email = require_user(request)

    if bot_id not in BOTS:
        raise HTTPException(status_code=400, detail="Unknown bot_id")

    if is_ephemeral_bot(bot_id):
        return {"chat_id": None, "title": "Help chat"}

    user_id = get_chatbot_user_id(email)
    conv = create_conversation(db=db, email=email, bot_id=bot_id, user_id=user_id)
    return {"chat_id": conv.id, "title": conv.title}


@app.get("/api/history/{bot_id}/{chat_id}")
def history_get(bot_id: str, chat_id: int, request: Request, db: Session = Depends(get_db)):
    email = require_user(request)

    if is_ephemeral_bot(bot_id):
        return {"chat_id": chat_id, "messages": [], "title": "Help chat", "updated_at": None}

    conv = (
        db.query(Conversation)
        .filter(
            Conversation.id == chat_id,
            Conversation.email == email,
            Conversation.bot_id == bot_id,
            Conversation.is_deleted == False,
        )
        .first()
    )
    if not conv:
        return {"chat_id": chat_id, "messages": []}

    msgs = (
        db.query(Message)
        .filter(Message.conversation_id == conv.id)
        .order_by(Message.created_at.asc())
        .all()
    )

    return {
        "chat_id": conv.id,
        "messages": [
            {
                "id": m.id,
                "role": m.role,
                "content": m.content,
                "is_edited": m.is_edited,
                "edited_at": m.edited_at.isoformat() if m.edited_at else None,
            }
            for m in msgs
        ],
        "title": summarize_title(conv.title or "New chat"),
        "updated_at": conv.updated_at.isoformat() if conv.updated_at else None
    }

@app.post("/api/history/{bot_id}/{chat_id}/rename")
def history_rename(bot_id: str, chat_id: int, payload: RenameChatPayload, request: Request, db: Session = Depends(get_db)):
    email = require_user(request)

    if bot_id not in BOTS:
        raise HTTPException(status_code=400, detail="Unknown bot_id")

    if is_ephemeral_bot(bot_id):
        return {
            "ok": True,
            "chat_id": chat_id,
            "title": "Help chat",
            "updated_at": None,
            "full_title": "Help chat",
        }

    new_title = " ".join((payload.title or "").strip().split())
    if not new_title:
        raise HTTPException(status_code=400, detail="title required")

    conv = (
        db.query(Conversation)
        .filter(
            Conversation.id == chat_id,
            Conversation.email == email,
            Conversation.bot_id == bot_id,
            Conversation.is_deleted == False,
        )
        .first()
    )
    if not conv:
        raise HTTPException(status_code=404, detail="chat not found")

    conv.title = unique_title(db=db, email=email, bot_id=bot_id, base=new_title, exclude_id=conv.id)
    conv.updated_at = utcnow()
    db.commit()

    return {
        "ok": True,
        "chat_id": conv.id,
        "title": summarize_title(conv.title or "New chat"),
        "updated_at": conv.updated_at.isoformat() if conv.updated_at else None,
        "full_title": conv.title,
    }

@app.post("/api/history/{bot_id}/{chat_id}/messages/{message_id}/edit")
def message_edit(
    bot_id: str,
    chat_id: int,
    message_id: int,
    payload: EditMessagePayload,
    request: Request,
    db: Session = Depends(get_db),
):
    email = require_user(request)

    if bot_id not in BOTS:
        raise HTTPException(status_code=400, detail="Unknown bot_id")

    if is_ephemeral_bot(bot_id):
        return {"ok": True, "chat_id": chat_id, "message_id": message_id}

    new_content = (payload.content or "").strip()
    if not new_content:
        raise HTTPException(status_code=400, detail="content required")

    msg = (
        db.query(Message)
        .join(Conversation, Message.conversation_id == Conversation.id)
        .filter(
            Conversation.id == chat_id,
            Conversation.email == email,
            Conversation.bot_id == bot_id,
            Conversation.is_deleted == False,
            Message.id == message_id,
        )
        .first()
    )
    if not msg:
        raise HTTPException(status_code=404, detail="message not found")

    # Optional: only allow editing user messages
    if msg.role != "user":
        raise HTTPException(status_code=400, detail="only user messages can be edited")

    msg.content = new_content
    msg.is_edited = True
    msg.edited_at = utcnow()
    if msg.conversation:
        msg.conversation.updated_at = utcnow()

    assistant_message = None
    if payload.regenerate:
        # Remove messages after the edited one
        db.query(Message).filter(
            Message.conversation_id == msg.conversation_id,
            Message.id > msg.id,
        ).delete(synchronize_session=False)

        conv = msg.conversation
        if conv:
            msgs = (
                db.query(Message)
                .filter(Message.conversation_id == conv.id)
                .order_by(Message.created_at.asc(), Message.id.asc())
                .all()
            )
            history = [{"role": m.role, "content": m.content} for m in msgs]
            session = {
                "history": history,
                "ui_lang": conv.ui_lang,
                "stage": conv.stage,
                "bot_mode": payload.bot_mode,
                "user_email": email,
            }
            reply = BOTS[bot_id]["runner"](new_content, session)
            now = utcnow()
            assistant_message = Message(
                conversation_id=conv.id,
                role="assistant",
                content=reply,
                created_at=now,
            )
            db.add(assistant_message)
            conv.ui_lang = session.get("ui_lang")
            conv.stage = session.get("stage") or conv.stage
            conv.updated_at = now

    db.commit()

    return {
        "ok": True,
        "chat_id": chat_id,
        "message_id": msg.id,
        "edited_at": msg.edited_at.isoformat() if msg.edited_at else None,
        "assistant_message_id": assistant_message.id if assistant_message else None,
    }


@app.post("/api/history/{bot_id}/{chat_id}/messages/{message_id}/edit/stream")
async def message_edit_stream(
    bot_id: str,
    chat_id: int,
    message_id: int,
    payload: EditMessagePayload,
    request: Request,
    db: Session = Depends(get_db),
):
    email = require_user(request)

    if bot_id not in BOTS:
        raise HTTPException(status_code=400, detail="Unknown bot_id")

    if is_ephemeral_bot(bot_id):
        def event_stream():
            yield sse_event({"chat_id": chat_id}, event="meta")
            yield sse_event({"chat_id": chat_id, "updated_at": utcnow().isoformat()}, event="done")
        resp = StreamingResponse(event_stream(), media_type="text/event-stream")
        resp.headers["Cache-Control"] = "no-cache"
        resp.headers["X-Accel-Buffering"] = "no"
        return resp

    new_content = (payload.content or "").strip()
    if not new_content:
        raise HTTPException(status_code=400, detail="content required")

    msg = (
        db.query(Message)
        .join(Conversation, Message.conversation_id == Conversation.id)
        .filter(
            Conversation.id == chat_id,
            Conversation.email == email,
            Conversation.bot_id == bot_id,
            Conversation.is_deleted == False,
            Message.id == message_id,
        )
        .first()
    )
    if not msg:
        raise HTTPException(status_code=404, detail="message not found")

    if msg.role != "user":
        raise HTTPException(status_code=400, detail="only user messages can be edited")

    msg.content = new_content
    msg.is_edited = True
    msg.edited_at = utcnow()

    # Remove messages after the edited one
    db.query(Message).filter(
        Message.conversation_id == msg.conversation_id,
        Message.id > msg.id,
    ).delete(synchronize_session=False)

    conv = msg.conversation

    def event_stream():
        assistant_parts = []
        try:
            yield sse_event({"chat_id": chat_id}, event="meta")

            msgs = (
                db.query(Message)
                .filter(Message.conversation_id == conv.id)
                .order_by(Message.created_at.asc(), Message.id.asc())
                .all()
            )
            history = [{"role": m.role, "content": m.content} for m in msgs]

            session = {
                "history": history,
                "ui_lang": conv.ui_lang,
                "stage": conv.stage,
                "bot_mode": payload.bot_mode,
                "user_email": email,
            }

            runner = BOTS[bot_id]["runner"]
            runner_stream = BOTS[bot_id].get("runner_stream")

            if runner_stream:
                for chunk in runner_stream(new_content, session):
                    if not chunk:
                        continue
                    assistant_parts.append(chunk)
                    yield sse_event({"delta": chunk}, event="delta")
            else:
                reply = runner(new_content, session)
                for i in range(0, len(reply), 20):
                    chunk = reply[i : i + 20]
                    assistant_parts.append(chunk)
                    yield sse_event({"delta": chunk}, event="delta")

            assistant_text = "".join(assistant_parts)
            now = utcnow()
            assistant_message = Message(
                conversation_id=conv.id,
                role="assistant",
                content=assistant_text,
                created_at=now,
            )
            db.add(assistant_message)

            conv.ui_lang = session.get("ui_lang")
            conv.stage = session.get("stage") or conv.stage
            conv.updated_at = now

            db.commit()

            yield sse_event(
                {"chat_id": conv.id, "updated_at": now.isoformat()},
                event="done",
            )
        except Exception:
            db.rollback()
            yield sse_event({"message": "server_error"}, event="error")

    resp = StreamingResponse(event_stream(), media_type="text/event-stream")
    resp.headers["Cache-Control"] = "no-cache"
    resp.headers["X-Accel-Buffering"] = "no"
    return resp

@app.post("/api/history/{bot_id}/{chat_id}/delete")
def history_delete(bot_id: str, chat_id: int, request: Request, db: Session = Depends(get_db)):
    email = require_user(request)

    if bot_id not in BOTS:
        raise HTTPException(status_code=400, detail="Unknown bot_id")

    if is_ephemeral_bot(bot_id):
        return {"ok": True, "chat_id": chat_id}

    conv = (
        db.query(Conversation)
        .filter(
            Conversation.id == chat_id,
            Conversation.email == email,
            Conversation.bot_id == bot_id,
            Conversation.is_deleted == False,
        )
        .first()
    )
    if not conv:
        raise HTTPException(status_code=404, detail="chat not found")

    conv.is_deleted = True
    conv.updated_at = utcnow()
    db.commit()

    resp = JSONResponse({"ok": True, "chat_id": chat_id})
    cookie_name = chat_cookie_name(bot_id)
    if request.cookies.get(cookie_name) == str(chat_id):
        resp.delete_cookie(cookie_name, path="/")
    return resp


@app.post("/api/chat/stream")
async def chat_api_stream(payload: dict, request: Request, db: Session = Depends(get_db)):
    email = require_user(request)

    bot_id = payload.get("bot_id")
    message = (payload.get("message") or "").strip()
    chat_id = payload.get("chat_id")
    bot_mode = payload.get("bot_mode")

    if not bot_id or not message:
        raise HTTPException(status_code=400, detail="bot_id and message required")
    if bot_id not in BOTS:
        raise HTTPException(status_code=400, detail="Unknown bot_id")

    if is_ephemeral_bot(bot_id):
        session = get_ephemeral_session(email=email, bot_id=bot_id, bot_mode=bot_mode)

        runner = BOTS[bot_id]["runner"]
        runner_stream = BOTS[bot_id].get("runner_stream")

        def event_stream():
            assistant_parts = []
            try:
                yield sse_event({"chat_id": None}, event="meta")

                if runner_stream:
                    for chunk in runner_stream(message, session):
                        if not chunk:
                            continue
                        assistant_parts.append(chunk)
                        yield sse_event({"delta": chunk}, event="delta")
                else:
                    reply = runner(message, session)
                    for i in range(0, len(reply), 20):
                        chunk = reply[i : i + 20]
                        assistant_parts.append(chunk)
                        yield sse_event({"delta": chunk}, event="delta")

                now = utcnow()
                yield sse_event(
                    {"chat_id": None, "title": "Help chat", "updated_at": now.isoformat()},
                    event="done",
                )
            except Exception:
                yield sse_event({"message": "server_error"}, event="error")

        resp = StreamingResponse(event_stream(), media_type="text/event-stream")
        resp.headers["Cache-Control"] = "no-cache"
        resp.headers["X-Accel-Buffering"] = "no"
        return resp

    conv = None

    if chat_id is not None:
        try:
            payload_id = int(chat_id)
            conv = (
                db.query(Conversation)
                .filter(
                    Conversation.id == payload_id,
                    Conversation.email == email,
                    Conversation.bot_id == bot_id,
                    Conversation.is_deleted == False,
                )
                .first()
            )
        except (ValueError, TypeError):
            pass

    if not conv:
        user_id = get_chatbot_user_id(email)
        conv = create_conversation(db=db, email=email, bot_id=bot_id, user_id=user_id)
    else:
        if conv.user_id is None:
            user_id = get_chatbot_user_id(email)
            if user_id:
                conv.user_id = user_id

    now = utcnow()
    user_message = Message(
        conversation_id=conv.id, role="user", content=message, created_at=now
    )
    db.add(user_message)

    msgs = (
        db.query(Message)
        .filter(Message.conversation_id == conv.id)
        .order_by(Message.created_at.asc())
        .limit(60)
        .all()
    )
    history = [{"role": m.role, "content": m.content} for m in msgs]

    session = {
        "history": history,
        "ui_lang": conv.ui_lang,
        "stage": conv.stage,
        "bot_mode": bot_mode,
        "user_email": email,
    }

    runner = BOTS[bot_id]["runner"]
    runner_stream = BOTS[bot_id].get("runner_stream")

    def event_stream():
        assistant_parts = []
        try:
            yield sse_event({"chat_id": conv.id}, event="meta")

            if runner_stream:
                for chunk in runner_stream(message, session):
                    if not chunk:
                        continue
                    assistant_parts.append(chunk)
                    yield sse_event({"delta": chunk}, event="delta")
            else:
                reply = runner(message, session)
                for i in range(0, len(reply), 20):
                    chunk = reply[i : i + 20]
                    assistant_parts.append(chunk)
                    yield sse_event({"delta": chunk}, event="delta")

            assistant_text = "".join(assistant_parts)
            assistant_message = Message(
                conversation_id=conv.id,
                role="assistant",
                content=assistant_text,
                created_at=now,
            )
            db.add(assistant_message)

            conv.ui_lang = session.get("ui_lang")
            conv.stage = session.get("stage") or conv.stage
            conv.updated_at = now

            if conv.title == "New chat":
                base = make_title(message)
                if base != "New chat":
                    conv.title = unique_title(
                        db=db,
                        email=email,
                        bot_id=bot_id,
                        base=base,
                        exclude_id=conv.id,
                    )

            db.commit()

            title = summarize_title(conv.title or "New chat")
            updated_at = (conv.updated_at or utcnow()).isoformat()
            yield sse_event(
                {"chat_id": conv.id, "title": title, "updated_at": updated_at},
                event="done",
            )
        except Exception:
            db.rollback()
            yield sse_event({"message": "server_error"}, event="error")

    resp = StreamingResponse(event_stream(), media_type="text/event-stream")
    resp.headers["Cache-Control"] = "no-cache"
    resp.headers["X-Accel-Buffering"] = "no"
    return resp

@app.delete("/api/history/{bot_id}/{chat_id}")
def history_delete_rest(bot_id: str, chat_id: int, request: Request, db: Session = Depends(get_db)):
    return history_delete(bot_id=bot_id, chat_id=chat_id, request=request, db=db)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000, reload=True)
