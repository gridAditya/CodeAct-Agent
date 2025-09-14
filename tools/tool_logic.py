from colorama import Fore, Style
from services.code_executor import CodeExecutor

from config import logging_config
import logging

_code_executor: CodeExecutor | None = None

def set_code_executor(code_executor: CodeExecutor):
    global _code_executor
    _code_executor = code_executor

async def execute_code(code: str):
    exec_result = _code_executor.execute(code=code)
    logging.info(f"{Fore.BLUE}{Style.BRIGHT}[+] Execute Code:\n{Fore.BLACK}{code}{Style.RESET_ALL}")
    logging.info(f"{Fore.BLUE}{Style.BRIGHT}[+] Code Output:\n{Fore.BLACK}{exec_result}{Style.RESET_ALL}")
    return exec_result