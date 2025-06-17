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
from xhtml2pdf import pisa
import pdfkit
import nltk
nltk.download('punkt')

# Import your actual graph object and Command class
from prompts import REPORT_STRUCTURE
from graph import graph

app = Flask(__name__)

# --- Configuration for PDF Generation (xhtml2pdf) ---
# No external binary needed for xhtml2pdf
# --- End PDF Config ---

# --- Configuration for wkhtmltopdf (UPDATE THIS PATH) ---
# IMPORTANT: Replace with the actual path to your wkhtmltopdf binary
path_to_wkhtmltopdf = '/usr/bin/wkhtmltopdf'
config = pdfkit.configuration(wkhtmltopdf=path_to_wkhtmltopdf)
# --- End wkhtmltopdf Config ---

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

def prepare_report_for_pdf(report_text):
    report_text = replace_pmid_with_links(report_text)
    report_text = replace_ncbi_with_links(report_text)
    report_text = replace_pubmed_with_links(report_text)
    report_text = replace_latex_brackets_with_dollars(report_text)

    html_content = markdown2.markdown(report_text, extras=["tables", "fenced-code-blocks"])

    css = """
    <style>
        body { font-family: Arial, sans-serif; line-height: 1.6; margin: 20mm; }
        h1, h2, h3, h4, h5, h6 { color: #333; margin-top: 1em; margin-bottom: 0.5em; }
        table { width: 100%; border-collapse: collapse; margin-bottom: 20px; }
        th, td { border: 1px solid #ddd; padding: 8px; text-align: left; }
        th { background-color: #f2f2f2; }
        pre, code {
            font-family: 'Courier New', monospace;
            background-color: #eee;
            border: 1px solid #ddd;
            padding: 5px;
            border-radius: 3px;
            overflow-x: auto;
        }
        pre {
            padding: 10px;
        }
        a { color: #007bff; text-decoration: none; }
        a:hover { text-decoration: underline; }
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
        <title>AI Generated Report</title>
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

<<<<<<< HEAD
@app.route('/generate_and_download_pdf', methods=['POST'])
def generate_and_download_pdf():
    data = request.json
    topic = data.get('topic')
    user_instructions = data.get('user_instructions')

    if not topic:
        return jsonify({"error": "Topic is required"}), 400

    thread_config = {
        "configurable": {
            "thread_id": str(uuid.uuid4()),
            "search_api": "tavily",
            "planner_provider": "openai",
            "planner_model": "o3-mini",
            "writer_provider": "openai",
            "writer_model": "gpt-4.1",
            "report_structure": REPORT_STRUCTURE,
            "max_search_depth": 2,
            "number_of_queries": 3,
            "user_instructions": user_instructions
        }
    }

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

    try:
        full_html_for_pdf = prepare_report_for_pdf(final_report_raw)
        temp_dir = tempfile.mkdtemp()
        pdf_path = os.path.join(temp_dir, "generated_report.pdf")

        options = {
            'enable-local-file-access': True,
            'enable-javascript': True,
            'no-stop-slow-scripts': True,
        }

        pdfkit.from_string(full_html_for_pdf, pdf_path, configuration=config, options=options)

        # Background thread to clean up temp folder after a delay
        def delayed_cleanup(path):
            time.sleep(10)  # give browser time to finish download
            try:
                shutil.rmtree(path)
                print(f"[CLEANUP] Deleted temp folder: {path}")
            except Exception as e:
                print(f"[CLEANUP ERROR] {e}")

        threading.Thread(target=delayed_cleanup, args=(temp_dir,), daemon=True).start()

        return send_file(
            pdf_path,
            as_attachment=True,
            download_name="generated_report.pdf",
            mimetype='application/pdf',
            conditional=False
        )

    except Exception as e:
        return jsonify({"error": f"Failed to generate PDF: {str(e)}"}), 500

@app.route('/send_pdf_email', methods=['POST'])
def send_pdf_email():
    data = request.json
    topic = data.get('topic')
    user_instructions = data.get('user_instructions')
    email = data.get('email')
    message = data.get('message', '')  # Accept optional message

    if not topic or not email:
        return jsonify({'error': 'Topic and email are required'}), 400

    thread_config = {
        "configurable": {
            "thread_id": str(uuid.uuid4()),
            "search_api": "tavily",
            "planner_provider": "openai",
            "planner_model": "o3-mini",
            "writer_provider": "openai",
            "writer_model": "gpt-4.1-mini",
            "report_structure": REPORT_STRUCTURE,
            "max_search_depth": 2,
            "number_of_queries": 3,
            "user_instructions": user_instructions
        }
    }

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

    try:
        full_html_for_pdf = prepare_report_for_pdf(final_report_raw)
        temp_dir = tempfile.mkdtemp()
        pdf_path = os.path.join(temp_dir, "generated_report.pdf")
        options = {
            'enable-local-file-access': True,
            'enable-javascript': True,
            'no-stop-slow-scripts': True,
        }
        pdfkit.from_string(full_html_for_pdf, pdf_path, configuration=config, options=options)

        # Send PDF and metadata to n8n webhook instead of direct email
        import requests
        n8n_webhook_url = "https://podhealthn8n.4gd.ai/prod/v1/5155b38d-47e4-4be9-a3d5-6803cbe044e7"
        with open(pdf_path, 'rb') as f:
            files = {'pdf': ('generated_report.pdf', f, 'application/pdf')}
            data = {'email': email, 'topic': topic, 'message': message}
            response = requests.post(n8n_webhook_url, data=data, files=files)

        # Clean up temp folder
        shutil.rmtree(temp_dir)

        if response.status_code == 200:
            return jsonify({'success': True}), 200
        else:
            return jsonify({'error': f'n8n webhook failed: {response.text}'}), 500
    except Exception as e:
        return jsonify({'error': f'Failed to send email: {str(e)}'}), 500
=======
# In-memory cache for research results (for demo; use persistent storage in production)
RESEARCH_CACHE = {}
>>>>>>> 6c4f2e908317bb77ade922d278877b8721ba2d29

@app.route('/start_research', methods=['POST'])
def start_research():
    data = request.json
    topic = data.get('topic')
    depth = data.get('depth', 'basic')
    user_instructions = ''
    if depth == 'detailed':
        user_instructions = 'Provide a detailed analysis with tables and examples.'
    elif depth == 'deep':
        user_instructions = 'Provide a deep research report with in-depth analysis, multiple tables, and comprehensive references.'
    else:
        user_instructions = 'Provide a basic overview.'

    thread_config = {
        "configurable": {
            "thread_id": str(uuid.uuid4()),
            "search_api": "tavily",
            "planner_provider": "openai",
            "planner_model": "o3-mini",
            "writer_provider": "openai",
            "writer_model": "gpt-4.1-mini",
            "report_structure": REPORT_STRUCTURE,
            "max_search_depth": 2,
            "number_of_queries": 3,
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
    full_html_for_pdf = prepare_report_for_pdf(final_report_raw)
    temp_dir = tempfile.mkdtemp()
    pdf_path = os.path.join(temp_dir, f"{research_id}.pdf")
    options = {
        'enable-local-file-access': True,
        'enable-javascript': True,
        'no-stop-slow-scripts': True,
    }
    pdfkit.from_string(full_html_for_pdf, pdf_path, configuration=config, options=options)
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
    temp_dir = RESEARCH_CACHE[research_id]['temp_dir']
    # Schedule cleanup
    def delayed_cleanup(path):
        time.sleep(10)
        try:
            shutil.rmtree(path)
        except Exception as e:
            print(f"[CLEANUP ERROR] {e}")
    threading.Thread(target=delayed_cleanup, args=(temp_dir,), daemon=True).start()
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
    temp_dir = RESEARCH_CACHE[research_id]['temp_dir']
    try:
        import requests
        n8n_webhook_url = "https://podhealthn8n.4gd.ai/prod/v1/5155b38d-47e4-4be9-a3d5-6803cbe044e7"
        with open(pdf_path, 'rb') as f:
            files = {'pdf': ('generated_report.pdf', f, 'application/pdf')}
            data = {'email': email, 'topic': topic, 'message': message}
            response = requests.post(n8n_webhook_url, data=data, files=files)
        shutil.rmtree(temp_dir)
        if response.status_code == 200:
            return jsonify({'success': True}), 200
        else:
            return jsonify({'error': f'n8n webhook failed: {response.text}'}), 500
    except Exception as e:
        return jsonify({'error': f'Failed to send email: {str(e)}'}), 500


if __name__ == '__main__':
 
    app.run(host='0.0.0.0', port=80)
