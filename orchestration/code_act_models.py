from dataclasses import dataclass
from typing import Optional

@dataclass
class AgentRunParams:
    conversation_id: str
    model: str
    system_prompt: str
    workspace_dir_path: str
    reasoning_effort: Optional[str]
    tools: list
    tool_choice: str
    # llm_client: LLMClient
    # code_executor: CodeExecutor
    max_iterations: int
    user_input: str
    # agent_repo: AgentRepository

@dataclass
class ConvHistory:
    conv_id: str

@dataclass
class UpdateConvHistory:
    conv_id: str
    conv_history: list

@dataclass
class LLMParams:
    params: dict

@dataclass
class CodeInputs:
    code: str

@dataclass
class ExecuteTools:
    tools: list
    iteration_count: int
    max_iterations: int