import os
import asyncpg
import asyncio

from temporalio import activity
from colorama import Fore, Style
from openai.types.responses.response_reasoning_item import ResponseReasoningItem
from openai.types.responses.response_output_message import ResponseOutputMessage
from openai.types.responses.response_function_tool_call import ResponseFunctionToolCall
from openai.types.responses.response_function_web_search import ResponseFunctionWebSearch
from models.models import Conversation
from orchestration.code_act_models import ConvHistory, UpdateConvHistory, LLMParams, CodeInputs, ExecuteTools
from repositories.conversations import ConversationCRUD
from services.llm_client import LLMClient
from services.code_executor import CodeExecutor
from tools.tool_logic import execute_code

from config import logging_config # load the logging configurations
import logging

_conv_repo: ConversationCRUD | None = None
_func_dict = {
    "python_tool": execute_code
}

def set_conv_repo(repo: ConversationCRUD):
    global _conv_repo
    _conv_repo = repo


def decode_conv_history_to_openai_format(conv_history: list)->list:
        if not isinstance(conv_history, list):
            raise ValueError("conv_history must be a list of dicts")

        decoded_history = []
        for item in conv_history:
            if ("role" in item) and (item["role"] == "user"):
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

@activity.defn(name="get_conversation_history")
async def get_agent_conversation_history(params: ConvHistory)->Conversation:
    response = await _conv_repo.get_by_id(conversation_id=params.conv_id)
    if not response:
        return []
    return response.conversation_history

@activity.defn(name="update_conv_history")
async def update_conv_history(params: UpdateConvHistory):
    logging.info(
            f"{Fore.BLUE}{Style.BRIGHT}[+] Updating conversation_history for: {Fore.BLACK}{params.conv_id}{Style.RESET_ALL}"
        )
    conv_id, conv_history = params.conv_id, params.conv_history
    response = await _conv_repo.update_conversation_history(conversation_id=conv_id, conversation_history=conv_history)

    if not response:
        logging.warning(f"{Fore.YELLOW}{Style.BRIGHT}[!] Conversation not found (no update performed): {Fore.BLACK}{conv_id}{Style.RESET_ALL}")
    
    logging.info(
            f"{Fore.GREEN}{Style.BRIGHT}[+] Updated conversation_history for: {Fore.BLACK}{conv_id}{Style.RESET_ALL}"
        )

@activity.defn(name="get_last_modified_file")
async def get_last_modified_file(dir_path: str) -> str:
    if not os.path.isdir(dir_path):
        return ""
    
    # List all non-hidden files (exclude files starting with ".")
    files = [
        os.path.join(dir_path, f)
        for f in os.listdir(dir_path)
        if os.path.isfile(os.path.join(dir_path, f)) and not f.startswith(".")
    ]
    
    if not files:
        return ""
    
    # Get the file with the maximum modification time
    last_modified_file = max(files, key=os.path.getmtime)
    return os.path.abspath(last_modified_file)

@activity.defn(name="get_response_from_llm")
async def get_llm_response(llm_params: LLMParams):
    # Decode the conv-history
    llm_params.params['input'] = decode_conv_history_to_openai_format(llm_params.params['input'])
    llm_client = LLMClient(base_url=os.getenv("BASE_URL"), api_key=os.getenv("API_KEY"))
    response = await llm_client.get_via_responses_api(**llm_params.params) # get response from LLM
    return response

@activity.defn(name="execute_tools")
async def execute_tools(tools: ExecuteTools):
    tool_list, iter_count, max_iter = tools.tools, tools.iteration_count, tools.max_iterations
    task_list = []

    for tool in tool_list:
        tool_name = tool["name"]
        tool_kwargs = tool["arguments"]
        task_list.append(asyncio.create_task(_func_dict[tool_name](**tool_kwargs)))
    
    tool_output = await asyncio.gather(*task_list, return_exceptions=True)
    parsed_tool_output = []
    for idx, output in enumerate(tool_output):
        tool_name = tool_list[idx]["name"]
        tool_call_id = tool_list[idx]["call_id"]
        tool_result = str(output) + f"\n\n- RUN {iter_count+1}/{max_iter}"

        parsed_tool_output.append({
            "type": "function_call_output",
            "call_id": tool_call_id,
            "output": tool_result
        })
    return parsed_tool_output
