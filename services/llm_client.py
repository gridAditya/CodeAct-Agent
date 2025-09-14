from litellm import aresponses
from config import logging_config
from colorama import Fore, Style

import logging

class LLMClient:
    def __init__(self, base_url: str, api_key: str): 
        # Validate the data types
        if not isinstance(base_url, str):
            raise ValueError(
                f"Expected 'base_url' to be of type str, but got {type(base_url).__name__}"
            )
        if not isinstance(api_key, str):
            raise ValueError(
                f"Expected 'api_key' to be of type str, but got {type(api_key).__name__}"
            )
        
        # Set the attributes
        self.base_url = base_url
        self.api_key = api_key

    async def get_via_responses_api(self, **kwargs):
        # Get response from API provider
        logging.info(f"{Fore.BLUE}{Style.BRIGHT}[GET] Sending request to: {Fore.BLACK}{self.base_url}{Style.RESET_ALL}")
        response = await aresponses(base_url=self.base_url, api_key=self.api_key, **kwargs)
        return response