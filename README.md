Multi-Agent Customer Support System
An enterprise-grade, multi-agent AI customer support platform built for AcmeCloud, a fictional project-management software company. The system features a team of specialized AI agents that resolve customer inquiries by interacting with real database records and internal company documents, citing their sources, and executing human handoffs when necessary.

What It Does
When a customer submits an inquiry through the chat interface, the system processes it through a multi-tier agentic workflow:

Intent Routing: A supervisor agent classifies each incoming message and determines which specialized desk should handle it: billing, technical, general support, or a human agent.

Specialized Agents:

Billing: Handles charges, duplicate payments, refunds, invoices, and orders.

Technical: Resolves API errors, login problems, synchronization issues, and performance bottlenecks.

General: Answers questions regarding subscription plans, product features, account settings, and privacy policies.

Tool Integration: Agents execute deterministic tools to query customer databases, check payment histories, search internal knowledge bases, and automatically open support tickets.

Human Handoff: When a customer explicitly requests a human representative or expresses high frustration, the system initiates an escalated ticket routing workflow and provides a tracking number.

Core Features
Multi-Agent Routing with Context: Uses conversation history to interpret short follow-up expressions correctly.

Retrieval-Augmented Generation (RAG): Answers policy, pricing, and troubleshooting questions exclusively from official company markdown documentation with precise citation tracking.

Strict Customer Data Isolation: Server-side validation guarantees that a customer can never query or access another user's financial records or orders.

Persistent Conversation Memory: Conversations are stored securely using a SQLite checkpointer, allowing sessions to persist across page reloads and server restarts.

Loop Protection & Guardrails: Limits tool invocation rounds per message, blocks identical duplicate tool calls, and enforces strict graph step boundaries.

Comprehensive Testing Suite: Includes offline automated tests leveraging a scriptable mock LLM along with retrieval evaluation metrics (Hit@K and Mean Reciprocal Rank).

Architecture & Workflow
Ingestion & Validation: Requests hit a FastAPI server which verifies customer authentication and loads the active session state from SQLite memory.

Classification: The router model analyzes the intent and routes the payload to the appropriate specialist agent.

Execution & Retrieval: The agent calls backend tools to fetch order records, validate transactions, or perform keyword-driven BM25 searches across chunked documentation.

Response Generation: The agent synthesizes a grounded answer referencing specific document sections and returns it alongside tool logs and citation chips.

Technology Stack
Orchestration: LangGraph (state graphs, conditional edges, checkpointers)

Language Model: Groq API integration via LangChain

API Framework: FastAPI and Uvicorn

Database & Storage: SQLAlchemy 2.0 with SQLite

Search & Ranking: Heading-aware document chunking and BM25 keyword ranking with Snowball stemming

Testing: PyTest and FastAPI TestClient
