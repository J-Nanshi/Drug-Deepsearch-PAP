# NeuroDeep Search - System Flow Diagram

## Complete Request Processing Flow

```mermaid
graph TD
    A[User Request] --> B{Input Validation}
    B -->|Valid| C[Generate Session UUID]
    B -->|Invalid| D[Return Error Response]
    
    C --> E[Initialize LangGraph Pipeline]
    E --> F[Configure Thread Settings]
    F --> G[Set Model Providers]
    G --> H[Configure Search APIs]
    
    H --> I[START: Report Planner Agent]
    I --> J[Analyze Research Topic]
    J --> K[Generate Section Structure]
    K --> L[Create Research Plan]
    
    L --> M[Query Writer Agent]
    M --> N[Generate Search Queries]
    N --> O[Optimize for Multiple APIs]
    O --> P[Validate Query Quality]
    
    P --> Q{Parallel Section Processing}
    
    Q --> R1[Section Writer 1]
    Q --> R2[Section Writer 2] 
    Q --> R3[Section Writer N]
    
    R1 --> S1[Search API Calls]
    R2 --> S2[Search API Calls]
    R3 --> S3[Search API Calls]
    
    S1 --> T1[Content Extraction]
    S2 --> T2[Content Extraction]
    S3 --> T3[Content Extraction]
    
    T1 --> U1[FAISS Similarity Search]
    T2 --> U2[FAISS Similarity Search]
    T3 --> U3[FAISS Similarity Search]
    
    U1 --> V1[Generate Section Content]
    U2 --> V2[Generate Section Content]
    U3 --> V3[Generate Section Content]
    
    V1 --> W[Collect Completed Sections]
    V2 --> W
    V3 --> W
    
    W --> X[Final Section Writer]
    X --> Y[Compile Complete Report]
    Y --> Z[Section Grader Agent]
    
    Z --> AA{Quality Check}
    AA -->|Pass| BB[Finalize Report]
    AA -->|Fail| CC[Generate Improvement Queries]
    CC --> M
    
    BB --> DD[Convert to HTML]
    DD --> EE[Generate PDF]
    EE --> FF[Cache Results]
    FF --> GG[Return Response with UUID]
    
    GG --> HH{PDF Download Request?}
    HH -->|Yes| II[Retrieve from Cache]
    HH -->|No| JJ[Return Metadata Only]
    
    II --> KK[Serve PDF File]
    
    JJ --> LL{Email Request?}
    LL -->|Yes| MM[Send via n8n Webhook]
    LL -->|No| NN[Complete Transaction]
    
    MM --> NN
    KK --> NN
    
    style A fill:#e1f5fe
    style I fill:#fff3e0
    style Q fill:#f3e5f5
    style Z fill:#e8f5e8
    style EE fill:#fce4ec
```

## LangGraph State Machine Flow

```mermaid
stateDiagram-v2
    [*] --> ReportPlanner
    
    ReportPlanner --> QueryWriter : sections planned
    
    QueryWriter --> SectionWriter : queries generated
    
    SectionWriter --> SectionWriter : parallel processing
    SectionWriter --> FinalWriter : all sections complete
    
    FinalWriter --> SectionGrader : report compiled
    
    SectionGrader --> QueryWriter : quality fail
    SectionGrader --> [*] : quality pass
    
    note right of SectionWriter : Multiple instances\nrunning in parallel
    note right of SectionGrader : Iterative improvement\nloop if quality fails
```

## Search API Integration Flow

```mermaid
graph LR
    A[Search Query] --> B{API Selection}
    
    B --> C[Tavily API]
    B --> D[PubMed API]  
    B --> E[ArXiv API]
    B --> F[DuckDuckGo API]
    B --> G[Perplexity API]
    
    C --> H1[Web Search Results]
    D --> H2[Medical Literature]
    E --> H3[Academic Papers]
    F --> H4[General Web Results]
    G --> H5[AI-Enhanced Results]
    
    H1 --> I[Content Extraction]
    H2 --> I
    H3 --> I
    H4 --> I
    H5 --> I
    
    I --> J[Text Processing]
    J --> K[Vector Embedding]
    K --> L[FAISS Indexing]
    L --> M[Similarity Search]
    M --> N[Ranked Results]
    
    style A fill:#e3f2fd
    style I fill:#f1f8e9
    style N fill:#fff8e1
```

## PDF Generation Pipeline

```mermaid
graph TD
    A[Final Report Text] --> B[Citation Processing]
    B --> C[Convert PMID Links]
    C --> D[Convert PMC Links]
    D --> E[Process LaTeX Math]
    E --> F[Markdown to HTML]
    F --> G[Apply CSS Styling]
    G --> H[wkhtmltopdf Processing]
    H --> I[Generate PDF File]
    I --> J[Store in Temp Directory]
    J --> K[Cache PDF Path]
    K --> L[Return Download Link]
    
    style A fill:#e8eaf6
    style F fill:#e0f2f1
    style I fill:#fce4ec
    style L fill:#f3e5f5
```

## Cache Management System

```mermaid
graph TD
    A[Research Request] --> B[Generate UUID]
    B --> C[Create Temp Directory]
    C --> D[Store Session Data]
    D --> E[Process Research]
    E --> F[Generate PDF]
    F --> G[Update Cache Entry]
    
    G --> H[Background Cleanup Thread]
    H --> I{Check File Age}
    I -->|> 1 hour| J[Delete Files]
    I -->|< 1 hour| K[Keep Files]
    J --> L[Remove Cache Entry]
    K --> M[Continue Monitoring]
    L --> M
    M --> H
    
    style A fill:#e1f5fe
    style H fill:#fff3e0
    style J fill:#ffebee
    style K fill:#e8f5e8
```

## Error Handling Flow

```mermaid
graph TD
    A[System Operation] --> B{Error Occurs?}
    B -->|No| C[Continue Normal Flow]
    B -->|Yes| D{Error Type}
    
    D --> E[API Error]
    D --> F[LLM Error]
    D --> G[PDF Generation Error]
    D --> H[System Error]
    
    E --> I[Retry with Backoff]
    F --> J[Fallback Model]
    G --> K[Alternative PDF Method]
    H --> L[Log and Alert]
    
    I --> M{Retry Success?}
    J --> N{Fallback Success?}
    K --> O{Alternative Success?}
    L --> P[Return Error Response]
    
    M -->|Yes| C
    M -->|No| Q[Use Cached Result]
    N -->|Yes| C
    N -->|No| Q
    O -->|Yes| C
    O -->|No| P
    
    Q --> R{Cache Available?}
    R -->|Yes| C
    R -->|No| P
    
    style A fill:#e3f2fd
    style D fill:#fff3e0
    style P fill:#ffebee
    style C fill:#e8f5e8
```