# Import the required modules
from langchain_openai import OpenAI
from langchain.prompts import PromptTemplate

# Define your prompt template using the PromptTemplate class
template = """Question: {question}\nAnswer:"""

# Set your OpenAI API key
api_key = "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"

# Instantiate the OpenAI LLM with the API key
llm = OpenAI(api_key=api_key, temperature=0.4)

# Create a prompt chain by combining the prompt template and the OpenAI LLM
prompt = PromptTemplate(template=template, input_variables=["question"])
llm_chain = prompt | llm

#print(llm_chain)

# Define a function to interact with the chatbot
def chat_with_bot():
    print("Type 'exit' to quit.\n\n")
    while True:
        # Get user input
        user_input = input("Prompt >>  ")

        # Generate a response using the prompt chain
        response = llm_chain.invoke({"question": user_input})

        # Print the response
        print("Answer >> ", response)

        # Check for exit condition
        if user_input.lower() == "exit":
            break

# Call the function to start the chat
chat_with_bot()