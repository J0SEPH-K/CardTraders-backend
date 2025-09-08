from typing import List, Optional
from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    email: str
    password: str


class Pfp(BaseModel):
    url: Optional[str] = None
    storage: Optional[str] = None


class UserPublic(BaseModel):
    id: Optional[str] = Field(default=None, description="Mongo _id as string")
    userId: str
    username: str
    email: str
    phone_num: Optional[str] = None
    address: Optional[str] = None
    signup_date: Optional[str] = None
    suggested_num: Optional[int] = 0
    starred_item: List[str] = []
    messages: Optional[list] = []
    premade_messages: List[str] = []
    notification: Optional[bool] = True
    blocked_users: List[str] = []
    pfp: Optional[Pfp] = None
    bank_acc: Optional[str] = None


class LoginResponse(BaseModel):
    user: UserPublic
