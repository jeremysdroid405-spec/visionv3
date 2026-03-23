#!/bin/bash
# PropVision Production Setup Script
# Run this ONCE after copying files to your server

set -e  # Exit on error

echo "=========================================="
echo "PropVision Production Setup"
echo "=========================================="

# Configuration - EDIT THESE
APP_DIR="/var/www/visionv3"
BACKEND_DIR="$APP_DIR/backend"
FRONTEND_DIR="$APP_DIR/frontend"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

print_status() { echo -e "${GREEN}[✓]${NC} $1"; }
print_warning() { echo -e "${YELLOW}[!]${NC} $1"; }
print_error() { echo -e "${RED}[✗]${NC} $1"; }

# Check if running as root
if [ "$EUID" -ne 0 ]; then
    print_error "Please run as root (sudo ./setup_production.sh)"
    exit 1
fi

# Create log directory
echo ""
echo "1. Creating log directory..."
mkdir -p /var/log/propvision
chmod 755 /var/log/propvision
print_status "Log directory created: /var/log/propvision"

# Setup Backend
echo ""
echo "2. Setting up Backend..."
cd "$BACKEND_DIR"

# Check if Python 3.11+ is installed
PYTHON_VERSION=$(python3 --version 2>&1 | cut -d' ' -f2 | cut -d'.' -f1,2)
print_status "Python version: $PYTHON_VERSION"

# Install system dependencies if needed
echo "   Installing system dependencies..."
apt-get update -qq
apt-get install -y -qq python3-pip python3-venv > /dev/null 2>&1
print_status "System dependencies installed"

# Create virtual environment
echo "   Creating virtual environment..."
python3 -m venv venv
print_status "Virtual environment created: $BACKEND_DIR/venv"

# Activate and install dependencies
echo "   Installing Python packages (this may take a few minutes)..."
source venv/bin/activate
pip install --upgrade pip -q
pip install -r requirements.txt -q
print_status "Python packages installed"

# Verify .env file exists
if [ ! -f ".env" ]; then
    print_error ".env file not found! Create it with:"
    echo "    cp .env.example .env"
    echo "    nano .env  # Edit with your API keys"
    exit 1
fi
print_status ".env file found"

# Setup Frontend
echo ""
echo "3. Setting up Frontend..."
cd "$FRONTEND_DIR"

# Check Node.js version
NODE_VERSION=$(node --version 2>&1 || echo "not installed")
print_status "Node.js version: $NODE_VERSION"

if [ "$NODE_VERSION" == "not installed" ]; then
    print_warning "Node.js not found. Installing..."
    curl -fsSL https://deb.nodesource.com/setup_18.x | bash - > /dev/null 2>&1
    apt-get install -y nodejs > /dev/null 2>&1
fi

# Install npm packages
echo "   Installing npm packages..."
npm install --silent
print_status "npm packages installed"

# Build frontend for production
echo "   Building frontend for production..."
npm run build
print_status "Frontend built successfully"

# Setup PM2
echo ""
echo "4. Setting up PM2..."
npm install -g pm2 --silent 2>/dev/null || true

# Stop any existing processes
pm2 delete propvision-backend 2>/dev/null || true
pm2 delete propvision-frontend 2>/dev/null || true

# Create PM2 startup script
cat > "$APP_DIR/start.sh" << 'STARTSCRIPT'
#!/bin/bash
cd /var/www/visionv3/backend
source venv/bin/activate
exec uvicorn server:app --host 0.0.0.0 --port 8001
STARTSCRIPT
chmod +x "$APP_DIR/start.sh"

# Start backend with PM2
pm2 start "$APP_DIR/start.sh" --name propvision-backend
print_status "Backend started with PM2"

# For frontend, use serve for production build
npm install -g serve --silent 2>/dev/null || true
pm2 start "serve -s $FRONTEND_DIR/build -l 3000" --name propvision-frontend
print_status "Frontend started with PM2"

# Save PM2 process list
pm2 save
pm2 startup
print_status "PM2 configured for auto-restart"

# Test the setup
echo ""
echo "5. Testing setup..."
sleep 3

BACKEND_STATUS=$(curl -s http://localhost:8001/api/v3/status 2>&1 || echo "FAILED")
if [[ "$BACKEND_STATUS" == *"success"* ]]; then
    print_status "Backend API responding correctly"
else
    print_error "Backend not responding. Check logs: pm2 logs propvision-backend"
fi

# Summary
echo ""
echo "=========================================="
echo "Setup Complete!"
echo "=========================================="
echo ""
echo "Backend running on: http://localhost:8001"
echo "Frontend running on: http://localhost:3000"
echo ""
echo "Commands:"
echo "  pm2 status              - Check process status"
echo "  pm2 logs                - View all logs"
echo "  pm2 restart all         - Restart all services"
echo ""
echo "NEXT STEP - Initialize Database:"
echo "  curl -X POST http://localhost:8001/api/v3/init-database"
echo ""
echo "Or manually trigger a sync:"
echo "  curl -X POST http://localhost:8001/api/v3/sync"
echo ""
