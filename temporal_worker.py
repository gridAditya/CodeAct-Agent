import os
import asyncio

from temporalio.client import Client
from temporalio.worker import Worker
from orchestration.code_act_workflow import CodeActWorkflow
from orchestration import code_act_activities
from tools import tool_logic
from repositories.conversations import ConversationCRUD
from services.code_executor import CodeExecutor
from db.connection import create_pool
from utils.utils import to_absolute_path

# Load the env vars
from dotenv import load_dotenv
load_dotenv(override=True)

async def main():
    dsn = f"postgresql://{os.getenv('DB_USER')}:{os.getenv('DB_PASSWORD')}@{os.getenv('DB_HOST')}:{os.getenv('DB_PORT')}/{os.getenv('DB_NAME')}"
    pool = await create_pool(dsn=dsn) # intialise the connection pool
    conv_crud = ConversationCRUD(pool=pool)

    workspace_path = to_absolute_path('./') + '/datasets/workspace'
    code_executor = CodeExecutor(allowed_filesystem_paths=[workspace_path], allowed_file_modes=['r', 'w']) # allow filesystem operations in workspace directory

    code_act_activities.set_conv_repo(repo=conv_crud) # inject the conv_repo(activities are executed in worker-process and not in workflow sandbox)
    tool_logic.set_code_executor(code_executor=code_executor)
    client = await Client.connect("localhost:7233")
    worker = Worker(
        client,
        task_queue="codeact-agent-queue",
        workflows=[CodeActWorkflow],
        activities=[
            code_act_activities.get_agent_conversation_history,
            code_act_activities.get_last_modified_file,
            code_act_activities.get_llm_response,
            code_act_activities.execute_tools,
            code_act_activities.update_conv_history
            ],
    )
    await worker.run()

if __name__ == "__main__":
    asyncio.run(main())