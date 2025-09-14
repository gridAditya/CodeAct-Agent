tools = [{
    "type": "function",
    "name": "python_tool",
    "description": "Executes python code and returns result.\n- Always use **ABSOLUTE PATHS** to read from and write to filesystem.",
    "parameters": {
        "type": "object",
        "properties": {
            "code": {
                "type": "string",
                "description": "The python code to execute"
            }
        },
        "required": ["code"],
        "additionalProperties": False
    },
    "strict": True
}]