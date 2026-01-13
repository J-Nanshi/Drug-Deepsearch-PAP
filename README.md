# Deep Search Agent - FastAPI

A streamlined FastAPI application for automated drug effect research reports in precision oncology.

## Features

- **Automated Research**: Generate comprehensive drug effect research reports using AI
- **Tavily Search Integration**: Uses Tavily API for web research
- **Manual Feedback**: Review and provide feedback on research plans before execution
- **PDF Generation**: Automatically generate PDF reports
- **Latest Models**: Uses GPT-4o and Claude models for best results
- **FastAPI**: Modern async API architecture

## Installation

1. **Clone the repository**
   ```bash
   cd pod_neuro_deep_search
   ```

2. **Create virtual environment**
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```

3. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

4. **Set up environment variables**
   Create a `.env` file in the root directory:
   ```env
   TAVILY_API_KEY=your_tavily_api_key
   OPENAI_API_KEY=your_openai_api_key
   
   # Optional: Override default models
   PLANNER_MODEL=gpt-4o-2024-11-20
   WRITER_MODEL=gpt-4o-2024-11-20
   ```

5. **Run the application**
   ```bash
   python main.py
   ```
   Or using uvicorn directly:
   ```bash
   uvicorn main:app --host 0.0.0.0 --port 8000 --reload
   ```

## Usage

### API Endpoints

- `GET /` - Main UI page
- `POST /api/start_research` - Start a new research task
  ```json
  {
    "topic": "Drug Name"
  }
  ```
- `GET /api/progress/{research_id}` - Get research progress
- `POST /api/feedback` - Submit feedback on research plan
  ```json
  {
    "research_id": "uuid",
    "feedback": "approve" or "your feedback text"
  }
  ```
- `GET /api/result/{research_id}` - Get final research result
- `GET /api/report/{research_id}` - Get report as HTML
- `GET /api/download/{research_id}` - Download report as PDF

### Web UI

1. Open `http://localhost:8000` in your browser
2. Enter a drug name
3. Click "Start Research"
4. Review the research plan when prompted (optional feedback)
5. Download or view the generated report

## Architecture

The application consists of:

- **main.py**: FastAPI server with endpoints
- **agent.py**: Consolidated agent logic (graph, state, prompts, utils)
- **templates/index.html**: Web UI

## Configuration

### Models

Default models can be configured via environment variables:
- `PLANNER_MODEL`: Model for planning (default: `gpt-4o-2024-11-20`)
- `WRITER_MODEL`: Model for writing (default: `gpt-4o-2024-11-20`)

Supported models:
- GPT-4o (`gpt-4o-2024-11-20`)
- GPT-5 (if available)
- Claude 3.7 Sonnet (`claude-3-7-sonnet-latest`)

### Search Configuration

The agent uses Tavily API for web search. Configure via:
- `TAVILY_API_KEY`: Your Tavily API key

### Research Parameters

Adjustable in `main.py`:
- `max_search_depth`: Maximum research iterations (default: 2)
- `number_of_queries`: Queries per iteration (default: 3)

## Development

### Project Structure

```
pod_neuro_deep_search/
├── main.py              # FastAPI application
├── agent.py             # Agent logic (graph, prompts, utils)
├── templates/
│   └── index.html       # Web UI
├── requirements.txt     # Python dependencies
├── README.md           # This file
└── .env                # Environment variables (create this)
```

## Notes

- Reports are automatically cleaned up after 1 hour
- PDF generation requires WeasyPrint (included in requirements)
- The application uses in-memory storage (no database required)
- For production, consider adding proper database and caching

## License

Proprietary - Internal use only
