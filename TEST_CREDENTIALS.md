# Test Credentials & Notes

## Supabase Rate Limiting

Your Supabase project has email rate limiting enabled (which is normal for free tier). 

### To Fix:
1. Go to your Supabase Dashboard: https://app.supabase.com/project/pqkfcybnvvhvbqglsmvz
2. Go to **Authentication** > **Providers** > **Email**
3. Disable "Enable email confirmations" for testing
4. Or wait a few minutes between signup attempts

## Test User (if you can create one):
- Email: `demo@bestbets.com`
- Password: `BestBets2024!`

## Mock Data
The `/api/best-bets` endpoint currently returns mock data showing:
- 8 NBA players with prop bets
- Market comparisons vs PrizePicks lines  
- Demon line highlights (purple glow)
- Best bet scores and matchup grades

Once you can sign up successfully, you'll see the full dashboard with:
- ✅ Live indicator (green pulsing dot)
- ✅ FREE tier badge
- ✅ Pro upsell banner
- ✅ Auto-refresh every 5 minutes
- ✅ 8 Best bet opportunities
- ✅ Confidence scores (Pro only - currently hidden)
- ✅ Demon lines (Pro only - currently hidden)
