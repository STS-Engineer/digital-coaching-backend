# auth.py
import os
from datetime import datetime, timedelta, timezone
from jose import jwt, JWTError
from passlib.context import CryptContext
import hashlib
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

# Utilisez pbkdf2_sha256 au lieu de bcrypt
pwd_context = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto")

def hash_password(password: str) -> str:
    """Hash un mot de passe avec pbkdf2_sha256"""
    return pwd_context.hash(password)

def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Vérifie un mot de passe"""
    try:
        return pwd_context.verify(plain_password, hashed_password)
    except:
        # Fallback SHA256
        return hashlib.sha256(plain_password.encode()).hexdigest() == hashed_password

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