# NeuroDeep Search - Technical Documentation

## Table of Contents
1. [System Architecture](#system-architecture)
2. [Component Analysis](#component-analysis)
3. [Data Flow & Pipeline](#data-flow--pipeline)
4. [API Specifications](#api-specifications)
5. [Database & State Management](#database--state-management)
6. [Security & Performance](#security--performance)
7. [Deployment Architecture](#deployment-architecture)
8. [Knowledge Transfer](#knowledge-transfer)

---

## System Architecture

### High-Level Architecture Overview

NeuroDeep Search implements a sophisticated multi-agent research system using modern AI orchestration patterns. The system follows a microservices-inspired architecture with clear separation of concerns.

```
┌──────────────────────────────────────────────────────────────┐
│                    CLIENT LAYER                              │
├──────────────────────────────────────────────────────────────┤
│  Web Interface (HTML/CSS/JS) │  API Clients (Python/JS)      │
└──────────────────┬───────────────────────────────────────────┘
                   │
┌──────────────────▼───────────────────────────────────────────┐
│                APPLICATION LAYER                             │
├──────────────────────────────────────────────────────────────┤
│              Flask Web Server (app.py)                      │
│  ┌─────────────┐ ┌─────────────┐ ┌─────────────────────────┐ │
│  │   Routes    │ │    Cache    │ │    PDF Generation       │ │
│  │  Handler    │ │  Management │ │      Service            │ │
│  └─────────────┘ └─────────────┘ └─────────────────────────┘ │
└──────────────────┬───────────────────────────────────────────┘
                   │
┌──────────────────▼───────────────────────────────────────────┐
│               ORCHESTRATION LAYER                            │
├──────────────────────────────────────────────────────────────┤
│                 LangGraph Pipeline                           │
│  ┌─────────────┐ ┌─────────────┐ ┌─────────────────────────┐ │
│  │   Planner   │ │   Writer    │ │       Grader           │ │
│  │    Agent    │ │   Agents    │ │       Agent            │ │
│  └─────────────┘ └─────────────┘ └─────────────────────────┘ │
└──────────────────┬───────────────────────────────────────────┘
                   │
┌──────────────────▼───────────────────────────────────────────┐
│                INTEGRATION LAYER                             │
├──────────────────────────────────────────────────────────────┤
│ ┌─────────────┐ ┌─────────────┐ ┌─────────────────────────┐  │
│ │   Search    │ │     LLM     │ │      Vector Store      │  │
│ │    APIs     │ │  Providers  │ │      (FAISS)           │  │
│ └─────────────┘ └─────────────┘ └─────────────────────────┘  │
└──────────────────────────────────────────────────────────────┘
```

### Technology Stack

#### Backend Core
- **Flask 2.0+**: Lightweight web framework for API endpoints
- **LangGraph**: State machine orchestration for multi-agent workflows
- **LangChain**: LLM integration and document processing pipeline

#### AI/ML Stack
- **OpenAI GPT Models**: Primary language model provider
- **Sentence Transformers**: Text embedding generation
- **FAISS**: Vector similarity search and indexing
- **NLTK**: Natural language processing utilities

#### Document Processing
- **PyMuPDF (fitz)**: PDF text extraction and processing
- **BeautifulSoup4**: HTML parsing and web scraping
- **pdfkit + wkhtmltopdf**: PDF generation from HTML
- **markdown2**: Markdown to HTML conversion

#### External Integrations
- **Tavily API**: Primary web search provider
- **PubMed API**: Medical literature search
- **ArXiv API**: Academic paper retrieval
- **n8n Webhook**: Email service integration

---

## Component Analysis

### 1. Flask Application Layer (`app.py`)

#### Core Responsibilities
- HTTP request routing and response handling
- Research session management and caching
- PDF generation orchestration
- Background task management

#### Key Components

##### Request Handlers
```python
@app.route('/start_research', methods=['POST'])
def start_research():
    # Orchestrates the entire research pipeline
    # Handles depth configuration and user instructions
    # Manages asynchronous LangGraph execution
```

##### PDF Generation Engine
```python
def prepare_report_for_pdf(report_text):
    # Converts markdown to HTML with proper styling
    # Handles citation link generation (PMID, PMC)
    # Applies CSS styling for professional formatting
```

##### Cache Management System
```python
RESEARCH_CACHE = {}  # In-memory cache for research sessions

def cleanup_old_temp_dirs():
    # Background thread for automatic cleanup
    # Prevents memory leaks and disk space issues
    # Configurable cleanup intervals
```

#### Configuration Parameters
- **Thread Configuration**: UUID-based session management
- **Model Selection**: Configurable LLM providers and models
- **Search Depth**: Adjustable research thoroughness
- **Output Format**: PDF styling and citation formatting

### 2. LangGraph Orchestration Engine (`graph.py`)

#### Multi-Agent Architecture

The system implements a sophisticated state machine using LangGraph's workflow orchestration:

```python
# State Graph Structure
StateGraph(ReportState)
├── Planner Agent (report_planner)
├── Query Writer (query_writer) 
├── Section Writers (section_writer) [Parallel Execution]
├── Final Writer (final_section_writer)
└── Quality Grader (section_grader)
```

##### Agent Specifications

**1. Planner Agent**
- **Input**: Research topic and user requirements
- **Output**: Structured section plan with research priorities
- **Model**: OpenAI o3-mini (configurable)
- **Function**: Strategic decomposition of research topic

**2. Query Writer Agent**
- **Input**: Section descriptions and research context
- **Output**: Targeted search queries for each section
- **Model**: GPT-4o-mini (configurable)
- **Function**: Query optimization for multiple search APIs

**3. Section Writer Agents**
- **Execution**: Parallel processing using Send() API
- **Input**: Search results and section specifications
- **Output**: Formatted section content with citations
- **Model**: GPT-4o-mini (configurable)
- **Function**: Content generation with academic rigor

**4. Quality Grader Agent**
- **Input**: Generated section content
- **Output**: Quality assessment and improvement suggestions
- **Model**: GPT-4o-mini (configurable)
- **Function**: Content validation and iterative improvement

#### State Management

##### ReportState Schema
```python
class ReportState(TypedDict):
    topic: str                          # Primary research topic
    feedback_on_report_plan: str        # Planning feedback
    sections: list[Section]             # Section specifications
    completed_sections: list[Section]   # Finished sections
    report_sections_from_research: str  # Research content
    final_report: str                   # Complete report
    evaluation_report: str              # Quality assessment
```

##### SectionState Schema
```python
class SectionState(TypedDict):
    topic: str                    # Research topic
    section: Section              # Current section
    search_iterations: int        # Iteration count
    search_queries: list[SearchQuery]  # Query history
    source_str: str              # Formatted sources
    url_list: list               # Source URLs
```

### 3. Configuration Management (`configuration.py`)

#### Configuration Class Structure
```python
@dataclass(kw_only=True)
class Configuration:
    report_structure: str         # Template structure
    number_of_queries: int       # Search query limit
    max_search_depth: int        # Research depth limit
    planner_provider: str        # LLM provider selection
    planner_model: str          # Model specification
    writer_provider: str        # Writer LLM provider
    writer_model: str          # Writer model specification
    search_api: SearchAPI       # Search provider enum
    user_instructions: str      # Custom user requirements
```

#### Search API Enumeration
```python
class SearchAPI(Enum):
    PERPLEXITY = "perplexity"    # AI-powered search
    TAVILY = "tavily"           # General web search
    EXA = "exa"                 # Semantic search
    ARXIV = "arxiv"             # Academic papers
    PUBMED = "pubmed"           # Medical literature
    LINKUP = "linkup"           # Link aggregation
    DUCKDUCKGO = "duckduckgo"   # Privacy-focused search
    GOOGLESEARCH = "googlesearch" # Google search API
```

### 4. Utility Functions (`utils.py`)

#### Search Integration Functions

##### Multi-API Search Handler
```python
async def select_and_execute_search(queries, search_api, config):
    # Handles multiple search API implementations
    # Provides failover and rate limiting
    # Returns formatted search results
```

##### Search API Implementations
- **Tavily Integration**: `tavily_search_async()`
- **PubMed Integration**: `pubmed_search()`
- **ArXiv Integration**: `arxiv_search()`
- **DuckDuckGo Integration**: `duckduckgo_search()`

#### Document Processing Pipeline

##### PDF Processing
```python
def extract_text_from_pdf(url):
    # Downloads and processes PDF documents
    # Extracts clean text using PyMuPDF
    # Handles various PDF formats and encodings
```

##### Text Chunking and Vectorization
```python
def chunk_text(text, chunk_size=500):
    # Intelligent text segmentation
    # Preserves semantic boundaries
    # Optimized for embedding generation

def build_faiss_index(chunks):
    # Creates vector index using FAISS
    # Enables semantic similarity search
    # Supports real-time query matching
```

---

## Data Flow & Pipeline

### Research Request Processing Flow

```
1. User Request Reception
   ├── Topic validation and sanitization
   ├── Depth parameter configuration
   └── Session UUID generation

2. LangGraph Pipeline Initialization
   ├── Thread configuration setup
   ├── Model provider selection
   └── Search API configuration

3. Planning Phase
   ├── Topic decomposition analysis
   ├── Section structure generation
   └── Research priority assignment

4. Query Generation Phase
   ├── Section-specific query creation
   ├── Search API optimization
   └── Query validation and filtering

5. Research Execution Phase (Parallel)
   ├── Multi-API search execution
   ├── Content extraction and cleaning
   ├── Relevance scoring and ranking
   └── Source citation tracking

6. Content Generation Phase
   ├── Section-wise content creation
   ├── Academic formatting application
   ├── Citation integration
   └── Quality assessment

7. Report Assembly Phase
   ├── Section compilation and ordering
   ├── Cross-reference resolution
   ├── Formatting standardization
   └── Final quality review

8. Output Generation Phase
   ├── Markdown to HTML conversion
   ├── PDF generation and styling
   ├── Cache storage and cleanup
   └── Response delivery
```

### State Transitions in LangGraph

```
START
  │
  ▼
report_planner
  │
  ▼
query_writer
  │
  ▼
section_writer (Parallel Execution)
  │
  ▼
final_section_writer
  │
  ▼
section_grader
  │
  ▼
END (with conditional loops for quality improvement)
```

### Data Persistence Strategy

#### In-Memory Cache Structure
```python
RESEARCH_CACHE = {
    "session_uuid": {
        "topic": str,
        "summary": str,
        "pages": int,
        "sources": int,
        "time": str,
        "report": str,
        "pdf_path": str,
        "temp_dir": str,
        "timestamp": datetime
    }
}
```

#### Temporary File Management
- **Creation**: Unique temporary directories per research session
- **Cleanup**: Background thread with configurable intervals
- **Security**: Automatic file permissions and access control
- **Performance**: Memory-mapped file access for large documents

---

## API Specifications

### REST API Endpoints

#### 1. Research Initiation Endpoint

**Endpoint**: `POST /start_research`

**Request Schema**:
```json
{
    "topic": {
        "type": "string",
        "required": true,
        "description": "Research topic or question",
        "example": "Neuroplasticity in Alzheimer's Disease"
    },
    "depth": {
        "type": "string",
        "required": false,
        "enum": ["basic", "detailed", "deep"],
        "default": "basic",
        "description": "Research depth and thoroughness level"
    }
}
```

**Response Schema**:
```json
{
    "topic": "string",
    "summary": "string (truncated first 300 characters)",
    "pages": "integer (estimated page count)",
    "sources": "integer (number of sources found)",
    "time": "string (processing time in Minutes/Seconds)",
    "research_id": "string (UUID for session tracking)"
}
```

**Error Handling**:
```json
{
    "error": "string (detailed error message)",
    "status_code": "integer (HTTP status code)"
}
```

#### 2. PDF Generation Endpoint

**Endpoint**: `POST /generate_and_download_pdf`

**Request Schema**:
```json
{
    "research_id": {
        "type": "string",
        "required": true,
        "description": "UUID from research initiation response"
    }
}
```

**Response**: Binary PDF file with appropriate headers

#### 3. Email Delivery Endpoint

**Endpoint**: `POST /send_pdf_email`

**Request Schema**:
```json
{
    "research_id": {
        "type": "string",
        "required": true,
        "description": "UUID from research initiation"
    },
    "email": {
        "type": "string",
        "required": true,
        "format": "email",
        "description": "Recipient email address"
    },
    "message": {
        "type": "string",
        "required": false,
        "description": "Optional message to include with email"
    }
}
```

### Internal API Specifications

#### LangGraph Node Interfaces

**Planner Node Interface**:
```python
Input: ReportStateInput
Output: ReportState with populated sections field
```

**Section Writer Node Interface**:
```python
Input: SectionState
Output: SectionOutputState with completed_sections
```

**Quality Grader Interface**:
```python
Input: Section content
Output: Feedback object with grade and improvement suggestions
```

---

## Database & State Management

### State Persistence Architecture

#### Memory-Based State Management
The system uses LangGraph's MemorySaver for state persistence during workflow execution:

```python
from langgraph.checkpoint.memory import MemorySaver

# State checkpointing for workflow recovery
memory = MemorySaver()
graph = StateGraph(ReportState).add_checkpoint(memory)
```

#### Cache Management Strategy

##### Research Session Cache
```python
# Session-based caching for completed research
RESEARCH_CACHE = {
    session_id: {
        'metadata': ResearchMetadata,
        'content': GeneratedContent,
        'resources': FileResources,
        'expiry': datetime
    }
}
```

##### Cleanup Automation
```python
def cleanup_old_temp_dirs():
    """
    Background cleanup process:
    - Runs every 10 minutes
    - Removes files older than 1 hour
    - Prevents disk space accumulation
    - Handles concurrent access safely
    """
```

#### Vector Store Management

##### FAISS Index Structure
```python
# Document embedding and retrieval
class FAISSManager:
    def __init__(self):
        self.model = SentenceTransformer('all-MiniLM-L6-v2')
        self.index = None
        self.documents = []
    
    def build_index(self, texts):
        # Creates searchable vector index
        
    def search(self, query, k=5):
        # Returns most relevant document chunks
```

### Data Models and Schemas

#### Core Data Structures

**Section Model**:
```python
class Section(BaseModel):
    name: str = Field(description="Section title")
    description: str = Field(description="Content overview")
    research: bool = Field(description="Requires web research")
    content: str = Field(description="Generated content")
    images: str = Field(description="Related image URLs")
```

**Search Query Model**:
```python
class SearchQuery(BaseModel):
    search_query: str = Field(description="Optimized search string")
```

**Quality Feedback Model**:
```python
class Feedback(BaseModel):
    grade: Literal["pass", "fail"] = Field(description="Quality assessment")
    follow_up_queries: List[SearchQuery] = Field(description="Improvement queries")
```

---

## Security & Performance

### Security Measures

#### API Key Management
- Environment variable isolation
- No hardcoded credentials
- Secure key rotation support
- Rate limiting per API provider

#### Input Validation
```python
def validate_research_request(data):
    """
    - Topic length and content validation
    - Depth parameter enumeration checking
    - Injection attack prevention
    - Sanitization of user inputs
    """
```

#### File Security
- Temporary file sandboxing
- Automatic cleanup processes
- Access permission management
- Path traversal prevention

### Performance Optimization

#### Concurrent Processing
```python
# Parallel section writing using LangGraph Send API
for section in sections:
    yield Send("section_writer", {
        "topic": state["topic"],
        "section": section,
        # ... other parameters
    })
```

#### Caching Strategy
- In-memory result caching
- PDF generation caching
- Search result memoization
- Background cleanup optimization

#### Resource Management
```python
# Memory-efficient processing
def process_large_document(url):
    # Streaming text extraction
    # Chunked processing
    # Garbage collection optimization
```

### Monitoring and Logging

#### Error Handling Patterns
```python
try:
    # LangGraph execution
    result = loop.run_until_complete(run_graph())
except Exception as e:
    logger.error(f"Graph execution failed: {str(e)}")
    return jsonify({"error": f"Error generating report: {str(e)}"}), 500
```

#### Performance Metrics
- Processing time tracking
- Token usage monitoring  
- API rate limit management
- Cache hit/miss ratios

---

## Deployment Architecture

### Local Development Environment

#### Setup Requirements
```bash
# System dependencies
brew install wkhtmltopdf  # macOS
apt-get install wkhtmltopdf  # Ubuntu

# Python environment
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

#### Configuration Files
```python
# .env file structure
TAVILY_API_KEY=your_tavily_key
OPENAI_API_KEY=your_openai_key

# Optional configurations
ANTHROPIC_API_KEY=your_anthropic_key
PERPLEXITY_API_KEY=your_perplexity_key
```

### Production Deployment Options

#### Docker Containerization
```dockerfile
FROM python:3.9-slim

# System dependencies
RUN apt-get update && apt-get install -y \
    wkhtmltopdf \
    && rm -rf /var/lib/apt/lists/*

# Application setup
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .
EXPOSE 80
CMD ["gunicorn", "app:app", "--bind", "0.0.0.0:80"]
```

#### Heroku Platform Deployment
```python
# Procfile
web: gunicorn app:app --bind 0.0.0.0:$PORT

# runtime.txt
python-3.9.20
```

#### Cloud Infrastructure Requirements
- **Memory**: Minimum 2GB RAM for LLM processing
- **Storage**: SSD preferred for temporary file operations
- **Network**: High bandwidth for API calls and document processing
- **CPU**: Multi-core for parallel section processing

### Scaling Considerations

#### Horizontal Scaling
- Stateless application design
- External cache implementation (Redis)
- Load balancer configuration
- Session sticky routing

#### Vertical Scaling
- Memory optimization for large documents
- CPU optimization for parallel processing
- Disk I/O optimization for PDF generation
- Network optimization for API calls

---

## Knowledge Transfer

### System Understanding Prerequisites

#### Required Technical Knowledge
1. **Python Advanced Concepts**
   - Asyncio and concurrent programming
   - Decorators and context managers
   - Type hints and dataclasses
   - Exception handling patterns

2. **LangChain/LangGraph Framework**
   - State machine concepts
   - Multi-agent orchestration
   - LLM provider abstraction
   - Document processing pipelines

3. **Flask Web Framework**
   - Route handling and middleware
   - Request/response cycles
   - Session management
   - Background task processing

4. **AI/ML Concepts**
   - Language model interactions
   - Vector embeddings and similarity search
   - Prompt engineering principles
   - Content generation workflows

### Key Learning Resources

#### Documentation References
- [LangGraph Documentation](https://langchain-ai.github.io/langgraph/)
- [LangChain Documentation](https://python.langchain.com/)
- [Flask Documentation](https://flask.palletsprojects.com/)
- [OpenAI API Documentation](https://platform.openai.com/docs)

#### Critical Code Sections to Understand

**1. LangGraph Workflow Definition** (`graph.py:580-613`)
```python
# Understanding the state machine flow
workflow = StateGraph(ReportState, input=ReportStateInput, output=ReportStateOutput)
workflow.add_node("report_planner", report_planner)
workflow.add_node("query_writer", query_writer)
# ... additional nodes
```

**2. Asynchronous Processing Pattern** (`app.py:200-220`)
```python
# Critical async pattern for LangGraph execution
loop = asyncio.new_event_loop()
asyncio.set_event_loop(loop)
async def run_graph():
    # Understanding async iteration and state management
```

**3. Search API Integration** (`utils.py:100-300`)
```python
# Multi-API search pattern
async def select_and_execute_search(queries, search_api, config):
    # Understanding API abstraction and error handling
```

### Maintenance and Extension Guidelines

#### Adding New Search APIs
1. Extend `SearchAPI` enum in `configuration.py`
2. Implement search function in `utils.py`
3. Add API key configuration in environment setup
4. Update documentation and examples

#### Modifying Report Structure
1. Update `REPORT_STRUCTURE` in `prompts.py`
2. Modify section planning logic in graph nodes
3. Adjust PDF formatting in `app.py`
4. Test with various topic types

#### Scaling Considerations for New Features
1. **State Management**: Consider state size impact on memory
2. **API Rate Limits**: Implement proper throttling
3. **Error Recovery**: Add robust exception handling
4. **Testing Strategy**: Unit tests for each component

### Common Issues and Solutions

#### 1. PDF Generation Failures
```python
# Diagnosis steps
1. Verify wkhtmltopdf installation and path
2. Check HTML content for malformed markup
3. Validate CSS styles for PDF compatibility
4. Monitor memory usage during generation
```

#### 2. LangGraph State Issues
```python
# Debugging state problems
1. Enable LangGraph debugging output
2. Validate state schema compliance
3. Check for state mutation issues
4. Monitor memory usage in state transitions
```

#### 3. Search API Timeouts
```python
# Handling API reliability
1. Implement exponential backoff
2. Add circuit breaker patterns
3. Use multiple API fallbacks
4. Cache successful results
```

### Development Best Practices

#### Code Organization
- Follow single responsibility principle
- Maintain clear separation between agents
- Use type hints consistently
- Document complex business logic

#### Testing Strategy
```python
# Unit test examples
def test_section_writer():
    # Test individual agent functionality
    
def test_search_integration():
    # Test API integration with mocking
    
def test_pdf_generation():
    # Test document generation pipeline
```

#### Error Handling Patterns
```python
# Consistent error handling
try:
    result = await api_call()
except APIError as e:
    logger.error(f"API call failed: {e}")
    # Fallback strategy
except Exception as e:
    logger.exception("Unexpected error")
    # General error recovery
```

### Future Enhancement Roadmap

#### Immediate Improvements (1-2 months)
- Add Redis caching for production
- Implement proper logging and monitoring
- Add unit and integration tests
- Optimize memory usage for large documents

#### Medium-term Features (3-6 months)
- Multi-language support
- Custom report templates
- Real-time collaboration features
- Advanced analytics dashboard

#### Long-term Enhancements (6+ months)
- Machine learning model fine-tuning
- Advanced citation management
- Integration with reference managers
- Mobile application development

---

## Conclusion

NeuroDeep Search represents a sophisticated implementation of modern AI orchestration patterns, combining multiple language models, search APIs, and document processing capabilities into a cohesive research automation system. The architecture prioritizes modularity, scalability, and maintainability while providing comprehensive research capabilities.

The system's strength lies in its multi-agent approach using LangGraph, which enables parallel processing, quality control, and iterative improvement of research outputs. The comprehensive API integration and flexible configuration system make it adaptable to various research domains and requirements.

For successful knowledge transfer and maintenance, focus on understanding the LangGraph state machine patterns, async processing workflows, and the multi-API integration strategy. These core concepts form the foundation for extending and maintaining the system effectively.

---

*This technical documentation serves as a comprehensive guide for developers, system administrators, and researchers working with the NeuroDeep Search platform. Regular updates to this documentation should accompany any significant system changes or enhancements.*