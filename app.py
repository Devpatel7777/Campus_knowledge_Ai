import os
import io
from pathlib import Path
import streamlit as st
from dotenv import load_dotenv
from pypdf import PdfReader

# LangChain components for RAG
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_chroma import Chroma
from langchain_groq import ChatGroq
from langchain_core.prompts import PromptTemplate
from langchain_core.documents import Document

# -----------------------------------------------------------------------------
# 1. Page Configuration & Clean Styling
# -----------------------------------------------------------------------------
st.set_page_config(
    page_title="Campus Knowledge Assistant",
    page_icon="🎓",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Custom CSS for clean layout and preventing bottom input overlap
st.markdown(
    """
    <style>
    #MainMenu, footer {visibility: hidden;}
    
    /* Ensure chat messages have enough bottom clearance above input bar */
    .block-container {
        padding-top: 2rem;
        padding-bottom: 8rem !important;
        max-width: 960px;
    }
    
    /* Subtle hero card styling */
    .hero-banner {
        background: linear-gradient(135deg, #1e1b4b 0%, #312e81 60%, #1e3a8a 100%);
        padding: 1.4rem 1.6rem;
        border-radius: 12px;
        margin-bottom: 1.2rem;
        border: 1px solid #3730a3;
    }
    .hero-banner h1 {
        color: #ffffff;
        margin: 0;
        font-size: 1.7rem;
        font-weight: 700;
    }
    .hero-banner p {
        color: #c7d2fe;
        margin: 0.3rem 0 0 0;
        font-size: 0.95rem;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

# Render Header Banner
st.markdown(
    """
    <div class="hero-banner">
        <h1>🎓 Campus Knowledge Assistant</h1>
        <p>Ask questions about college rules, exams, attendance policy, and placements.</p>
    </div>
    """,
    unsafe_allow_html=True,
)

# -----------------------------------------------------------------------------
# 2. Load API Key from .env
# -----------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent
DOCS_DIR = BASE_DIR / "documents"

load_dotenv(BASE_DIR / ".env")
groq_api_key = os.getenv("GROQ_API_KEY", "").strip()
groq_model = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b").strip()

if not groq_api_key:
    st.error("GROQ_API_KEY is missing. Please add it to your .env file.")
    st.stop()

# -----------------------------------------------------------------------------
# 3. Document Text Extractor (PDF & TXT)
# -----------------------------------------------------------------------------
def load_document_text(file_source, filename):
    """Extract text from a PDF or TXT file into LangChain Documents."""
    docs = []
    # If text file
    if filename.lower().endswith(".txt"):
        if hasattr(file_source, "read_text"):
            text = file_source.read_text(encoding="utf-8", errors="ignore")
        else:
            text = file_source.getvalue().decode("utf-8", errors="ignore")
        if text.strip():
            docs.append(Document(page_content=text.strip(), metadata={"source": filename, "page": 1}))
    # If PDF file
    elif filename.lower().endswith(".pdf"):
        if hasattr(file_source, "read_bytes"):
            reader = PdfReader(str(file_source))
        else:
            reader = PdfReader(io.BytesIO(file_source.getvalue()))
        for page_num, page in enumerate(reader.pages, start=1):
            text = (page.extract_text() or "").strip()
            if text:
                docs.append(Document(page_content=text, metadata={"source": filename, "page": page_num}))
    return docs

# -----------------------------------------------------------------------------
# 4. Embedding Model (Hugging Face sentence-transformers)
# -----------------------------------------------------------------------------
@st.cache_resource(show_spinner=False)
def get_embedding_model():
    """Load lightweight local sentence transformer model (384 dimensions)."""
    return HuggingFaceEmbeddings(model_name="sentence-transformers/all-MiniLM-L6-v2")

# -----------------------------------------------------------------------------
# 5. Chroma Vector Store Builder
# -----------------------------------------------------------------------------
@st.cache_resource(show_spinner=False)
def build_default_vector_store():
    """Load all campus documents from the documents folder and create Chroma vector store."""
    all_docs = []
    if DOCS_DIR.exists():
        for file in sorted(DOCS_DIR.iterdir()):
            if file.suffix.lower() in [".pdf", ".txt"]:
                all_docs.extend(load_document_text(file, file.name))

    text_splitter = RecursiveCharacterTextSplitter(chunk_size=800, chunk_overlap=150)
    chunks = text_splitter.split_documents(all_docs)

    embeddings = get_embedding_model()
    return Chroma.from_documents(chunks, embedding=embeddings)


def build_vector_store_from_upload(uploaded_file):
    """Build Chroma index in memory for a user uploaded document."""
    docs = load_document_text(uploaded_file, uploaded_file.name)
    if not docs:
        return None
    text_splitter = RecursiveCharacterTextSplitter(chunk_size=800, chunk_overlap=150)
    chunks = text_splitter.split_documents(docs)
    embeddings = get_embedding_model()
    return Chroma.from_documents(chunks, embedding=embeddings)

# -----------------------------------------------------------------------------
# 6. Sidebar: Document Selection & Retrieval Settings
# -----------------------------------------------------------------------------
with st.sidebar:
    st.header("📄 Document Source")
    doc_option = st.radio(
        "Choose Knowledge Source:",
        ["All Campus Documents (Preloaded)", "Upload Custom PDF / TXT"]
    )

    uploaded_file = None
    if doc_option == "Upload Custom PDF / TXT":
        uploaded_file = st.file_uploader("Upload college handbook / document:", type=["pdf", "txt"])

    st.markdown("---")
    st.header("⚙️ Settings")
    top_k = st.slider("Number of retrieved chunks (top-k)", min_value=1, max_value=6, value=3)
    temperature = st.slider("LLM Temperature", min_value=0.0, max_value=1.0, value=0.1, step=0.05)
    show_sources = st.checkbox("Show source citations", value=True)

    st.markdown("---")
    if st.button("🗑️ Clear Chat History", use_container_width=True):
        st.session_state.messages = []
        st.rerun()

# Determine active database
if doc_option == "Upload Custom PDF / TXT":
    if uploaded_file is not None:
        with st.spinner(f"Indexing {uploaded_file.name}..."):
            active_db = build_vector_store_from_upload(uploaded_file)
        st.caption(f"📌 Active Document: **{uploaded_file.name}**")
    else:
        st.warning("Please upload a PDF or TXT file from the sidebar.")
        active_db = None
else:
    with st.spinner("Loading campus knowledge base..."):
        active_db = build_default_vector_store()
    st.caption("📌 Active Source: **Preloaded Campus Knowledge Base (Handbook, Rules, FAQs)**")

# -----------------------------------------------------------------------------
# 7. Initialize Chat History
# -----------------------------------------------------------------------------
if "messages" not in st.session_state:
    st.session_state.messages = []

# Show sample questions if chat is empty
if len(st.session_state.messages) == 0:
    st.info(
        "💡 **Sample questions you can ask:**\n\n"
        "- *What is the minimum attendance required for exams?*\n"
        "- *When is the library open on Saturday?*\n"
        "- *What is the minimum CGPA required for placements?*\n"
        "- *What are the hostel gate timings?*"
    )

# Render previous messages from history
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])
        if msg.get("sources") and show_sources:
            with st.expander("📚 View Document Sources"):
                for i, s in enumerate(msg["sources"], 1):
                    st.markdown(f"**Passage {i}:** `{s['source']}` (Page {s['page']})")
                    st.caption(f'"{s["excerpt"][:200]}..."')

# -----------------------------------------------------------------------------
# 8. Strict Grounded RAG Prompt Template
# -----------------------------------------------------------------------------
prompt_template = PromptTemplate(
    input_variables=["context", "question"],
    template="""You are the official Campus Knowledge Assistant.
Answer the question accurately and clearly using ONLY the information provided in the context below.

Instructions:
1. Answer directly and politely based strictly on the context.
2. If the exact information is not in the context, respond clearly:
   "I couldn't find that information in the uploaded campus documents."
3. If related information is available (such as ID replacement fee, library rules, or hostel deposit), mention it briefly.
4. Do not make up facts, dates, percentages, or policies not mentioned in the context.

Context:
{context}

Question: {question}

Answer:"""
)

# -----------------------------------------------------------------------------
# 9. User Input & Streaming Response
# -----------------------------------------------------------------------------
user_question = st.chat_input("Ask a question about campus rules, exams, placement...")

if user_question:
    if active_db is None:
        st.warning("Please select or upload a document before asking a question.")
        st.stop()

    # Append user question to chat
    st.session_state.messages.append({"role": "user", "content": user_question})
    with st.chat_message("user"):
        st.markdown(user_question)

    # Generate assistant answer
    with st.chat_message("assistant"):
        with st.spinner("Searching documents..."):
            # Step A: Retrieve top-k relevant chunks
            results = active_db.similarity_search(user_question, k=top_k)
            
            # Step B: Combine chunks into formatted context
            context_text = "\n\n".join([
                f"[Document: {doc.metadata.get('source', 'Unknown')} | Page: {doc.metadata.get('page', 1)}]\n{doc.page_content}"
                for doc in results
            ])
            
            # Step C: Prepare Groq LLM call
            llm = ChatGroq(
                model=groq_model,
                temperature=temperature,
                api_key=groq_api_key
            )
            formatted_prompt = prompt_template.format(
                context=context_text,
                question=user_question
            )

            # Step D: Stream response live
            response_placeholder = st.empty()
            full_response = ""
            for chunk in llm.stream(formatted_prompt):
                if chunk.content:
                    full_response += chunk.content
                    response_placeholder.markdown(full_response + "▌")
            response_placeholder.markdown(full_response)

            # Step E: Format and display citations
            sources_info = [
                {
                    "source": doc.metadata.get("source", "Campus Document"),
                    "page": doc.metadata.get("page", 1),
                    "excerpt": doc.page_content
                }
                for doc in results
            ]

            if show_sources and sources_info:
                with st.expander("📚 View Document Sources"):
                    for i, s in enumerate(sources_info, 1):
                        st.markdown(f"**Passage {i}:** `{s['source']}` (Page {s['page']})")
                        st.caption(f'"{s["excerpt"][:200]}..."')

            # Append assistant message to chat history
            st.session_state.messages.append({
                "role": "assistant",
                "content": full_response,
                "sources": sources_info
            })
