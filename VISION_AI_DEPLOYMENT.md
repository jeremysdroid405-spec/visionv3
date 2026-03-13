# Vision AI Integration - Deployment Guide

## Overview

The Vision AI system generates "badass" sports betting insights using Claude Sonnet 4.5. It analyzes player props and produces sharp, aggressive 1-sentence insights for pro bettors.

## Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                        Vision AI Integration                         │
├─────────────────────────────────────────────────────────────────────┤
│                                                                      │
│  Option A: FastAPI Direct (RECOMMENDED - Already Deployed)           │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐           │
│  │ Daily Sync   │───▶│ Vision AI    │───▶│ Claude       │           │
│  │ (4:00 AM)    │    │ Service      │    │ Sonnet 4.5   │           │
│  └──────────────┘    └──────────────┘    └──────────────┘           │
│         │                   │                                        │
│         ▼                   ▼                                        │
│  ┌──────────────┐    ┌──────────────┐                               │
│  │ MongoDB      │◀───│ Update       │                               │
│  │ daily_insights│    │ insight_     │                               │
│  │              │    │ summary      │                               │
│  └──────────────┘    └──────────────┘                               │
│                                                                      │
│  Option B: Supabase Edge Function (Optional)                         │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐           │
│  │ FastAPI      │───▶│ Edge         │───▶│ Claude       │           │
│  │ Trigger      │    │ Function     │    │ API Direct   │           │
│  └──────────────┘    └──────────────┘    └──────────────┘           │
│                                                                      │
└─────────────────────────────────────────────────────────────────────┘
```

## Option A: FastAPI Direct (Already Deployed)

The Vision AI service is already integrated into your FastAPI backend using the Emergent LLM key.

### API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/v3/vision/generate-insight` | POST | Generate single AI insight |
| `/api/v3/vision/trigger-batch` | POST | Batch generate for all eligible players |
| `/api/v3/vision/status` | GET | Check Vision AI service status |

### Test Single Insight

```bash
curl -X POST https://best-bet-finder-1.preview.emergentagent.com/api/v3/vision/generate-insight \
  -H "Content-Type: application/json" \
  -d '{
    "player_name": "Kevin Durant",
    "stat_type": "points",
    "current_line": 25.5,
    "l10_rate": 80,
    "pace_factor": 1.05,
    "fatigue": "Normal",
    "usage_bump": 0,
    "volatility": "Med",
    "is_demon": true,
    "is_goblin": false
  }'
```

### Trigger Batch Processing

```bash
curl -X POST https://best-bet-finder-1.preview.emergentagent.com/api/v3/vision/trigger-batch
```

### Check Status

```bash
curl https://best-bet-finder-1.preview.emergentagent.com/api/v3/vision/status
```

## Option B: Supabase Edge Function (Manual Deployment)

If you prefer to use Supabase Edge Functions instead:

### Prerequisites

1. Supabase CLI installed: `npm install -g supabase`
2. Logged into Supabase: `supabase login`
3. Anthropic API key (for direct Claude access)

### Step 1: Deploy the Edge Function

```bash
cd /app/supabase/functions
supabase functions deploy generate-vision-insight --project-ref pqkfcybnvvhvbqglsmvz
```

### Step 2: Set Secrets

```bash
# Set your Anthropic API key
supabase secrets set ANTHROPIC_API_KEY=your_key_here --project-ref pqkfcybnvvhvbqglsmvz
```

### Step 3: Create Database Table (if using Supabase DB)

```sql
CREATE TABLE IF NOT EXISTS daily_insights (
  id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  player_id TEXT NOT NULL,
  player_name TEXT NOT NULL,
  insight_summary TEXT,
  ai_generated_at TIMESTAMPTZ,
  ai_model TEXT,
  created_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX idx_daily_insights_player ON daily_insights(player_id);
```

### Step 4: Test Edge Function

```bash
curl -X POST 'https://pqkfcybnvvhvbqglsmvz.supabase.co/functions/v1/generate-vision-insight' \
  -H 'Authorization: Bearer YOUR_SERVICE_ROLE_KEY' \
  -H 'Content-Type: application/json' \
  -d '{
    "player_id": "kevin-durant",
    "name": "Kevin Durant",
    "current_line": 25.5,
    "l10_rate": 80,
    "pace_factor": 1.05,
    "fatigue": "Normal",
    "usage": 0,
    "risk_level": "Med",
    "stat_type": "points",
    "is_demon": true,
    "is_goblin": false
  }'
```

## AI Prompt Engineering

### System Prompt
```
You are a sharp, aggressive sports betting analyst for an elite 2026 app called "Demon & Goblin."
Analyze player prop data and provide a 1-sentence "badass" insight.
Do not use filler words. Focus on why the "future" favors this bet.
Use a punchy, high-tech tone. Be prophetic and confident.
Never mention uncertainty. Speak as if you've seen the future.
Maximum 25 words per insight.
```

### Discrepancy Edge Detection

When the AI's projected score differs from the betting line by >15%, the insight will explicitly mention the edge:

```
CRITICAL EDGE: Model projects 28.5 vs line 24.5. 16% discrepancy favors OVER. Mention this edge.
```

## Cost Management

The Vision AI only processes:
- **Demons** (high payout potential)
- **Goblins** (high safety picks)
- **High Volatility** players

This typically means ~50-100 API calls per day instead of 400+.

### Rate Limiting
- Max 3 concurrent API calls
- 0.5s delay between batches
- ~150 tokens per insight

## Daily Sync Integration

The Vision AI is automatically triggered after the daily sync:

```
4:00 AM UTC Daily Sync Order:
1. Sync player stats (BallDontLie + NBA.com)
2. Sync odds from The Odds API
3. Calculate advanced analytics (pace, fatigue, usage)
4. Generate Vision AI insights ← NEW
```

## Monitoring

Check the backend logs for Vision AI activity:

```bash
tail -f /var/log/supervisor/backend.err.log | grep VISION
```

Expected log output:
```
[VISION] Batch insight generation triggered
[VISION] Processing 62 eligible players out of 410 total
[VISION] Generated insight for Kevin Durant: The 80% L10 hit rate...
[VISION] Completed: 62 insights generated
```

## Troubleshooting

### "EMERGENT_LLM_KEY not configured"
Add to `/app/backend/.env`:
```
EMERGENT_LLM_KEY=sk-emergent-7F7De8c244e9051464
```

### "Vision AI Service not initialized"
Restart the backend:
```bash
sudo supervisorctl restart backend
```

### Rate limit errors
Increase delay in `vision_ai_service.py`:
```python
delay_between: float = 1.0  # Increase from 0.5
```
