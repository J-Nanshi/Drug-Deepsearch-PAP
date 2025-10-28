import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# API Keys
os.environ["TAVILY_API_KEY"] = os.getenv("TAVILY_API_KEY")
os.environ["OPENAI_API_KEY"] = os.getenv("OPENAI_API_KEY")

# app.py
from flask import Flask, request, jsonify, render_template, send_file
import uuid
import re
import asyncio
from langgraph.types import Command
import os
import markdown2
import tempfile
import shutil
import time
import threading
# xhtml2pdf removed - using WeasyPrint for PDF generation
try:
    from weasyprint import HTML, CSS
    WEASYPRINT_AVAILABLE = True
except ImportError:
    WEASYPRINT_AVAILABLE = False
    print("Warning: WeasyPrint not available. PDF generation may fail.")

# Ensure NLTK punkt data is available (auto-download if missing)
import nltk
try:
    nltk.data.find('tokenizers/punkt')
except LookupError:
    nltk.download('punkt')

# Import your actual graph object and Command class
from prompts import REPORT_STRUCTURE
from graph import graph

app = Flask(__name__)

# --- Configuration for PDF Generation (WeasyPrint) ---
# Using WeasyPrint instead of discontinued wkhtmltopdf
# No external binary needed for WeasyPrint - pure Python solution
# --- End PDF Config ---

# --- Helper Functions for Markdown and PDF Generation ---
def replace_pmid_with_links(text):
    modified_lines = []
    for line in text.splitlines():
        if 'https://pubmed' not in line:
            line = re.sub(r'PMID:\s*(\d{7,8})', r'[PMID: \1](https://pubmed.ncbi.nlm.nih.gov/\1/)', line)
        modified_lines.append(line)
    return '\n'.join(modified_lines)

def replace_ncbi_with_links(text):
    modified_lines = []
    for line in text.splitlines():
        if 'https://pmc.ncbi.nlm.nih.gov' not in line:
            line = re.sub(r'PMC\s*(\d{7,8})', r'[PMC \1](https://pmc.ncbi.nlm.nih.gov/articles/PMC\1/)', line)
        modified_lines.append(line)
    return '\n'.join(modified_lines)

def replace_pubmed_with_links(text):
    modified_lines = []
    for line in text.splitlines():
        if 'https://pubmed' not in line:
            line = re.sub(r'PubMed \s*(\d{7,8})', r'[PubMed \1](https://pubmed.ncbi.nlm.nih.gov/\1/)', line)
        modified_lines.append(line)
    return '\n'.join(modified_lines)

def replace_latex_brackets_with_dollars(text):
    text = re.sub(r'\\\[(.*?)\\\]', r'$$\1$$', text, flags=re.DOTALL)
    text = re.sub(r'\\\((.*?)\\\)', r'$\1$', text)
    return text

def prepare_report_for_pdf(report_text, topic=None):
    report_text = replace_pmid_with_links(report_text)
    report_text = replace_ncbi_with_links(report_text)
    report_text = replace_pubmed_with_links(report_text)
    report_text = replace_latex_brackets_with_dollars(report_text)

    # Insert only the topic headline at the top if provided
    if topic:
        topic_html = f'<div style="text-align:center; margin-bottom: 18pt;"><h1 style="color:#1a5490; font-size: 2.1em; font-weight: 700; margin-bottom: 0;">{topic}</h1></div>'
        report_text = topic_html + '\n\n' + report_text

    html_content = markdown2.markdown(report_text, extras=["tables", "fenced-code-blocks", "header-ids"])

    # Professional, modern CSS with optimal spacing and beautiful formatting
    css = """
    <style>
        @page {
            size: A4;
            margin: 15mm 20mm;
        }
        
        body { 
            font-family: 'Segoe UI', 'Arial', sans-serif;
            line-height: 1.7;
            color: #2c3e50;
            font-size: 10.5pt;
            margin: 0;
            padding: 0;
        }
        
        h1 { 
            color: #1a5490;
            font-size: 22pt;
            font-weight: 700;
            margin: 20pt 0 12pt 0;
            padding-bottom: 8pt;
            border-bottom: 3px solid #3498db;
            page-break-after: avoid;
        }
        
        h2 { 
            color: #2980b9;
            font-size: 16pt;
            font-weight: 600;
            margin: 18pt 0 10pt 0;
            padding-bottom: 4pt;
            border-bottom: 2px solid #ecf0f1;
            page-break-after: avoid;
        }
        
        h3 { 
            color: #34495e;
            font-size: 13pt;
            font-weight: 600;
            margin: 14pt 0 8pt 0;
            page-break-after: avoid;
        }
        
        h4, h5, h6 { 
            color: #34495e;
            font-size: 11pt;
            font-weight: 600;
            margin: 12pt 0 6pt 0;
        }
        
        p { 
            margin: 0 0 10pt 0;
            text-align: justify;
            orphans: 3;
            widows: 3;
        }
        
        ul, ol {
            margin: 8pt 0 10pt 20pt;
            padding: 0;
        }
        
        li {
            margin-bottom: 4pt;
        }
        
        table { 
            width: 100%;
            border-collapse: collapse;
            margin: 12pt 0;
            font-size: 9.5pt;
            page-break-inside: avoid;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        }
        
        th { 
            background: linear-gradient(to bottom, #3498db, #2980b9);
            color: white;
            padding: 10pt 8pt;
            text-align: left;
            font-weight: 600;
            border: 1px solid #2980b9;
        }
        
        td { 
            border: 1px solid #dfe6e9;
            padding: 8pt;
            text-align: left;
        }
        
        tr:nth-child(even) {
            background-color: #f8f9fa;
        }
        
        tr:hover {
            background-color: #e8f4f8;
        }
        
        pre {
            background: linear-gradient(to right, #f8f9fa, #ecf0f1);
            border-left: 4px solid #3498db;
            padding: 12pt;
            margin: 10pt 0;
            overflow-x: auto;
            font-size: 9pt;
            border-radius: 4px;
            page-break-inside: avoid;
        }
        
        code {
            font-family: 'Consolas', 'Monaco', 'Courier New', monospace;
            background-color: #f1f3f5;
            padding: 2pt 4pt;
            border-radius: 3px;
            font-size: 9pt;
            color: #c7254e;
        }
        
        pre code {
            background-color: transparent;
            padding: 0;
            color: #2c3e50;
        }
        
        a { 
            color: #3498db;
            text-decoration: none;
            font-weight: 500;
        }
        
        a:hover { 
            text-decoration: underline;
            color: #2980b9;
        }
        
        blockquote {
            border-left: 4px solid #3498db;
            margin: 12pt 0;
            padding: 8pt 12pt;
            background-color: #f8f9fa;
            font-style: italic;
            page-break-inside: avoid;
        }
        
        strong {
            color: #2c3e50;
            font-weight: 600;
        }
        
        em {
            color: #34495e;
        }
        
        .page-break {
            page-break-before: always;
        }
        
        /* Scientific notation styling */
        sup, sub {
            font-size: 7.5pt;
        }
        
        /* Footer page numbers */
        @page {
            @bottom-right {
                content: counter(page);
                font-size: 9pt;
                color: #7f8c8d;
            }
        }
    </style>
    """

    mathjax_script = """
    <script type="text/javascript" async
        src="https://cdn.jsdelivr.net/npm/mathjax@3/es5/tex-mml-chtml.js">
    </script>
    <script>
        MathJax = {
            tex: {
                inlineMath: [['$', '$'], ['\\(', '\\)']],
                displayMath: [['$$', '$$'], ['\\[', '\\]']]
            }
        };
    </script>
    """

    full_html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>NeuroDeep Research Report</title>
        {css}
        {mathjax_script}
    </head>
    <body>
    {html_content}
    </body>
    </html>
    """
    return full_html


# --- Flask Routes ---
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/healthz')
def healthz():
    return 'OK', 200

from flask import after_this_request

# In-memory cache for research results (for demo; use persistent storage in production)
RESEARCH_CACHE = {}
TEMP_DIRS = set()

# Background cleanup thread to remove temp_dirs older than 1 hour
import glob
from datetime import datetime, timedelta

def cleanup_old_temp_dirs():
    while True:
        now = time.time()
        for research_id, entry in list(RESEARCH_CACHE.items()):
            temp_dir = entry['temp_dir']
            if os.path.exists(temp_dir):
                mtime = os.path.getmtime(temp_dir)
                if now - mtime > 3600:  # 1 hour
                    try:
                        shutil.rmtree(temp_dir)
                    except Exception as e:
                        print(f"[CLEANUP ERROR] {e}")
                    RESEARCH_CACHE.pop(research_id, None)
        time.sleep(600)  # Run every 10 minutes

def start_cleanup_thread():
    t = threading.Thread(target=cleanup_old_temp_dirs, daemon=True)
    t.start()

start_cleanup_thread()

@app.route('/start_research', methods=['POST'])
def start_research():
    data = request.json
    topic = data.get('topic')
    depth = data.get('depth', 'basic')
    
    # Optimized configuration for fast, high-quality neuroscience research
    user_instructions = ''
    search_api = "pubmed"
    max_depth = 2
    num_queries = 3
    
    if depth == 'detailed':
        user_instructions = '''Provide a comprehensive neuroscience research report with:
- Latest research findings from 2023-2025
- Detailed molecular mechanisms and pathways
- Clinical implications and therapeutic strategies
- Well-formatted tables and diagrams descriptions
- Proper scientific citations with PMID/PMC IDs
- Use bullet points and structured formatting for clarity'''
        search_api = "pubmed"
        max_depth = 2
        num_queries = 4
    elif depth == 'deep':
        user_instructions = '''Provide an in-depth neuroscience research report with:
- Cutting-edge research from top-tier journals (2023-2025)
- Comprehensive molecular, cellular, and systems-level mechanisms
- Clinical trials and translational research insights
- Multiple well-formatted comparison tables
- Extensive references with PMID/PMC citations
- Visual descriptions of pathways and mechanisms
- Use structured formatting with clear sections'''
        search_api = "pubmed"
        max_depth = 3
        num_queries = 5
    else:
        user_instructions = 'Provide a clear overview with recent scientific findings and structured formatting.'
        search_api = "pubmed"
        max_depth = 2
        num_queries = 3

    thread_config = {
        "configurable": {
            "thread_id": str(uuid.uuid4()),
            "search_api": search_api,
            "planner_provider": "openai",
            "planner_model": "gpt-4o",
            "writer_provider": "openai",
            "writer_model": "gpt-4o",
            "report_structure": REPORT_STRUCTURE,
            "max_search_depth": max_depth,
            "number_of_queries": num_queries,
            "user_instructions": user_instructions
        }
    }
    start_time = time.time()
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        async def run_graph():
            async for event in graph.astream({"topic": topic}, thread_config, stream_mode="updates"):
                if '__interrupt__' in event:
                    break
            async for event in graph.astream(Command(resume=True), thread_config, stream_mode="updates"):
                pass
            return graph.get_state(thread_config).values.get('final_report')
        final_report_raw = loop.run_until_complete(run_graph())
    except Exception as e:
        return jsonify({"error": f"Error generating report: {str(e)}"}), 500
    finally:
        loop.close()
    # Estimate summary, pages, sources, time
    summary = ''
    if final_report_raw:
        summary = final_report_raw.split('\n')[0][:300]  # First paragraph or 300 chars
    word_count = len(final_report_raw.split()) if final_report_raw else 0
    pages = max(1, word_count // 400)  # Roughly 400 words per page
    import re
    sources = set(re.findall(r'https?://\S+', final_report_raw or ''))
    elapsed = time.time() - start_time
    minutes = int(elapsed // 60)
    seconds = int(elapsed % 60)
    time_str = f"{minutes} Minutes" if minutes else f"{seconds} Seconds"
    # Generate PDF and cache everything
    research_id = str(uuid.uuid4())
    full_html_for_pdf = prepare_report_for_pdf(final_report_raw, topic=topic)
    temp_dir = tempfile.mkdtemp()
    pdf_path = os.path.join(temp_dir, f"{research_id}.pdf")
    
    # Generate PDF using WeasyPrint (modern alternative to wkhtmltopdf)
    try:
        if WEASYPRINT_AVAILABLE:
            html_doc = HTML(string=full_html_for_pdf)
            html_doc.write_pdf(pdf_path)
        else:
            # Fallback: create a text file if no PDF library available
            with open(pdf_path.replace('.pdf', '.txt'), 'w', encoding='utf-8') as f:
                f.write(final_report_raw)
            pdf_path = pdf_path.replace('.pdf', '.txt')
    except Exception as e:
        print(f"PDF generation failed: {e}")
        # Fallback: create a text file
        with open(pdf_path.replace('.pdf', '.txt'), 'w', encoding='utf-8') as f:
            f.write(final_report_raw)
        pdf_path = pdf_path.replace('.pdf', '.txt')
    RESEARCH_CACHE[research_id] = {
        'topic': topic,
        'summary': summary,
        'pages': pages,
        'sources': len(sources),
        'time': time_str,
        'user_instructions': user_instructions,
        'report': final_report_raw,
        'pdf_path': pdf_path,
        'temp_dir': temp_dir
    }
    return jsonify({
        "topic": topic,
        "summary": summary,
        "pages": pages,
        "sources": len(sources),
        "time": time_str,
        "research_id": research_id
    })

@app.route('/generate_and_download_pdf', methods=['POST'])
def generate_and_download_pdf():
    data = request.json
    research_id = data.get('research_id')
    if not research_id or research_id not in RESEARCH_CACHE:
        return jsonify({"error": "Invalid or expired research ID. Please run research again."}), 400
    pdf_path = RESEARCH_CACHE[research_id]['pdf_path']
    # Do NOT delete temp_dir here; let background thread handle it
    return send_file(
        pdf_path,
        as_attachment=True,
        download_name="generated_report.pdf",
        mimetype='application/pdf',
        conditional=False
    )

@app.route('/send_pdf_email', methods=['POST'])
def send_pdf_email():
    data = request.json
    research_id = data.get('research_id')
    email = data.get('email')
    message = data.get('message', '')
    if not research_id or research_id not in RESEARCH_CACHE or not email:
        return jsonify({'error': 'Research ID and email are required'}), 400
    pdf_path = RESEARCH_CACHE[research_id]['pdf_path']
    topic = RESEARCH_CACHE[research_id]['topic']
    user_instructions = RESEARCH_CACHE[research_id]['user_instructions']
    # Do NOT delete temp_dir here; let background thread handle it
    try:
        import requests
        n8n_webhook_url = "https://podhealthn8n.4gd.ai/prod/v1/5155b38d-47e4-4be9-a3d5-6803cbe044e7"
        with open(pdf_path, 'rb') as f:
            files = {'pdf': ('generated_report.pdf', f, 'application/pdf')}
            data = {'email': email, 'topic': topic, 'message': message}
            response = requests.post(n8n_webhook_url, data=data, files=files)
        if response.status_code == 200:
            return jsonify({'success': True}), 200
        else:
            return jsonify({'error': f'n8n webhook failed: {response.text}'}), 500
    except Exception as e:
        return jsonify({'error': f'Failed to send email: {str(e)}'}), 500


if __name__ == '__main__':
 
    app.run(host='0.0.0.0', port=8002)
