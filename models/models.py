from typing import Optional
from pydantic import BaseModel
from datetime import datetime

class CreateUserRequest(BaseModel):
    username: str
    email: str
    password: str

class CreateConversationRequest(BaseModel):
    user_id: str

class ListConversationsRequest(BaseModel):
    user_id: str

class LoginRequest(BaseModel):
    email: str
    password: str

class Users(BaseModel):
    user_id: str
    username: str
    email: str

class SendMessageRequest(BaseModel):
    conversation_id: str
    user_input: str


class Conversation(BaseModel):
    conversation_id: str
    conversation_title: Optional[str] = None
    conversation_history: list
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

class ConversationList(BaseModel):
    conv_list: list

