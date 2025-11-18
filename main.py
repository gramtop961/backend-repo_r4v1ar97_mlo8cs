import os
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from fastapi import FastAPI, HTTPException, Depends, Header
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, EmailStr
from jose import JWTError, jwt
from passlib.context import CryptContext
from bson import ObjectId

from database import db, create_document, get_documents

# JWT/SECURITY CONFIG
SECRET_KEY = os.getenv("JWT_SECRET", "supersecret-key-change")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24 * 7  # 7 days
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

app = FastAPI(title="Wallpaper SaaS API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# -------------------- MODELS --------------------
class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


class RegisterRequest(BaseModel):
    name: str
    email: EmailStr
    password: str


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class SubscriptionRequest(BaseModel):
    plan: str  # free | pro | elite


class WallpaperCreate(BaseModel):
    title: str
    category: str
    image_url: str
    thumbnail_url: Optional[str] = None
    resolution: str = "3840x2160"
    tags: List[str] = []
    is_live: bool = False
    author: Optional[str] = None


# -------------------- UTILS --------------------

def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


async def get_current_user(authorization: Optional[str] = Header(None)):
    if not authorization:
        raise HTTPException(status_code=401, detail="Not authenticated")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status_code=401, detail="Invalid auth header")
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id: str = payload.get("sub")
        if user_id is None:
            raise HTTPException(status_code=401, detail="Invalid token")
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid token")

    user = db["user"].find_one({"_id": ObjectId(user_id)})
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    # Convert ObjectId
    user["id"] = str(user["_id"]) 
    user.pop("_id", None)
    return user


# -------------------- CORE --------------------
@app.get("/")
def root():
    return {"message": "Wallpaper SaaS Backend Running"}


@app.get("/test")
def test_database():
    resp = {
        "backend": "✅ Running",
        "database": "❌ Not Available",
        "database_url": "✅ Set" if os.getenv("DATABASE_URL") else "❌ Not Set",
        "database_name": "✅ Set" if os.getenv("DATABASE_NAME") else "❌ Not Set",
        "connection_status": "Not Connected",
        "collections": []
    }
    try:
        if db is not None:
            resp["database"] = "✅ Connected"
            resp["connection_status"] = "Connected"
            resp["collections"] = db.list_collection_names()
    except Exception as e:
        resp["database"] = f"⚠️ {str(e)[:60]}"
    return resp


# -------------------- AUTH --------------------
@app.post("/auth/register", response_model=Token)
def register(body: RegisterRequest):
    existing = db["user"].find_one({"email": body.email})
    if existing:
        raise HTTPException(status_code=400, detail="Email already registered")
    doc = {
        "name": body.name,
        "email": body.email,
        "password_hash": hash_password(body.password),
        "role": "user",
        "subscribed": False,
        "plan": "free",
        "plan_ends_at": None,
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
    }
    result = db["user"].insert_one(doc)
    token = create_access_token({"sub": str(result.inserted_id)})
    return {"access_token": token}


@app.post("/auth/login", response_model=Token)
def login(body: LoginRequest):
    user = db["user"].find_one({"email": body.email})
    if not user or not verify_password(body.password, user.get("password_hash", "")):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    token = create_access_token({"sub": str(user["_id"])})
    return {"access_token": token}


@app.get("/me")
def me(current_user: dict = Depends(get_current_user)):
    return current_user


# -------------------- SUBSCRIPTION --------------------
@app.post("/subscribe")
def subscribe(body: SubscriptionRequest, current_user: dict = Depends(get_current_user)):
    if body.plan not in ["free", "pro", "elite"]:
        raise HTTPException(status_code=400, detail="Invalid plan")
    subscribed = body.plan != "free"
    ends_at = None
    if subscribed:
        ends_at = datetime.now(timezone.utc) + timedelta(days=30)
    db["user"].update_one({"_id": ObjectId(current_user["id"])}, {"$set": {
        "plan": body.plan,
        "subscribed": subscribed,
        "plan_ends_at": ends_at,
        "updated_at": datetime.now(timezone.utc)
    }})
    return {"status": "ok", "plan": body.plan, "subscribed": subscribed, "plan_ends_at": ends_at}


# -------------------- CATEGORIES --------------------
DEFAULT_CATEGORIES = [
    {"slug": "anime", "title": "Anime", "description": "Stylized and cinematic anime art"},
    {"slug": "nature", "title": "Nature", "description": "Forests, oceans, and beyond"},
    {"slug": "scenery", "title": "Scenery", "description": "Landscapes and cityscapes"},
    {"slug": "live", "title": "Live Wallpapers", "description": "Dynamic video wallpapers"},
]


@app.get("/categories")
def get_categories():
    # Seed if empty
    if db["category"].count_documents({}) == 0:
        for c in DEFAULT_CATEGORIES:
            db["category"].update_one({"slug": c["slug"]}, {"$setOnInsert": {**c}}, upsert=True)
    cats = list(db["category"].find({}))
    for c in cats:
        c["id"] = str(c.pop("_id"))
    return cats


# -------------------- WALLPAPERS --------------------
@app.get("/wallpapers")
def list_wallpapers(category: Optional[str] = None, limit: int = 50, authorization: Optional[str] = Header(None)):
    # Optional auth to identify subscription status
    subscribed = False
    if authorization:
        try:
            _, _, token = authorization.partition(" ")
            payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
            user_id: str = payload.get("sub")
            if user_id:
                user = db["user"].find_one({"_id": ObjectId(user_id)})
                if user:
                    subscribed = bool(user.get("subscribed"))
        except Exception:
            pass

    query = {}
    if category:
        query["category"] = category
    cursor = db["wallpaper"].find(query).sort("created_at", -1).limit(limit)
    items = []
    for w in cursor:
        item = {
            "id": str(w["_id"]),
            "title": w.get("title"),
            "category": w.get("category"),
            "resolution": w.get("resolution"),
            "thumbnail_url": w.get("thumbnail_url") or w.get("image_url"),
            "is_live": w.get("is_live", False),
            "author": w.get("author"),
            "downloads": w.get("downloads", 0),
            "image_url": w.get("image_url") if subscribed else f"{w.get('image_url')}?wm=1",
            "watermarked": not subscribed,
        }
        items.append(item)
    return {"items": items, "subscribed": subscribed}


@app.get("/wallpapers/{wallpaper_id}/download")
def download_wallpaper(wallpaper_id: str, current_user: dict = Depends(get_current_user)):
    wp = db["wallpaper"].find_one({"_id": ObjectId(wallpaper_id)})
    if not wp:
        raise HTTPException(status_code=404, detail="Wallpaper not found")
    if not current_user.get("subscribed"):
        raise HTTPException(status_code=402, detail="Subscription required for full-resolution download")
    db["wallpaper"].update_one({"_id": ObjectId(wallpaper_id)}, {"$inc": {"downloads": 1}})
    return {"url": wp.get("image_url")}


# Admin create wallpaper
@app.post("/admin/wallpapers")
def admin_create_wallpaper(body: WallpaperCreate, current_user: dict = Depends(get_current_user)):
    if current_user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin only")
    doc = {
        **body.model_dump(),
        "downloads": 0,
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
    }
    result = db["wallpaper"].insert_one(doc)
    return {"id": str(result.inserted_id), **doc}


# Seed some sample wallpapers (idempotent)
@app.post("/admin/seed")
def seed_sample(current_user: dict = Depends(get_current_user)):
    if current_user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin only")
    samples = [
        {
            "title": "Galactic Neon Wave",
            "category": "scenery",
            "image_url": "https://images.unsplash.com/photo-1500530855697-b586d89ba3ee",
            "thumbnail_url": "https://images.unsplash.com/photo-1500530855697-b586d89ba3ee?w=1200",
            "tags": ["neon", "galaxy"],
        },
        {
            "title": "Mystic Forest",
            "category": "nature",
            "image_url": "https://images.unsplash.com/photo-1501785888041-af3ef285b470",
            "thumbnail_url": "https://images.unsplash.com/photo-1501785888041-af3ef285b470?w=1200",
            "tags": ["forest", "fog"],
        },
        {
            "title": "City Night Drive",
            "category": "scenery",
            "image_url": "https://images.unsplash.com/photo-1508057198894-247b23fe5ade",
            "thumbnail_url": "https://images.unsplash.com/photo-1508057198894-247b23fe5ade?w=1200",
            "tags": ["city", "night"],
        },
        {
            "title": "Anime Neon Alley",
            "category": "anime",
            "image_url": "https://images.unsplash.com/photo-1542396601-dca920ea2807",
            "thumbnail_url": "https://images.unsplash.com/photo-1542396601-dca920ea2807?w=1200",
            "tags": ["anime", "neon"],
        },
    ]
    for s in samples:
        existing = db["wallpaper"].find_one({"title": s["title"]})
        if not existing:
            db["wallpaper"].insert_one({
                **s,
                "resolution": "3840x2160",
                "is_live": False,
                "downloads": 0,
                "created_at": datetime.now(timezone.utc),
                "updated_at": datetime.now(timezone.utc),
            })
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
