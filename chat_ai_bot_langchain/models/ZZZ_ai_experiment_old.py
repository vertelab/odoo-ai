import json
from langchain_core.utils.function_calling import convert_to_openai_tool
from langchain_openai import ChatOpenAI

oo_key="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"

def multiply(a: int, b: int) -> int:
    """Multiply two integers together.

    Args:
        a: First integer
        b: Second integer
    """
    return a * b
    
def divide(a: int, b: int) -> int:
    """Divide two integers.

    Args:
        a: First integer
        b: Second integer
    """
    return a / b
    
def add(a: int, b: int) -> int:
    """Add two integers together.

    Args:
        a: First integer
        b: Second integer
    """
    return a + b
    
def subtract(a: int, b: int) -> int:
    """Subtract integer b from integer a.

    Args:
        a: First integer
        b: Second integer
    """
    return a - b

print(json.dumps(convert_to_openai_tool(add), indent=4))
print(json.dumps(convert_to_openai_tool(subtract), indent=4))
print(json.dumps(convert_to_openai_tool(multiply), indent=4))
print(json.dumps(convert_to_openai_tool(divide), indent=4))

llm = ChatOpenAI(openai_api_key = oo_key)

#Here we need make the model decide a correct count case:

#Full list:
llm_with_tool = llm.bind(tools=[convert_to_openai_tool(add),convert_to_openai_tool(subtract),convert_to_openai_tool(multiply),convert_to_openai_tool(divide)])

#----------------------------------------------Test----------------------------------------------
result = llm_with_tool.invoke("what is seven minus 7")

# Extracting information from the result
tool_calls = result.additional_kwargs['tool_calls']
if tool_calls:
    first_tool_call = tool_calls[0]
    
    # Extracting function name and arguments
    function_name = first_tool_call['function']['name']
    function_arguments = first_tool_call['function']['arguments']

    # Now you can use these variables as needed
    print("Function Name:", function_name)
    print("Function Arguments:", function_arguments)
else:
    print("No tool calls found in the result.")

#----------------------------------------------End of test----------------------------------------------
#res = llm_with_tool.invoke("what is seven plus 7")
#print(res.__dict__['additional_kwargs']['tool_calls'][0]['function']['name'])
print(llm_with_tool.invoke("what is seven minus 7"))
print(llm_with_tool.invoke("what is 8 times two"))
print(llm_with_tool.invoke("what is eleven divided by 7"))

#If muliply:
#llm_with_tool = llm.bind(tools=[convert_to_openai_tool(multiply)])

#If divide:
#llm_with_tool = llm.bind(tools=[convert_to_openai_tool(divide)])
#set if case: so NO divide by zero! (FORBIDDEN) 

#If add:
#llm_with_tool = llm.bind(tools=[convert_to_openai_tool(add)])

#If subtract:
#llm_with_tool = llm.bind(tools=[convert_to_openai_tool(subtract)])

response = llm_with_tool.invoke(
    "what does the fox say"	
#    "say: 'Done'"
#    "don't answer my question. no do answer my question. no don't. what's five times four"
)
print("\n", response.__dict__)
