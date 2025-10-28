# NeuroDeep Search - Quick Start Guide

## Project Overview

**NeuroDeep Search** is an advanced AI-powered research automation system that generates comprehensive, well-structured research reports on any topic using multiple LLM agents and search APIs.

## 📁 Project Structure

```
pod_neuro_deep_search-deepsearch/
├── app.py                          # Main Flask application
├── graph.py                        # LangGraph multi-agent workflow
├── configuration.py                # System configuration management
├── prompts.py                      # LLM prompts and templates
├── state.py                        # Data models and state definitions
├── utils.py                        # Utility functions and API integrations
├── requirements.txt                # Python dependencies
├── runtime.txt                     # Python version specification
├── Procfile                        # Heroku deployment configuration
├── Dockerfile                      # Docker containerization
├── deploy.sh                       # Deployment script
├── templates/
│   └── index.html                  # Web interface frontend
├── README.md                       # This file - project documentation
├── TECHNICAL_DOCUMENTATION.md      # Comprehensive technical guide
├── FLOW_DIAGRAMS.md               # System flow diagrams
└── generate_tech_doc_pdf.py       # PDF generation utility
```

## 🚀 Quick Installation

### 1. Environment Setup
```bash
# Clone repository
git clone <repository-url>
cd pod_neuro_deep_search-deepsearch

# Create virtual environment (recommended)
python3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

### 2. API Keys Configuration
Create a `.env` file in the project root:
```env
TAVILY_API_KEY=your_tavily_api_key_here
OPENAI_API_KEY=your_openai_api_key_here
```

### 3. System Dependencies
Install wkhtmltopdf for PDF generation:
```bash
# macOS
brew install wkhtmltopdf

# Ubuntu/Debian
sudo apt-get install wkhtmltopdf
```

Update the path in `app.py` (line 37):
```python
path_to_wkhtmltopdf = '/usr/local/bin/wkhtmltopdf'  # Update this
```

### 4. Run the Application
```bash
python3 app.py
```

Access at: `http://localhost:80`

## 🔧 How It Works (Backend Focus)

### Core Architecture
The system uses **LangGraph** for multi-agent orchestration with the following flow:

1. **Planning Agent** - Analyzes topic and creates research structure
2. **Query Writer** - Generates optimized search queries  
3. **Section Writers** - Process research in parallel using multiple search APIs
4. **Quality Grader** - Validates and improves content quality
5. **Final Assembly** - Compiles complete report with citations

### Key Backend Components

#### 1. Flask API Server (`app.py`)
- **Endpoints**: `/start_research`, `/generate_and_download_pdf`, `/send_pdf_email`
- **Features**: Async processing, session caching, background cleanup
- **PDF Generation**: Converts markdown → HTML → PDF using wkhtmltopdf

#### 2. LangGraph Workflow (`graph.py`)
- **Multi-agent orchestration** with state management
- **Parallel processing** for faster research generation
- **Quality control loop** for content improvement
- **Configurable models** (OpenAI, Anthropic support)

#### 3. Search Integration (`utils.py`)
- **Multiple APIs**: Tavily, PubMed, ArXiv, DuckDuckGo, Perplexity
- **FAISS vector search** for content relevance
- **Citation processing** (PMID, PMC link generation)
- **Rate limiting and error handling**

#### 4. Configuration System (`configuration.py`)
- **Flexible model selection** (providers and models)
- **Search API configuration** with fallbacks
- **Report structure templates** (basic/detailed/deep)
- **Performance tuning parameters**

## 🌐 API Usage Examples

### Start Research
```bash
curl -X POST http://localhost/start_research \
  -H "Content-Type: application/json" \
  -d '{
    "topic": "Neuroplasticity in Alzheimer Disease",
    "depth": "detailed"
  }'
```

### Download PDF
```bash
curl -X POST http://localhost/generate_and_download_pdf \
  -H "Content-Type: application/json" \
  -d '{"research_id": "your-uuid-here"}' \
  --output report.pdf
```

## 🔍 Backend Technical Highlights

### Multi-Agent Processing
```python
# Parallel section processing using LangGraph Send API
for section in sections:
    yield Send("section_writer", {
        "topic": state["topic"],
        "section": section,
        "search_iterations": 0
    })
```

### Search API Integration
```python
# Multi-API search with failover
search_results = await select_and_execute_search(
    queries=search_queries,
    search_api="tavily",  # or pubmed, arxiv, etc.
    config=search_config
)
```

### Vector Similarity Search
```python
# FAISS-based content relevance ranking
embeddings = sentence_transformer.encode(chunks)
index = faiss.IndexFlatL2(embedding_dim)
index.add(embeddings)
similarities = index.search(query_embedding, k=5)
```

### Quality Control Loop
```python
# Iterative improvement based on AI grading
feedback = await section_grader(section_content)
if feedback.grade == "fail":
    # Generate follow-up queries and re-research
    return Command(goto="query_writer")
```

## 🚀 Deployment Options

### Local Development
```bash
python3 app.py  # Runs on localhost:80
```

### Docker
```bash
docker build -t neurodeep-search .
docker run -p 80:80 --env-file .env neurodeep-search
```

### Heroku
```bash
# Uses Procfile: web: gunicorn app:app --bind 0.0.0.0:$PORT
git push heroku main
```

## 📊 Performance Characteristics

- **Processing Time**: 2-5 minutes for detailed reports
- **Parallel Processing**: Multiple sections processed simultaneously
- **Source Integration**: 10-20 sources per report on average
- **Output Quality**: Academic-grade with proper citations
- **Scalability**: Stateless design for horizontal scaling

## 🔧 Configuration Options

### Research Depth Levels
- **Basic**: Quick overview (2-3 sections, 5-10 sources)
- **Detailed**: Comprehensive analysis (4-6 sections, 10-15 sources)  
- **Deep**: In-depth research (6-8 sections, 15-25 sources)

### Model Configuration
```python
config = {
    "planner_provider": "openai",     # or "anthropic"
    "planner_model": "o3-mini",       # planning agent model
    "writer_provider": "openai", 
    "writer_model": "gpt-4o-mini",    # content generation model
    "max_search_depth": 2,            # research iterations
    "number_of_queries": 3            # queries per section
}
```

## 🛠️ Troubleshooting

### Common Issues

1. **PDF Generation Fails**
   - Check wkhtmltopdf installation and path in `app.py`
   - Verify HTML content doesn't have syntax errors

2. **API Rate Limits**
   - Monitor API usage in logs
   - Consider upgrading API plans for production use

3. **Memory Issues**
   - Background cleanup runs every 10 minutes
   - Adjust cache size limits for production

4. **Search API Errors**
   - Verify API keys in `.env` file  
   - Check network connectivity to APIs
   - Review API rate limits and quotas

## 📚 Documentation Files

- **README.md** (this file): Quick start and overview
- **TECHNICAL_DOCUMENTATION.md**: Comprehensive technical guide
- **FLOW_DIAGRAMS.md**: System architecture and flow diagrams
- **generate_tech_doc_pdf.py**: Utility to generate PDF documentation

To generate PDF documentation:
```bash
pip install markdown2 pdfkit  # Additional dependencies
python3 generate_tech_doc_pdf.py
```

## 🔮 Advanced Features

- **Multi-language support** (configurable)
- **Custom report templates** (modifiable in prompts.py)
- **Citation management** (automatic PMID/PMC linking)
- **Email integration** (via n8n webhook)
- **Background processing** (async with cleanup)
- **Quality assurance** (AI-powered content grading)

## 🤝 Contributing

1. Fork the repository
2. Create feature branch: `git checkout -b feature/amazing-feature`
3. Commit changes: `git commit -m 'Add amazing feature'`
4. Push to branch: `git push origin feature/amazing-feature`
5. Open Pull Request

## 📄 License

MIT License - see LICENSE file for details.

---

**For detailed technical information, refer to `TECHNICAL_DOCUMENTATION.md`**