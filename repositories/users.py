import asyncpg
import bcrypt
from pydantic import BaseModel
from models.models import Users, ConversationList

class UserAlreadyExistsError(Exception):
    """Raised when a user with the given user_id or email already exists in the DB."""

class InvalidCredentialsError(Exception):
    """Raised when the user provides invalid credentials."""

class UserCRUD:
    """Repository for operations on the `users` table."""

    SELECT_BY_ID = """
        SELECT user_id, username, email FROM users WHERE user_id = $1
    """

    SELECT_BY_EMAIL = """
        SELECT user_id, username, email, password FROM users WHERE email = $1
    """

    INSERT_USER = """
        INSERT INTO users (user_id, username, email, password) VALUES ($1, $2, $3, $4)
    """

    INSERT_USER_CONVERSATION = """
        INSERT INTO user_conversation (user_id, conversation_id) VALUES ($1, $2)
    """

    LIST_ALL_CONVERSATIONS = """
        SELECT
            c.conversation_id,
            c.conversation_title,
            c.created_at,
            c.updated_at
        FROM conversations c
        INNER JOIN user_conversation uc ON c.conversation_id = uc.conversation_id
        WHERE uc.user_id = $1
    """

    def __init__(self, pool: asyncpg.Pool):
        if not isinstance(pool, asyncpg.Pool):
            raise ValueError("pool must be an instance of asyncpg.Pool")
        self.pool = pool

    async def create_user(self, user_id: str, username: str, email: str, password: str) -> Users:
        # Data Validation
        if not user_id:
            raise ValueError("user_id must be provided")
        if not username:
            raise ValueError("username must be provided")
        if not email:
            raise ValueError("email must be provided")
        if not password:
            raise ValueError("password must be provided")

        if not isinstance(user_id, str):
            raise ValueError("user_id must be a string")
        if not isinstance(username, str):
            raise ValueError("username must be a string")
        if not isinstance(email, str):
            raise ValueError("email must be a string")
        if not isinstance(password, str):
            raise ValueError("password must be a string")

        async with self.pool.acquire() as conn:
            # Standardize data
            username = username.lower().strip()
            email = email.lower().strip()

            # Check if user already exists
            existing_user = await conn.fetchrow(self.SELECT_BY_EMAIL, email)
            if existing_user:
                raise UserAlreadyExistsError("User with this ID or email already exists")

            # Hash password
            hashed_password = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

            # Insert new user
            await conn.execute(
                self.INSERT_USER,
                user_id, username, email, hashed_password
            )

            return Users(
                user_id=user_id,
                username=username,
                email=email
            )

    async def login_user(self, email: str, password: str) -> Users:
        async with self.pool.acquire() as conn:
            user_record = await conn.fetchrow(self.SELECT_BY_EMAIL, email)

            if not user_record:
                raise InvalidCredentialsError("Invalid email or password")

            # Verify password
            if not bcrypt.checkpw(password.encode('utf-8'), user_record['password'].encode('utf-8')):
                raise InvalidCredentialsError("Invalid email or password")

            return Users(
                user_id=user_record['user_id'],
                username=user_record['username'],
                email=user_record['email']
            )

    async def list_user_conversations(self, user_id: str) -> ConversationList:
        async with self.pool.acquire() as conn:
            conversation_records = await conn.fetch(self.LIST_ALL_CONVERSATIONS, user_id)
            conv_list = [dict(**record) for record in conversation_records]
            return ConversationList(conv_list=conv_list)

    async def add_user_conversation(self, user_id: str, conversation_id: str):
        async with self.pool.acquire() as conn:
            await conn.execute(self.INSERT_USER_CONVERSATION, user_id, conversation_id)
