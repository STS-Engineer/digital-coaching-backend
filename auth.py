# auth.py
import os
import base64
import hashlib
import logging
from datetime import datetime, timedelta, timezone
from jose import jwt, JWTError
import bcrypt
from dotenv import load_dotenv

# Chargez les variables d'environnement
load_dotenv()

# Utilisez une clé par défaut si elle n'est pas définie
SECRET_KEY = os.getenv("SECRET_KEY") 
if not SECRET_KEY:
    print("⚠️  SECRET_KEY non définie, utilisation d'une clé par défaut")
    SECRET_KEY = "votre_cle_secrete_tres_longue_pour_la_securite_1234567890"
    
ALGORITHM = "HS256"
COOKIE_NAME = "access_token"

logger = logging.getLogger(__name__)

def _normalize_password(password: str) -> str:
    """
    Normalise le mot de passe pour respecter la limite bcrypt (72 octets).
    Si >72 octets, pr?-hash SHA256 puis encode en base64.
    """
    password_bytes = password.encode("utf-8")
    if len(password_bytes) > 72:
        sha_hash = hashlib.sha256(password_bytes).digest()
        return base64.b64encode(sha_hash).decode("ascii")
    return password

def hash_password(password: str) -> str:
    """Hash un mot de passe avec bcrypt (sans passlib)."""
    normalized = _normalize_password(password)
    normalized_bytes = normalized.encode("utf-8")
    if len(normalized_bytes) > 72:
        raise ValueError("Password normalization failed: >72 bytes")
    salt = bcrypt.gensalt()
    hashed = bcrypt.hashpw(normalized_bytes, salt)
    return hashed.decode("utf-8")

def verify_password(plain_password: str, hashed_password: str) -> bool:
    """V?rifie un mot de passe avec bcrypt (sans passlib)."""
    try:
        normalized = _normalize_password(plain_password)
        normalized_bytes = normalized.encode("utf-8")
        hashed_bytes = hashed_password.encode("utf-8")
        return bcrypt.checkpw(normalized_bytes, hashed_bytes)
    except Exception as e:
        logger.error(f"Password verification error: {e}")
        return False

def create_token(subject_email: str) -> str:
    """Crée un token JWT"""
    exp = datetime.now(timezone.utc) + timedelta(hours=8)
    
    # Assurez-vous que la clé est une chaîne
    key = str(SECRET_KEY)
    
    # Debug
    print(f"🔐 Création token pour: {subject_email}")
    print(f"🔐 Clé utilisée: {key[:10]}... (longueur: {len(key)})")
    
    return jwt.encode(
        {"sub": subject_email, "exp": exp}, 
        key, 
        algorithm=ALGORITHM
    )

def decode_token(token: str) -> str | None:
    """Décode et valide un token JWT"""
    try:
        key = str(SECRET_KEY)
        payload = jwt.decode(token, key, algorithms=[ALGORITHM])
        return payload.get("sub")
    except JWTError as e:
        print(f"❌ Erreur décodage token: {e}")
        return None
