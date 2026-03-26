# PropVision Production Deployment Guide

## Complete Fresh Install on Ubuntu VPS (Namecheap Pulsar)

This guide assumes you're starting with a fresh Ubuntu server.

---

## ⚠️ COMMON DATABASE ISSUES AND SOLUTIONS

The most common deployment failure is MongoDB connection issues. Here's what to check:

### Issue 1: MongoDB not accepting connections
```bash
# Check if MongoDB is running
sudo systemctl status mongod

# If not running, check logs
sudo tail -100 /var/log/mongodb/mongod.log

# Common fix: MongoDB might not have started due to lock file
sudo rm /tmp/mongodb-27017.sock
sudo systemctl restart mongod
```

### Issue 2: Authentication failure
```bash
# If you created a user but can't connect, ensure authSource is correct
# The authSource should be the database where the user was created
mongosh "mongodb://propvision_user:PASSWORD@localhost:27017/pick_vision?authSource=pick_vision"

# If that fails, try without auth first to debug:
mongosh "mongodb://localhost:27017/pick_vision"
```

### Issue 3: Database is empty
```bash
# Check collection counts
mongosh pick_vision --eval "db.dg_cached_board.countDocuments()"
mongosh pick_vision --eval "db.nba_master_hub_2026.countDocuments()"

# If 0, you need to run the sync endpoints (see Step 12)
```

### Issue 4: Python can't connect to MongoDB
```bash
# Test Python connection
cd /var/www/propvision/backend
source venv/bin/activate
python3 -c "
from motor.motor_asyncio import AsyncIOMotorClient
import os
from dotenv import load_dotenv
load_dotenv()

mongo_url = os.environ.get('MONGO_URL')
print(f'Connecting to: {mongo_url}')

client = AsyncIOMotorClient(mongo_url)
db = client[os.environ.get('DB_NAME', 'pick_vision')]
import asyncio
async def test():
    try:
        collections = await db.list_collection_names()
        print('SUCCESS! Collections:', collections)
    except Exception as e:
        print(f'ERROR: {e}')
asyncio.run(test())
"
```

---

## STEP 1: Initial Server Setup

SSH into your server and run these commands:

```bash
# Update system
sudo apt update && sudo apt upgrade -y

# Install essential tools
sudo apt install -y curl wget git build-essential software-properties-common

# Set timezone (optional but recommended)
sudo timedatectl set-timezone America/New_York
```

---

## STEP 2: Install MongoDB 7.0

**This is critical - MongoDB must be installed correctly!**

```bash
# Import MongoDB GPG key
curl -fsSL https://www.mongodb.org/static/pgp/server-7.0.asc | sudo gpg -o /usr/share/keyrings/mongodb-server-7.0.gpg --dearmor

# Add MongoDB repo (for Ubuntu 22.04)
echo "deb [ arch=amd64,arm64 signed-by=/usr/share/keyrings/mongodb-server-7.0.gpg ] https://repo.mongodb.org/apt/ubuntu jammy/mongodb-org/7.0 multiverse" | sudo tee /etc/apt/sources.list.d/mongodb-org-7.0.list

# For Ubuntu 24.04, use this instead:
# echo "deb [ arch=amd64,arm64 signed-by=/usr/share/keyrings/mongodb-server-7.0.gpg ] https://repo.mongodb.org/apt/ubuntu noble/mongodb-org/7.0 multiverse" | sudo tee /etc/apt/sources.list.d/mongodb-org-7.0.list

# Update and install MongoDB
sudo apt update
sudo apt install -y mongodb-org

# Start MongoDB and enable on boot
sudo systemctl start mongod
sudo systemctl enable mongod

# Verify MongoDB is running
sudo systemctl status mongod
```

**Test MongoDB connection:**
```bash
mongosh --eval "db.runCommand({ connectionStatus: 1 })"
```

You should see `ok: 1` in the output.

---

## STEP 3: Create MongoDB Database and User

```bash
# Connect to MongoDB
mongosh

# In the MongoDB shell, run these commands:
```

```javascript
// Switch to pick_vision database
use pick_vision

// Create a user for the app (CHANGE THE PASSWORD!)
db.createUser({
  user: "propvision_user",
  pwd: "YOUR_SECURE_PASSWORD_HERE",
  roles: [{ role: "readWrite", db: "pick_vision" }]
})

// Create required collections
db.createCollection("dg_cached_board")
db.createCollection("nba_master_hub_2026")
db.createCollection("users")
db.createCollection("dvp_rankings")
db.createCollection("team_pace_rankings")

// Create indexes for performance
db.dg_cached_board.createIndex({ "player_name": 1 })
db.dg_cached_board.createIndex({ "game_id": 1 })
db.nba_master_hub_2026.createIndex({ "display_name": 1 })
db.nba_master_hub_2026.createIndex({ "bdl_id": 1 })
db.users.createIndex({ "email": 1 }, { unique: true })

// Verify collections were created
show collections

// Exit MongoDB shell
exit
```

---

## STEP 4: Install Python 3.11

```bash
# Add deadsnakes PPA for Python 3.11
sudo add-apt-repository ppa:deadsnakes/ppa -y
sudo apt update

# Install Python 3.11
sudo apt install -y python3.11 python3.11-venv python3.11-dev

# Verify installation
python3.11 --version
```

---

## STEP 5: Install Node.js 20

```bash
# Install Node.js 20 via NodeSource
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
sudo apt install -y nodejs

# Verify installation
node --version
npm --version

# Install yarn globally
sudo npm install -g yarn

# Install PM2 globally (process manager)
sudo npm install -g pm2
```

---

## STEP 6: Install Nginx

```bash
sudo apt install -y nginx

# Start and enable Nginx
sudo systemctl start nginx
sudo systemctl enable nginx
```

---

## STEP 7: Clone Your Repository

```bash
# Create app directory
sudo mkdir -p /var/www/propvision
sudo chown $USER:$USER /var/www/propvision

# Clone your repo
cd /var/www/propvision
git clone https://github.com/YOUR_USERNAME/YOUR_REPO.git .

# Or if repo is private:
# git clone https://YOUR_TOKEN@github.com/YOUR_USERNAME/YOUR_REPO.git .
```

---

## STEP 8: Setup Backend

```bash
cd /var/www/propvision/backend

# Create virtual environment
python3.11 -m venv venv

# Activate virtual environment
source venv/bin/activate

# Upgrade pip
pip install --upgrade pip

# Install dependencies
pip install -r requirements.txt

# Install emergent integrations (if used)
pip install emergentintegrations --extra-index-url https://d33sy5i8bnduwe.cloudfront.net/simple/
```

### Create Backend .env file:

```bash
cat > /var/www/propvision/backend/.env << 'EOF'
# MongoDB Connection - UPDATE PASSWORD!
MONGO_URL=mongodb://propvision_user:YOUR_SECURE_PASSWORD_HERE@localhost:27017/pick_vision?authSource=pick_vision
DB_NAME=pick_vision

# API Keys - ADD YOUR KEYS!
BDL_API_KEY=your_balldontlie_api_key_here
ODDS_API_KEY=your_odds_api_key_here
GOOGLE_API_KEY=your_google_gemini_api_key_here

# JWT Secret (generate a random string)
JWT_SECRET=your_random_jwt_secret_here_make_it_long_and_random

# Environment
ENV=production
EOF
```

**IMPORTANT: Edit the .env file with your actual values:**
```bash
nano /var/www/propvision/backend/.env
```

---

## STEP 9: Setup Frontend

```bash
cd /var/www/propvision/frontend

# Install dependencies
yarn install

# Create frontend .env file
cat > /var/www/propvision/frontend/.env << 'EOF'
REACT_APP_BACKEND_URL=https://yourdomain.com
EOF
```

**Update with your actual domain:**
```bash
nano /var/www/propvision/frontend/.env
```

### Build Frontend:

```bash
yarn build
```

---

## STEP 10: Configure Nginx

```bash
sudo nano /etc/nginx/sites-available/propvision
```

Paste this configuration (update YOUR_DOMAIN):

```nginx
server {
    listen 80;
    server_name YOUR_DOMAIN.com www.YOUR_DOMAIN.com;

    # Frontend (React build)
    location / {
        root /var/www/propvision/frontend/build;
        index index.html;
        try_files $uri $uri/ /index.html;
    }

    # Backend API proxy
    location /api {
        proxy_pass http://127.0.0.1:8001;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection 'upgrade';
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_cache_bypass $http_upgrade;
        proxy_read_timeout 300s;
        proxy_connect_timeout 75s;
    }

    # Increase max body size for file uploads
    client_max_body_size 10M;
}
```

Enable the site:

```bash
# Enable site
sudo ln -s /etc/nginx/sites-available/propvision /etc/nginx/sites-enabled/

# Remove default site
sudo rm /etc/nginx/sites-enabled/default

# Test Nginx config
sudo nginx -t

# Reload Nginx
sudo systemctl reload nginx
```

---

## STEP 11: Setup PM2 for Backend

```bash
cd /var/www/propvision/backend

# Create PM2 ecosystem file
cat > ecosystem.config.js << 'EOF'
module.exports = {
  apps: [{
    name: 'propvision-backend',
    script: 'venv/bin/uvicorn',
    args: 'server:app --host 0.0.0.0 --port 8001',
    cwd: '/var/www/propvision/backend',
    interpreter: 'none',
    env: {
      PATH: '/var/www/propvision/backend/venv/bin:' + process.env.PATH
    },
    watch: false,
    max_memory_restart: '500M',
    error_file: '/var/www/propvision/logs/backend-error.log',
    out_file: '/var/www/propvision/logs/backend-out.log',
    log_file: '/var/www/propvision/logs/backend-combined.log',
    time: true
  }]
};
EOF

# Create logs directory
mkdir -p /var/www/propvision/logs

# Start the backend with PM2
pm2 start ecosystem.config.js

# Save PM2 process list
pm2 save

# Setup PM2 to start on boot
pm2 startup
# Run the command it outputs!
```

---

## STEP 12: Initialize Database with Data

**This is crucial - you need to populate the database!**

```bash
cd /var/www/propvision/backend
source venv/bin/activate

# Test database connection first
python3 -c "
from motor.motor_asyncio import AsyncIOMotorClient
import os
from dotenv import load_dotenv
load_dotenv()

client = AsyncIOMotorClient(os.environ.get('MONGO_URL'))
db = client[os.environ.get('DB_NAME')]
import asyncio
async def test():
    collections = await db.list_collection_names()
    print('Connected! Collections:', collections)
asyncio.run(test())
"
```

If connection works, trigger a full data sync:

```bash
# The app has sync endpoints - trigger them via curl after the server starts
# First, make sure backend is running:
pm2 status

# Then trigger sync (replace with your domain or use localhost):
curl -X POST http://localhost:8001/api/v3/sync
```

---

## STEP 13: Setup SSL with Let's Encrypt

```bash
# Install certbot
sudo apt install -y certbot python3-certbot-nginx

# Get SSL certificate (replace with your domain)
sudo certbot --nginx -d YOUR_DOMAIN.com -d www.YOUR_DOMAIN.com

# Certbot will automatically update Nginx config

# Test auto-renewal
sudo certbot renew --dry-run
```

---

## STEP 14: Verify Everything Works

```bash
# Check MongoDB
sudo systemctl status mongod

# Check backend
pm2 status
pm2 logs propvision-backend --lines 50

# Check Nginx
sudo systemctl status nginx

# Test API endpoint
curl http://localhost:8001/api/health

# Test from outside (replace with your domain)
curl https://YOUR_DOMAIN.com/api/health
```

---

## Troubleshooting

### MongoDB Connection Issues

```bash
# Check if MongoDB is running
sudo systemctl status mongod

# Check MongoDB logs
sudo tail -100 /var/log/mongodb/mongod.log

# Test connection with credentials
mongosh "mongodb://propvision_user:YOUR_PASSWORD@localhost:27017/pick_vision?authSource=pick_vision"
```

### Backend Won't Start

```bash
# Check PM2 logs
pm2 logs propvision-backend --lines 100

# Check if port 8001 is in use
sudo lsof -i :8001

# Try running manually to see errors
cd /var/www/propvision/backend
source venv/bin/activate
uvicorn server:app --host 0.0.0.0 --port 8001
```

### Frontend 404 Errors

```bash
# Make sure build exists
ls -la /var/www/propvision/frontend/build

# Rebuild if needed
cd /var/www/propvision/frontend
yarn build
```

### API Returns Empty Data

```bash
# Check if database has data
mongosh pick_vision --eval "db.dg_cached_board.countDocuments()"
mongosh pick_vision --eval "db.nba_master_hub_2026.countDocuments()"

# If counts are 0, trigger sync:
curl -X POST https://YOUR_DOMAIN.com/api/v3/sync
```

---

## Quick Reference Commands

```bash
# Backend
pm2 start propvision-backend
pm2 stop propvision-backend
pm2 restart propvision-backend
pm2 logs propvision-backend

# MongoDB
sudo systemctl start mongod
sudo systemctl stop mongod
sudo systemctl restart mongod

# Nginx
sudo systemctl reload nginx
sudo nginx -t

# View logs
pm2 logs
sudo tail -f /var/log/nginx/error.log
sudo tail -f /var/log/mongodb/mongod.log
```

---

## Environment Variables Summary

### Backend (.env)
```
MONGO_URL=mongodb://propvision_user:PASSWORD@localhost:27017/pick_vision?authSource=pick_vision
DB_NAME=pick_vision
BDL_API_KEY=your_key
ODDS_API_KEY=your_key
GOOGLE_API_KEY=your_key
JWT_SECRET=random_string
ENV=production
```

### Frontend (.env)
```
REACT_APP_BACKEND_URL=https://yourdomain.com
```
