import os
from enum import Enum
from dataclasses import dataclass, fields
from typing import Any, Optional, Dict 

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.runnables import RunnableConfig
from dataclasses import dataclass

DEFAULT_REPORT_STRUCTURE = """Use this structure to create a comprehensive neuroscience research report:

1. Executive Summary (no research needed)
   - Brief overview and key findings (50-100 words)

2. Background and Current Understanding
   - Foundational concepts and mechanisms
   - Current state of knowledge
   - Summary tables of core concepts

3. Recent Advances (2023-2025)
   - Latest research findings
   - Breakthrough discoveries
   - Comparative tables of studies

4. Molecular and Cellular Mechanisms
   - Detailed pathways and interactions
   - Genetic and epigenetic factors
   - Mechanism summary tables

5. Clinical Implications
   - Disease relevance and biomarkers
   - Therapeutic strategies
   - Clinical trials data

6. Future Directions
   - Research gaps and opportunities
   - Potential therapeutic targets

7. Conclusion
   - Key takeaways and outlook (150-200 words)

8. References
   - Comprehensive citations with PMID/PMC IDs"""

class SearchAPI(Enum):
    PERPLEXITY = "perplexity"
    TAVILY = "tavily"
    EXA = "exa"
    ARXIV = "arxiv"
    PUBMED = "pubmed"
    LINKUP = "linkup"
    DUCKDUCKGO = "duckduckgo"
    GOOGLESEARCH = "googlesearch"

@dataclass(kw_only=True)
class Configuration:
    """The configurable fields for the chatbot."""
    report_structure: str = DEFAULT_REPORT_STRUCTURE # Defaults to the default report structure
    number_of_queries: int = 4 # Number of search queries to generate per iteration (optimized for speed)
    max_search_depth: int = 2 # Maximum number of reflection + search iterations (balanced for speed)
    planner_provider: str = "openai"  # Using OpenAI for best performance
    planner_model: str = "gpt-4o" # Best OpenAI model for planning
    writer_provider: str = "openai" # Using OpenAI for best performance
    writer_model: str = "gpt-4o" # Best OpenAI model for high-quality content
    search_api: SearchAPI = SearchAPI.PUBMED # Default to PUBMED for neuroscience research
    search_api_config: Optional[Dict[str, Any]] = None
    user_instructions: str

    @classmethod
    def from_runnable_config(
        cls, config: Optional[RunnableConfig] = None
    ) -> "Configuration":
        """Create a Configuration instance from a RunnableConfig."""
        configurable = (
            config["configurable"] if config and "configurable" in config else {}
        )
        values: dict[str, Any] = {
            f.name: os.environ.get(f.name.upper(), configurable.get(f.name))
            for f in fields(cls)
            if f.init
        }
        return cls(**{k: v for k, v in values.items() if v})
