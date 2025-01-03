import faiss, base64
from langchain_community.docstore.in_memory import InMemoryDocstore
from langchain_community.vectorstores import FAISS
from langchain_core.documents import Document
from langchain_core.runnables import RunnableConfig
from langgraph.graph import START, StateGraph
from typing_extensions import List, TypedDict
# from langchain_huggingface.embeddings import HuggingFaceEmbeddings
from langchain_community.embeddings import HuggingFaceInferenceAPIEmbeddings





from odoo import models, fields, api, _
from odoo.exceptions import UserError, AccessError, ValidationError
import logging

_logger = logging.getLogger(__name__)

class State(TypedDict):
    message: str
    context: List[Document]
    answer: str

class AIQuest(models.Model):
    _inherit = 'ai.quest'

    ai_type = fields.Selection(selection_add=[('chat_rag', 'Chat RAG')], ondelete={'chat_rag': 'cascade'})

    def retrieve(state: State, config: RunnableConfig):
        retrieved_docs = configurable_retriever.invoke(state["message"])
        return {"context": retrieved_docs}


    # def generate(state: State):
    #     docs_content = "\n\n".join(doc.page_content for doc in state["context"])
    #     messages = prompt.invoke({"question": state["question"], "context": docs_content})
    #     response = llm.invoke(messages)
    #     return {"answer": response.content}


    def chat(self, message):
        if self.ai_type == "chat_rag":
            if self.init_type == 'chat' or "channel" and self.channel_id:
                self.vectorstore(message,message.attachment_ids)
                vals = self._chat_values(message)
                graph_builder = StateGraph(State).add_sequence([self.retrieve, vals['agent'].prompt_agent])
                graph_builder.add_edge(START, "retrieve")
                graph = graph_builder.compile()
                return graph.invoke(
                                        {"message": message.description}, 
                                        config={"configurable": {"search_kwargs": {"source": "attachment"}}}
                                    )
        else:
            _logger.error(f"Det fungerar"*10)
            return super(AIQuest,self).chat(message=message)



    # def graph_response(self):
    #     graph_builder = StateGraph(State).add_sequence([self.retrieve, vals['agent'].prompt_agent])
    #     graph_builder.add_edge(START, "retrieve")
    #     graph = graph_builder.compile()
    #     return graph.invoke({
    #                             "message": message.body}, 
    #                             config={"configurable": {"search_kwargs": {"source": "attachment"}
    #                         }})

    # def chat(self,message):
    #     session = super(AIQuestAgent,self).chat(message=message)
    #     _logger.error(f"{message=} {session=}")
    #     if self.init_type == 'chat' or "channel" and self.channel_id:
    #         self.vectorstore(message,attachments)
    #         vals = self._chat_values(message)
    #         graph_builder = StateGraph(State).add_sequence([self.retrieve, vals['agent'].prompt_agent])
    #         graph_builder.add_edge(START, "retrieve")
    #         graph = graph_builder.compile()
    #         return graph.invoke(
    #                                 {"message": message.body}, 
    #                                 config={"configurable": {"search_kwargs": {"source": "attachment"}}})
                                
            # response = vals['agent'].prompt_agent(message=message.body,session=vals['session'],attachment=attachment)'        


    def vectorstore(self,message,attachments):

        _logger.error(f"{message.description=} {attachments=} {self.embedding()}")

        index = faiss.IndexFlatL2(len(self.embedding().embed_query(message.description)))
        
        vector_store = FAISS(
            embedding_function=self.embedding(),
            index=index,
            docstore=InMemoryDocstore(),
            index_to_docstore_id={},
        )

        for attachment in attachments:

            attachment_text = base64.b64decode(attachment.datas)

            document = Document(
                page_content=attachment_text,
                metadata={"source": "attachment"},
            )

            uuid = str(uuid4())

            vector_store.add_documents(documents=document, ids=uuid)


    def configurable_retriever(self):
        configurable_retriever = retriever.configurable_fields(
         search_kwargs=ConfigurableField(
            id="search_kwargs",
            name="Search Kwargs",
            description="The search kwargs to use",
            )
        )
        return configurable_retriever

    def embedding(self):
        return HuggingFaceInferenceAPIEmbeddings(model_name="sentence-transformers/all-mpnet-base-v2",api_key=self.ai_agent_ids[0].ai_agent_id.ai_agent_llm_id.ai_api_key)

# class MailChannel(models.Model):
#     # _name="mail.channel"
#     _inherit = 'mail.channel'

#     @api.returns('mail.message', lambda value: value.id)
#     def message_post(self, **kwargs):
#         message = super(MailChannel, self).message_post(**kwargs)
#         _logger.error(f"{message.attachment_ids=}")
#         return message
