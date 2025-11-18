"""
Database Schemas for Premium Wallpaper SaaS

Each Pydantic model represents a MongoDB collection. The collection name is the
lowercase of the class name (e.g., User -> "user").
"""

from pydantic import BaseModel, Field, EmailStr
from typing import Optional, List
from datetime import datetime


class User(BaseModel):
    name: str = Field(..., description="Full name")
    email: EmailStr = Field(..., description="Unique email address")
    password_hash: str = Field(..., description="BCrypt password hash")
    role: str = Field("user", description="user | admin")
    subscribed: bool = Field(False, description="Has an active subscription")
    plan: Optional[str] = Field(None, description="free | pro | elite")
    plan_ends_at: Optional[datetime] = Field(None, description="When subscription ends")
    avatar_url: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class Category(BaseModel):
    slug: str = Field(..., description="URL-friendly identifier")
    title: str = Field(..., description="Display name")
    description: Optional[str] = None


class Wallpaper(BaseModel):
    title: str = Field(...)
    category: str = Field(..., description="Category slug, e.g., anime/nature/scenery/live")
    image_url: str = Field(..., description="Origin image URL")
    thumbnail_url: Optional[str] = Field(None, description="Optional thumbnail URL")
    resolution: str = Field("3840x2160", description="e.g., 3840x2160 for 4K")
    tags: List[str] = Field(default_factory=list)
    is_live: bool = Field(False, description="Whether it's a live wallpaper/video")
    author: Optional[str] = None
    downloads: int = Field(0)
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class SubscriptionEvent(BaseModel):
    user_id: str
    plan: str
    status: str = Field("active", description="active | canceled")
    amount: int = Field(0, description="Amount in cents for record keeping")
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
