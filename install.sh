#!/bin/bash

# NeuroDeep Search - Updated Installation Script
# This script installs the necessary dependencies after wkhtmltopdf was discontinued

echo "🧠 NeuroDeep Search - Installation Script"
echo "========================================="
echo ""

# Check if Python 3 is available
if ! command -v python3 &> /dev/null; then
    echo "❌ Python 3 is not installed. Please install Python 3.8+ first."
    exit 1
fi

echo "✅ Python 3 found: $(python3 --version)"

# Check if pip is available
if ! command -v pip3 &> /dev/null && ! command -v pip &> /dev/null; then
    echo "❌ pip is not installed. Please install pip first."
    exit 1
fi

# Create virtual environment if it doesn't exist
if [ ! -d "venv" ]; then
    echo "📦 Creating virtual environment..."
    python3 -m venv venv
    echo "✅ Virtual environment created"
fi

# Activate virtual environment
echo "🔄 Activating virtual environment..."
source venv/bin/activate

# Upgrade pip
echo "⬆️  Upgrading pip..."
pip install --upgrade pip

# Install main requirements
echo "📚 Installing Python dependencies..."
pip install -r requirements.txt

# Install additional PDF generation dependencies
echo "🔧 Installing PDF generation tools..."
pip install weasyprint reportlab beautifulsoup4

# Check for system dependencies (optional but recommended)
echo ""
echo "🔍 Checking system dependencies..."

# Check for system libraries that WeasyPrint might need
case "$(uname -s)" in
    Darwin*)
        echo "🍎 macOS detected"
        if command -v brew &> /dev/null; then
            echo "🍺 Homebrew found - installing system libraries..."
            # WeasyPrint dependencies on macOS
            brew install cairo pango gdk-pixbuf libffi
        else
            echo "⚠️  Homebrew not found. Some PDF features might not work perfectly."
            echo "   Consider installing Homebrew: https://brew.sh"
        fi
        ;;
    Linux*)
        echo "🐧 Linux detected"
        echo "ℹ️  You may need to install system packages:"
        echo "   Ubuntu/Debian: sudo apt-get install build-essential python3-dev python3-pip python3-cffi libcairo2 libpango-1.0-0 libpangocairo-1.0-0 libgdk-pixbuf2.0-0 libffi-dev shared-mime-info"
        echo "   CentOS/RHEL: sudo yum install gcc python3-devel python3-pip python3-cffi cairo pango gdk-pixbuf2 libffi-devel"
        ;;
    *)
        echo "❓ Unknown operating system"
        ;;
esac

# Create .env file if it doesn't exist
if [ ! -f ".env" ]; then
    echo ""
    echo "📝 Creating .env file template..."
    cat > .env << EOF
# NeuroDeep Search API Keys
TAVILY_API_KEY=your_tavily_api_key_here
OPENAI_API_KEY=your_openai_api_key_here

# Optional API Keys
ANTHROPIC_API_KEY=your_anthropic_api_key_here
PERPLEXITY_API_KEY=your_perplexity_api_key_here
EOF
    echo "✅ .env file created. Please edit it with your actual API keys."
else
    echo "ℹ️  .env file already exists"
fi

# Test the installation
echo ""
echo "🧪 Testing installation..."
python3 -c "
import flask, markdown2, weasyprint, reportlab
print('✅ All main dependencies installed successfully!')

try:
    from weasyprint import HTML
    HTML(string='<h1>Test</h1>').write_pdf('/tmp/test.pdf')
    import os
    os.remove('/tmp/test.pdf')
    print('✅ WeasyPrint PDF generation works!')
except Exception as e:
    print(f'⚠️  WeasyPrint test failed: {e}')
    print('   PDF generation will use fallback method')
"

echo ""
echo "🎉 Installation complete!"
echo ""
echo "📋 Next steps:"
echo "1. Edit .env file with your API keys"
echo "2. Run the application: python3 app.py"
echo "3. Access the web interface at: http://localhost:80"
echo ""
echo "📚 Documentation:"
echo "- README.md - Quick start guide"
echo "- TECHNICAL_DOCUMENTATION.md - Comprehensive technical guide"
echo "- KNOWLEDGE_TRANSFER.md - Complete system overview"
echo ""
echo "🔧 Generate PDF documentation:"
echo "   python3 generate_tech_doc_pdf.py"
echo ""
echo "Happy researching! 🚀"