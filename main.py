import os
import asyncpg

from fastapi import FastAPI, HTTPException, Depends
from temporalio.client import Client
from temporalio.common import RetryPolicy
from datetime import timedelta
from contextlib import asynccontextmanager
from colorama import Fore, Style
from services.llm_client import LLMClient
from services.code_executor import CodeExecutor
from db.connection import create_pool, close_pool
from repositories.conversations import ConversationCRUD, ConversationAlreadyExists
from repositories.users import UserCRUD, UserAlreadyExistsError, InvalidCredentialsError
from utils.utils import generate_unique_id, read_file_content, to_absolute_path, ensure_directory
from orchestration.code_act_models import AgentRunParams
from models.models import CreateUserRequest, CreateConversationRequest, LoginRequest, Users, ListConversationsRequest, SendMessageRequest, Conversation
from tools.tool_definition import tools

from config import logging_config # load the logging configurations
import logging

from dotenv import load_dotenv
load_dotenv(override=True) # load the environment variables

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Initialize Temporal Client
    app.state.temporal_client = await Client.connect("localhost:7233")
    try:
        app.state.llm_client = LLMClient(base_url=os.getenv("BASE_URL"), api_key=os.getenv("API_KEY")) # intialise llm_client
        app.state.code_executor = CodeExecutor(allowed_filesystem_paths=[to_absolute_path('./datasets')], allowed_file_modes=['r', 'w']) # intialise code_executor

        dsn = f"postgresql://{os.getenv('DB_USER')}:{os.getenv('DB_PASSWORD')}@{os.getenv('DB_HOST')}:{os.getenv('DB_PORT')}/{os.getenv('DB_NAME')}"
        app.state.pool = await create_pool(dsn=dsn, min_size=5, max_size=20) # intialise the connection pool
        app.state.user_crud = UserCRUD(pool=app.state.pool) # initialise the user_repo
        app.state.conv_crud = ConversationCRUD(pool=app.state.pool) # initialise the conv_repo

        # Load the System-Prompt
        try: 
            sys_prompt_path = to_absolute_path("./") + "/prompts/openai_v1.txt"
            system_prompt = read_file_content(sys_prompt_path)
            app.state.code_act_sys_prompt = system_prompt
        except FileNotFoundError as e:
            logging.error(f"{Fore.RED}{Style.BRIGHT}[-] Unable to locate: {Fore.BLACK}{sys_prompt_path} file{Style.RESET_ALL}")
            raise HTTPException(status_code=500, detail="Internal Server Error")
        logging.info(f"{Fore.GREEN}{Style.BRIGHT}[+] Application Initialised Successfully.....{Style.RESET_ALL}")
        yield
    finally:
        await close_pool(pool=app.state.pool) # close the database connection pool
        logging.info(f"{Fore.BLUE}{Style.BRIGHT}[+] Database connection pool closed{Style.RESET_ALL}")
        logging.info(f"{Fore.GREEN}{Style.BRIGHT}[+] Cleanup processes successfully executed....{Style.RESET_ALL}")

app = FastAPI(lifespan=lifespan)

# Dependency Injection
def get_conversation_crud()->ConversationCRUD:
    return app.state.conv_crud

def get_user_crud()->UserCRUD:
    return app.state.user_crud

def get_temporal_client():
    return app.state.temporal_client

# APIs
@app.post("/create_user")
async def create_user(user: CreateUserRequest, user_crud: UserCRUD=Depends(get_user_crud)):
    try:
        user_id = generate_unique_id()
        user: Users = await user_crud.create_user(user_id=user_id, username=user.username, email=user.email, password=user.password)
        logging.info(f"{Fore.GREEN}{Style.BRIGHT}[+] Created new user with ID: {Fore.BLACK}{user_id}{Style.RESET_ALL}")
        return {"user_id": user}
    except UserAlreadyExistsError as e:
        logging.error(f"{Fore.RED}{Style.BRIGHT}[-] User already exists with email: {user.email}{Style.RESET_ALL}")
        raise HTTPException(status_code=400, detail="User already exists")
    except Exception as e:
        logging.error(f"{Fore.RED}{Style.BRIGHT}[-] Error creating user: {Fore.BLACK}{e}{Style.RESET_ALL}")
        raise HTTPException(status_code=500, detail="Internal Server Error")

@app.post("/login_user")
async def login_user(login_req: LoginRequest, user_crud: UserCRUD=Depends(get_user_crud)):
    try:
        user: Users = await user_crud.login_user(email=login_req.email, password=login_req.password)
        logging.info(f"{Fore.GREEN}{Style.BRIGHT}[+] User logged in successfully with email: {login_req.email}{Style.RESET_ALL}")
        return {"user_id": user.user_id}
    except InvalidCredentialsError as e:
        logging.info(f"{Fore.RED}{Style.BRIGHT}[-] Invalid Credentials{Style.RESET_ALL}")
        raise HTTPException(status_code=401, detail="Internal Server Error")
    except Exception as e:
        logging.info(f"{Fore.RED}{Style.BRIGHT}[-] Error logging in user: {Fore.BLACK}{str(e)}{Style.RESET_ALL}")
        raise HTTPException(status_code=500, detail="Internal Server Error")

@app.post("/create_conversation")
async def start_conversation(create_conv_req: CreateConversationRequest, conv_crud: ConversationCRUD=Depends(get_conversation_crud), user_crud: UserCRUD=Depends(get_user_crud)):
    conv_id = generate_unique_id() # generate unique conversation_id

    # Update the tables 'conversations', 'agents'
    try:
        await conv_crud.create_conversation(conversation_id=conv_id, conversation_title=None, conversation_history=[]) # create a new conv in db
        await user_crud.add_user_conversation(user_id=create_conv_req.user_id, conversation_id=conv_id) # associate this conversation with user
    except ConversationAlreadyExists as e:
        logging.error(f"{Fore.RED}{Style.BRIGHT}[-] Conversation with id: {conv_id} already exists{Style.RESET_ALL}")
        raise HTTPException(status_code=400, detail="Conversation already exists")
    
    # Create the workspace directory
    dir_path = to_absolute_path("./") + f"/datasets/workspace/{conv_id}"
    is_created = ensure_directory(dir_path) # create-dir if not exists
    if not is_created:
        raise HTTPException(status_code=500, detail="Internal Server Error")
    logging.info(f"{Fore.BLUE}{Style.BRIGHT}[+] Successfully created workspace directory at path: {dir_path}{Style.RESET_ALL}")
    logging.info(f"{Fore.GREEN}{Style.BRIGHT}[+] New Conversation successfully created.....{Style.RESET_ALL}")
    return {"conversation_id": conv_id}

@app.post("/list_user_conversations")
async def list_user_conversations(user_req: ListConversationsRequest, user_crud: UserCRUD=Depends(get_user_crud)):
    user_id = user_req.user_id
    try:
        conv_list = await user_crud.list_user_conversations(user_id=user_id)
        logging.info(f"{Fore.GREEN}{Style.BRIGHT}[+] Fetched {len(conv_list.conv_list)} conversations for user_id: {Fore.BLACK}{user_id}{Style.RESET_ALL}")
        return conv_list.conv_list
    except Exception as e:
        logging.error(f"{Fore.RED}{Style.BRIGHT}[-] Error fetching conversation for user_id {user_id}: {Fore.BLACK}{str(e)}{Style.RESET_ALL}")
        raise HTTPException(status_code=500, detail="Internal Server Error")

@app.post("/send_message")
async def send_message(user_req: SendMessageRequest, conv_crud: ConversationCRUD=Depends(get_conversation_crud), temporal_client=Depends(get_temporal_client)):
    # Get the agent corresponding to this conversation
    try:
        conv: Conversation = await conv_crud.get_by_id(conversation_id=user_req.conversation_id)
        if not conv:
            raise HTTPException(status_code=400, detail="Conversation does not exist")
    except HTTPException as e:
        raise
    except asyncpg.PostgresError as e:
        logging.error(f"{Fore.RED}{Style.BRIGHT}[-] Unable to fetch agent from database: {Fore.BLACK}{e}{Style.RESET_ALL}")
        raise HTTPException(status_code=500, detail="Internal Server Error")
    except Exception as e:
        logging.error(f"{Fore.RED}{Style.BRIGHT}[-] Unexpected error occured: {Fore.BLACK}{e}{Style.RESET_ALL}")
        raise HTTPException(status_code=500, detail="Internal Server Error")
    
    # Define the workflow-run params
    workspace_dir_path = to_absolute_path(f"./datasets/workspace/{user_req.conversation_id}") # Workspace directory
    agent_run_params = AgentRunParams(
        conversation_id=user_req.conversation_id,
        model=os.getenv("MODEL_NAME"),
        system_prompt=app.state.code_act_sys_prompt,
        workspace_dir_path=workspace_dir_path,
        max_iterations=10,
        user_input=user_req.user_input,
        reasoning_effort=os.getenv("REASONING_EFFORT"),
        tools=tools,
        tool_choice="auto"
    )
    
    # Execute the CodeAct-Workflow
    logging.info(f"{Fore.BLUE}{Style.BRIGHT}[+] Workflow Execution Stated......{Style.RESET_ALL}")
    workflow_id = generate_unique_id() # generate unique_id corresponding to this workflow
    agent_response: str = await temporal_client.execute_workflow(
        "code_act_workflow",
        agent_run_params,
        id=f"codeact:{workflow_id}",
        task_queue="codeact-agent-queue",
        retry_policy=RetryPolicy(
            initial_interval=timedelta(seconds=1),
            maximum_interval=timedelta(minutes=10),
            backoff_coefficient=2.0,
            maximum_attempts=1  # limit number of retries
        )
    )
    logging.info(f"{Fore.GREEN}{Style.BRIGHT}[+] Workflow Execution Completed{Style.RESET_ALL}")
    logging.info(f"{Fore.GREEN}{Style.BRIGHT}[+] Workflow Response:\n{Fore.BLACK}{agent_response}{Style.RESET_ALL}")
    return {"message": agent_response}
