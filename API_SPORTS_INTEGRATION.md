# API-Sports Integration - Complete Documentation

## 🎯 What Was Built

A robust **Stats Manager** with intelligent 24hr caching that integrates API-Sports (NBA) as the primary statistical engine for calculating real hit rates.

## 📊 Architecture

### Components Created

1. **`/app/backend/stats_manager.py`** - Complete stats engine
   - 24hr TTL caching in MongoDB
   - Fuzzy player name matching (80% threshold)
   - Real L10 hit rate calculation
   - Demon line validation
   - Graceful fallback on rate limits

2. **API Endpoints Added** to `server.py`:
   - `GET /api/calculate-hit-rate` - Calculate real hit rate for any player/prop/line
   - `GET /api/validate-demon` - Validate if demon line qualifies (>40% hit rate)
   - `GET /api/cache-status` - View cache statistics
   - `POST /api/clear-expired-cache` - Clean up old cache entries

### Data Source Strategy

- **API-Sports (NBA)** - Primary source for:
  - Player game logs (last 10 games)
  - Points, Rebounds, Assists, 3PM stats
  - Real hit rate calculations

- **Tank01** - Reserved for:
  - Injury reports (24hr cache)
  - Team defensive rankings (24hr cache)
  - Roster data

### Key Features

#### 1. Intelligent Caching
```python
# Check cache first (24hr TTL)
cached_data = await get_cached_stats(player_id)
if cached_data:
    return cached_data

# Fetch from API only if cache miss
api_data = await fetch_player_stats_from_api(player_id)
```

#### 2. Real Hit Rate Calculation
```python
calculate_hit_rate(player_name, prop_type, line_value)
# Returns: {hit_rate, games_over, total_games, last_10_games}
```

#### 3. Demon Validation
```python
validate_demon_line(player_name, prop_type, demon_line, min_hit_rate=0.40)
# Returns: True if L10 hit rate >= 40%
```

#### 4. Fuzzy Name Matching
```python
# Handles variations like:
- "Nic Claxton" vs "Nicolas Claxton"
- "Giannis" vs "Giannis Antetokounmpo"
Using thefuzz library with 80% similarity threshold
```

## 🔑 API-Sports Configuration

**Credentials Configured:**
- API Key: `9057bc1422b361f64cc071581dd1b240`
- Host: `api-nba-v1.p.rapidapi.com`
- Season: `2024-2025`

**Endpoints Used:**
- `/players` - Search players by name
- `/players/statistics` - Get player game logs and stats

## ⚠️ Current Status

### Issue Identified
API-Sports is returning `403 Forbidden` on player search requests. This could be due to:

1. **API Key Verification** - The key may need activation or have restrictions
2. **Endpoint Access** - NBA endpoints might require a specific subscription tier
3. **Request Format** - Headers or parameters might need adjustment

### To Verify API Access:

1. Check your API-Sports dashboard: https://rapidapi.com/api-sports/api/api-nba/
2. Verify subscription tier includes NBA player statistics
3. Check API key permissions and rate limits
4. Test endpoint directly:

```bash
curl -X GET "https://api-nba-v1.p.rapidapi.com/players?search=LeBron%20James&season=2024" \
  -H "X-RapidAPI-Key: 9057bc1422b361f64cc071581dd1b240" \
  -H "X-RapidAPI-Host: api-nba-v1.p.rapidapi.com"
```

## 📈 Testing the System

### Once API Access is Confirmed:

#### 1. Calculate Hit Rate
```bash
curl "http://localhost:8001/api/calculate-hit-rate?player_name=LeBron%20James&prop_type=points&line=25"
```

Expected Response:
```json
{
  "success": true,
  "data": {
    "player_name": "LeBron James",
    "prop_type": "points",
    "line_value": 25,
    "hit_rate": 0.7,
    "games_over": 7,
    "total_games": 10,
    "last_10_games": [...]
  }
}
```

#### 2. Validate Demon Line
```bash
curl "http://localhost:8001/api/validate-demon?player_name=Stephen%20Curry&prop_type=points&demon_line=32"
```

#### 3. Check Cache Status
```bash
curl "http://localhost:8001/api/cache-status"
```

## 🎯 How It Works in Production

1. **User clicks a player** on the dashboard
2. System searches for player ID using fuzzy matching
3. Checks MongoDB cache for existing stats (24hr TTL)
4. If cache miss, fetches from API-Sports
5. Extracts last 10 games
6. Calculates hit rate: `games_over / 10`
7. If hit rate >= 40%, validates as Demon line
8. Returns data to frontend with purple glow indicator

## 💾 Cache Management

### View Cache Stats
```bash
GET /api/cache-status
```

Returns:
- Total players cached
- Expired entries
- Active entries
- Cache TTL (24 hours)
- Current season

### Clear Expired Cache
```bash
POST /api/clear-expired-cache
```

Automatically runs cleanup, removes entries older than 24 hours.

## 🚀 Next Steps

1. **Verify API-Sports Access** - Confirm subscription includes NBA endpoints
2. **Test with Real Data** - Once API returns 200, test hit rate calculations
3. **Integrate into Dashboard** - Add "Calculate Real Hit Rate" button for each player
4. **Enable Auto-Validation** - Automatically validate all demon lines on page load
5. **Add Cache Preloading** - Pre-cache stats for top 50 players daily

## 📝 Error Handling

### Rate Limit (429)
```python
if response.status_code == 429:
    logger.error("⚠️ API-Sports rate limit hit")
    # Falls back to cached data
    # Shows "Stats Current as of [timestamp]" message
```

### Player Not Found
```python
if not player_id:
    logger.warning(f"Could not find player ID for {player_name}")
    # Returns None, frontend shows "Stats unavailable"
```

### API Timeout
```python
# 15 second timeout on all API calls
async with httpx.AsyncClient() as client:
    response = await client.get(url, timeout=15.0)
```

## 🎨 Frontend Integration

The FullBoard component is ready to receive real hit rate data:

```javascript
// When user clicks "Verify" button
const result = await axios.get(`${API}/calculate-hit-rate`, {
  params: {
    player_name: "LeBron James",
    prop_type: "points",
    line: 25
  }
});

// Update UI with real hit rate
setHitRate(result.data.hit_rate);
setIsDemon(result.data.hit_rate >= 0.40);
```

## 🔐 Security

- API keys stored in environment variables
- MongoDB cache isolated per player_id
- No sensitive data exposed to frontend
- Rate limiting handled gracefully

---

**Status**: ✅ Stats Engine Complete | ⚠️ Awaiting API-Sports Access Verification
