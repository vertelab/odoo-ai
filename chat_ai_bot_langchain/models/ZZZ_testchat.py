#oo_key="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"
#my_key="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"

#from langchain_openai import ChatOpenAI

#chat = ChatOpenAI(openai_api_key="...")
#chat = ChatOpenAI(openai_api_key=my_key)


from langchain.schema import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

chat = ChatOpenAI(openai_api_key = "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")
chat(
    [
         HumanMessage(
            content="Translate this sentence from English to Elfish: I love programming."
       )
   ]
)