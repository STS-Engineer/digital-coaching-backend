from datetime import datetime, timezone
import re

from fastapi import FastAPI, Request, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import func

from auth import COOKIE_NAME, decode_token, create_token, hash_password, verify_password
from bots import BOTS
from db import User, get_db, init_db, Conversation, Message

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

def unique_title(db: Session, email: str, bot_id: str, base: str, exclude_id: int | None = None) -> str:
    base = (base or "New chat").strip()
    if not base:
        base = "New chat"

    q = db.query(Conversation.title).filter(Conversation.email == email, Conversation.bot_id == bot_id)
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
        .filter(Conversation.email == email, Conversation.bot_id == bot_id)
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

def create_conversation(db: Session, email: str, bot_id: str) -> Conversation:
    now = utcnow()
    conv = Conversation(
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

# Initialisez la base de données AVANT l'application
print("🚀 Initialisation de l'application...")
init_db()

class SignupPayload(BaseModel):
    full_name: str
    email: str
    password: str
    confirm_password: str

class LoginPayload(BaseModel):
    email: str
    password: str


def require_user(request: Request) -> str:
    token = request.cookies.get(COOKIE_NAME)
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

        exists = db.query(User).filter(User.email == email).first()
        if exists:
            print(f"Email already exists: {email}")
            raise HTTPException(status_code=409, detail="email already exists")

        user = User(
            full_name=full_name,
            email=email,
            password_hash=hash_password(password),
        )
        db.add(user)
        db.commit()
        db.refresh(user)

        token = create_token(email)

        response = JSONResponse(
            {
                "ok": True,
                "user": {
                    "id": user.id,
                    "email": user.email,
                    "full_name": user.full_name,
                },
            }
        )
        response.set_cookie(
            COOKIE_NAME,
            token,
            httponly=True,
            samesite="lax",
            secure=False,
            max_age=60 * 60 * 8,
            path="/",
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

        user = db.query(User).filter(User.email == email).first()
        if not user:
            raise HTTPException(status_code=401, detail="invalid credentials")

        if not verify_password(password, user.password_hash):
            raise HTTPException(status_code=401, detail="invalid credentials")

        token = create_token(email)
        resp = JSONResponse(
            {
                "ok": True,
                "user": {
                    "id": user.id,
                    "email": user.email,
                    "full_name": user.full_name,
                },
            }
        )
        resp.set_cookie(
            key=COOKIE_NAME,
            value=token,
            httponly=True,
            samesite="lax",
            secure=False,
            path="/",
            max_age=60 * 60 * 8,
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
    
    if not bot_id or not message:
        raise HTTPException(status_code=400, detail="bot_id and message required")
    if bot_id not in BOTS:
        raise HTTPException(status_code=400, detail="Unknown bot_id")

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
                    Conversation.bot_id == bot_id
                )
                .first()
            )
        except (ValueError, TypeError):
            pass  # chat_id invalide, on va créer une nouvelle conversation
    
    # 2) Si aucune conversation trouvée, EN CRÉER UNE NOUVELLE
    if not conv:
        print(f"[DEBUG] Création nouvelle conversation pour le message: {message[:50]}...")
        conv = create_conversation(db=db, email=email, bot_id=bot_id)
        print(f"[DEBUG] Nouvelle conversation créée: {conv.id}")
    else:
        print(f"[DEBUG] Utilisation conversation existante: {conv.id}")

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
    
    resp.set_cookie(
        chat_cookie_name(bot_id), 
        str(conv.id),
        path="/", 
        samesite="lax",
        httponly=True,
        max_age=60*60*24*30
    )
    
    return resp



@app.get("/api/history/{bot_id}")
def history_list(bot_id: str, request: Request, db: Session = Depends(get_db)):
    email = require_user(request)

    convs = list_conversations(db=db, email=email, bot_id=bot_id)
    return {"items": build_history_items(convs)}


@app.post("/api/history/{bot_id}/new")
def history_new(bot_id: str, request: Request, db: Session = Depends(get_db)):
    email = require_user(request)

    if bot_id not in BOTS:
        raise HTTPException(status_code=400, detail="Unknown bot_id")

    conv = create_conversation(db=db, email=email, bot_id=bot_id)
    return {"chat_id": conv.id, "title": conv.title}


@app.get("/api/history/{bot_id}/{chat_id}")
def history_get(bot_id: str, chat_id: int, request: Request, db: Session = Depends(get_db)):
    email = require_user(request)

    conv = (
        db.query(Conversation)
        .filter(Conversation.id == chat_id, Conversation.email == email, Conversation.bot_id == bot_id)
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
        "messages": [{"role": m.role, "content": m.content} for m in msgs],
        "title": summarize_title(conv.title or "New chat"),
        "updated_at": conv.updated_at.isoformat() if conv.updated_at else None
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000, reload=True)
