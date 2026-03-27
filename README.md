# PropVision / PickVision

NBA player props analytics platform with AI-powered insights.

## Quick Start

### Prerequisites
- Python 3.11+
- Node.js 18+
- MongoDB 7.0+
- API Keys: Odds API, BallDontLie, Google Gemini

### Backend Setup

```bash
cd backend

# Create virtual environment
python3.11 -m venv venv
source venv/bin/activate  # Linux/Mac
# or: venv\Scripts\activate  # Windows

# Install dependencies
pip install -r requirements.txt

# Copy environment file and add your keys
cp .env.example .env
nano .env  # Edit with your API keys

# Run the server
uvicorn server:app --host 0.0.0.0 --port 8001 --reload
```

### Frontend Setup

```bash
cd frontend

# Install dependencies
yarn install

# Create environment file
echo "REACT_APP_BACKEND_URL=http://localhost:8001" > .env

# Start development server
yarn start
```

### Initialize Database

After the backend is running, populate the database:

```bash
# Option 1: Import existing dump (fastest)
mongorestore --db=pick_vision dump/pick_vision/

# Option 2: Run sync endpoints
curl -X POST http://localhost:8001/api/v3/sync
curl -X POST http://localhost:8001/api/hub/bdl-sync
```

---

## Project Structure

```
/app
├── backend/
│   ├── config/              # Settings, API versioning
│   ├── data/                # Static data (milestones, context)
│   ├── middleware/          # Rate limiting, tracing
│   ├── models/              # Pydantic schemas
│   ├── repositories/        # Database access layer
│   ├── routes/              # API endpoints
│   ├── services/            # Business logic
│   │   └── engines/         # Core orchestration engines
│   ├── scripts/             # Utility scripts
│   ├── utils/               # Helper functions
│   └── server.py            # FastAPI application
│
├── frontend/
│   ├── src/
│   │   ├── components/      # React components
│   │   ├── context/         # Auth context
│   │   ├── hooks/           # Custom React hooks
│   │   ├── lib/             # Utilities
│   │   ├── pages/           # Page components
│   │   ├── providers/       # Query providers
│   │   ├── services/        # API client (DataService.js)
│   │   └── styles/          # CSS
│   └── public/
│
├── scripts/                 # Deployment scripts
├── memory/                  # PRD and documentation
└── docs/                    # Additional documentation
```

---

## MongoDB Collections

### Authoritative (Source of Truth)
| Collection | Purpose |
|------------|---------|
| `nba_master_hub_2026` | Player profiles, stats, game logs |
| `dg_master_roster` | Active roster, team mappings |
| `bdl_player_mapping` | BallDontLie ID mappings |
| `odds_api_mapping_master` | Odds API name normalization |
| `player_photos` | Headshot URLs |
| `dvp_rankings` | Defense vs Position data |
| `users` | User accounts |

### Derived (Rebuilt on Sync)
| Collection | Purpose |
|------------|---------|
| `dg_cached_board` | Frontend-ready player data |
| `dg_live_props` | Live betting props |
| `dg_parlay_builder` | Parlay recommendations |

### Cache (Ephemeral)
| Collection | Purpose |
|------------|---------|
| `dg_*_cache` | Various API response caches |
| `ticker_cache` | News ticker data |

---

## API Endpoints

See [API_SURFACE.md](./API_SURFACE.md) for complete documentation.

### Key Endpoints
- `GET /api/v3/board` - Get all players and props
- `GET /api/v3/player-with-badges/{name}` - Player detail with intel suite
- `GET /api/v3/war-zone` - High-risk picks (demons)
- `GET /api/v3/safe-haven` - Safe picks (goblins)
- `GET /api/v3/parlay-builder` - Parlay recommendations
- `POST /api/v3/sync` - Trigger data sync

---

## Scheduled Jobs

The backend runs these scheduled jobs (APScheduler):

| Job | Time (EST) | Description |
|-----|------------|-------------|
| Daily Sync | 4:00 AM | Full BDL + Odds sync |
| Roster Sync | Sunday 00:00 | Weekly roster update |
| Ticker Sync | 4:15 AM | News refresh |

---

## Environment Variables

### Required
```
MONGO_URL=mongodb://localhost:27017
DB_NAME=pick_vision
ODDS_API_KEY=xxx
BDL_API_KEY=xxx
GOOGLE_API_KEY=xxx
JWT_SECRET=xxx
```

### Optional
```
CORS_ORIGINS="*"
RATE_LIMITING_ENABLED=true
```

---

## Production Deployment

See [DEPLOYMENT_GUIDE.md](./DEPLOYMENT_GUIDE.md) for complete instructions.

Quick steps:
1. Install MongoDB, Python 3.11, Node.js 20, Nginx
2. Clone repo to `/var/www/propvision`
3. Set up backend venv and install requirements
4. Copy `.env.example` to `.env` and add keys
5. Build frontend: `yarn build`
6. Configure Nginx reverse proxy
7. Start backend with PM2
8. Import database or run sync

---

## Development

### Running Tests
```bash
cd backend
pytest tests/
```

### Linting
```bash
# Backend
ruff check backend/

# Frontend
cd frontend && yarn lint
```

### Adding New Features
1. Create model in `backend/models/`
2. Add repository in `backend/repositories/`
3. Create service in `backend/services/`
4. Add route in `backend/routes/`
5. Update frontend `DataService.js`

---

## Contributing

1. Fork the repository
2. Create feature branch: `git checkout -b feature/my-feature`
3. Commit changes: `git commit -am 'Add my feature'`
4. Push to branch: `git push origin feature/my-feature`
5. Submit pull request

---

## License

Proprietary - All rights reserved.
