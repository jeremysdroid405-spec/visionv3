# Deployment Checklist

## Pre-Deployment Verification

### 1. Environment Variables
- [ ] `MONGO_URL` - MongoDB connection string
- [ ] `DB_NAME` - Database name (`pick_vision`)
- [ ] `ODDS_API_KEY` - The Odds API key
- [ ] `BDL_API_KEY` - BallDontLie API key
- [ ] `GOOGLE_API_KEY` - Google Gemini API key
- [ ] `JWT_SECRET` - JWT signing secret

### 2. Database Connection
```bash
# Test MongoDB connection
mongosh "your_connection_string" --eval "db.adminCommand('ping')"

# Verify collections exist
mongosh pick_vision --eval "db.getCollectionNames().length"
# Should return: 30+
```

### 3. Collection Data
```bash
# Check critical collections have data
mongosh pick_vision --eval "
print('nba_master_hub_2026: ' + db.nba_master_hub_2026.countDocuments());
print('dg_cached_board: ' + db.dg_cached_board.countDocuments());
print('dg_live_props: ' + db.dg_live_props.countDocuments());
"
# Expected: 500+, 50+, 1000+
```

### 4. Indexes
```bash
# Run index script
cd /var/www/propvision/backend
python scripts/ensure_indexes.py
```

### 5. Backend Startup
```bash
# Start backend
pm2 start ecosystem.config.js

# Check status
pm2 status

# Verify no errors in logs
pm2 logs propvision-backend --lines 50 | grep -i error
```

### 6. API Health
```bash
# Test core endpoints
curl http://localhost:8001/api/v3/board | jq '.players_count'
curl http://localhost:8001/api/v3/sync-status | jq '.success'
curl http://localhost:8001/api/v3/war-zone | jq '.picks | length'
```

---

## Deployment Steps

### 1. Pull Latest Code
```bash
cd /var/www/propvision
git pull origin main
```

### 2. Install Backend Dependencies
```bash
cd backend
source venv/bin/activate
pip install -r requirements.txt
```

### 3. Install Frontend Dependencies
```bash
cd frontend
yarn install
```

### 4. Build Frontend
```bash
yarn build
```

### 5. Run Index Script
```bash
cd backend
python scripts/ensure_indexes.py
```

### 6. Restart Services
```bash
# Restart backend
pm2 restart propvision-backend

# Reload Nginx
sudo nginx -t && sudo systemctl reload nginx
```

### 7. Verify Deployment
```bash
# Test external URL
curl https://yourdomain.com/api/v3/board | jq '.success'
```

---

## Scheduled Jobs Verification

### Check APScheduler Jobs
The backend automatically starts these scheduled jobs on startup:

| Job | Time (EST) | Verify Command |
|-----|------------|----------------|
| daily_sync | 4:00 AM | Check logs at 4:05 AM |
| ticker_sync | 4:15 AM | Check ticker_cache collection |
| badge_sync | 4:20 AM | Check context badges on players |
| bdl_game_logs_sync | 4:25 AM | Check nba_master_hub_2026 |

### Manual Job Trigger
```bash
# Trigger sync manually
curl -X POST https://yourdomain.com/api/v3/sync

# Check sync status
curl https://yourdomain.com/api/v3/sync-status
```

---

## Rollback Plan

### If Backend Fails
```bash
# Stop backend
pm2 stop propvision-backend

# Check logs
pm2 logs propvision-backend --lines 100

# Restore previous version
git checkout HEAD~1
pip install -r requirements.txt
pm2 restart propvision-backend
```

### If Database Issues
```bash
# Restore from backup
mongorestore --db=pick_vision dump/pick_vision/

# Or sync fresh data
curl -X POST http://localhost:8001/api/v3/sync
```

---

## Post-Deployment Monitoring

### Key Metrics to Watch
1. **API Response Times** - Board endpoint should return < 500ms
2. **Error Rate** - Check `pm2 logs` for errors
3. **Memory Usage** - `pm2 monit`
4. **Database Connections** - Check MongoDB Atlas metrics

### Log Locations
- Backend: `pm2 logs propvision-backend`
- Nginx: `/var/log/nginx/error.log`
- MongoDB: `/var/log/mongodb/mongod.log`

---

## Emergency Contacts
- MongoDB Atlas: cloud.mongodb.com
- BallDontLie: api.balldontlie.io/status
- Odds API: the-odds-api.com/status

---

## Final Checklist

- [ ] All environment variables set
- [ ] Database connected and has data
- [ ] Indexes created
- [ ] Backend starts without errors
- [ ] Frontend builds successfully
- [ ] API endpoints return data
- [ ] Scheduled jobs registered
- [ ] Nginx configured correctly
- [ ] SSL certificate valid
- [ ] No debug code remaining

**Status: READY / NOT READY**
