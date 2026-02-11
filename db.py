import os
from datetime import datetime
from urllib.parse import quote_plus
from sqlalchemy import create_engine, Text, Integer, text
from sqlalchemy.orm import sessionmaker, DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.sql import func
from sqlalchemy import Text, Integer, ForeignKey, DateTime, func

# Configuration
DB_USER = "administrationSTS"
DB_PASSWORD = "St$@0987"
DB_HOST = "avo-adb-002.postgres.database.azure.com"
DB_PORT = "5432"
DB_NAME = "digital_coaching_DB"

# Encodez le mot de passe
password_encoded = quote_plus(DB_PASSWORD)
print(f"  Mot de passe encodé: {password_encoded}")

# Construire l'URL
DATABASE_URL = f"postgresql+psycopg2://{DB_USER}:{password_encoded}@{DB_HOST}:{DB_PORT}/{DB_NAME}?sslmode=require"

# Créer le moteur
try:
    engine = create_engine(
        DATABASE_URL,
        pool_pre_ping=True,
        pool_recycle=300,
        pool_timeout=30,
        max_overflow=10,
        echo=True
    )
    print("✅ Moteur SQLAlchemy créé")
except Exception as e:
    print(f"❌ Erreur création moteur: {e}")
    engine = None

if engine:
    SessionLocal = sessionmaker(
        bind=engine,
        autocommit=False,
        autoflush=False,
        expire_on_commit=False
    )
else:
    SessionLocal = None

class Base(DeclarativeBase):
    pass

class User(Base):
    __tablename__ = "users"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    full_name: Mapped[str] = mapped_column(Text, nullable=False)
    email: Mapped[str] = mapped_column(Text, unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(default=func.now(), nullable=False)
    
    def __repr__(self):
        return f"<User(id={self.id}, email='{self.email}')>"

class Conversation(Base):
    __tablename__ = "conversations"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)  # UUID as text for simplicity
    email: Mapped[str] = mapped_column(Text, index=True)
    bot_id: Mapped[str] = mapped_column(Text, index=True)
    title: Mapped[str] = mapped_column(Text, default="New chat")
    ui_lang: Mapped[str | None] = mapped_column(Text, nullable=True)
    stage: Mapped[str] = mapped_column(Text, default="select_lang")
    created_at: Mapped[datetime] = mapped_column(default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(default=func.now(), nullable=False)

    messages: Mapped[list["Message"]] = relationship(
        back_populates="conversation",
        cascade="all, delete-orphan",
        order_by="Message.created_at.asc()",
    )

class Message(Base):
    __tablename__ = "messages"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True) 
    conversation_id: Mapped[int] = mapped_column(Integer, ForeignKey("conversations.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(Text)     # user|assistant
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    conversation: Mapped["Conversation"] = relationship(back_populates="messages")

def init_db():
    """Initialise la base de données (crée les tables)"""
    try:
        print("🔄 Création des tables...")
        Base.metadata.create_all(bind=engine)
        print("✅ Tables créées avec succès!")
        
        # Vérifier
        with engine.connect() as conn:
            from sqlalchemy import text
            result = conn.execute(text("""
                SELECT COUNT(*) 
                FROM information_schema.tables 
                WHERE table_schema = 'public'
            """))
            count = result.scalar()
            print(f"📊 Nombre de tables: {count}")
            
    except Exception as e:
        print(f"❌ Erreur lors de la création des tables: {e}")
        import traceback
        traceback.print_exc()
        
def get_db():
    if not SessionLocal:
        raise Exception("Database not initialized")
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def test_connection():
    """Test simple de connexion"""
    if not engine:
        print("❌ Moteur non initialisé")
        return False
    
    try:
        print("\n🧪 Test de connexion...")
        with engine.connect() as conn:
            # IMPORTANT: Utilisez text() pour les requêtes SQL brutes
            result = conn.execute(text("SELECT version()"))
            version = result.scalar()
            print(f"✅ Connexion réussie! PostgreSQL {version}")
            
            # Vérifier les tables existantes
            result = conn.execute(text("""
                SELECT table_name 
                FROM information_schema.tables 
                WHERE table_schema = 'public'
            """))
            tables = result.fetchall()
            table_names = [t[0] for t in tables]
            print(f"📋 Tables existantes: {table_names}")
            
            return True
    except Exception as e:
        print(f"❌ Erreur de connexion: {e}")
        import traceback
        traceback.print_exc()
        return False

def create_tables():
    """Crée les tables si elles n'existent pas"""
    if not engine:
        print("❌ Moteur non initialisé")
        return
    
    try:
        
        # Créer avec SQLAlchemy
        Base.metadata.create_all(bind=engine)
        print("✅ Tables créées")
        
        # Vérifier
        with engine.connect() as conn:
            result = conn.execute(text("""
                SELECT COUNT(*) 
                FROM information_schema.tables 
                WHERE table_schema = 'public'
            """))
            count = result.scalar()
            print(f"📊 Nombre de tables: {count}")
            
    except Exception as e:
        print(f"⚠️  Erreur création tables: {e}")
        import traceback
        traceback.print_exc()

def test_insert_user():
    """Test d'insertion d'un utilisateur"""
    if not SessionLocal:
        print("❌ Session non initialisée")
        return
    
    db = SessionLocal()
    try:
        print("\n👤 Test d'insertion utilisateur...")
        
        # Vérifier si la table existe
        from sqlalchemy import inspect
        inspector = inspect(engine)
        table_names = inspector.get_table_names()
        print(f"Tables détectées: {table_names}")
        
        if 'users' not in table_names:
            print("❌ Table 'users' n'existe pas, création...")
            # Créer la table manuellement
            with engine.connect() as conn:
                conn.execute(text("""
                    CREATE TABLE users (
                        id SERIAL PRIMARY KEY,
                        full_name TEXT NOT NULL,
                        email TEXT UNIQUE NOT NULL,
                        password_hash TEXT NOT NULL,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """))
                conn.commit()
            print("✅ Table 'users' créée manuellement")
        
        # Vérifier si l'utilisateur test existe
        test_user = db.query(User).filter(User.email == "test@example.com").first()
        
        if not test_user:
            # Hasher un mot de passe simple
            import hashlib
            password = "Test1234!"
            password_hash = hashlib.sha256(password.encode()).hexdigest()
            
            # Créer l'utilisateur
            new_user = User(
                full_name="Test User",
                email="test@example.com",
                password_hash=password_hash
            )
            db.add(new_user)
            db.commit()
            print("✅ Utilisateur test créé")
        else:
            print("ℹ️  Utilisateur test existe déjà")
        
        # Compter
        count = db.query(User).count()
        print(f"👥 Nombre d'utilisateurs: {count}")
        
        # Lister les utilisateurs
        users = db.query(User).all()
        for user in users:
            print(f"  - {user.email} ({user.full_name})")
        
    except Exception as e:
        print(f"❌ Erreur insertion: {e}")
        import traceback
        traceback.print_exc()
        db.rollback()
    finally:
        db.close()
