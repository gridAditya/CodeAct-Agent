import asyncpg
import json
from uuid import UUID
from colorama import Fore, Style
from typing import Optional, Union
from models.models import Conversation
from openai.types.responses.response_reasoning_item import ResponseReasoningItem
from openai.types.responses.response_output_message import ResponseOutputMessage
from openai.types.responses.response_function_tool_call import ResponseFunctionToolCall
from openai.types.responses.response_function_web_search import ResponseFunctionWebSearch


from config import logging_config # load the logging configurations
import logging


class ConversationAlreadyExists(Exception):
    """Raised when a conversation with the given conversation_id already exists in the DB."""

class ConversationCRUD:
    """
    Repository for operations on the `conversations` table.
    """

    SELECT_BY_ID = """
    SELECT conversation_id, conversation_title, conversation_history, created_at, updated_at
    FROM conversations
    WHERE LOWER(conversation_id) = LOWER($1)
    """

    # Ensure we cast conversation_id to text before LOWER, and mark $2 as jsonb
    UPDATE_HISTORY = """
    UPDATE conversations
    SET conversation_history = $2
    WHERE LOWER(conversation_id) = LOWER($1)
    RETURNING conversation_id, conversation_title, conversation_history
    """

    # Explicitly cast $1 as uuid, $2 as text and $3 as jsonb to avoid type inference issues
    INSERT_CONVERSATION = """
    INSERT INTO conversations (conversation_id, conversation_title, conversation_history)
    VALUES ($1, $2, $3)
    """

    def __init__(self, pool: asyncpg.Pool):
        self.pool = pool

    def encode_conv_history_from_openai_format(self, conv_history: list)->list[dict]:
        if not isinstance(conv_history, list):
            raise ValueError("conv_history mist be a list")
        encoded_history = [item.model_dump() if not isinstance(item, dict) else item for item in conv_history]
        encoded_history = json.dumps(encoded_history)
        return encoded_history

    def decode_conv_history_to_openai_format(self, conv_history: list)->list:
        if not isinstance(conv_history, list):
            raise ValueError("conv_history must be a list of dicts")

        decoded_history = []
        for item in conv_history:
            if ("role" in item) and ((item["role"] == "user") or (item["role"] == "system")):
                decoded_history.append(item)
            elif "type" in item:
                if item["type"] == "reasoning":
                    decoded_history.append(ResponseReasoningItem(**item))
                elif item["type"] == "function_call":
                    decoded_history.append(ResponseFunctionToolCall(**item))
                elif item["type"] == "function_call_output":
                    decoded_history.append(item)
                elif item["type"] == "web_search_call":
                    decoded_history.append(ResponseFunctionWebSearch(**item))
                elif item["type"] == "message":
                    decoded_history.append(ResponseOutputMessage(**item))
                else:
                    raise ValueError(f"Unknown item type: {item['type']}")
        return decoded_history

    async def get_by_id(self, conversation_id: UUID) -> Conversation:
        """
        Return a Conversation for given conversation_id, or None if not found.
        """
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(self.SELECT_BY_ID, conversation_id)

        if not row:
            logging.warning(f"{Fore.YELLOW}{Style.BRIGHT}[!] Conversation not found: {Fore.BLACK}{conversation_id}{Style.RESET_ALL}")
            return None
        
        conversation_history = self.decode_conv_history_to_openai_format(json.loads(row["conversation_history"]))
        conversation = Conversation(
            conversation_id=row["conversation_id"],
            conversation_title=row["conversation_title"],
            conversation_history=conversation_history,
            created_at=row["created_at"],
            updated_at=row["updated_at"]
        )
        return conversation

    async def update_conversation_history(self, conversation_id: str, conversation_history: list) -> Union[Conversation, None]:
        """
        Update conversation_history for the given conversation_id.
        Returns the updated Conversation on success, or None if the conversation was not found.
        """
        if not conversation_id:
            raise ValueError("conversation_id is required")
        if not isinstance(conversation_id, str):
            raise ValueError("conversation_id must be a string")
        if not isinstance(conversation_history, list):
            logging.error(f"{Fore.RED}{Style.BRIGHT}[-] conversation_history must be a list (got {type(conversation_history)}){Style.RESET_ALL}")
            raise ValueError("conversation_history must be a list of dicts")

        is_valid = await self.get_by_id(conversation_id=conversation_id)
        if not is_valid:
            raise ValueError(f"Conversation with id {conversation_id} does not exist")
        
        conversation_history = self.encode_conv_history_from_openai_format(conv_history=conversation_history) # encode the conv-history to be stored in db
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(self.UPDATE_HISTORY, conversation_id, conversation_history)

        if not row:
            return None

        updated_conv = Conversation(
            conversation_id=row["conversation_id"],
            conversation_title=row["conversation_title"],
            conversation_history=json.loads(row["conversation_history"]) or []
        )
        return updated_conv

    async def create_conversation(
        self,
        conversation_id: str,
        conversation_title:str|None=None,
        conversation_history: list=[],
    ) -> bool:
        """
        Create a new conversation record.
        - If conversation_id already exists: raises ConversationAlreadyExists
        - On other DB errors: lets asyncpg exceptions bubble up (API layer should catch + log)
        Returns True on success.
        """
        if not conversation_id:
            raise ValueError("conversation_id is required")
        if not isinstance(conversation_history, list):  # basic validation
            raise ValueError("conversation_history must be a list (e.g. list[dict])")

        conversation_history = self.encode_conv_history_from_openai_format(conversation_history) # encode the conv-history to be stored in db
        
        async with self.pool.acquire() as conn:
            # check existence first
            existing = await conn.fetchrow(self.SELECT_BY_ID, conversation_id)
            if existing:
                raise ConversationAlreadyExists(f"Conversation with id {conversation_id} already exists")

            try:
                # Passing Python list is fine because we used $3::jsonb in SQL.
                await conn.execute(self.INSERT_CONVERSATION, conversation_id, conversation_title, conversation_history)
            except asyncpg.exceptions.UniqueViolationError as exc:
                raise ConversationAlreadyExists(f"Conversation with id {conversation_id} already exists") from exc
        return True
