#!/usr/bin/env python3
"""
Script to generate PDF from the technical documentation
Uses WeasyPrint instead of wkhtmltopdf (which has been discontinued)
"""

import markdown2
import os
import sys
try:
    from weasyprint import HTML, CSS
    WEASYPRINT_AVAILABLE = True
except ImportError:
    WEASYPRINT_AVAILABLE = False
    print("WeasyPrint not available. Install with: pip install weasyprint")

def read_markdown_file(file_path):
    """Read markdown file content"""
    try:
        with open(file_path, 'r', encoding='utf-8') as file:
            return file.read()
    except FileNotFoundError:
        print(f"Error: File {file_path} not found")
        sys.exit(1)
    except Exception as e:
        print(f"Error reading file {file_path}: {e}")
        sys.exit(1)

def convert_markdown_to_html(markdown_content):
    """Convert markdown content to HTML with styling"""
    
    # Convert markdown to HTML
    html_content = markdown2.markdown(
        markdown_content, 
        extras=[
            "tables", 
            "fenced-code-blocks", 
            "header-ids",
            "toc",
            "code-friendly"
        ]
    )
    
    # CSS styling for professional PDF
    css_styles = """
    <style>
        @page {
            margin: 20mm;
            @top-center {
                content: "NeuroDeep Search - Technical Documentation";
                font-size: 10pt;
                color: #666;
            }
            @bottom-center {
                content: counter(page);
                font-size: 10pt;
                color: #666;
            }
        }
        
        body {
            font-family: 'Arial', 'Helvetica', sans-serif;
            line-height: 1.6;
            color: #333;
            font-size: 11pt;
        }
        
        h1 {
            color: #2c3e50;
            font-size: 24pt;
            margin-top: 30px;
            margin-bottom: 15px;
            page-break-before: always;
            border-bottom: 3px solid #3498db;
            padding-bottom: 10px;
        }
        
        h1:first-of-type {
            page-break-before: auto;
        }
        
        h2 {
            color: #34495e;
            font-size: 18pt;
            margin-top: 25px;
            margin-bottom: 12px;
            border-bottom: 2px solid #3498db;
            padding-bottom: 5px;
        }
        
        h3 {
            color: #2c3e50;
            font-size: 14pt;
            margin-top: 20px;
            margin-bottom: 10px;
            font-weight: bold;
        }
        
        h4 {
            color: #34495e;
            font-size: 12pt;
            margin-top: 15px;
            margin-bottom: 8px;
            font-weight: bold;
        }
        
        h5, h6 {
            color: #2c3e50;
            font-size: 11pt;
            margin-top: 12px;
            margin-bottom: 6px;
            font-weight: bold;
        }
        
        p {
            margin-bottom: 10px;
            text-align: justify;
        }
        
        code {
            font-family: 'Courier New', monospace;
            background-color: #f8f9fa;
            padding: 2px 4px;
            border-radius: 3px;
            font-size: 10pt;
            border: 1px solid #e9ecef;
        }
        
        pre {
            font-family: 'Courier New', monospace;
            background-color: #f8f9fa;
            padding: 15px;
            border-radius: 5px;
            border: 1px solid #e9ecef;
            font-size: 9pt;
            line-height: 1.4;
            overflow-x: auto;
            margin: 15px 0;
        }
        
        pre code {
            background-color: transparent;
            padding: 0;
            border: none;
        }
        
        table {
            width: 100%;
            border-collapse: collapse;
            margin: 15px 0;
            font-size: 10pt;
        }
        
        th {
            background-color: #3498db;
            color: white;
            padding: 10px;
            text-align: left;
            font-weight: bold;
        }
        
        td {
            padding: 8px 10px;
            border-bottom: 1px solid #ddd;
        }
        
        tr:nth-child(even) {
            background-color: #f8f9fa;
        }
        
        ul, ol {
            margin: 10px 0 10px 20px;
        }
        
        li {
            margin-bottom: 5px;
        }
        
        blockquote {
            border-left: 4px solid #3498db;
            padding-left: 15px;
            margin: 15px 0;
            font-style: italic;
            background-color: #f8f9fa;
            padding: 10px 15px;
        }
        
        .toc {
            background-color: #f8f9fa;
            border: 1px solid #e9ecef;
            padding: 20px;
            margin: 20px 0;
            border-radius: 5px;
        }
        
        .toc h2 {
            margin-top: 0;
            color: #2c3e50;
            border-bottom: none;
        }
        
        .highlight {
            background-color: #fff3cd;
            padding: 10px;
            border-left: 4px solid #ffc107;
            margin: 15px 0;
        }
        
        .page-break {
            page-break-after: always;
        }
        
        .no-break {
            page-break-inside: avoid;
        }
        
        a {
            color: #3498db;
            text-decoration: none;
        }
        
        a:hover {
            text-decoration: underline;
        }
        
        .footer {
            position: fixed;
            bottom: 0;
            width: 100%;
            text-align: center;
            font-size: 9pt;
            color: #666;
        }
    </style>
    """
    
    # Complete HTML document
    html_document = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <title>NeuroDeep Search - Technical Documentation</title>
        {css_styles}
    </head>
    <body>
        {html_content}
    </body>
    </html>
    """
    
    return html_document

def generate_pdf_weasyprint(html_content, output_path):
    """Generate PDF from HTML content using WeasyPrint"""
    
    if not WEASYPRINT_AVAILABLE:
        print("Error: WeasyPrint is not available. Install with:")
        print("  pip install weasyprint")
        sys.exit(1)
    
    try:
        # Generate PDF using WeasyPrint
        html_doc = HTML(string=html_content)
        html_doc.write_pdf(output_path)
        print(f"PDF generated successfully using WeasyPrint: {output_path}")
        
    except Exception as e:
        print(f"Error generating PDF with WeasyPrint: {e}")
        print("Trying alternative method...")
        generate_pdf_reportlab(html_content, output_path)

def generate_pdf_reportlab(html_content, output_path):
    """Alternative PDF generation using ReportLab (fallback method)"""
    
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.lib.units import inch
        from bs4 import BeautifulSoup
        import re
        
        # Parse HTML and extract text
        soup = BeautifulSoup(html_content, 'html.parser')
        
        # Remove style tags
        for script in soup(["style", "script"]):
            script.decompose()
        
        text_content = soup.get_text()
        
        # Create PDF
        doc = SimpleDocTemplate(output_path, pagesize=A4,
                              rightMargin=72, leftMargin=72,
                              topMargin=72, bottomMargin=18)
        
        styles = getSampleStyleSheet()
        story = []
        
        # Split content into paragraphs
        paragraphs = text_content.split('\n\n')
        
        for para in paragraphs:
            if para.strip():
                # Check if it's a heading (simple heuristic)
                if para.strip().startswith('#'):
                    # Remove markdown heading symbols
                    cleaned_para = re.sub(r'^#+\s*', '', para.strip())
                    story.append(Paragraph(cleaned_para, styles['Heading1']))
                else:
                    story.append(Paragraph(para.strip(), styles['Normal']))
                story.append(Spacer(1, 12))
        
        doc.build(story)
        print(f"PDF generated successfully using ReportLab: {output_path}")
        
    except ImportError:
        print("Error: Neither WeasyPrint nor ReportLab is available.")
        print("Install one of them:")
        print("  pip install weasyprint")
        print("  pip install reportlab beautifulsoup4")
        sys.exit(1)
    except Exception as e:
        print(f"Error generating PDF with ReportLab: {e}")
        sys.exit(1)

def generate_pdf(html_content, output_path):
    """Main PDF generation function with fallbacks"""
    
    # Try WeasyPrint first (best quality)
    if WEASYPRINT_AVAILABLE:
        try:
            generate_pdf_weasyprint(html_content, output_path)
            return
        except Exception as e:
            print(f"WeasyPrint failed: {e}")
            print("Trying alternative method...")
    
    # Fallback to ReportLab
    generate_pdf_reportlab(html_content, output_path)

def main():
    """Main function to generate technical documentation PDF"""
    
    # File paths
    current_dir = os.path.dirname(os.path.abspath(__file__))
    markdown_file = os.path.join(current_dir, "TECHNICAL_DOCUMENTATION.md")
    output_pdf = os.path.join(current_dir, "NeuroDeep_Search_Technical_Documentation.pdf")
    
    print("NeuroDeep Search - Technical Documentation PDF Generator")
    print("=" * 60)
    
    # Check if markdown file exists
    if not os.path.exists(markdown_file):
        print(f"Error: Technical documentation file not found at {markdown_file}")
        sys.exit(1)
    
    print(f"Reading markdown file: {markdown_file}")
    markdown_content = read_markdown_file(markdown_file)
    
    print("Converting markdown to HTML...")
    html_content = convert_markdown_to_html(markdown_content)
    
    print("Generating PDF...")
    generate_pdf(html_content, output_pdf)
    
    print("\nPDF Generation Complete!")
    print(f"Output file: {output_pdf}")
    print(f"File size: {os.path.getsize(output_pdf) / 1024 / 1024:.2f} MB")

if __name__ == "__main__":
    main()