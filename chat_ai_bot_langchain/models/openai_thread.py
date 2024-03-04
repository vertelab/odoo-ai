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
        
        ###
        # base_url = user.openai_base_url or 'http://192.168.1.68:8000/v1'
        # temperature = user.openai_temperature or 0.5
        
        # adapter = LocalLLMAdapter(config_path="langchain.yaml")
        # conversation = Conversation(adapter=adapter)
        ###
        
        client = ChatOpenAI(base_url=user.openai_base_url or 'http://192.168.1.68:8000/v1', temperature="0.5") #https://api.openai.com/v1       
        return client
     
    @api.model
    def thread_init(self, client, channel, recipient, author):
        
    # TODO driver type from recipient, is it us?
    
        if recipient.llm_type != "langchain":
                return super(OpenAIThread, self).thread_init(client, channel, recipient, author)
            
        thread = super(OpenAIThread, self).thread_init(client, channel, recipient, author)

        return thread
    
    def add_message(self, client, message, user_id, role='odoo user'):
        
        _logger.info(f"Added message: [\"\033[1;32m{message}\033[0m\"]")
        response = client.invoke([
                    #SystemMessage(content=self.role), #You are a covetous and jelous dragon.
                    HumanMessage(content=message)
        ])
        self.write({'message': message, 'role': role})
        return response

    def wait4response(self, client, user_id):
        
        if user_id.llm_type != "langchain":
                return super(OpenAIThread, self).wait4response(client, user_id)
              
        response = self.add_message(client, self.message, user_id) 
        
        _logger.info(f"Message is processing...")
                   
        if isinstance(response, AIMessage) and hasattr(response, 'content'):
            extracted_content = response.content
            _logger.info(f"Response.content")
            
        else:
            extracted_content = str(response)
            _logger.info(f"Str response")
            
        #return f"<b>AI Boy:</b> {extracted_content}"
        return f"<span style='color:purple;'><b>AI Boy:</b></span> <i>{extracted_content}</i>"

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
