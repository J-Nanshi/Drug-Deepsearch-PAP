# NeuroDeep Search - AI-Powered Research Report Generator

![NeuroDeep Search](https://img.shields.io/badge/NeuroDeep-Search-blue?style=for-the-badge)
![Python](https://img.shields.io/badge/Python-3.8+-blue?style=flat-square)
![Flask](https://img.shields.io/badge/Flask-2.0+-green?style=flat-square)
![LangGraph](https://img.shields.io/badge/LangGraph-Latest-orange?style=flat-square)

## 🧠 Overview

NeuroDeep Search is an advanced AI-powered research report generation system that leverages state-of-the-art language models and search APIs to create comprehensive, well-structured research reports on any topic. The system uses LangGraph for orchestating complex multi-agent workflows and provides both web interface and API endpoints for seamless integration.

## 🚀 Key Features

- **Multi-Agent Research Pipeline**: Sophisticated workflow using LangGraph with planner, writer, and grader agents
- **Multiple Search APIs**: Supports Tavily, PubMed, ArXiv, Perplexity, and more
- **Intelligent Content Generation**: Context-aware section writing with FAISS-based similarity search
- **PDF Generation**: Automatic PDF report generation with proper formatting and citations
- **Email Integration**: Send reports directly via email using n8n webhook
- **Configurable Depth**: Basic, Detailed, and Deep research modes
- **Real-time Processing**: Asynchronous processing with background cleanup
- **Citation Management**: Automatic PMID and PMC link generation

## 📋 Table of Contents

- [Architecture](#-architecture)
- [Installation](#-installation)
- [Configuration](#-configuration)
- [API Endpoints](#-api-endpoints)
- [Usage Examples](#-usage-examples)
- [Backend Architecture](#-backend-architecture)
- [Research Pipeline](#-research-pipeline)
- [Dependencies](#-dependencies)
- [Deployment](#-deployment)
- [Troubleshooting](#-troubleshooting)

## 🏗️ Architecture

```
┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐
│   Frontend      │    │   Flask API     │    │   LangGraph     │
│   (HTML/JS)     │───▶│   (app.py)      │───▶│   Pipeline      │
└─────────────────┘    └─────────────────┘    └─────────────────┘
                                │                       │
                                │                       ▼
┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐
│   PDF Generator │◀───│   Report Cache  │    │   Search APIs   │
│   (WeasyPrint)  │    │   (Memory)      │    │   (Tavily/etc)  │
└─────────────────┘    └─────────────────┘    └─────────────────┘
                                │
                                ▼
                       ┌─────────────────┐
                       │   Email Service │
                       │   (n8n webhook) │
                       └─────────────────┘
```

## 💻 Installation

### Prerequisites

- Python 3.8+ (Recommended: 3.10–3.12)
- OpenAI API Key
- Tavily API Key
### Step 4: NLTK Data (EC2/Production)

The app will automatically download the required NLTK 'punkt' data on first run if it is missing. No manual step is needed. If you want to pre-install it (optional):

```bash
source venv/bin/activate
python -m nltk.downloader punkt
```

### Step 1: Clone Repository

```bash
git clone <repository-url>
cd pod_neuro_deep_search-deepsearch
```

### Step 2: Install Dependencies

```bash
pip install -r requirements.txt
```

### Step 3: Install PDF Generation Dependencies

**Note**: We use WeasyPrint (pure Python) for PDF generation. System dependencies are required for WeasyPrint.

**Automatic Installation (Recommended):**
```bash
./install.sh
```

**Manual Installation:**
```bash
# Install WeasyPrint for PDF generation
pip install weasyprint reportlab

# macOS - Install system dependencies
brew install cairo pango gdk-pixbuf libffi

# Ubuntu/Debian - Install system dependencies  
sudo apt-get install build-essential python3-dev python3-cffi libcairo2 libpango-1.0-0 libpangocairo-1.0-0 libgdk-pixbuf2.0-0 libffi-dev shared-mime-info
```

### Step 5: Environment Setup

Create a `.env` file in the project root:

```env
TAVILY_API_KEY=your_tavily_api_key_here
OPENAI_API_KEY=your_openai_api_key_here
```

## ⚙️ Configuration

The system supports multiple configuration options through the `Configuration` class:

### Search APIs
- **Tavily** (Default): General web search
- **PubMed**: Medical/scientific literature
- **ArXiv**: Academic papers
- **Perplexity**: AI-powered search
- **DuckDuckGo**: Privacy-focused search

### Model Configuration
```python
{
    "planner_provider": "openai",
    "planner_model": "o3-mini",
    "writer_provider": "openai", 
    "writer_model": "gpt-4.1-mini",
    "max_search_depth": 2,
    "number_of_queries": 3
}
```

### Research Depths
- **Basic**: Quick overview with essential information
- **Detailed**: Comprehensive analysis with tables and examples
- **Deep**: In-depth research with extensive references and analysis

## 🔌 API Endpoints

### 1. Start Research
**POST** `/start_research`

Initiates a new research report generation.

**Request Body:**
```json
{
    "topic": "Neuroplasticity in Alzheimer's Disease",
    "depth": "detailed"
}
```

**Response:**
```json
{
    "topic": "Neuroplasticity in Alzheimer's Disease",
    "summary": "This report explores the relationship between...",
    "pages": 8,
    "sources": 15,
    "time": "3 Minutes",
    "research_id": "uuid-string"
}
```

### 2. Download PDF
**POST** `/generate_and_download_pdf`

Downloads the generated PDF report.

**Request Body:**
```json
{
    "research_id": "uuid-string"
}
```

**Response:** PDF file download

### 3. Email Report
**POST** `/send_pdf_email`

Sends the report via email using n8n webhook.

**Request Body:**
```json
{
    "research_id": "uuid-string",
    "email": "user@example.com",
    "message": "Please find the research report attached."
}
```

## 📚 Usage Examples

### Basic Python Usage

```python
import requests

# Start research
response = requests.post('http://localhost/start_research', json={
    'topic': 'Machine Learning in Healthcare',
    'depth': 'detailed'
})

research_data = response.json()
research_id = research_data['research_id']

# Download PDF
pdf_response = requests.post('http://localhost/generate_and_download_pdf', json={
    'research_id': research_id
})

with open('report.pdf', 'wb') as f:
    f.write(pdf_response.content)
```

### JavaScript Frontend Integration

```javascript
// Start research
const startResearch = async (topic, depth) => {
    const response = await fetch('/start_research', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ topic, depth })
    });
    return await response.json();
};

// Usage
const result = await startResearch('AI in Drug Discovery', 'deep');
console.log(`Report generated with ${result.pages} pages`);
```

## 🔧 Backend Architecture

### Core Components

#### 1. Flask Application (`app.py`)
- **Routes**: `/start_research`, `/generate_and_download_pdf`, `/send_pdf_email`
- **PDF Generation**: Using WeasyPrint (no external binary required)
- **Caching**: In-memory research cache with automatic cleanup
- **Background Tasks**: Cleanup thread for temporary files

#### 2. LangGraph Pipeline (`graph.py`)
- **Multi-Agent Workflow**: Planner → Query Writer → Section Writer → Grader
- **State Management**: Complex state transitions with ReportState and SectionState
- **Parallel Processing**: Concurrent section writing using Send() API

#### 3. Configuration Management (`configuration.py`)
- **Provider Settings**: OpenAI, Anthropic model configurations
- **Search API Selection**: Multiple search provider support
- **Report Structure**: Customizable report templates

#### 4. Utility Functions (`utils.py`)
- **Search Integration**: Multiple API implementations
- **Text Processing**: PDF extraction, HTML parsing, text chunking
- **Vector Search**: FAISS-based similarity search
- **Citation Processing**: PMID/PMC link generation

### Data Flow

```
User Request → Flask Route → LangGraph Pipeline → Search APIs → Content Generation → PDF Creation → Response
```

## 🔍 Research Pipeline

### Phase 1: Planning
1. **Topic Analysis**: Break down user topic into research areas
2. **Section Planning**: Generate structured report outline
3. **Query Generation**: Create targeted search queries

### Phase 2: Research
1. **Multi-Source Search**: Query multiple APIs simultaneously
2. **Content Extraction**: Parse and clean retrieved content
3. **Relevance Scoring**: FAISS-based content ranking

### Phase 3: Writing
1. **Section Writing**: Generate content for each section
2. **Citation Integration**: Add proper academic citations
3. **Quality Grading**: Automated content quality assessment

### Phase 4: Assembly
1. **Report Compilation**: Combine all sections
2. **Formatting**: Apply consistent styling and structure
3. **PDF Generation**: Create downloadable report

## 📦 Dependencies

### Core Framework
- **Flask**: Web application framework
- **LangGraph**: Multi-agent workflow orchestration
- **LangChain**: LLM integration and document processing

### AI/ML Libraries
- **OpenAI**: GPT model integration
- **Sentence Transformers**: Text embeddings
- **FAISS**: Vector similarity search
- **NLTK**: Natural language processing

### Document Processing
- **PyMuPDF**: PDF text extraction
- **BeautifulSoup4**: HTML parsing
- **markdown2**: Markdown to HTML conversion

### Search APIs
- **tavily-python**: Tavily search integration
- **duckduckgo-search**: DuckDuckGo API
- **langchain-community**: ArXiv, PubMed retrievers

## 🚀 Deployment

### Local Development
```bash
python app.py
```
Access at `http://localhost:80`

### Docker Deployment
```bash
docker build -t neurodeep-search .
docker run -p 80:80 --env-file .env neurodeep-search
```


### Production Deployment (EC2/Cloud)
1. SSH into your EC2 instance
2. Clone the repository and follow steps 1–5 above
3. Ensure all system dependencies for WeasyPrint are installed (see Step 3)
4. The app will auto-download NLTK 'punkt' data if missing
5. Run:
```bash
source venv/bin/activate
python app.py
```
6. (Optional) Use Gunicorn for production:
```bash
gunicorn app:app --bind 0.0.0.0:8002
```

### Environment Variables for Production
```env
TAVILY_API_KEY=your_key
OPENAI_API_KEY=your_key
PORT=80
```

## 🔧 Troubleshooting

### Common Issues

#### 1. PDF Generation Fails
```bash
# Check WeasyPrint installation
python -c "import weasyprint; print(weasyprint.__version__)"
# Ensure all system dependencies (cairo, pango, etc.) are installed
```

#### 2. Search API Errors
- Verify API keys in `.env` file
- Check rate limits for your APIs
- Test with minimal queries first

#### 3. Memory Issues
- Monitor temp directory cleanup
- Adjust `RESEARCH_CACHE` size limits
- Check for memory leaks in long-running processes

#### 4. Model Access Issues
```python
# Verify model availability
from langchain_openai import ChatOpenAI
model = ChatOpenAI(model="gpt-4o-mini")  # Use available model
```

### Performance Optimization

#### 1. Caching Strategy
- Implement Redis for production caching
- Add result caching for repeated queries
- Use CDN for static assets

#### 2. Search Optimization
- Reduce `number_of_queries` for faster processing
- Lower `max_search_depth` for basic reports
- Implement query result caching

#### 3. Resource Management
```python
# Optimize memory usage
import gc
gc.collect()  # Force garbage collection

# Limit concurrent requests
from concurrent.futures import ThreadPoolExecutor
executor = ThreadPoolExecutor(max_workers=4)
```

## 🤝 Contributing

1. Fork the repository
2. Create feature branch (`git checkout -b feature/amazing-feature`)
3. Commit changes (`git commit -m 'Add amazing feature'`)
4. Push to branch (`git push origin feature/amazing-feature`)
5. Open Pull Request

## 📄 License

This project is licensed under the MIT License - see the LICENSE file for details.

## 🆘 Support

For technical support or questions:
- Create an issue in the GitHub repository
- Check the troubleshooting section above
- Review the technical documentation

## 🔮 Future Enhancements

- [ ] Support for additional search APIs
- [ ] Real-time collaboration features
- [ ] Advanced citation formatting
- [ ] Custom report templates
- [ ] Integration with reference managers
- [ ] Multi-language support
- [ ] Advanced analytics dashboard

---

**Built with ❤️ using LangGraph and modern AI technologies**