import json, logging, time
from langchain.schema import HumanMessage, SystemMessage, AIMessage
from langchain_openai import ChatOpenAI
#from langchain import Conversation, LocalLLMAdapter

from odoo import models, fields, api, _
from odoo.exceptions import MissingError, AccessError, UserError

_logger = logging.getLogger(__name__)

# TODO driver type on recipient so we know if it's us

class OpenAIThread(models.TransientModel):
    _inherit = 'openai.thread'

    def thread_values(self, channel, recipient, author):
        return super(OpenAIThread, self).thread_values(channel, recipient, author)

    @api.model
    def client_init(self, user):

        if user.llm_type != "langchain":
            return super().client_init(user)
        
        _logger.info(f"OpenAI Base URL (from user): \033[1;32m[{user.openai_base_url}]\033[0m")
        
        ###
        # base_url = user.openai_base_url or 'http://192.168.1.68:8000/v1'
        # temperature = user.openai_temperature or 0.5
        
        # adapter = LocalLLMAdapter(config_path="langchain.yaml")
        # conversation = Conversation(adapter=adapter)
        ###
        
        client = ChatOpenAI(
            base_url = user.openai_base_url or 'http://192.168.1.68:8000/v1',
            temperature = user.openai_temperature or 0.5,
            max_tokens = user.openai_max_tokens or 200) 
        
        #https://api.openai.com/v1
        #_logger.info(f"OpenAI Base URL (used): \033[1;32m[{client.base_url}]\033[0m")
        #_logger.info(f"Base URL: \033[1;32m[{client}]\033[0m")
        _logger.info(f"Temperature: \033[1;32m[{client.temperature}]\033[0m")
        _logger.info(f"Max tokens: \033[1;32m[{client.max_tokens}]\033[0m")       
        return client
     
    @api.model
    def thread_init(self, client, channel, recipient, author):
        
    # TODO driver type from recipient, is it us?
    
        if recipient.llm_type != "langchain":
                return super(OpenAIThread, self).thread_init(client, channel, recipient, author)
            
        thread = super(OpenAIThread, self).thread_init(client, channel, recipient, author)

        return thread
    
    def add_message(self, client, message, user, role='odoo user'):
              
        _logger.info(f"Added message: [\"\033[1;32m{message}\033[0m\"]")
        response = client.invoke([
                    HumanMessage(content=message)],
                    max_tokens = user.openai_max_tokens                   
        )   
        _logger.info(f"Response: \033[1;32m{response}\033[0m")     
        self.write({'message': message, 'role': role})
        return response

    def wait4response(self, client, user_id):
        
        if user_id.llm_type != "langchain":
                return super(OpenAIThread, self).wait4response(client, user_id)
            
        if self.message is None:
            raise ValueError("Message is None")    
              
        response = self.add_message(client, self.message, user_id)
               
        # Testing logging
         
        _logger.info(f"\033[1;37mThis is a info message\033[0m") 
        _logger.info(f"\033[5;35mThis is a info message\033[0m") 
        _logger.info(f"\033[2;32mThis is a info message\033[0m") 
        _logger.info(f"\033[6;31mThis is a error message! ⛓️‍💥\033[0m") 
        _logger.info(f"\033[3;33mThis is a warning message\033[0m") 
        _logger.info(f"\033[4;34mThis is a debug message\033[0m") 
        _logger.info(f"\033[7;36mWe are in a critical situation!!!\033[0m")       
                   
        if isinstance(response, AIMessage) and hasattr(response, 'content'):
            extracted_content = (f"{response.content} [END]")
            _logger.info(f"Response.content")
            
        else:
            extracted_content = str(response)
            _logger.info(f"Str response")
            
        return f"<span style='color:purple;'><b>LangChain:</b></span> <i>{extracted_content}</i>"

    def _thread_unlink(self, client, channel):

        if self.recipient_id.llm_type != "LangChain":
            return super(OpenAIThread, self)._thread_unlink(client, channel)
        
        #TODO Where are I'm supposed to get the client from???
        
        # try:
        #     client.beta.assistants.delete(self.assistant)
        # except client.openai.APIConnectionError as e:
        #     _logger.warning(f"LangChain: Delete The server could not be reached {e.__cause__}")
            
        # except client.openai.RateLimitError as e:
        #     _logger.warning(f"LangChain: Delete Ratelimit {e.status_code} {e.response}")
            
        # except client.openai.APIStatusError as e:
        #     _logger.warning(f"LangChain: Delete Status error {e.status_code} {e.response}")
            
        # self.recipient_id.openai_assistant = None
