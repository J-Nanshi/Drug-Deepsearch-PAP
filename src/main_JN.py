"""
FastAPI Deep Search Application
Streamlined FastAPI server for drug effect research
"""

import os
import argparse
import uuid
import time
import asyncio
import re
import tempfile
import shutil
import threading
import sys
from typing import Optional, Dict
from pathlib import Path

# Helper function to print and flush immediately
def log_print(*args, **kwargs):
    print(*args, **kwargs)
    sys.stdout.flush()

from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dotenv import load_dotenv
import markdown2

# PDF generation removed - using Markdown download only

from agent_JN import graph, REPORT_PLANNER_INSTRUCTIONS, DEFAULT_REPORT_STRUCTURE


def parse_cancer_name_from_argv() -> str:
    """Parse global cancer context from CLI args."""
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("-i", "--input-cancer", default="breast")
    args, _ = parser.parse_known_args()
    cancer_name = (args.input_cancer or "breast").strip().lower()
    return cancer_name or "breast"


CANCER_NAME = parse_cancer_name_from_argv()
CANCER_NAME_TITLE = CANCER_NAME.title()

# Load environment variables
load_dotenv()

app = FastAPI(title="Deep Search API", version="2.0")

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve static files
static_dir = Path(__file__).parent / "templates"
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

# In-memory storage
RESEARCH_PROGRESS: Dict[str, Dict] = {}
RESEARCH_CACHE: Dict[str, Dict] = {}

# Cleanup old research data
def cleanup_old_research():
    """Remove research data older than 1 hour."""
    while True:
        try:
            current_time = time.time()
            to_remove = []
            for research_id, data in RESEARCH_CACHE.items():
                if current_time - data.get('created_at', current_time) > 3600:
                    to_remove.append(research_id)
            
            for research_id in to_remove:
                RESEARCH_CACHE.pop(research_id, None)
                RESEARCH_PROGRESS.pop(research_id, None)
        except Exception as e:
            print(f"Cleanup error: {e}")
        time.sleep(3600)  # Run every hour

# Start cleanup thread
cleanup_thread = threading.Thread(target=cleanup_old_research, daemon=True)
cleanup_thread.start()

# Request/Response models
class ResearchRequest(BaseModel):
    topic: str
    manual_feedback: Optional[str] = None

class FeedbackRequest(BaseModel):
    research_id: str
    feedback: str

# Helper functions
def replace_pmid_with_links(text: str) -> str:
    """Replace PMID references with links."""
    lines = []
    for line in text.splitlines():
        if 'https://pubmed' not in line:
            line = re.sub(r'PMID:\s*(\d{7,8})', r'[PMID: \1](https://pubmed.ncbi.nlm.nih.gov/\1/)', line)
        lines.append(line)
    return '\n'.join(lines)

# PDF generation function removed - using Markdown download

# Routes
@app.get("/", response_class=HTMLResponse)
async def root():
    """Serve the main HTML page."""
    html_file = Path(__file__).parent / "templates" / "index.html"
    if html_file.exists():
        return html_file.read_text(encoding='utf-8')
    return "<h1>Deep Search API</h1><p>API is running. Use /docs for API documentation.</p>"

@app.post("/api/start_research")
async def start_research(request: ResearchRequest, background_tasks: BackgroundTasks):
    """Start a new research task."""
    log_print("\n" + "="*80)
    log_print("🔥🔥🔥 API ENDPOINT HIT - /api/start_research 🔥🔥🔥")
    log_print("="*80)
    log_print(f"Request received: topic='{request.topic}'")
    sys.stdout.flush()
    
    if not request.topic:
        raise HTTPException(status_code=400, detail="Topic is required")
    
    research_id = str(uuid.uuid4())
    start_time = time.time()
    
    log_print(f"\n🚀 API Request: Starting research for '{request.topic}'")
    log_print(f"   Research ID: {research_id}")
    sys.stdout.flush()
    
    user_instructions = f'''Provide an in-depth drug effect research report for {CANCER_NAME} cancer with:
- Comprehensive mechanism of action with subtype-specific nuances
- Extensive pathway analysis covering upregulated/downregulated and sensitivity/resistance pathways
- Detailed primary human targets with mechanistic annotations
- Thorough sensitivity and resistance mechanisms stratified by {CANCER_NAME} cancer subtype
- Comprehensive clinical trial data (Phase II/III) with NCT IDs
- Detailed contraindications and safety from regulatory sources (FDA/EMA/PMDA)
- Multiple well-formatted tables for pathways, targets, subtypes, and clinical evidence
- Extensive references with PMID, ChEMBL IDs, DrugBank IDs, pathway identifiers, NCT IDs
- Complete {CANCER_NAME} cancer subtype-stratified evidence
- Pathway evidence table with clear regulation and effect directionality
- Use structured formatting with clear sections
- Be concise but comprehensive - prioritize quality over length'''
    
    # Use best OpenAI models
    planner_model = os.getenv("PLANNER_MODEL", "gpt-4o")
    writer_model = os.getenv("WRITER_MODEL", "gpt-4o")
    try:
        llm_min_interval_sec = max(0.0, float(os.getenv("LLM_MIN_INTERVAL_SEC", "0.3")))
    except ValueError:
        llm_min_interval_sec = 0.3
    
    thread_config = {
        "configurable": {
            "thread_id": research_id,
            "planner_provider": "openai",
            "planner_model": planner_model,
            "writer_provider": "openai",
            "writer_model": writer_model,
            "report_structure": DEFAULT_REPORT_STRUCTURE.format(
                cancer_name=CANCER_NAME,
                cancer_name_title=CANCER_NAME_TITLE,
            ),
            "max_search_depth": 2,
            "number_of_queries": 3,
            "user_instructions": user_instructions,
            "cancer_name": CANCER_NAME,
            "llm_min_interval_sec": llm_min_interval_sec,
        }
    }
    
    RESEARCH_PROGRESS[research_id] = {
        'status': 'initializing',
        'progress': 5,
        'stage': 'Starting research...',
        'start_time': start_time,
        'needs_feedback': False,
        'plan_sections': None
    }
    
    # Start research in background (manual_feedback disabled - auto-approve)
    log_print(f"🚀 Adding research task to background for topic: {request.topic}")
    log_print(f"   Background task will start immediately after response")
    sys.stdout.flush()
    
    # IMPORTANT: Add to background tasks - this will run after response is sent
    background_tasks.add_task(run_research, research_id, request.topic, thread_config, None)
    log_print(f"   ✓ Background task added successfully")
    sys.stdout.flush()
    
    return {
        "research_id": research_id,
        "status": "started",
        "message": "Research started. Use /api/progress/{research_id} to check progress."
    }

async def run_research(research_id: str, topic: str, thread_config: Dict, manual_feedback: Optional[str]):
    """Run the research graph in background."""
    import traceback
    
    # Immediate log to verify function is called
    log_print("\n\n" + "!"*80)
    log_print("RUN_RESEARCH FUNCTION CALLED - STARTING NOW!")
    log_print("!"*80)
    sys.stdout.flush()
    
    log_print("\n" + "="*80)
    log_print(f"🔬 DEEP SEARCH RESEARCH STARTED")
    log_print("="*80)
    log_print(f"Research ID: {research_id}")
    log_print(f"Topic: {topic}")
    log_print("="*80 + "\n")
    sys.stdout.flush()
    
    try:
        max_iterations = 100
        iteration_count = 0
        node_count = 0
        
        log_print("📊 Starting graph execution...\n")
        sys.stdout.flush()
        
        log_print(f"🔄 Invoking graph with topic: {topic}")
        log_print(f"   Thread config: {thread_config['configurable']['thread_id']}")
        sys.stdout.flush()
        
        # Auto-approve mode - no manual feedback needed
        log_print(f"   Input state: topic={topic}, manual_feedback=approve")
        sys.stdout.flush()
        
        async for event in graph.astream(
            {"topic": topic, "manual_feedback": "approve"},  # Always approve automatically
            thread_config,
            stream_mode="updates"
        ):
            iteration_count += 1
            node_count += len(event.keys())
            
            log_print(f"\n🎯 EVENT RECEIVED - Iteration {iteration_count}")
            log_print(f"   Nodes in event: {list(event.keys())}")
            
            # Log what's happening
            for node_name, node_data in event.items():
                log_print(f"✓ Node executed: {node_name}")
                if node_data:
                    try:
                        log_print(f"  └─ Data keys: {list(node_data.keys())}")
                    except:
                        log_print(f"  └─ Data: {type(node_data)}")
            sys.stdout.flush()
            
            # Update progress
            progress = min(85, 5 + (node_count * 5))
            stage = 'Processing...'
            
            if 'generate_report_plan' in event:
                stage = 'Generating research plan...'
                progress = 15
                log_print(f"\n📋 [Step 1/6] Generating research plan for '{topic}'...")
                sys.stdout.flush()
                # Check if we need feedback
                state = graph.get_state(thread_config)
                if state and state.values and 'sections' in state.values:
                    sections = state.values['sections']
                    log_print(f"   ✓ Generated {len(sections)} sections:")
                    for i, s in enumerate(sections, 1):
                        section_name = s.name if hasattr(s, 'name') else (s.get('name') if isinstance(s, dict) else 'Unknown')
                        section_desc = s.description if hasattr(s, 'description') else (s.get('description', '') if isinstance(s, dict) else '')
                        log_print(f"      {i}. {section_name} - {section_desc[:60]}...")
                    sys.stdout.flush()
                    # No feedback needed - auto-approved
            elif 'human_feedback' in event:
                stage = 'Auto-approving plan...'
                progress = 20
                log_print(f"\n✅ [Step 2/6] Auto-approving research plan (proceeding automatically)...")
                sys.stdout.flush()
            elif 'build_section_with_web_research' in event:
                stage = 'Researching sections...'
                progress = min(70, 20 + (node_count * 3))
                log_print(f"\n🔍 [Step 3/6] Researching sections with web search...")
                sys.stdout.flush()
                if event.get('build_section_with_web_research'):
                    section_data = event['build_section_with_web_research']
                    if isinstance(section_data, dict) and 'section' in section_data:
                        section_name = section_data['section'].get('name', 'Unknown') if isinstance(section_data['section'], dict) else getattr(section_data['section'], 'name', 'Unknown')
                        log_print(f"   → Researching section: {section_name}")
                        sys.stdout.flush()
            elif 'gather_completed_sections' in event:
                stage = 'Gathering completed sections...'
                progress = 75
                log_print(f"\n📦 [Step 4/6] Gathering completed research sections...")
                sys.stdout.flush()
            elif 'write_final_sections' in event:
                stage = 'Writing final sections...'
                progress = 80
                log_print(f"\n✍️  [Step 5/6] Writing final sections without research...")
                sys.stdout.flush()
            elif 'compile_final_report' in event:
                stage = 'Compiling final report...'
                progress = 90
                log_print(f"\n📝 [Step 6/6] Compiling final report...")
                sys.stdout.flush()
            
            RESEARCH_PROGRESS[research_id].update({
                'status': 'processing',
                'progress': progress,
                'stage': stage
            })
            
            # Check for final report
            state = graph.get_state(thread_config)
            if state and state.values and 'final_report' in state.values:
                final_report = state.values['final_report']
                if final_report and len(str(final_report).strip()) > 0:
                    word_count = len(str(final_report).split())
                    pages = max(1, word_count // 400)
                    sources = set(re.findall(r'https?://\S+', str(final_report)))
                    
                    log_print(f"\n✅ RESEARCH COMPLETE!")
                    log_print(f"   • Word count: {word_count:,}")
                    log_print(f"   • Estimated pages: {pages}")
                    log_print(f"   • Sources found: {len(sources)}")
                    log_print("="*80 + "\n")
                    sys.stdout.flush()
                    
                    RESEARCH_PROGRESS[research_id].update({
                        'status': 'complete',
                        'progress': 100,
                        'stage': 'Complete!'
                    })
                    
                    # Cache results
                    RESEARCH_CACHE[research_id] = {
                        'topic': topic,
                        'report': str(final_report),
                        'temp_dir': None,  # No need to store temp files for markdown
                        'pages': pages,
                        'sources': len(sources),
                        'created_at': time.time()
                    }
                    
                    return
            
            if iteration_count > max_iterations:
                break
        
        # If we got here, try to get partial results
        state = graph.get_state(thread_config)
        if state and state.values:
            final_report = state.values.get('final_report')
            if final_report:
                RESEARCH_PROGRESS[research_id].update({
                    'status': 'complete',
                    'progress': 100,
                    'stage': 'Complete!'
                })
                
                word_count = len(str(final_report).split())
                pages = max(1, word_count // 400)
                sources = set(re.findall(r'https?://\S+', str(final_report)))
                
                RESEARCH_CACHE[research_id] = {
                    'topic': topic,
                    'report': str(final_report),
                    'temp_dir': None,
                    'pages': pages,
                    'sources': len(sources),
                    'created_at': time.time()
                }
        
    except Exception as e:
        log_print(f"\n❌ ERROR in research execution:")
        log_print(f"   {type(e).__name__}: {str(e)}")
        import traceback
        log_print("\nFull traceback:")
        traceback.print_exc()
        log_print("="*80 + "\n")
        sys.stdout.flush()
        
        RESEARCH_PROGRESS[research_id].update({
            'status': 'error',
            'error': str(e),
            'stage': f'Error: {str(e)}'
        })

@app.post("/api/feedback")
async def submit_feedback(request: FeedbackRequest, background_tasks: BackgroundTasks):
    """Submit manual feedback for research plan."""
    if request.research_id not in RESEARCH_PROGRESS:
        raise HTTPException(status_code=404, detail="Research ID not found")
    
    RESEARCH_PROGRESS[request.research_id].update({
        'manual_feedback': request.feedback,
        'needs_feedback': False,
        'status': 'processing',
        'stage': 'Processing feedback and continuing...'
    })
    
    # Resume graph execution with feedback
    thread_config = {
        "configurable": {
            "thread_id": request.research_id
        }
    }
    
    # Resume the graph with feedback
    background_tasks.add_task(resume_with_feedback, request.research_id, request.feedback, thread_config)
    
    return {"status": "feedback_received", "message": "Feedback submitted. Research will continue."}

async def resume_with_feedback(research_id: str, feedback: str, thread_config: Dict):
    """Resume graph execution with feedback."""
    try:
        # Get current state and update with feedback
        state = graph.get_state(thread_config)
        if state and state.values:
            # Update state with feedback and resume
            config = thread_config.copy()
            config["configurable"]["manual_feedback"] = feedback
            
            # Continue graph execution
            max_iterations = 50
            iteration_count = 0
            
            async for event in graph.astream(
                {"manual_feedback": feedback},
                config,
                stream_mode="updates"
            ):
                iteration_count += 1
                
                # Update progress
                progress = RESEARCH_PROGRESS[research_id].get('progress', 20)
                RESEARCH_PROGRESS[research_id].update({
                    'status': 'processing',
                    'progress': min(90, progress + 1),
                    'stage': 'Continuing research with feedback...'
                })
                
                # Check for completion
                state = graph.get_state(config)
                if state and state.values and 'final_report' in state.values:
                    final_report = state.values['final_report']
                    if final_report and len(str(final_report).strip()) > 0:
                        # Save results (reuse logic from run_research)
                        RESEARCH_PROGRESS[research_id].update({
                            'status': 'complete',
                            'progress': 100,
                            'stage': 'Complete!'
                        })
                        
                        word_count = len(str(final_report).split())
                        pages = max(1, word_count // 400)
                        sources = set(re.findall(r'https?://\S+', str(final_report)))
                        
                        RESEARCH_CACHE[research_id] = {
                            'topic': state.values.get('topic', ''),
                            'report': str(final_report),
                            'temp_dir': None,
                            'pages': pages,
                            'sources': len(sources),
                            'created_at': time.time()
                        }
                        break
                
                if iteration_count > max_iterations:
                    break
                    
    except Exception as e:
        RESEARCH_PROGRESS[research_id].update({
            'status': 'error',
            'error': str(e)
        })

@app.get("/api/progress/{research_id}")
async def get_progress(research_id: str):
    """Get progress for a research task."""
    if research_id not in RESEARCH_PROGRESS:
        raise HTTPException(status_code=404, detail="Research ID not found")
    
    progress_data = RESEARCH_PROGRESS[research_id].copy()
    
    if 'start_time' in progress_data:
        elapsed = time.time() - progress_data['start_time']
        minutes = int(elapsed // 60)
        seconds = int(elapsed % 60)
        progress_data['elapsed_time'] = f"{minutes}m {seconds}s"
    else:
        progress_data['elapsed_time'] = '0s'
    
    return progress_data

@app.get("/api/result/{research_id}")
async def get_result(research_id: str):
    """Get final research result."""
    if research_id not in RESEARCH_CACHE:
        if research_id in RESEARCH_PROGRESS:
            progress = RESEARCH_PROGRESS[research_id]
            if progress.get('status') != 'complete':
                raise HTTPException(status_code=202, detail="Research not yet complete")
        raise HTTPException(status_code=404, detail="Research ID not found")
    
    cache_data = RESEARCH_CACHE[research_id]
    word_count = len(cache_data['report'].split())
    pages = max(1, word_count // 400)
    sources = set(re.findall(r'https?://\S+', cache_data['report']))
    
    return {
        "topic": cache_data.get('topic', ''),
        "pages": pages,
        "sources": len(sources),
        "research_id": research_id
    }

@app.get("/api/report/{research_id}")
async def get_report(research_id: str):
    """Get the full report content as HTML."""
    if research_id not in RESEARCH_CACHE:
        raise HTTPException(status_code=404, detail="Research ID not found")
    
    report_text = RESEARCH_CACHE[research_id].get('report', '')
    if not report_text:
        raise HTTPException(status_code=404, detail="Report content not found")
    
    html_content = markdown2.markdown(report_text, extras=["tables", "fenced-code-blocks", "header-ids"])
    # Return JSON with HTML for frontend to parse
    return JSONResponse(content={"html": html_content, "markdown": report_text})

@app.get("/api/download/{research_id}")
async def download_markdown(research_id: str):
    """Download the report as Markdown."""
    if research_id not in RESEARCH_CACHE:
        raise HTTPException(status_code=404, detail="Research ID not found")
    
    cache_data = RESEARCH_CACHE[research_id]
    report_text = cache_data.get('report', '')
    
    if not report_text:
        raise HTTPException(status_code=404, detail="Report content not available")
    
    # Create a temporary markdown file
    topic = cache_data.get('topic', 'report')
    # Sanitize filename
    safe_topic = "".join(c for c in topic if c.isalnum() or c in (' ', '-', '_')).rstrip()
    safe_topic = safe_topic.replace(' ', '_')
    
    temp_dir = tempfile.mkdtemp()
    md_path = os.path.join(temp_dir, f"{safe_topic}.md")
    
    # Write markdown file
    with open(md_path, 'w', encoding='utf-8') as f:
        f.write(report_text)
    
    return FileResponse(
        md_path,
        media_type="text/markdown",
        filename=f"{safe_topic}.md",
        headers={"Content-Disposition": f'attachment; filename="{safe_topic}.md"'}
    )

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8009)



