import faiss, base64, uuid
from langchain_community.docstore.in_memory import InMemoryDocstore
from langchain_community.vectorstores import FAISS
from langchain_core.messages.ai import AIMessage
from langchain_core.documents import Document
from langchain_core.runnables import RunnableConfig
from langgraph.graph import START, StateGraph
from typing_extensions import List, TypedDict
from langchain_huggingface.embeddings import HuggingFaceEmbeddings
#from langchain_community.embeddings import HuggingFaceInferenceAPIEmbeddings
from langchain_openai import OpenAIEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter
from odoo import models, fields, api, _
from odoo.exceptions import UserError, AccessError, ValidationError
from odoo.tools.mail import html2plaintext

import logging

_logger = logging.getLogger(__name__)


class AIQuestSession(models.Model):
    _inherit = 'ai.quest.session'


class State(TypedDict):
    message: str
    session: AIQuestSession
    vector_store: FAISS
    context: List[Document]
    answer: AIMessage


class AIQuest(models.Model):
    _inherit = 'ai.quest'

    ai_type = fields.Selection(selection_add=[('chat_rag', 'Chat RAG')], ondelete={'chat_rag': 'cascade'})
    llm_api_key = fields.Char(related="ai_agent_ids.ai_agent_id.ai_agent_llm_id.ai_api_key")

    def convertHtml2Text(self, text):
        return html2plaintext(text)

    def retrieve(self, state: State):
        retrieved_docs = state["vector_store"].similarity_search(state["message"])
        _logger.warning("in retriver")
        return {"context": retrieved_docs}

    def generate(self, state: State):
        _logger.warning("in generator")
        docs_content = "\n\n".join(doc.page_content for doc in state["context"])
        agent = self.env["ai.agent"].search([("ai_type", "=", "chat_rag")])
        response = agent.prompt_agent(message=state["message"], context=docs_content, session=state["session"])
        # _logger.error(f"{response=}")
        return {"answer": response}

    def _chat_values(self, **kwargs):
        kwargs = super(AIQuest, self)._chat_values(**kwargs)
        vector_store = self.vectorstore(kwargs["message"], kwargs["message"].attachment_ids)
        kwargs["vector_store"] = vector_store
        return kwargs

    def graph_prompt_agent(self, message, vector_store, session):
        graph_builder = StateGraph(State).add_sequence([self.retrieve, self.generate])
        graph_builder.add_edge(START, "retrieve")
        graph = graph_builder.compile()
        result = graph.invoke({"message": message, "vector_store": vector_store, "session": session})
        _logger.error(f"{result=}")
        return result["answer"]

    def vectorstore(self, message, attachments):
        #hf_embedding = HuggingFaceInferenceAPIEmbeddings(model_name="sentence-transformers/all-MiniLM-l6-v2",api_key=self.ai_agent_ids[0].ai_agent_id.ai_agent_llm_id.ai_api_key)
        #hf_embedding = HuggingFaceEmbeddings(model_name="sentence-transformers/all-mpnet-base-v2")
        hf_embedding = OpenAIEmbeddings(model="text-embedding-3-large",
                                        api_key=self.ai_agent_ids[0].ai_agent_id.ai_agent_llm_id.ai_api_key)

        index = faiss.IndexFlatL2(len(hf_embedding.embed_query(self.convertHtml2Text(message.body))))

        text_splitter = RecursiveCharacterTextSplitter(
            # Set a really small chunk size, just to show.
            chunk_size=100,
            chunk_overlap=20,
            length_function=len,
            is_separator_regex=False,
        )

        vector_store = FAISS(embedding_function=hf_embedding, index=index, docstore=InMemoryDocstore(),
                             index_to_docstore_id={}, )

        for attachment in attachments:
            attachment_text = base64.b64decode(attachment.datas).decode('utf-8')

            docs = text_splitter.create_documents([attachment_text])

            # document = Document(
            #     page_content=texts,
            #     metadata={"source": "attachment"},
            # )

            vector_store.add_documents(documents=docs)

        return vector_store
