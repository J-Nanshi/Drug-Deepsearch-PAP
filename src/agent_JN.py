"""
Consolidated Deep Search Agent
Combines graph, state, prompts, utils, and configuration into a single module
"""

import os
import sys
import asyncio
import re
from typing import Literal, Annotated, List, TypedDict, Optional, Dict, Any
from typing_extensions import TypedDict as ExtTypedDict
from pydantic import BaseModel, Field
import operator
import nltk

# Hard-disable CUDA to avoid device moves on systems without proper GPU support
os.environ.setdefault("CUDA_VISIBLE_DEVICES", "")

# Prefer a project-local NLTK data directory if present to avoid user-profile issues on Windows
try:
    import nltk  # ensure available before adjusting path
    _project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    _nltk_dir = os.path.join(_project_root, "nltk_data")
    if os.path.isdir(_nltk_dir) and _nltk_dir not in nltk.data.path:
        nltk.data.path.insert(0, _nltk_dir)
except Exception:
    pass

def ensure_nltk():
    try:
        nltk.data.find("tokenizers/punkt")
    except (LookupError, OSError):
        # Ensure both resources; safe to re-download
        try:
            nltk.download("punkt", quiet=True)
        except Exception:
            pass
        try:
            nltk.download("punkt_tab", quiet=True)
        except Exception:
            pass

ensure_nltk()

# Helper function to print and flush immediately
def log_print(*args, **kwargs):
    print(*args, **kwargs)
    sys.stdout.flush()

from langchain.chat_models import init_chat_model
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langgraph.constants import Send
from langgraph.graph import START, END, StateGraph
from langgraph.checkpoint.memory import MemorySaver
from sentence_transformers import SentenceTransformer
from tavily import AsyncTavilyClient
from bs4 import BeautifulSoup
import requests
from urllib.parse import urlparse
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
try:
    import fitz  # PyMuPDF
    PYMUPDF_AVAILABLE = True
except ImportError:
    PYMUPDF_AVAILABLE = False
from nltk.tokenize import sent_tokenize
import nltk

import numpy as np  # Ensure numpy is always available for annotations and runtime
try:
    import faiss
    FAISS_AVAILABLE = True
except ImportError:
    FAISS_AVAILABLE = False

# ---------------- HTTP and Domain Controls ----------------
# Open-access domains allowlist (extend as needed)
ALLOWED_DOMAINS = {
    "pubmed.ncbi.nlm.nih.gov",
    "pmc.ncbi.nlm.nih.gov",
    "ncbi.nlm.nih.gov",          # keep for PMC/PubMed subpaths
    "clinicaltrials.gov",
    "fda.gov",
    "ema.europa.eu",
    "cancer.gov",
    "who.int",
}

def is_allowed_url(url: str) -> bool:
    try:
        host = urlparse(url).hostname or ""
        return any(host == d or host.endswith("." + d) for d in ALLOWED_DOMAINS)
    except Exception:
        return False

# Shared HTTP session with retries and sane headers
_session = requests.Session()
_retries = Retry(
    total=2,
    backoff_factor=0.5,
    status_forcelist=[429, 500, 502, 503, 504],
    allowed_methods=["GET", "HEAD"],
    raise_on_status=False,
)
_adapter = HTTPAdapter(max_retries=_retries)
_session.mount("http://", _adapter)
_session.mount("https://", _adapter)

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/121.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.google.com/",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
}

# Ensure NLTK punkt data is available
try:
    # Some NLTK versions may raise OSError for punkt_tab lookups on Windows
    nltk.data.find('tokenizers/punkt_tab')
except (LookupError, OSError):
    try:
        nltk.download('punkt_tab', quiet=True)
    except Exception:
        # Fallback to punkt if punkt_tab is not available
        try:
            nltk.data.find('tokenizers/punkt')
        except (LookupError, OSError):
            try:
                nltk.download('punkt', quiet=True)
            except Exception:
                pass
# Global embedding model for similarity search
_embedding_model = None

def get_embedding_model():
    """Get the global embedding model, initializing it if necessary."""
    global _embedding_model
    if _embedding_model is None:
        log_print("   🤖 Initializing embedding model...")
        # Force CPU to avoid 'meta tensor' issues with some PyTorch/Transformers versions
        # Also helps on Windows environments without CUDA
        try:
            import torch  # Local import to avoid import at module load
            _embedding_model = SentenceTransformer(
                "all-MiniLM-L6-v2",
                device="cpu",
                model_kwargs={
                    # Disable meta-tensor, load full weights on CPU memory
                    "low_cpu_mem_usage": False,
                    # Ensure no accelerate device mapping triggers meta tensors
                    "device_map": None,
                    # Force standard dtype on CPU
                    "torch_dtype": torch.float32,
                },
            )
            # Extra safety: ensure model is on CPU
            try:
                _embedding_model.to("cpu")
            except Exception:
                pass
        except NotImplementedError as e:
            # Fallback path if a meta-tensor error still bubbles up
            log_print("   ⚠️ Meta-tensor init issue detected, retrying with safer settings...", e)
            _embedding_model = SentenceTransformer("all-MiniLM-L6-v2", device="cpu")
    return _embedding_model

# ============ STATE MODELS ============

class Section(BaseModel):
    name: str = Field(description="Name for this section of the report.")
    description: str = Field(description="Brief overview of the main topics covered in this section.")
    research: bool = Field(description="Whether to perform web research for this section.")
    content: str = Field(default="", description="The content of the section.")

class Sections(BaseModel):
    sections: List[Section] = Field(description="Sections of the report.")

class SearchQuery(BaseModel):
    search_query: str = Field(description="Query for web search.")

class Queries(BaseModel):
    queries: List[SearchQuery] = Field(description="List of search queries.")

class Feedback(BaseModel):
    grade: Literal["pass", "fail"] = Field(description="Evaluation result.")
    follow_up_queries: List[SearchQuery] = Field(description="List of follow-up search queries.")

class ReportStateInput(ExtTypedDict):
    topic: str

class ReportStateOutput(ExtTypedDict):
    final_report: str
    evaluation_report: str

class ReportState(ExtTypedDict):
    topic: str
    feedback_on_report_plan: Optional[str]
    sections: List[Section]
    completed_sections: Annotated[List, operator.add]
    report_sections_from_research: str
    final_report: str
    evaluation_report: str
    manual_feedback: Optional[str]  # New field for manual feedback

class SectionState(ExtTypedDict):
    topic: str
    section: Section
    search_iterations: int
    search_queries: List[SearchQuery]
    source_str: str
    url_list: List[str]
    report_sections_from_research: str
    completed_sections: List[Section]

class SectionOutputState(ExtTypedDict):
    completed_sections: List[Section]

# ============ CONFIGURATION ============

DEFAULT_REPORT_STRUCTURE = """Use this structure to create a concise drug effect research report for breast cancer (MAXIMUM 7-8 PAGES TOTAL):

1. Drug Summary
   - Very brief overview (1-2 paragraphs, maximum 4-5 lines)
   - Major indications and high-level mechanism of action only
   - Include key citations

2. Identifiers & Synonyms
   - ChEMBL ID, DrugBank ID only (table format preferred, 50-100 words max)
   - Top 3-5 most important synonyms/brand names

3. Mechanism of Action (Breast Cancer Context)
   - Concise MoA (maximum 150 words)
   - Primary biochemical mechanism only
   - Key subtype relevance in 1-2 sentences

4. Primary Targets (Human)
   - Brief table or bullet points (maximum 10 targets, 100-150 words max)
   - One-line annotation per target
   - Citations

5. Pathways (Brief Overview)
   - List top 5-8 key pathways only (brief 1-2 lines each, 150-200 words max)
   - Pathway identifiers (MSigDB / Reactome / KEGG)
   - Regulation direction (Up/Down) and Effect (Sensitive/Resistant) only
   - NO detailed subsections - keep minimal

6. Breast Cancer Subtype Evidence
   - NOT tabular format - write as narrative paragraphs
   - Key subtypes: HR+/HER2–, HER2+, TNBC, gBRCA-mutated
   - For each subtype found: 3-4 sentences summarizing overall effect, combining all knowledge
   - Maximum 200 words total - be concise and focused on overall combined effect per subtype

7. Contraindications and Safety
   - Short narrative format: 3-4 paragraphs only
   - Focus on most critical contraindications and safety concerns
   - Maximum 200 words total - be concise
   - Key citations from FDA/EMA/NCCN

8. Key Clinical Trials
   - Top 2-3 most relevant trials with NCT IDs (100 words max)
   - Brief one-line outcomes

9. Pathway Evidence Table (MAIN FOCUS - MOST IMPORTANT SECTION - MUST BE LAST SECTION BEFORE REFERENCES)
   - ONE SINGLE comprehensive structured table with pathways
   - NO subsections, NO multiple tables, NO numbered subsections (9.1, 9.2, etc.)
   - Columns: Pathway ID/Name, Regulation (Up/Down), Effect (Sensitive/Resistant), Biological Rationale, References
   - CRITICAL: Limit to maximum 12 rows (12 pathways only) - select the most important/relevant pathways
   - Must appear as section 9, immediately before References section
   - After the table, include ### Sources listing ALL source URLs used for this section

10. References
    - CRITICAL: Collect ALL sources from the entire report
    - Extract all citation numbers [1], [2], [3] etc. from ALL sections of the report
    - Include sources from Pathway Evidence Table section (which has its own ### Sources subsection)
    - Also include any sources cited in other sections (even if they don't have ### Sources subsections)
    - For each citation number found anywhere in the report, include the corresponding source URL and information
    - Must match citations used in the report - no extra sources
    - Format: [1] Source Title - URL (if PMID/ChEMBL/DrugBank/NCT ID available, include it)
    - DO NOT include sources that were not cited anywhere in the report content
    - Each reference must correspond to an actual [citation number] used in the report text
"""

class Configuration:
    """Configuration for the agent."""
    def __init__(self, config: Optional[RunnableConfig] = None):
        configurable = (config.get("configurable", {}) if config else {})
        
        self.report_structure = configurable.get("report_structure", DEFAULT_REPORT_STRUCTURE)
        self.number_of_queries = int(configurable.get("number_of_queries", 3))
        self.max_search_depth = int(configurable.get("max_search_depth", 2))
        self.planner_provider = configurable.get("planner_provider", "openai")
        self.planner_model = configurable.get("planner_model", "gpt-4o")
        self.writer_provider = configurable.get("writer_provider", "openai")
        self.writer_model = configurable.get("writer_model", "gpt-4o")
        self.user_instructions = configurable.get("user_instructions", "")

# ============ UTILITY FUNCTIONS ============

def extract_text_from_pdf(url: str) -> str:
    """Extract text from PDF URL."""
    if not PYMUPDF_AVAILABLE:
        raise ImportError("PyMuPDF not available. Install with: pip install pymupdf")
    try:
        response = _session.get(url, headers=DEFAULT_HEADERS, timeout=20)
        response.raise_for_status()
        with open("./temp.pdf", "wb") as f:
            f.write(response.content)
        doc = fitz.open("./temp.pdf")
        text = " ".join([page.get_text() for page in doc])
        doc.close()
        return text
    except Exception as e:
        log_print(f"Error extracting PDF from {url}: {e}")
        return ""

def extract_text_from_html(url: str) -> str:
    """Extract text from HTML URL with robust headers/retries; skip office docs."""
    try:
        # Skip known unsupported/blocked document types
        if url.lower().endswith((".doc", ".docx", ".ppt", ".pptx", ".xls", ".xlsx")):
            return ""

        resp = _session.get(url, headers=DEFAULT_HEADERS, timeout=20)
        resp.raise_for_status()

        # Try to improve decoding to reduce replacement characters
        if not resp.encoding:
            try:
                resp.encoding = resp.apparent_encoding  # type: ignore[attr-defined]
            except Exception:
                resp.encoding = "utf-8"

        soup = BeautifulSoup(resp.text, "html.parser")
        paragraphs = soup.find_all("p")
        return " ".join([p.get_text(separator=" ", strip=True) for p in paragraphs])
    except Exception as e:
        log_print(f"Error extracting HTML from {url}: {e}")
        return ""

def chunk_text(text: str, chunk_size: int = 10) -> List[str]:
    """Chunk text into sentences."""
    try:
        sentences = sent_tokenize(text)
    except LookupError:
        # If tokenizer not found, try to download it
        try:
            nltk.download('punkt_tab', quiet=True)
        except:
            nltk.download('punkt', quiet=True)
        sentences = sent_tokenize(text)
    return [" ".join(sentences[i:i + chunk_size]) for i in range(0, len(sentences), chunk_size)]

def build_faiss_index(embeddings: np.ndarray):
    """Build FAISS index from embeddings."""
    if not FAISS_AVAILABLE:
        return None
    embeddings = embeddings / np.linalg.norm(embeddings, axis=1, keepdims=True)
    index = faiss.IndexFlatIP(embeddings.shape[1])
    index.add(embeddings)
    return index

def search_faiss(query: str, model: SentenceTransformer, index, chunks: List[str], top_n: int = 3):
    """Search FAISS index."""
    if not FAISS_AVAILABLE or index is None:
        return []
    query_embedding = model.encode([query], convert_to_numpy=True)
    query_embedding = query_embedding / np.linalg.norm(query_embedding, axis=1, keepdims=True)
    scores, indices = index.search(query_embedding, top_n)
    return [(int(idx), chunks[idx], float(scores[0][i])) for i, idx in enumerate(indices[0])]

async def tavily_search_async(search_queries: List[str]):
    """Perform concurrent web searches using Tavily API, restricted to allowlisted domains."""
    tavily_client = AsyncTavilyClient(api_key=os.getenv("TAVILY_API_KEY"))
    include_domains = list(ALLOWED_DOMAINS)
    search_tasks = []
    for query in search_queries:
        # Prefer open-access allowlisted sources
        try:
            search_tasks.append(
                tavily_client.search(
                    query,
                    max_results=8,
                    include_raw_content=True,
                    topic="general",
                    include_domains=include_domains,
                )
            )
        except TypeError:
            # Older client versions may not support include_domains
            search_tasks.append(
                tavily_client.search(
                    query,
                    max_results=8,
                    include_raw_content=True,
                    topic="general",
                )
            )
    return await asyncio.gather(*search_tasks)

def format_search_results(search_docs: List[Dict]) -> tuple[str, List[str]]:
    """Format Tavily search results into context string, filtering to allowlisted URLs."""
    formatted_text = ""
    url_list = []

    for doc in search_docs:
        if not doc or 'results' not in doc:
            continue

        query = doc.get('query', '')
        formatted_text += f"\n{'='*80}\n"
        formatted_text += f"SEARCH QUERY: {query}\n"
        formatted_text += f"{'='*80}\n\n"

        for result in doc['results']:
            url = result.get('url', '')
            if not url or not is_allowed_url(url):
                continue  # drop non-allowed domains early

            title = result.get('title', '')
            content = result.get('content', '')
            raw_content = result.get('raw_content', '')

            url_list.append(url)

            # Truncate raw_content to prevent massive context inflation
            final_content = raw_content if raw_content else content
            if len(final_content) > 10000:
                final_content = final_content[:10000] + "... [Truncated]"

            formatted_text += f"Title: {title}\n"
            formatted_text += f"URL: {url}\n"
            formatted_text += f"Content: {final_content}\n\n"

        formatted_text += f"{'='*80}\n\n"

    return formatted_text.strip(), url_list

# Helper functions for robust section handling
def get_section_attr(section, attr: str, default=""):
    """Safely get an attribute from a section (handles both dict and Pydantic object)."""
    if isinstance(section, dict):
        return section.get(attr, default)
    return getattr(section, attr, default)

def set_section_attr(section, attr: str, value):
    """Safely set an attribute on a section (handles both dict and Pydantic object)."""
    if isinstance(section, dict):
        section[attr] = value
    else:
        setattr(section, attr, value)

def ensure_section(section) -> Section:
    """Ensure a section is a proper Section object."""
    if isinstance(section, Section):
        return section
    if isinstance(section, dict):
        return Section(
            name=section.get("name", ""),
            description=section.get("description", ""),
            research=section.get("research", True),
            content=section.get("content", "")
        )
    return section

def ensure_sections_list(sections) -> List[Section]:
    """Ensure all items in sections list are proper Section objects."""
    return [ensure_section(s) for s in sections]

def trim_context(context: str, max_chars: int = 400000) -> str:
    """Trim context to a safe character limit (approx 100k-120k tokens)."""
    if len(context) <= max_chars:
        return context
    
    log_print(f"   ⚠️ Truncating context from {len(context)} to {max_chars} characters")
    # Try to keep the first part and the last part, or just the first part
    half = max_chars // 2
    return context[:half] + "\n\n... [TRUNCATED DUE TO LENGTH] ...\n\n" + context[-half:]

def format_sections(sections: List[Section]) -> str:
    """Format sections into a string."""
    formatted_str = ""
    for idx, section in enumerate(sections, 1):
        # Handle both dict and Pydantic object
        name = get_section_attr(section, 'name', 'Unknown')
        description = get_section_attr(section, 'description', '')
        research = get_section_attr(section, 'research', True)
        content = get_section_attr(section, 'content', '')
        
        formatted_str += f"""
{'='*60}
Section {idx}: {name}
{'='*60}
Description:
{description}
Requires Research: {research}

Content:
{content if content else '[Not yet written]'}

"""
    return formatted_str

# ============ PROMPTS ============

REPORT_PLANNER_QUERY_WRITER_INSTRUCTIONS = """You are performing deep drug effect research for a precision-oncology report.

<Drug Name>
{topic}
</Drug Name>

<Report organization>
{report_organization}
</Report organization>

<Task>
Your goal is to generate {number_of_queries} web search queries that will help gather comprehensive information for planning the drug effect research report sections.

The queries should:
1. Focus on the drug "{topic}" in human breast cancer context (Homo sapiens only)
2. Cover: mechanism of action, targets, pathways, sensitivity/resistance mechanisms, clinical trials, safety/contraindications
3. Help satisfy the requirements specified in the report organization
4. Target authoritative sources: PubMed, ChEMBL, DrugBank, FDA/EMA labels, NCCN/ESMO guidelines, clinical trials

Make the queries specific enough to find high-quality, authoritative sources while covering the breadth needed for the report structure.
</Task>

<Instructions>
Your primary consideration should be to fetch info that is richer than the following content. Don't try to follow the sections or content exactly.
Just try to make a 100 percent better report than this:
{primary_report_gpt}

Focus on human breast cancer data only, with proper citations from authoritative sources.
</Instructions>
 
<Format>
Call the Queries tool
</Format>
"""

REPORT_PLANNER_INSTRUCTIONS = """I want a plan for a comprehensive drug effect research report that is focused on precision oncology.

<Drug Name>
The drug being researched is:
{topic}
</Drug Name>

<Report organization>
The report should follow this organization:
{report_organization}
</Report organization>

<Context>
Here is context to use to plan the sections of the report:
{context}
</Context>

<Task>
Generate a list of sections for the drug effect research report on "{topic}" in human breast cancer. Your plan should be comprehensive and focused on precision-oncology research with NO overlapping sections.

The report must cover (MAXIMUM 7-8 PAGES TOTAL):
- Drug summary (brief, 100-150 words max)
- Identifiers and synonyms (brief, 50-100 words max)
- Mechanism of action (brief, 150 words max)
- Primary human targets (brief table, 100-150 words max)
- Pathways overview (brief list only, 150-200 words max, NO detailed subsections)
- Breast cancer subtype evidence (narrative format, 3-4 sentences per subtype, 200 words max)
- Contraindications and safety (3-4 paragraphs only, 200 words max)
- Key clinical trials (brief, 100 words max)
- Pathway Evidence Table (MAIN FOCUS - ONE SINGLE table, maximum 12 rows/pathways, most important section, NO subsections, NO numbered subsections like 9.1/9.2, NO multiple tables, NO category headings - entire section is one continuous table only)

Each section should have the fields:
- Name - Name for this section of the report.
- Description - Brief overview of the main topics covered in this section.
- Research - Whether to perform web research for this section of the report (Yes/No).
- Content - The content of the section, which you will leave blank for now.

Integration guidelines:
- Ensure each section has a distinct purpose with no content overlap
- Follow the exact order specified in the report organization
- Sections must focus on human (Homo sapiens) breast cancer data only
- All sections requiring citations should be marked for research

Before submitting, review your structure to ensure it follows the report organization exactly and covers all required drug effect research aspects.
</Task>

<Feedback>
Here is feedback on the report structure from review (if any):
{feedback}
</Feedback>

<Format>
Call the Sections tool
</Format>
"""

QUERY_WRITER_INSTRUCTIONS = """You are an expert precision-oncology researcher crafting targeted web search queries.

<Drug Name>
{topic}
</Drug Name>

<Section topic>
{section_topic}
</Section topic>

<Task>
Your goal is to generate {number_of_queries} search queries that will help gather comprehensive information for the section topic on the drug "{topic}" in human breast cancer.

The queries should:
1. Focus on the drug "{topic}" in human (Homo sapiens) breast cancer context
2. Examine different aspects of the section topic (mechanisms, pathways, clinical evidence, safety, etc.)
3. Target authoritative sources: PubMed, ChEMBL, DrugBank, FDA/EMA labels, NCCN/ESMO guidelines, clinical trials databases
4. Include queries for subtype-specific information (TNBC, HR+/HER2-, HER2+, gBRCA-mutated) when relevant

Make the queries specific enough to find high-quality, peer-reviewed, regulatory, or guideline-based sources.
</Task>

<Format>
Call the Queries tool
</Format>
"""

CONTEXT_FETCH = """You are a researcher researching on the topic: {topic}. You are currently writing a section of the report named: {section}.
You are given this link: {link}.
Try to read this and fetch most relevant paragraphs of this paper for the given section of the given topic.
If you can't read this link, try to read as much as possible and write a contextual information for the same.

<Instructions>:
1. Max word limit: 300 words strictly.
2. Be precise and fetch information only 100% match is found.
3. If exploring any links other than the given link, return links as sources.
4. Strictly refrain from exploring other links if the given link article is fully accessible.
5. All links must be returned in https format.
</Instructions>
"""

SECTION_WRITER_INSTRUCTIONS = """Write one section of a drug effect research report for precision oncology.

<Task>
1. Review the drug name, section name, and section topic carefully.
2. CAREFULLY review ALL sources in the <Source material> section - note the actual URLs and any identifiers present.
3. CRITICAL: ONLY use information and citations from the ACTUAL sources provided in <Source material>.
   - Extract real identifiers (PMID, ChEMBL, DrugBank, NCT) ONLY from the actual source URLs/content
   - DO NOT invent, guess, or make up any citation information
   - Each citation must trace back to an actual source URL provided
4. CRITICAL: Total report must be MAXIMUM 7-8 pages. Follow strict word limits per section.
5. For "Breast Cancer Subtype Evidence" section: Write in narrative paragraph format (NOT tabular). For each subtype found (HR+/HER2–, HER2+, TNBC, gBRCA-mutated), provide 3-4 sentences summarizing the overall combined effect. Maximum 200 words total.
6. For "Contraindications and Safety" section: Write in short narrative format with 3-4 paragraphs only. Focus on most critical contraindications and safety concerns. Maximum 200 words total.
7. For "Pathway Evidence Table" section: This is the MAIN FOCUS - create ONE SINGLE table with maximum 12 rows (12 pathways only), NO subsections like 9.1, 9.2, NO multiple tables, NO numbered subsections of any kind). The entire section must contain ONLY one table - no breaks, no subsections, no category groupings. Select the most important/relevant pathways.
8. For all other sections: Be extremely concise - use tables/lists where possible, maximum word limits apply.
7. Include only essential findings, mechanisms, and data from the provided authoritative sources.
8. Use proper scientific terminology and cite with identifiers ONLY if they appear in the actual sources.
9. Prioritize tables or lists to organize complex information (pathways, targets, contraindications) to save space.
10. Prioritize human (Homo sapiens) breast cancer data only.
11. Follow these specific instructions: {user_instructions}
</Task>

<Writing Guidelines>
- CRITICAL: Total report must be MAXIMUM 7-8 pages (approximately 2800-3200 words total)
- Write concise, focused content - be brief and essential only
- For Pathway Evidence Table section: This is the MAIN FOCUS - create ONE SINGLE table with maximum 12 rows (12 pathways only), NO subsections, NO numbered subsections like 9.1/9.2, NO multiple tables, NO category headings within the section - ONLY one continuous table. Select the most important/relevant pathways.
- For all other sections: Maximum word limits strictly enforced
- Use tables wherever possible to save space
- Cite with PMID, ChEMBL IDs, DrugBank IDs, pathway identifiers (ONLY if found in actual sources)
- Use ## for section title (Markdown format)
- Human (Homo sapiens) breast cancer data only
- CRITICAL: Do NOT include ### Sources at the end of sections (except Pathway Evidence Table)
- All sources will be collected together in the References section at the end
- MANDATORY: Always create tables in Markdown format when listing multiple items
</Writing Guidelines>

<Scientific Citation Rules>
CRITICAL: ONLY cite from the ACTUAL sources provided in the <Source material> section. DO NOT make up, invent, or guess any citation information.

1. Citation Source Validation:
   - ONLY use citations from sources explicitly listed in the <Source material> section
   - Extract real identifiers (PMID, ChEMBL, DrugBank, NCT) ONLY if they appear in the actual source URLs or content
   - DO NOT invent or generate any citation IDs that are not present in the provided sources

2. Citation Format:
   - Assign each unique URL from <Source material> that you ACTUALLY USE a sequential citation number (1, 2, 3, ...)
   - In text: cite as [1], [2], [3] etc. corresponding to the source number
   - CRITICAL: Only assign citation numbers to sources you ACTUALLY CITE in your written content
   - CRITICAL: Do NOT include ### Sources at the end of sections (except for Pathway Evidence Table section)
   - All sources will be collected together in the References section at the end of the report
   - Format: [1] Title (from source) - URL (actual URL from source)

3. Reference Section (at end of report):
   - All sources from all sections will be collected together in the final References section
   - List ONLY the sources that you ACTUALLY CITED in your written content with [1], [2], [3], etc.
   - Match the citation numbers exactly
   - Do NOT include sources you didn't cite, even if they were in <Source material>
</Scientific Citation Rules>
"""

SECTION_WRITER_INPUTS_TEMPLATE = """
<Report topic>
{topic}
</Report topic>

<Section name>
{section_name}
</Section name>

<Section topic>
{section_topic}
</Section topic>

<Existing section content (if populated)>
{section_content}
</Existing section content>

<Source material>
IMPORTANT: The following sources are the ONLY sources you may cite. Extract citation information (PMIDs, ChEMBL IDs, DrugBank IDs, NCT IDs) ONLY from these actual URLs and content. DO NOT invent or make up any citation information.

{context}

CRITICAL: Look for URLs in the content above. URLs typically appear as:
- Full URLs starting with http:// or https://
- URLs in "Source:" or "URL:" labeled sections
- URLs listed at the end under "AVAILABLE SOURCE URLs"
- URLs in "### Sources" sections from other report sections (these are VALID sources)

END OF SOURCE MATERIAL - You may ONLY cite from the sources listed above.
</Source material>
"""

SECTION_GRADER_INSTRUCTIONS = """Review a drug effect research report section relative to the specified drug and section topic:

<Drug Name>
{topic}
</Drug Name>

<section topic>
{section_topic}
</section topic>

<section content>
{section}
</section content>

<task>
1. Evaluate whether the section content adequately addresses the section topic for the drug "{topic}" in human breast cancer.
2. Check if the section content is fulfilling the user instructions: {user_instructions}.
3. CRITICAL: Verify citation accuracy:
   - All citations must trace back to ACTUAL sources from the provided source material
   - Check that all PMIDs, ChEMBL IDs, DrugBank IDs, NCT IDs are real (extracted from actual URLs, not invented)
   - Verify that citation numbers [1], [2], [3] match actual sources from the source material
   - Flag any citations that cannot be traced to provided sources as "FAIL"
4. Verify that:
   - All claims are properly cited with sources that exist in the provided source material
   - Human (Homo sapiens) data only is used
   - Breast cancer subtype context is specified where relevant

If the section content contains fabricated citations, citations not traceable to sources, or lacks proper citations, generate {number_of_follow_up_queries} follow-up search queries to gather missing information.
</task>

<format>
Call the Feedback tool
</format>
"""

FINAL_SECTION_WRITER_INSTRUCTIONS = """You are an expert precision-oncology researcher crafting a section that synthesizes information from the rest of the drug effect research report.

<Drug Name>
{topic}
</Drug Name>

<Section name>
{section_name}
</Section name>

<Section topic>
{section_topic}
</Section topic>

<Available report content>
{context}
</Available report content>

<Task>
For Pathway Evidence Table section:
- Use ## for section title (Markdown format): "## Pathway Evidence Table"
- This is the MAIN FOCUS and most important section - it should be the LAST main section before References
- ABSOLUTELY CRITICAL: Create ONLY ONE SINGLE TABLE - do NOT create subsections (no 9.1, 9.2, etc.), do NOT create multiple tables, do NOT use ### or #### headings
- MANDATORY: You MUST create ONE populated table with pathways
- CRITICAL: Limit to maximum 12 rows (12 pathways only) - select the most important/relevant pathways from available sources
- Extract pathway information from available sources in the <Available report content>
- Include selected pathways in ONE SINGLE TABLE with columns: Pathway ID/Name, Regulation (Up/Down), Effect (Sensitive/Resistant), Biological Rationale, References
- Format: ONE Markdown table with proper headers
- After the table, end with ### Sources listing ALL source URLs used for this section

For References section:
- Use ## for section title (Markdown format): "## References"
- CRITICAL: Extract ALL citation numbers [1], [2], [3], etc. from the ENTIRE report content above
- Include sources from Pathway Evidence Table section (which has its own ### Sources subsection)
- Also include any sources cited in other sections (even if they don't have ### Sources subsections)
- List ONLY the sources that correspond to citation numbers actually used anywhere in the report
- Format: [1] Source Title - https://actual-url.com (PMID: 12345678 if available)
- Collect ALL sources from all sections together in this References section

For all other sections:
- Use ## for section title (Markdown format)
- Follow strict word limits from report structure
- Be extremely concise - use tables/lists where possible
- CRITICAL: Do NOT include ### Sources at the end - all sources will be collected in References section
</Task>
"""

# ============ GRAPH NODES ============

async def generate_report_plan(state: ReportState, config: RunnableConfig):
    """Generate the initial report plan with sections."""
    topic = state["topic"]
    feedback = state.get("feedback_on_report_plan") or state.get("manual_feedback")
    
    log_print(f"\n📋 Generating report plan for: {topic}")
    if feedback:
        log_print(f"   Using feedback: {feedback[:100]}...")
    
    cfg = Configuration(config)
    
    # Generate queries for planning
    writer_model = init_chat_model(model=cfg.writer_model, model_provider=cfg.writer_provider)
    structured_llm = writer_model.with_structured_output(Queries)
    
    primary_report_gpt = writer_model.invoke([
        SystemMessage(content='You are an assistant research report writer.'),
        HumanMessage(content=f"Write a report on {topic}")
    ])
    
    system_instructions_query = REPORT_PLANNER_QUERY_WRITER_INSTRUCTIONS.format(
        topic=topic,
        report_organization=cfg.report_structure,
        number_of_queries=cfg.number_of_queries,
        primary_report_gpt=primary_report_gpt.content
    )
    
    results = structured_llm.invoke([
        SystemMessage(content=system_instructions_query),
        HumanMessage(content="Generate search queries for planning the report sections.")
    ])
    
    # Handle both Queries object and dict (from state serialization)
    if isinstance(results, dict):
        queries_list = results.get("queries", [])
    else:
        queries_list = results.queries
    
    # Search using Tavily
    query_list = []
    for query in queries_list:
        if isinstance(query, dict):
            query_list.append(query.get("search_query", ""))
        else:
            query_list.append(query.search_query)
    
    log_print(f"   🔍 Executing {len(query_list)} search queries...")
    for i, q in enumerate(query_list, 1):
        log_print(f"      {i}. {q[:80]}...")
    sys.stdout.flush()
    
    search_docs = await tavily_search_async(query_list)
    source_str, url_list = format_search_results(search_docs)
    log_print(f"   ✓ Found {len(url_list)} sources")
    sys.stdout.flush()
    
    # Generate sections
    system_instructions_sections = REPORT_PLANNER_INSTRUCTIONS.format(
        topic=topic,
        report_organization=cfg.report_structure,
        context=source_str,
        feedback=feedback or "None"
    )
    
    # Use latest models
    if "claude-3-7" in cfg.planner_model.lower():
        planner_llm = init_chat_model(
            model=cfg.planner_model,
            model_provider=cfg.planner_provider,
            max_tokens=20000,
            thinking={"type": "enabled", "budget_tokens": 16000}
        )
    elif "gpt-5" in cfg.planner_model.lower() or "gpt-4o" in cfg.planner_model.lower():
        planner_llm = init_chat_model(
            model=cfg.planner_model,
            model_provider=cfg.planner_provider,
            max_completion_tokens=16000
        )
    else:
        planner_llm = init_chat_model(model=cfg.planner_model, model_provider=cfg.planner_provider)
    
    structured_llm_sections = planner_llm.with_structured_output(Sections)
    report_sections = structured_llm_sections.invoke([
        SystemMessage(content=system_instructions_sections),
        HumanMessage(content="Generate the sections of the report.")
    ])
    
    # Handle both dict and Pydantic Sections object (from state serialization or API response)
    if isinstance(report_sections, dict):
        sections_data = report_sections.get("sections", [])
        sections = []
        for s in sections_data:
            if isinstance(s, dict):
                sections.append(Section(
                    name=s.get("name", ""),
                    description=s.get("description", ""),
                    research=s.get("research", True),
                    content=s.get("content", "")
                ))
            else:
                sections.append(s)
    else:
        sections = report_sections.sections
    
    for section in sections:
        name = section.name.lower()
        if ('introduction' in name) or ('conclusion' in name) or ('reference' in name):
            section.research = False
        else:
            section.research = True
    
    return {"sections": sections}

def human_feedback(state: ReportState, config: RunnableConfig):
    """Auto-approve feedback - always proceed with research."""
    log_print("\n   ✅ Auto-approving research plan (manual feedback disabled)")
    sys.stdout.flush()
    # Always auto-approve - return a valid state update
    # LangGraph requires at least one key to be written
    return {"manual_feedback": "approve"}

def human_feedback_router(state: ReportState):
    """Always route to research - auto-approve enabled."""
    # Always approve and start research immediately
    topic = state["topic"]
    sections = state['sections']
    
    # Use helper to safely access research attribute
    research_sections = [s for s in sections if get_section_attr(s, 'research', True)]
    log_print(f"   → Starting research for {len(research_sections)} sections...")
    sys.stdout.flush()
    
    return [
        Send("build_section_with_web_research", {"topic": topic, "section": ensure_section(s), "search_iterations": 0})
        for s in sections
        if get_section_attr(s, 'research', True)
    ] if research_sections else "gather_completed_sections"

def generate_queries(state: SectionState, config: RunnableConfig):
    """Generate search queries for researching a specific section."""
    topic = state["topic"]
    section = state["section"]
    section_name = get_section_attr(section, 'name', 'Unknown')
    section_description = get_section_attr(section, 'description', '')
    log_print(f"\n   🔍 Generating search queries for section: {section_name}")
    sys.stdout.flush()
    cfg = Configuration(config)
    
    writer_model = init_chat_model(model=cfg.writer_model, model_provider=cfg.writer_provider)
    structured_llm = writer_model.with_structured_output(Queries)
    
    system_instructions = QUERY_WRITER_INSTRUCTIONS.format(
        topic=topic,
        section_topic=section_description,
        number_of_queries=cfg.number_of_queries
    )
    
    queries = structured_llm.invoke([
        SystemMessage(content=system_instructions),
        HumanMessage(content="Generate search queries on the provided topic.")
    ])
    
    # Handle both Queries object and dict (from state serialization)
    if isinstance(queries, dict):
        queries_list = queries.get("queries", [])
    else:
        queries_list = queries.queries
    
    return {"search_queries": queries_list}

async def search_web(state: SectionState, config: RunnableConfig):
    """Execute web searches for the section queries."""
    search_queries = state["search_queries"]
    
    # Handle both SearchQuery objects and dicts (from state serialization)
    query_list = []
    for query in search_queries:
        if isinstance(query, dict):
            query_list.append(query.get("search_query", ""))
        else:
            query_list.append(query.search_query)
    
    log_print(f"      → Searching web with {len(query_list)} queries...")
    sys.stdout.flush()
    search_docs = await tavily_search_async(query_list)
    source_str, url_list = format_search_results(search_docs)
    log_print(f"      ✓ Retrieved {len(url_list)} sources")
    sys.stdout.flush()
    
    return {
        "source_str": source_str,
        "search_iterations": state["search_iterations"] + 1,
        "url_list": url_list
    }

def write_section(state: SectionState, config: RunnableConfig):
    """Write a section of the report and evaluate if more research is needed."""
    topic = state["topic"]
    section = state["section"]
    
    # Safely extract section attributes
    section_name = get_section_attr(section, 'name', 'Unknown')
    section_description = get_section_attr(section, 'description', '')
    section_content = get_section_attr(section, 'content', '')
    
    source_str = state["source_str"]
    url_list = state['url_list']
    
    log_print(f"      ✍️  Writing section: {section_name} (iteration {state.get('search_iterations', 0) + 1})")
    sys.stdout.flush()
    
    cfg = Configuration(config)
    writer_model = init_chat_model(model=cfg.writer_model, model_provider=cfg.writer_provider)
    
    # Build context with FAISS similarity search
    context = ""
    if source_str:
        context += source_str + "\n\n"
        context += "="*80 + "\n"
        context += "EXTRACTED RELEVANT CONTENT FROM SOURCES:\n"
        context += "="*80 + "\n\n"
    
    model = get_embedding_model()
    
    for url in url_list:
        # Extra safety: skip non-allowed URLs to avoid 403/406/paywalls
        if not is_allowed_url(url):
            continue
        try:
            try:
                text = extract_text_from_pdf(url)
            except:
                text = extract_text_from_html(url)
            
            if text:
                chunks = chunk_text(text, chunk_size=10)
                embeddings = model.encode(chunks, convert_to_numpy=True)
                index = build_faiss_index(embeddings)
                
                if index:
                    results = search_faiss(section_name + ':' + section_description, model, index, chunks, top_n=5)
                    for idx, chunk, score in results:
                        context += f"Content from {url}:\n{chunk}\n\n"
        except Exception as e:
            log_print(f"Error processing URL {url}: {e}")
            context_fetch_formatted = CONTEXT_FETCH.format(topic=topic, section=section_name, link=url)
            context_url = writer_model.invoke([HumanMessage(content=context_fetch_formatted)])
            context += f"Content from {url}:\n{context_url.content}\n\n"
    
    if url_list:
        context += "\n" + "="*80 + "\n"
        context += "AVAILABLE SOURCE URLs (use these for citations):\n"
        for i, url in enumerate(url_list, 1):
            context += f"[{i}] {url}\n"
        context += "="*80 + "\n"
    
    # Write section
    trimmed_context = trim_context(context)
    section_writer_inputs_formatted = SECTION_WRITER_INPUTS_TEMPLATE.format(
        topic=topic,
        section_name=section_name,
        section_topic=section_description,
        context=trimmed_context,
        section_content=section_content
    )
    
    section_writer_instructions_formatted = SECTION_WRITER_INSTRUCTIONS.format(
        user_instructions=cfg.user_instructions
    )
    
    section_response = writer_model.invoke([
        SystemMessage(content=section_writer_instructions_formatted),
        HumanMessage(content=section_writer_inputs_formatted)
    ])
    
    # Update section content (handle both dict and Pydantic object)
    new_content = section_response.content
    set_section_attr(section, 'content', new_content)
    
    # Grade section
    section_grader_message = ("Grade the report and consider follow-up questions for missing information. "
                              "If the grade is 'pass', return empty strings for all follow-up queries. "
                              "If the grade is 'fail', provide specific search queries to gather missing information.")
    
    section_grader_instructions_formatted = SECTION_GRADER_INSTRUCTIONS.format(
        topic=topic,
        section_topic=section_description,
        section=new_content,
        number_of_follow_up_queries=cfg.number_of_queries,
        user_instructions=cfg.user_instructions
    )
    
    # Use planner model for reflection
    if "claude-3-7" in cfg.planner_model.lower():
        reflection_model = init_chat_model(
            model=cfg.planner_model,
            model_provider=cfg.planner_provider,
            max_tokens=20000,
            thinking={"type": "enabled", "budget_tokens": 16000}
        ).with_structured_output(Feedback)
    elif "gpt-5" in cfg.planner_model.lower() or "gpt-4o" in cfg.planner_model.lower():
        reflection_model = init_chat_model(
            model=cfg.planner_model,
            model_provider=cfg.planner_provider,
            max_completion_tokens=16000
        ).with_structured_output(Feedback)
    else:
        reflection_model = init_chat_model(
            model=cfg.planner_model,
            model_provider=cfg.planner_provider
        ).with_structured_output(Feedback)
    
    feedback = reflection_model.invoke([
        SystemMessage(content=section_grader_instructions_formatted),
        HumanMessage(content=section_grader_message)
    ])
    
    # Handle both Feedback object and dict (from state serialization)
    if isinstance(feedback, dict):
        grade = feedback.get("grade", "fail")
        follow_up_queries = feedback.get("follow_up_queries", [])
    else:
        grade = feedback.grade
        follow_up_queries = feedback.follow_up_queries
    
    # Store feedback for routing decision - ensure section is a proper Section object for completed sections
    updates = {}
    if grade == "pass" or state["search_iterations"] >= cfg.max_search_depth:
        log_print(f"      ✅ Section '{section_name}' completed (Grade: {grade}, Iterations: {state['search_iterations']})")
        sys.stdout.flush()
        # Ensure we return a proper Section object
        completed_section = ensure_section(section)
        completed_section.content = new_content
        updates["completed_sections"] = [completed_section]
        updates["_section_complete"] = True
    else:
        log_print(f"      🔄 Section '{section_name}' needs more research (Grade: {grade}, {len(follow_up_queries)} follow-up queries)")
        sys.stdout.flush()
        updates["search_queries"] = follow_up_queries
        updates["section"] = section
        updates["_section_complete"] = False
    
    return updates

def write_section_router(state: SectionState):
    """Route based on section completion status."""
    if state.get("_section_complete", False):
        return END
    return "search_web"

def write_final_sections(state: SectionState, config: RunnableConfig):
    """Write sections that don't require research."""
    cfg = Configuration(config)
    topic = state["topic"]
    section = state["section"]
    completed_report_sections = state["report_sections_from_research"]
    
    # Safely extract section attributes
    section_name = get_section_attr(section, 'name', 'Unknown')
    section_description = get_section_attr(section, 'description', '')
    
    # Extract URLs from completed sections
    url_pattern = r'https?://[^\s\)]+|www\.[^\s\)]+'
    urls_found = []
    
    if completed_report_sections:
        all_urls = re.findall(url_pattern, completed_report_sections)
        sources_sections = re.findall(r'### Sources.*?(?=###|$)', completed_report_sections, re.DOTALL | re.IGNORECASE)
        for sources_section in sources_sections:
            section_urls = re.findall(url_pattern, sources_section)
            urls_found.extend(section_urls)
        
        urls_found = list(set(urls_found))
        
        if 'pathway' in section_name.lower() and 'evidence' in section_name.lower():
            if urls_found:
                sources_context = "\n\n" + "="*80 + "\n"
                sources_context += "AVAILABLE SOURCE URLs FROM ALL REPORT SECTIONS:\n"
                sources_context += "="*80 + "\n"
                for i, url in enumerate(urls_found, 1):
                    sources_context += f"Source [{i}]: {url}\n"
                sources_context += "="*80 + "\n"
                completed_report_sections = completed_report_sections + sources_context
    
    system_instructions = FINAL_SECTION_WRITER_INSTRUCTIONS.format(
        topic=topic,
        section_name=section_name,
        section_topic=section_description,
        context=completed_report_sections
    )
    
    writer_model = init_chat_model(model=cfg.writer_model, model_provider=cfg.writer_provider)
    section_response = writer_model.invoke([
        SystemMessage(content=system_instructions),
        HumanMessage(content="Generate a report section based on the provided sources.")
    ])
    
    # Ensure we return a proper Section object
    completed_section = ensure_section(section)
    completed_section.content = section_response.content
    return {"completed_sections": [completed_section]}

def gather_completed_sections(state: ReportState):
    """Format completed sections as context for writing final sections."""
    completed_sections = state["completed_sections"]
    completed_report_sections = format_sections(completed_sections)
    return {"report_sections_from_research": completed_report_sections}

def compile_final_report(state: ReportState):
    """Compile all sections into the final report."""
    sections = state["sections"]
    
    # Build a mapping of section names to content (handle both dict and Pydantic)
    completed_sections = {}
    for s in state["completed_sections"]:
        name = get_section_attr(s, 'name', '')
        content = get_section_attr(s, 'content', '')
        completed_sections[name] = content
    
    # Compile final report
    all_content = []
    for section in sections:
        section_name = get_section_attr(section, 'name', '')
        content = completed_sections.get(section_name, '')
        if content:
            all_content.append(content)
    
    return {"final_report": "\n\n".join(all_content)}

def initiate_final_section_writing(state: ReportState):
    """Create parallel tasks for writing non-research sections."""
    return [
        Send("write_final_sections", {
            "topic": state["topic"],
            "section": ensure_section(s),
            "report_sections_from_research": state["report_sections_from_research"]
        })
        for s in state["sections"]
        if not get_section_attr(s, 'research', True)
    ]

# ============ BUILD GRAPH ============

section_builder = StateGraph(SectionState)
section_builder.add_node("generate_queries", generate_queries)
section_builder.add_node("search_web", search_web)
section_builder.add_node("write_section", write_section)

section_builder.add_edge(START, "generate_queries")
section_builder.add_edge("generate_queries", "search_web")
section_builder.add_edge("search_web", "write_section")
section_builder.add_conditional_edges("write_section", write_section_router, {END: END, "search_web": "search_web"})

builder = StateGraph(ReportState)
builder.add_node("generate_report_plan", generate_report_plan)
builder.add_node("human_feedback", human_feedback)

async def build_section_with_web_research_node(state: SectionState, config: RunnableConfig):
    """Wrapper node to control subgraph output and prevent state collisions."""
    # This node is called for each section via Send
    # We compile the subgraph and invoke it
    subgraph = section_builder.compile()
    
    # Use ainvoke for async execution, pass parent config so settings propagate
    result = await subgraph.ainvoke(state, config=config)
    
    # CRITICAL: Only return completed_sections to avoid merging shared keys (like topic)
    # which causes InvalidUpdateError in parallel branches.
    return {"completed_sections": result.get("completed_sections", [])}

builder.add_node("build_section_with_web_research", build_section_with_web_research_node)
builder.add_node("gather_completed_sections", gather_completed_sections)
builder.add_node("write_final_sections", write_final_sections)
builder.add_node("compile_final_report", compile_final_report)

builder.add_edge(START, "generate_report_plan")
builder.add_edge("generate_report_plan", "human_feedback")
builder.add_conditional_edges("human_feedback", human_feedback_router)
builder.add_edge("build_section_with_web_research", "gather_completed_sections")
builder.add_conditional_edges("gather_completed_sections", initiate_final_section_writing, ["write_final_sections"])
builder.add_edge("write_final_sections", "compile_final_report")
builder.add_edge("compile_final_report", END)

memory = MemorySaver()
graph = builder.compile(checkpointer=memory)

