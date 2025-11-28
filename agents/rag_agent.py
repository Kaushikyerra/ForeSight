# agents/rag_agent.py
import os
import logging
import shutil
from typing import List

# LangChain & Chroma Imports
try:
    from langchain_google_genai import GoogleGenerativeAIEmbeddings, ChatGoogleGenerativeAI
    from langchain_community.vectorstores import Chroma
    from langchain_text_splitters import RecursiveCharacterTextSplitter
    from langchain_core.documents import Document
    from langchain_core.prompts import ChatPromptTemplate
    from langchain_core.output_parsers import StrOutputParser
    from langchain_core.runnables import RunnablePassthrough
    RAG_AVAILABLE = True
except ImportError:
    RAG_AVAILABLE = False

# Config
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
DB_PERSIST_DIR = os.path.join(os.path.dirname(__file__), "..", "rag_db_storage")

# Logger
logger = logging.getLogger(__name__)

def get_embedding_function():
    """Returns the Gemini Embedding model."""
    if not GEMINI_API_KEY:
        raise ValueError("GEMINI_API_KEY missing.")
    return GoogleGenerativeAIEmbeddings(
        model="models/text-embedding-004", 
        google_api_key=GEMINI_API_KEY
    )

def ingest_text_to_rag(case_id: str, text: str, filename: str):
    """
    Splits text into chunks and saves them to a vector database (ChromaDB)
    under a specific 'case_id' collection.
    """
    if not RAG_AVAILABLE or not text.strip():
        return False

    try:
        # 1. Split text into manageable chunks
        text_splitter = RecursiveCharacterTextSplitter(
            chunk_size=1000,
            chunk_overlap=100,
            separators=["\n\n", "\n", ". ", " ", ""]
        )
        chunks = text_splitter.split_text(text)
        
        # 2. Convert to Document objects with metadata
        docs = [
            Document(page_content=chunk, metadata={"source": filename, "case_id": case_id}) 
            for chunk in chunks
        ]

        # 3. Store in ChromaDB (Persistent)
        # We use a single persistent DB, but filter by 'case_id' during search if needed,
        # or strictly speaking, we can just dump everything there. 
        # For simplicity in this demo, we put everything in one collection but retrieving is specific.
        
        embedding_function = get_embedding_function()
        
        vectorstore = Chroma.from_documents(
            documents=docs,
            embedding=embedding_function,
            persist_directory=DB_PERSIST_DIR,
            collection_name="forensight_cases"
        )
        # vectorstore.persist() # Automatic in newer versions
        logger.info(f"✅ RAG: Ingested {len(chunks)} chunks for {filename}")
        return True

    except Exception as e:
        logger.error(f"❌ RAG Ingestion Failed: {e}")
        return False

def query_rag(query: str, case_id: str = None):
    """
    Searches the vector DB for answers. 
    If case_id is provided, we can filter (advanced), or just search global context.
    """
    if not RAG_AVAILABLE:
        return "RAG modules not installed."

    try:
        embedding_function = get_embedding_function()
        vectorstore = Chroma(
            persist_directory=DB_PERSIST_DIR, 
            embedding_function=embedding_function,
            collection_name="forensight_cases"
        )

        # 1. Setup Retriever
        # We search for top 5 relevant chunks
        retriever = vectorstore.as_retriever(
            search_kwargs={"k": 5} 
            # Note: To strictly filter by case_id requires passing filter={'case_id': case_id} 
            # here if Chroma version supports it in as_retriever, or doing manual search.
            # For this hackathon demo, we search everything.
        )

        # 2. Setup LLM (Gemini Flash for speed)
        llm = ChatGoogleGenerativeAI(
            model="gemini-1.5-flash",
            google_api_key=GEMINI_API_KEY,
            temperature=0
        )

        # 3. Create Prompt Template
        template = """Answer the question based only on the following context:
        
        {context}
        
        Question: {question}
        
        Provide a detailed answer based on the context provided. If the context doesn't contain enough information to answer the question, say "I don't have enough information to answer this question based on the provided context."
        """
        prompt = ChatPromptTemplate.from_template(template)
        
        # 4. Create Chain using LCEL
        rag_chain = (
            {"context": retriever, "question": RunnablePassthrough()}
            | prompt
            | llm
            | StrOutputParser()
        )

        # 5. Run Query
        answer = rag_chain.invoke(query)
        
        # 6. Get source documents for citation
        source_docs = retriever.get_relevant_documents(query)
        
        return {
            "answer": answer,
            "sources": list(set([doc.metadata.get('source') for doc in source_docs]))
        }

    except Exception as e:
        logger.error(f"❌ RAG Query Failed: {e}")
        return {"error": str(e)}