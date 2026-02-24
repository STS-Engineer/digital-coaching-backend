from datetime import datetime, timezone
import os
import json
import re

from fastapi import FastAPI, Request, Depends, HTTPException, Response
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import func

from auth import COOKIE_NAME, decode_token, create_token, hash_password, verify_password
from bots import BOTS
from db import ChatbotUser, get_db, get_chatbot_session, Conversation, Message

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

        token = create_token(email)

        response = JSONResponse(
            {
                "ok": True,
                "token": token,
                "user": {
                    "id": str(chat_user_id),
                    "email": email,
                    "full_name": chat_user_full_name,
                },
            }
        )

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

        token = create_token(email)
        resp = JSONResponse(
            {
                "ok": True,
                "token": token,
                "user": {
                    "id": str(chat_user_id),
                    "email": email,
                    "full_name": chat_user_full_name,
                },
            }
        )
        return resp

    except HTTPException:
        raise
    except Exception as e:
        print(f"Login error: {e}")
        raise HTTPException(status_code=500, detail="server_error")

@app.post("/auth/logout")
def logout():
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(COOKIE_NAME, path="/")
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
