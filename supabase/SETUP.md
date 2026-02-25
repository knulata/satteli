# Satteli Supabase Setup Guide

## Step 1: Create Supabase Project

1. Go to https://supabase.com/dashboard
2. Click "New project"
3. Enter project name: `satteli`
4. Choose a strong database password (save it!)
5. Select region: `Singapore` (closest to Indonesia)
6. Click "Create new project"

---

## Step 2: Run the Schema

1. In your Supabase dashboard, go to **SQL Editor**
2. Click "New query"
3. Copy the entire contents of `schema.sql`
4. Paste into the SQL Editor
5. Click "Run" (or Cmd+Enter)

You should see:
```
Success. No rows returned
```

---

## Step 3: Get Your API Keys

1. Go to **Settings** → **API**
2. Copy these values:

| Key | Where to find | Usage |
|-----|---------------|-------|
| **Project URL** | Under "Project URL" | `SUPABASE_URL` |
| **anon public** | Under "Project API keys" | Dashboard (public) |
| **service_role** | Under "Project API keys" | Backend (secret!) |

---

## Step 4: Configure Environment Variables

### For Sentinel Hub Backend (Railway)

Add to `.env` or Railway dashboard:
```bash
SUPABASE_URL=https://xxxxxxxxxxxxx.supabase.co
SUPABASE_SERVICE_KEY=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...
```

### For Dashboard (Vercel)

Add environment variables:
```bash
NEXT_PUBLIC_SUPABASE_URL=https://xxxxxxxxxxxxx.supabase.co
NEXT_PUBLIC_SUPABASE_ANON_KEY=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...
```

---

## Step 5: Enable Authentication

1. Go to **Authentication** → **Providers**
2. Enable **Email** (already enabled by default)
3. Optional: Enable **Google** OAuth
   - Create OAuth app at https://console.cloud.google.com
   - Add Client ID and Secret to Supabase

### Auth Settings

1. Go to **Authentication** → **Settings**
2. Set Site URL: `https://satteli.com`
3. Add Redirect URLs:
   - `https://satteli.com/**`
   - `http://localhost:3000/**` (for dev)

---

## Step 6: Enable Storage (for satellite images)

1. Go to **Storage**
2. Create bucket: `satellite-images`
3. Set bucket to **Public** (for image display)
4. Create bucket: `reports`
5. Set bucket to **Private** (for customer reports)

### Storage Policies

Run in SQL Editor:
```sql
-- Allow authenticated users to read their own satellite images
CREATE POLICY "Users can view satellite images"
ON storage.objects FOR SELECT
TO authenticated
USING (bucket_id = 'satellite-images');

-- Allow service role to upload
CREATE POLICY "Service can upload images"
ON storage.objects FOR INSERT
TO service_role
WITH CHECK (bucket_id = 'satellite-images');
```

---

## Step 7: Verify Setup

### Test Query

In SQL Editor, run:
```sql
SELECT table_name FROM information_schema.tables
WHERE table_schema = 'public';
```

Should return:
- customers
- boundaries
- alerts
- notifications
- ndvi_history
- scan_logs

### Test Insert (Optional)

Uncomment and run the sample data section at the bottom of `schema.sql` to create test customers.

---

## Database Schema Overview

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│  customers  │────<│ boundaries  │────<│ ndvi_history│
└─────────────┘     └─────────────┘     └─────────────┘
       │                   │
       │                   │
       ▼                   ▼
┌─────────────┐     ┌─────────────┐
│   alerts    │────<│notifications│
└─────────────┘     └─────────────┘

┌─────────────┐
│  scan_logs  │  (standalone for batch job tracking)
└─────────────┘
```

---

## Row Level Security (RLS)

The schema includes RLS policies so:

- **Users** can only see their own data
- **Service role** (backend) has full access for batch processing
- **Scan logs** are only accessible by service role

---

## Useful Queries

### Get customer dashboard data
```sql
SELECT * FROM customer_dashboard WHERE customer_id = 'xxx';
```

### Get recent alerts
```sql
SELECT * FROM alert_summary
WHERE customer_id = 'xxx'
ORDER BY detected_at DESC
LIMIT 10;
```

### Check boundary health
```sql
SELECT name, hectares, current_ndvi, health_status, last_scan_at
FROM boundaries
WHERE customer_id = 'xxx' AND status = 'active';
```

---

## Troubleshooting

### "permission denied for table X"
- RLS is enabled. Make sure you're using the correct API key (service_role for backend, anon for dashboard)

### "relation X does not exist"
- Run the schema.sql file in SQL Editor

### Auth not working
- Check Site URL and Redirect URLs in Authentication settings
- Verify environment variables are set correctly

---

## Next Steps

1. ✅ Schema created
2. ⬜ Add test customer via Supabase UI or SQL
3. ⬜ Connect dashboard to Supabase (update js/supabase.js)
4. ⬜ Configure Sentinel Hub backend to write to Supabase
5. ⬜ Test end-to-end flow
