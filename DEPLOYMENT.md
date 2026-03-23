# PropVision - Standalone Deployment Guide

This application is fully standalone and can be deployed on any server infrastructure.

## Prerequisites

- Python 3.11+
- Node.js 18+
- MongoDB 6.0+ (local) OR MongoDB Atlas (cloud)
- A Google Cloud account with Gemini API enabled

## MongoDB Configuration

### Option A: Local MongoDB
```bash
MONGO_URL=mongodb://localhost:27017
```

### Option B: MongoDB Atlas (Recommended for Production)

1. Create a MongoDB Atlas cluster at https://cloud.mongodb.com
2. **Important:** Add your server's IP address to the Network Access whitelist
3. Get your connection string from Atlas (should start with `mongodb+srv://`)

```bash
MONGO_URL=mongodb+srv://username:password@cluster.xxxxx.mongodb.net/?retryWrites=true&w=majority
```

**Note:** The application automatically detects Atlas connections and enables:
- TLS encryption
- Increased connection timeouts (30s)
- Connection pooling
- Retry logic for writes and reads

### Troubleshooting Atlas Connections

If you see `timed out` errors:
1. **Check IP Whitelist:** Atlas → Network Access → Add your server IP
2. **Check Credentials:** Ensure username/password in connection string are correct
3. **Check DNS:** Ensure your server can resolve `*.mongodb.net` domains
4. **Check Firewall:** Ensure port 27017 is not blocked outbound

## Environment Variables

### Backend (`/backend/.env`)

```bash
# Database
MONGO_URL=mongodb://localhost:27017
DB_NAME=pick_vision

# CORS (adjust for your domain)
CORS_ORIGINS="https://yourdomain.com"

# Supabase Auth (optional - for user authentication)
SUPABASE_URL="your-supabase-url"
SUPABASE_ANON_KEY="your-anon-key"
SUPABASE_SERVICE_ROLE_KEY="your-service-role-key"
JWT_SECRET="generate-a-secure-random-string"

# External APIs
ODDS_API_KEY="your-odds-api-key"           # https://the-odds-api.com/
BDL_API_KEY="your-balldontlie-api-key"     # https://balldontlie.io/
GOOGLE_API_KEY="your-gemini-api-key"       # https://aistudio.google.com/

# Optional APIs
TANK01_API_KEY="your-tank01-key"           # For additional sports data
```

### Frontend (`/frontend/.env`)

```bash
REACT_APP_BACKEND_URL=https://api.yourdomain.com
REACT_APP_SUPABASE_URL=your-supabase-url
REACT_APP_SUPABASE_ANON_KEY=your-supabase-anon-key
```

## API Keys Required

| Service | Purpose | Get Key At |
|---------|---------|------------|
| **The Odds API** | Live betting lines & props | https://the-odds-api.com/ |
| **BallDontLie** | NBA stats, schedules, DvP rankings | https://balldontlie.io/ |
| **Google Gemini** | AI-powered insights | https://aistudio.google.com/ |
| **Supabase** | User authentication (optional) | https://supabase.com/ |

## Installation

### Backend

```bash
cd backend
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### Frontend

```bash
cd frontend
npm install  # or yarn install
```

## Running Locally

### Backend
```bash
cd backend
uvicorn server:app --host 0.0.0.0 --port 8001 --reload
```

### Frontend
```bash
cd frontend
npm start  # Runs on port 3000
```

## Production Deployment

### Docker (Recommended)

Create a `docker-compose.yml`:

```yaml
version: '3.8'

services:
  mongodb:
    image: mongo:6.0
    volumes:
      - mongo_data:/data/db
    ports:
      - "27017:27017"

  backend:
    build: ./backend
    ports:
      - "8001:8001"
    environment:
      - MONGO_URL=mongodb://mongodb:27017
      - DB_NAME=pick_vision
    depends_on:
      - mongodb

  frontend:
    build: ./frontend
    ports:
      - "3000:3000"
    environment:
      - REACT_APP_BACKEND_URL=http://backend:8001

volumes:
  mongo_data:
```

### Backend Dockerfile

```dockerfile
FROM python:3.11-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
EXPOSE 8001

CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8001"]
```

### Frontend Dockerfile

```dockerfile
FROM node:18-alpine as build

WORKDIR /app
COPY package*.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM nginx:alpine
COPY --from=build /app/build /usr/share/nginx/html
EXPOSE 3000
```

## Scheduled Jobs

The backend runs several scheduled jobs via APScheduler:

| Time (EST) | Job | Description |
|------------|-----|-------------|
| 4:00 AM | Daily Full Sync | Fetch odds, stats, injuries |
| 4:25 AM | BDL Game Logs | Player game-by-game stats for hit rates |
| 5:00 AM | Morning Props | Refresh prop lines |
| Sunday 00:00 | Roster Sync | Weekly team mappings |

## Architecture

```
/app
├── backend/
│   ├── server.py              # FastAPI main entry
│   ├── adaptive_sync_engine.py # Odds/props sync
│   ├── demon_goblin_engine.py  # Pick classification
│   ├── services/
│   │   ├── dvp_service.py      # Defense vs Position
│   │   ├── picks_getter_service.py
│   │   └── bdl_game_logs_sync.py
│   └── routes/
│       ├── tiers.py            # War Zone, Safe Haven APIs
│       └── cached_data.py      # Player detail APIs
└── frontend/
    └── src/
        ├── pages/Dashboard.jsx
        └── components/
```

## Monitoring

Health check endpoint: `GET /api/v3/status`

Returns:
```json
{
  "success": true,
  "data": {
    "last_sync": "2026-03-23T04:00:00Z",
    "unique_players": 450,
    "season": "2025-26"
  }
}
```

## Support

This is a standalone application with no external dependencies on any proprietary platforms.

---

## First-Time Setup / Database Initialization

After deploying to a new environment, the database will be empty. You need to run the initialization to populate it with player data, game logs, and odds.

### Option 1: API Endpoint (Recommended)

After the app is running, call the initialization endpoint:

```bash
curl -X POST https://your-domain.com/api/v3/init-database
```

This will:
1. Sync ~600 NBA players from BallDontLie
2. Fetch game-by-game stats for hit rate calculations
3. Load DvP (Defense vs Position) rankings
4. Sync current odds and props
5. Create database indexes

**Expected time:** 5-10 minutes (depending on API rate limits)

### Option 2: Command Line Script

```bash
cd backend
python scripts/init_database.py
```

### Verifying Initialization

Check the health endpoint:
```bash
curl https://your-domain.com/api/v3/status
```

You should see:
```json
{
  "success": true,
  "data": {
    "unique_players": 600+,
    "season": "2025-26"
  }
}
```

### If Database is Empty

If you see 0 players or empty picks:
1. Check that your server IP (203.161.43.198) is whitelisted in MongoDB Atlas
2. Run the init endpoint: `POST /api/v3/init-database`
3. Wait for the sync to complete (check logs)
4. Refresh the dashboard
