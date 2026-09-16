import os
from dotenv import load_dotenv
from langchain_groq import ChatGroq

load_dotenv()

llm = ChatGroq(
    groq_api_key=os.getenv("GROQ_API_KEY"),
    model_name=os.getenv("LLM_MODEL", "openai/gpt-oss-20b")
)

response = llm.invoke("Test connection. Reply in 3 words.")
print("Response:", response.content)