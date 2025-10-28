image_fetcher_instructions = """
You are reviewing a section of a research report.
Research topic: {topic}

Here is the written section:
{section_content}

Here are a few links that are being used to write the section:
{image_url_list}

<Task>
1. Judge what the section is talking about regarding the topic.
2. Decide whether the section requires any image to be understood. Note that image must be explicitly required for the topic to be explained, only then decide to have an image in the section.
3. If you have decided that the section requires image, return at most 2 images from the given list image urls which exactly match the section topic.
</Task>

"### Output Format:\n"

If image output is required:
        [image_link1, image_link2]
else:
        []
"""

context_fetch = """
You are a researcher researching on the topic: {topic}. You are currently writing a section of the report named: {section}.
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

<Sample Output>:
Mechanisms of Synaptic Remodeling in Neurodegeneration
(Source: PMC10216658)

This paper highlights several mechanisms central to synaptic remodeling in neurodegenerative disorders, particularly Alzheimer's disease (AD):

“Synaptic loss is one of the earliest events in AD pathogenesis and correlates strongly with cognitive decline. Aβ oligomers interact with various synaptic receptors (e.g., NMDA, AMPA, and mGluRs), leading to internalization or functional disruption of these receptors, causing synaptic dysfunction and spine loss.”

“Tau protein, particularly in its hyperphosphorylated form, is another key mediator of synaptic damage. It mislocalizes to dendritic spines, where it disrupts synaptic signaling and plasticity. Hyperphosphorylated tau reduces BDNF (brain-derived neurotrophic factor) signaling, further contributing to synaptic degeneration.”

“Glial cells play a significant role in synaptic remodeling. Microglia, upon activation, can engulf synaptic elements via the complement cascade (e.g., C1q, C3), particularly under inflammatory conditions. Astrocytes also contribute by altering glutamate homeostasis and releasing inflammatory cytokines that impair synaptic integrity.”

“BDNF-TrkB signaling, critical for synapse maintenance and plasticity, is notably downregulated in AD. Restoration of this pathway is considered a viable therapeutic strategy for preserving synaptic function.”

“The dysregulation of Rho GTPases (RhoA, Rac1, Cdc42) has also been implicated in dendritic spine pathology in AD. These molecules regulate actin cytoskeleton dynamics, which are essential for spine formation and maintenance.”

These mechanistic insights illustrate the multifactorial nature of synaptic remodeling in AD and support the rationale for targeting pathways like Aβ aggregation, tau phosphorylation, neuroinflammation, and trophic signaling in pharmacological interventions.

Source links:
link1
link2
link3 etc.
</Sample Output>

"""

report_planner_query_writer_instructions="""You are performing research for a report. 

<Report topic>
{topic}
</Report topic>

<Report organization>
{report_organization}
</Report organization>


<Task>
Your goal is to generate {number_of_queries} web search queries that will help gather information for planning the report sections. 

The queries should:

1. Be related to the Report topic
2. Help satisfy the requirements specified in the report organization

Make the queries specific enough to find high-quality, relevant sources while covering the breadth needed for the report structure.
</Task>

<Instructions>
Your primary consideration should be to fetch info that is enricher than the following content. Don't try to follow the sections or content.
Just try to make a 100 percent better report than this:
{primary_report_gpt}

</Instructions>

<Format>
Call the Queries tool 
</Format>
"""

report_planner_instructions="""I want a plan for a report that is concise and focused.

<Report topic>
The topic of the report is:
{topic}
</Report topic>

<Report organization>
The report should follow this organization: 
{report_organization}
</Report organization>

<Context>
Here is context to use to plan the sections of the report: 
{context}
</Context>

<Task>
Generate a list of sections for the report. Your plan should be tight and focused with NO overlapping sections or unnecessary filler. 

For example, a good report structure might look like:
1/ intro
2/ overview of topic A
3/ overview of topic B
4/ comparison between A and B
5/ conclusion

Each section should have the fields:

- Name - Name for this section of the report.
- Description - Brief overview of the main topics covered in this section.
- Research - Whether to perform web research for this section of the report.
- Content - The content of the section, which you will leave blank for now.

Integration guidelines:
- Include examples and implementation details within main topic sections, not as separate sections
- Ensure each section has a distinct purpose with no content overlap
- Combine related concepts rather than separating them

Before submitting, review your structure to ensure it has no redundant sections and follows a logical flow.
</Task>

<Feedback>
Here is feedback on the report structure from review (if any):
{feedback}
</Feedback>

<Format>
Call the Sections tool 
</Format>
"""

query_writer_instructions="""You are an expert technical writer crafting targeted web search queries that will gather comprehensive information for writing a technical report section.

<Report topic>
{topic}
</Report topic>

<Section topic>
{section_topic}
</Section topic>

<Task>
Your goal is to generate {number_of_queries} search queries that will help gather comprehensive information above the section topic. 

The queries should:

1. Be related to the topic 
2. Examine different aspects of the topic

Make the queries specific enough to find high-quality, relevant sources.
</Task>

<Format>
Call the Queries tool 
</Format>
"""

#3. Then, look at the provided Source material. You must access all URLs (if provided) to read the content.
section_writer_instructions = """Write one section of a research report.

<Task>
1. Review the report topic, section name, and section topic carefully.
2. If present, review any existing section content. 
3. Decide the sources that you will use to write a report section.
4. Write a comprehensive, well-researched section with scientific rigor.
5. Include specific findings, mechanisms, and data from the sources.
6. Use proper scientific terminology and cite with PMID/PMC IDs when available.
7. Include tables or lists to organize complex information clearly.
8. Prioritize the most recent research (2023-2025) when available.
9. Follow these specific instructions: {user_instructions}
</Task>

<Writing Guidelines for Neuroscience Research>
- Write detailed, information-rich paragraphs (not summaries)
- Include specific molecular mechanisms, pathways, and interactions
- Cite numerical data, effect sizes, and statistical findings when available
- Use tables to compare studies, mechanisms, or therapeutic approaches
- Mention specific brain regions, neurotransmitters, receptors, and proteins
- Include methodological details when relevant
- Discuss both preclinical and clinical findings
- Use ## for section title, ### for subsections (Markdown format)
- Aim for 400-600 words per section (detailed reports need depth)
</Writing Guidelines>

<Scientific Citation Rules>
- Assign each unique URL a single citation number
- Extract PMID or PMC IDs from PubMed URLs and cite as [PMID: 12345678]
- End with ### Sources listing each source with numbers
- Format: [1] Author et al. Title. Journal. Year. PMID: xxxxx - URL
- Number sources sequentially (1,2,3,4...) without gaps
</Scientific Citation Rules>

<Quality Standards>
1. Every scientific claim must be cited with source material
2. Include specific gene names, protein names, and molecular pathways
3. Use tables for comparing multiple studies or mechanisms
4. Mention specific experimental models (cell lines, animal models, clinical cohorts)
5. Include statistical significance and effect sizes when available
6. Discuss both positive and negative findings
</Quality Standards>
"""

#- Strict 150-200 word limit
#- Use short paragraphs (2-3 sentences max)

section_writer_inputs=""" 
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
{context}
</Source material>

"""

section_grader_instructions = """Review a report section relative to the specified topic:

<Report topic>
{topic}
</Report topic>

<section topic>
{section_topic}
</section topic>

<section content>
{section}
</section content>

<task>
1. Evaluate whether the section content adequately addresses the section topic.
2. Check if the section content is fulfiling the user instructions : {user_instructions}. Don't try to score based on user instructions if the section content is not relevant to user instructions.

If the section content does not adequately address the section topic, generate {number_of_follow_up_queries} follow-up search queries to gather missing information.
</task>

<format>
Call the Feedback tool and output with the following schema:

grade: Literal["pass","fail"] = Field(
    description="Evaluation result indicating whether the response meets requirements ('pass') or needs revision ('fail')."
)
follow_up_queries: List[SearchQuery] = Field(
    description="List of follow-up search queries.",
)
</format>
"""

final_section_writer_instructions="""You are an expert technical writer crafting a section that synthesizes information from the rest of the report.

<Report topic>
{topic}
</Report topic>

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
1. Section-Specific Approach:

For Introduction:
- Use # for report title (Markdown format)

- Write in simple and clear language
- Focus on the core motivation for the report in 1-2 paragraphs
- Use a clear narrative arc to introduce the report
- Include NO structural elements (no lists or tables)
- No sources section needed

For Conclusion/Summary:
- Use ## for section title (Markdown format)
- 100-150 word limit
- For comparative reports:
    * Must include a focused comparison table using Markdown table syntax
    * Table should distill insights from the report
    * Keep table entries clear and concise
- For non-comparative reports: 
    * Only use ONE structural element IF it helps distill the points made in the report:
    * Either a focused table comparing items present in the report (using Markdown table syntax)
    * Or a short list using proper Markdown list syntax:
      - Use `*` or `-` for unordered lists
      - Use `1.` for ordered lists
      - Ensure proper indentation and spacing
- End with specific next steps or implications
- No sources section needed

2. Writing Approach:
- Use concrete details over general statements
- Make every word count
- Focus on your single most important point
- Try to follow these instructions if not writing Introduction/Conclusion/Summary sections: {user_instructions}
</Task>

<Quality Checks>
- For introduction: 50-100 word limit, # for report title, no structural elements, no sources section
- For conclusion: 100-150 word limit, ## for section title, only ONE structural element at most, no sources section
- Markdown format
- Do not include word count or any preamble in your response
</Quality Checks>"""


REPORT_STRUCTURE = """
Use this structure to create a well-formatted, comprehensive neuroscience research report:

1. Executive Summary
   - Brief overview and key findings (75-100 words)
   - No references

2. Introduction and Background
   - Current understanding and foundational concepts
   - Historical context
   - Summary table of key concepts

3. Recent Research Advances (2023-2025)
   - Latest breakthrough discoveries
   - Comparative table of major studies
   - Novel methodologies and technologies
   - Cite with PMID/PMC IDs

4. Mechanistic Insights
   - Molecular and cellular mechanisms
   - Signaling pathways and key interactions
   - Genetic factors and regulation
   - Mechanism comparison tables

5. Clinical and Therapeutic Implications
   - Disease biomarkers and diagnostics
   - Current therapeutic strategies
   - Clinical trials summary table
   - Emerging therapeutic targets

6. Conclusion and Future Directions
   - Key takeaways (100-150 words)
   - Research gaps and opportunities
   - Clinical implications

7. References
   - Comprehensive citations with PMID/PMC IDs
   - Organized by relevance
"""