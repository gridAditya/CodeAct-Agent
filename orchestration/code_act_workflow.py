import os
import re
import json
import asyncpg

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError, ApplicationError
from datetime import timedelta
from colorama import Fore, Style
from orchestration.code_act_models import AgentRunParams, ConvHistory, LLMParams, CodeInputs, UpdateConvHistory, ExecuteTools

from openai.types.responses.response_reasoning_item import ResponseReasoningItem
from openai.types.responses.response_output_message import ResponseOutputMessage
from openai.types.responses.response_function_tool_call import ResponseFunctionToolCall
from openai.types.responses.response_function_web_search import ResponseFunctionWebSearch

from config import logging_config
import logging

def deserialise_response(response: dict)->list:
    if not isinstance(response, dict):
        raise ValueError("response must be a dict")

    decoded_history = []
    for item in response['output']:
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

@workflow.defn(name="code_act_workflow")
class CodeActWorkflow:
    def __init__(self):
        self.iteration_num = 0
    
    def _parse_response(self, response: str) -> tuple[str, list[str]]:
        """
        - If one or more <code>...</code> tags are present: return ("code", [code1, code2, ...])
        - Else if one or more <response>...</response> tags are present: return ("text", [resp1, resp2, ...])
        - Else: raise XML Error
        """
        if response is None:
            raise ValueError("Invalid XML Response Format: expected <code>...</code> or <response>...</response>")

        # Check for presence of both tag types (with possible attributes)
        has_code = bool(re.search(r"<code[^>]*>", response, flags=re.IGNORECASE))
        has_response = bool(re.search(r"<response[^>]*>", response, flags=re.IGNORECASE))

        if has_code and has_response:
            raise ValueError("Invalid XML Response Format: <code> and <response> cannot both be present. Only one is allowed.")

        # look for all <code>...</code> blocks (non-greedy), allowing attributes in opening tag
        code_pattern = r"<code[^>]*>(.*?)</code>"
        codes = re.findall(code_pattern, response, flags=re.DOTALL | re.IGNORECASE)
        if codes:
            # strip leading/trailing whitespace from each block
            codes = [c.strip() for c in codes]
            return "code", codes

        # look for all <response>...</response> blocks, allowing attributes in opening tag
        resp_pattern = r"<response[^>]*>(.*?)</response>"
        responses = re.findall(resp_pattern, response, flags=re.DOTALL | re.IGNORECASE)
        if responses:
            responses = [r.strip() for r in responses]
            return "text", responses

        # fallback: no tags found — return the whole input as a single text item
        raise ValueError("Invalid XML Response Format: expected <code>...</code> or <response>...</response>")
    
    def extract_llm_response(self, outputs: list)->str:
        text_response = ""
        for output in outputs:
            if output['type'] == 'message':
                text_response += "\n".join([content['text'] for content in output['content']])
        return text_response
    
    async def _get_conv_history(self):
        try:
            conv_history = await workflow.execute_activity(
                "get_conversation_history",
                ConvHistory(conv_id=self.conv_id),
                start_to_close_timeout=timedelta(seconds=5),
                retry_policy=RetryPolicy(
                    initial_interval=timedelta(seconds=1),
                    backoff_coefficient=2.0,
                    maximum_interval=timedelta(seconds=10),
                    maximum_attempts=3,
                    non_retryable_error_types=["asyncpg.PostgresError"]
                    )
                )
        except ActivityError as ae:
            cause = ae.cause
            if isinstance(cause, asyncpg.PostgresError):
                logging.info(f"{Fore.RED}{Style.BRIGHT}[-] Unable to fetch conversation history from database{Style.RESET_ALL}")
        except Exception as e:
            logging.error(f"{Fore.RED}{Style.BRIGHT}[-] 'get_conversation_history' activity execution encountered an unexpected error: {Fore.BLACK}{e}{Style.RESET_ALL}")
            logging.error(f"{Fore.RED}{Style.BRIGHT}ERROR: {Fore.BLACK}{e}{Style.RESET_ALL}")
            raise ApplicationError(str(e), non_retryable=False)
            # Above Exception causes Workflow-Suspension, not Workflow Failure, i.e it won't respect the RetryPolicy we defined during
            # workflow execution and will continue to execute indefinitely waiting for codefix, that's why we raise ApplicationError(non_retryable=False)
            # Temporal only applies retry logic to exceptions that are explicitly marked as retryable, such as ApplicationError. Everything else, including built-in exceptions, leads to indefinite suspension.      
        return conv_history
    
    async def _get_last_working_copy_path(self):
        try:
            last_working_copy_path = await workflow.execute_activity(
                "get_last_modified_file",
                self.workspace_dir_path,
                start_to_close_timeout=timedelta(seconds=5),
                retry_policy=RetryPolicy(
                    initial_interval=timedelta(seconds=1),
                    backoff_coefficient=2.0,
                    maximum_interval=timedelta(seconds=10),
                    maximum_attempts=3
                )
            )
        except Exception as e:
            logging.error(f"{Fore.RED}{Style.BRIGHT}[-] 'get_last_modified_file' activity execution encountered an unexpected error: {Fore.BLACK}{e}{Style.RESET_ALL}")
            logging.error(f"{Fore.RED}{Style.BRIGHT}ERROR: {Fore.BLACK}{e}{Style.RESET_ALL}")
            raise ApplicationError(str(e), non_retryable=False)
        return last_working_copy_path
    
    async def _get_llm_response(self, llm_params: dict):
        try:
            response = await workflow.execute_activity(
                "get_response_from_llm",
                LLMParams(params=llm_params),
                start_to_close_timeout=timedelta(seconds=120),
                retry_policy=RetryPolicy(
                    initial_interval=timedelta(seconds=1),
                    backoff_coefficient=2.0,
                    maximum_interval=timedelta(seconds=10),
                    maximum_attempts=3
                )
            )
        except Exception as e:
            logging.error(f"{Fore.RED}{Style.BRIGHT}[-] 'get_response_from_llm' activity execution encountered an unexpected error: {Fore.BLACK}{e}{Style.RESET_ALL}")
            logging.error(f"{Fore.RED}{Style.BRIGHT}ERROR: {Fore.BLACK}{e}{Style.RESET_ALL}")
            raise ApplicationError(str(e), non_retryable=False)
        return response
    
    async def _execute_code(self, code: str)->dict:
        if not isinstance(code, str):
            raise TypeError(f"Expected 'code' to be of type 'str' but got {type(code).__name__}") 
        try:
            exec_result = await workflow.execute_activity(
                "execute_code",
                CodeInputs(code=code),
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=RetryPolicy(
                    initial_interval=timedelta(seconds=1),
                    backoff_coefficient=2.0,
                    maximum_interval=timedelta(seconds=10),
                    maximum_attempts=3
                )
            )
        except Exception as e:
            logging.error(f"{Fore.RED}{Style.BRIGHT}[-] 'execute_code' activity execution encountered an unexpected error: {Fore.BLACK}{e}{Style.RESET_ALL}")
            logging.error(f"{Fore.RED}{Style.BRIGHT}ERROR: {Fore.BLACK}{e}{Style.RESET_ALL}")
            raise ApplicationError(str(e), non_retryable=False)
        
        return exec_result

    async def _update_conv_history(self, conv_id: str, conv_history: list):
        try:
            await workflow.execute_activity(
                "update_conv_history",
                UpdateConvHistory(conv_id=conv_id, conv_history=conv_history),
                start_to_close_timeout=timedelta(seconds=5),
                retry_policy=RetryPolicy(
                    initial_interval=timedelta(seconds=1),
                    backoff_coefficient=2.0,
                    maximum_interval=timedelta(seconds=10),
                    maximum_attempts=3,
                    non_retryable_error_types=["asyncpg.PostgresError"]
                )
            )
        except ActivityError as ae:
            cause = ae.cause
            if isinstance(cause, asyncpg.PostgresError):
                logging.info(f"{Fore.RED}{Style.BRIGHT}[-] Unable to fetch conversation history from database{Style.RESET_ALL}")
        except Exception as e:
            logging.error(f"{Fore.RED}{Style.BRIGHT}[-] 'update_conv_history' activity execution encountered an unexpected error: {Fore.BLACK}{e}{Style.RESET_ALL}")
            logging.error(f"{Fore.RED}{Style.BRIGHT}ERROR: {Fore.BLACK}{e}{Style.RESET_ALL}")
            raise ApplicationError(str(e), non_retryable=False)
    
    async def _get_tools_response(self, tools: list, iteration_count: int, max_iterations: int)->list:
        tool_response = await workflow.execute_activity(
            "execute_tools",
            ExecuteTools(tools=tools, iteration_count=iteration_count, max_iterations=max_iterations),
            start_to_close_timeout=timedelta(seconds=30),
                retry_policy=RetryPolicy(
                    initial_interval=timedelta(seconds=1),
                    backoff_coefficient=2.0,
                    maximum_interval=timedelta(seconds=10),
                    maximum_attempts=3
                )
            )
        return tool_response

    @workflow.run
    async def run(self, params: AgentRunParams)->str:
        logging.info(f"{Fore.BLUE}{Style.BRIGHT}[+] CodeAct-Agent Execution Execution Started.....{Style.RESET_ALL}")

        # Define the Agent-Variables
        self.model = params.model
        self.reasoning_effort = params.reasoning_effort
        self.tools = params.tools
        self.tool_choice = params.tool_choice
        self.iteration_num = 1
        self.max_iterations = params.max_iterations
        self.conv_id = params.conversation_id
        self.workspace_dir_path = params.workspace_dir_path
        self.curr_response = ""
        last_working_copy_path = await self._get_last_working_copy_path()
        self.final_response = ""
        # Load the conversation-history from the db
        self.conv_history = await self._get_conv_history()
        if not self.conv_history:
            self.conv_history = [{"role": "system", "content": params.system_prompt}]

        user_input = params.user_input + f"\n- RUN {self.iteration_num}/{self.max_iterations}"
        self.conv_history.append({"role": "user", "content": user_input}) # update Chat history
        logging.info(f"{Fore.BLUE}{Style.BRIGHT}[+] Starting Agent Execution for User-Input:\n{Fore.BLACK}{user_input}{Style.RESET_ALL}")

        # Start the Agentic Loop
        needs_another_iteration = True
        while True:
            if self.iteration_num > self.max_iterations: # Break if exceeded iteration limit
                logging.warning(f"{Fore.YELLOW}{Style.BRIGHT}[+] Max Limit for Agent Run Reached. Aborting......{Style.RESET_ALL}")
                break
            
            if not needs_another_iteration:
                break

            llm_params = {
                "model": self.model,
                "input": self.conv_history
            }
            if ("gpt-5" in self.model.lower()):
                llm_params["reasoning"] = {"effort": self.reasoning_effort}
                llm_params["include"] = ["reasoning.encrypted_content"]
            
            if self.tools:
                llm_params["tools"] = self.tools
                llm_params["tool_choice"] = self.tool_choice
            
            response = await self._get_llm_response(llm_params=llm_params) # get response from llm (SERIALISED OUTPUT)
            response = deserialise_response(response=response)
            logging.info(f"{Fore.BLUE}{Style.BRIGHT}[+] Agent Run: {Fore.BLACK}{self.iteration_num}/{self.max_iterations}{Style.RESET_ALL}")
            
            tool_call_requests, tool_response = [], []
            for output in response:
                if (not isinstance(output, dict)) and (output.type == 'message'):
                    llm_message = "\n".join([content.text for content in output.content])
                    self.final_response += llm_message
                    logging.info(f"{Fore.BLUE}{Style.BRIGHT}[+] LLM-Message: {Fore.BLACK}{llm_message}{Style.RESET_ALL}")
                elif (not isinstance(output, dict)) and (output.type == 'reasoning'):
                    reasoning_summary = "\n".join([summary.text for summary in output.summary])
                    if reasoning_summary:
                        logging.info(f"{Fore.YELLOW}{Style.BRIGHT}[+] LLM-Reasoning: {Fore.BLACK}{reasoning_summary}{Style.RESET_ALL}")
                elif (not isinstance(output, dict)) and (output.type == 'function_call'):
                    func_name = output.name
                    func_kwargs = json.loads(output.arguments)
                    call_id = output.call_id
                    tool_call_requests.append({"name": func_name, "arguments": func_kwargs, "call_id": call_id})
                    logging.info(f"{Fore.CYAN}{Style.BRIGHT}[+] {func_name} called with: {Fore.BLACK}{func_kwargs}{Style.RESET_ALL}")
            
            if tool_call_requests:
                tool_response = await self._get_tools_response(tools=tool_call_requests, iteration_count=self.iteration_num, max_iterations=self.max_iterations)
            
            # Update the conv-history
            self.conv_history = self.conv_history + response
            if tool_response:
                self.conv_history.extend(tool_response)

            if (not isinstance(response[-1], dict)) and (response[-1].type == "message"):
                needs_another_iteration = False
            self.iteration_num += 1 # update the iteration counter
        # Save the Conversation History back into database
        await self._update_conv_history(conv_id=self.conv_id, conv_history=self.conv_history)
        logging.info(f"{Fore.BLUE}{Style.BRIGHT}[+] CodeAct-Agent Execution Successfully Completed.....{Style.RESET_ALL}\n\n")
        return self.final_response