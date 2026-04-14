# NBA Best Bets Dashboard - Setup Guide

## 🎯 What's Been Built

A high-performance NBA Player Prop Dashboard that identifies "Best Bets" by comparing lines across the entire sports betting market.

### ✅ Features Implemented

1. **Supabase Authentication**
   - User signup/login with email & password
   - Profile management with subscription tiers (Free/Pro)
   - JWT-based authentication

2. **Tank01 API Integration**
   - `getNBAGamesForDate` - Fetches today's NBA games
   - `getNBAInjuryList` - Real-time injury reports
   - `getNBATeams` - Team statistics and defensive rankings

3. **Best Bet Algorithm**
   - Cross-book comparison (DraftKings, FanDuel, BetMGM, Caesars)
   - Line discrepancy calculation vs PrizePicks
   - Best Bet Score (combines line edge + matchup + hit rate)
   - Matchup grading system (A+ to C)

4. **Demon Line Analysis**
   - Purple glowing effect for high-value alt-lines
   - Historical hit rate calculation (last 10 games)
   - Risk/reward assessment

5. **Tiered Access Control**
   - Free tier: Basic comparison view
   - Pro tier: Confidence scores, Demon lines, advanced analytics

6. **Auto-Refresh System**
   - Updates every 5 minutes automatically
   - Manual refresh button available
   - Live indicator showing real-time status

7. **Dark Mode UI**
   - "Midnight Terminal" theme
   - Barlow Condensed headings
   - JetBrains Mono for data/odds
   - Purple glow for Demon lines
   - Green accent for positive EV bets

---

## 🔑 Required Credentials

### 1. Supabase Setup

You need to add your Supabase credentials to both backend and frontend `.env` files:

**Backend (`/app/backend/.env`):**
```env
SUPABASE_ANON_KEY=YOUR_SUPABASE_ANON_KEY_HERE
SUPABASE_SERVICE_ROLE_KEY=YOUR_SUPABASE_SERVICE_ROLE_KEY_HERE
JWT_SECRET=YOUR_JWT_SECRET_HERE
```

**Frontend (`/app/frontend/.env`):**
```env
REACT_APP_SUPABASE_ANON_KEY=YOUR_SUPABASE_ANON_KEY_HERE
```

**How to get these:**
1. Go to https://app.supabase.com
2. Select your project (URL: `https://pqkfcybnvvhvbqglsmvz.supabase.co`)
3. Go to Settings > API
4. Copy:
   - `anon` key (public)
   - `service_role` key (secret - backend only)
5. Go to Settings > Auth > JWT Settings
6. Copy the JWT Secret

### 2. Create Supabase Tables

Run this SQL in your Supabase SQL Editor:

```sql
-- Create profiles table
create table if not exists public.profiles (
  id uuid not null references auth.users on delete cascade,
  email text,
  full_name text,
  tier text default 'free' check (tier in ('free', 'pro')),
  created_at timestamp with time zone default now(),
  updated_at timestamp with time zone default now(),
  primary key (id)
);

-- Enable RLS
alter table public.profiles enable row level security;

-- Create policies
create policy "Profiles are viewable by authenticated users"
on public.profiles for select
to authenticated
using (true);

create policy "Users can update their own profile"
on public.profiles for update
to authenticated
using (id = auth.uid())
with check (id = auth.uid());

create policy "Users can insert their own profile"
on public.profiles for insert
to authenticated
with check (id = auth.uid());

-- Create trigger for automatic profile creation
create or replace function public.handle_new_user()
returns trigger
language plpgsql
security definer set search_path = ''
as $$
begin
  insert into public.profiles (id, email, full_name, tier)
  values (
    new.id,
    new.email,
    new.raw_user_meta_data ->> 'full_name',
    'free'
  );
  return new;
end;
$$;

create trigger on_auth_user_created
  after insert on auth.users
  for each row
  execute procedure public.handle_new_user();
```

### 3. API Keys (Already Configured)

These are already in your `.env` files:
- **Tank01 API**: `402edbcac6mshd04997e7ca01d17p1879eajsn65ab176cdb1e` ✅
- **Odds API**: `e1ae76ab21c34ee88ed552cffb4449fd` ✅

---

## 🚀 How to Use

1. **Add Supabase credentials** to the `.env` files (see above)

2. **Restart services:**
   ```bash
   sudo supervisorctl restart backend
   sudo supervisorctl restart frontend
   ```

3. **Access the app:**
   - URL: https://local-first-hub-2.preview.emergentagent.com
   - Create an account (starts as Free tier)
   - View NBA player prop opportunities

4. **Test the dashboard:**
   - Mock data is currently shown for demonstration
   - Once APIs are responding, real data will populate automatically

---

## 📊 API Endpoints

### Authentication
- `POST /api/auth/signup` - Create new account
- `POST /api/auth/login` - Login
- `GET /api/profile` - Get user profile

### NBA Data
- `GET /api/nba/games` - Today's NBA games
- `GET /api/nba/injuries` - Injury list
- `GET /api/nba/teams` - Team stats

### Betting Data
- `GET /api/odds/player-props` - Live odds from sportsbooks
- `GET /api/best-bets` - Calculated best bets (requires auth)

---

## 🎨 Design System

- **Background**: `#09090B` (deep black)
- **Cards**: `#18181B` (dark gray)
- **Primary Accent**: `#22c55e` (green - for positive EV)
- **Demon Accent**: `#a855f7` (purple - for demon lines)
- **Typography**: 
  - Headings: Barlow Condensed
  - Data/Odds: JetBrains Mono
  - UI Text: Inter

---

## 🔐 Security Notes

- JWT verification on all protected endpoints
- Row Level Security (RLS) on Supabase tables
- Service role key only used on backend (never exposed)
- Token stored in localStorage (consider httpOnly cookies for production)

---

## 📝 Next Steps

1. Add real-time odds data integration
2. Implement payment processing for Pro tier upgrades
3. Add historical performance tracking
4. Create detailed player analytics pages
5. Add push notifications for high-value opportunities
