# Satteli GEE Setup Guide

## Prerequisites

1. Google account with Earth Engine access
2. Python 3.9+
3. (Optional) Supabase account for production

---

## Step 1: Google Earth Engine Setup

### 1.1 Sign up for Earth Engine

1. Go to https://earthengine.google.com/signup/
2. Sign in with your Google account
3. Accept the terms of service
4. Wait for approval (usually instant for non-commercial use)

### 1.2 Create a GEE Project

1. Go to https://console.cloud.google.com/
2. Create a new project (e.g., "satteli-gee")
3. Enable the Earth Engine API:
   - Search for "Earth Engine API" in the API Library
   - Click "Enable"

### 1.3 Install Earth Engine CLI

```bash
pip install earthengine-api
```

### 1.4 Authenticate

```bash
earthengine authenticate
```

This will open a browser window. Sign in and authorize.

### 1.5 Test Connection

```python
import ee
ee.Initialize(project='your-project-id')
print(ee.Image('USGS/SRTMGL1_003').getInfo())
```

---

## Step 2: Local Development Setup

### 2.1 Clone and install

```bash
cd /path/to/satteli/gee
pip install -r requirements.txt
```

### 2.2 Create .env file

```bash
cp .env.example .env
```

Edit `.env`:
```
# Google Earth Engine
GEE_PROJECT_ID=your-gee-project-id

# Supabase (optional for local testing)
SUPABASE_URL=https://xxx.supabase.co
SUPABASE_SERVICE_KEY=eyJhbGc...

# Fonnte WhatsApp (optional)
FONNTE_TOKEN=your-fonnte-token

# Resend Email (optional)
RESEND_API_KEY=re_xxx
```

### 2.3 Update project ID in scripts

In `deforestation_detection.py` line 22:
```python
ee.Initialize(project='your-gee-project-id')  # <- Replace this
```

---

## Step 3: Test the Scripts

### 3.1 Test deforestation detection

```bash
python deforestation_detection.py
```

Expected output:
```
============================================================
SATTELI - Deforestation Detection
============================================================
Comparing periods:
  Previous: 2024-01-15 to 2024-02-14
  Recent:   2024-02-14 to 2024-03-15

📊 ANALYSIS RESULTS:
----------------------------------------
Customer: CUST001
Boundary: Block A - Riau North
...
```

### 3.2 Test batch scanner (dry run)

```bash
python batch_scanner.py --dry-run
```

---

## Step 4: Production Deployment (Railway)

### 4.1 Create Railway project

1. Go to https://railway.app
2. Create new project
3. Connect your GitHub repo

### 4.2 Add environment variables

In Railway dashboard, add:
- `GEE_PROJECT_ID`
- `SUPABASE_URL`
- `SUPABASE_SERVICE_KEY`
- `FONNTE_TOKEN`
- `RESEND_API_KEY`

### 4.3 Service account authentication

For production, use a service account instead of user credentials:

1. In Google Cloud Console, go to IAM & Admin > Service Accounts
2. Create a service account (e.g., "satteli-gee-worker")
3. Grant "Earth Engine Resource Writer" role
4. Create and download JSON key
5. Add the key content to Railway as `GOOGLE_APPLICATION_CREDENTIALS_JSON`

Update initialization:
```python
import json
import os

credentials_json = os.getenv('GOOGLE_APPLICATION_CREDENTIALS_JSON')
if credentials_json:
    credentials = ee.ServiceAccountCredentials(
        email=json.loads(credentials_json)['client_email'],
        key_data=credentials_json
    )
    ee.Initialize(credentials, project='your-project-id')
else:
    ee.Initialize(project='your-project-id')
```

### 4.4 Set up cron job

In `railway.json`:
```json
{
  "build": {
    "builder": "NIXPACKS"
  },
  "deploy": {
    "startCommand": "python batch_scanner.py",
    "cronSchedule": "0 6 * * 1"
  }
}
```

This runs every Monday at 6 AM UTC.

---

## Step 5: Supabase Setup

### 5.1 Create Supabase project

1. Go to https://supabase.com
2. Create new project
3. Note the URL and anon/service keys

### 5.2 Run schema migration

In Supabase SQL Editor, run the schema from `ARCHITECTURE.md`:

```sql
-- Customers table
CREATE TABLE customers (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name TEXT NOT NULL,
    email TEXT NOT NULL,
    phone TEXT,
    company TEXT,
    plan TEXT DEFAULT 'starter',
    total_hectares INTEGER,
    status TEXT DEFAULT 'active',
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Boundaries table (with PostGIS)
CREATE TABLE boundaries (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    customer_id UUID REFERENCES customers(id),
    name TEXT,
    geojson JSONB NOT NULL,
    hectares NUMERIC,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Alerts table
CREATE TABLE alerts (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    customer_id UUID REFERENCES customers(id),
    boundary_id UUID REFERENCES boundaries(id),
    type TEXT NOT NULL,
    severity TEXT,
    title TEXT,
    description TEXT,
    affected_hectares NUMERIC,
    coordinates JSONB,
    satellite_image_url TEXT,
    status TEXT DEFAULT 'new',
    detected_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Indexes
CREATE INDEX idx_alerts_customer ON alerts(customer_id);
CREATE INDEX idx_alerts_status ON alerts(status);
CREATE INDEX idx_boundaries_customer ON boundaries(customer_id);
```

### 5.3 Insert test customer

```sql
INSERT INTO customers (id, name, email, phone, company, total_hectares)
VALUES (
    'a1b2c3d4-e5f6-7890-abcd-ef1234567890',
    'PT Sawit Makmur',
    'ops@sawitmakmur.co.id',
    '6281234567890',
    'Sawit Makmur Group',
    5000
);

INSERT INTO boundaries (customer_id, name, geojson, hectares)
VALUES (
    'a1b2c3d4-e5f6-7890-abcd-ef1234567890',
    'Block A - Riau',
    '{"type": "Polygon", "coordinates": [[[102.05, 1.45], [102.15, 1.45], [102.15, 1.55], [102.05, 1.55], [102.05, 1.45]]]}',
    1100
);
```

---

## Troubleshooting

### "Earth Engine memory limit exceeded"
- Reduce `maxPixels` parameter
- Process smaller areas or split into tiles
- Use `bestEffort: true` in reduce operations

### "No images found for date range"
- Expand the date range
- Increase `cloud_cover_max` threshold
- Check if the area has Sentinel-2 coverage

### "Authentication failed"
- Re-run `earthengine authenticate`
- Check project ID is correct
- Verify Earth Engine API is enabled in Cloud Console

### "Quota exceeded"
- Earth Engine has free tier limits
- Wait 24 hours for quota reset
- Consider upgrading to commercial tier for production

---

## Useful Resources

- [Earth Engine Documentation](https://developers.google.com/earth-engine)
- [Sentinel-2 Data Guide](https://developers.google.com/earth-engine/datasets/catalog/COPERNICUS_S2_SR_HARMONIZED)
- [GEE Python API Reference](https://developers.google.com/earth-engine/apidocs)
- [NDVI Tutorial](https://developers.google.com/earth-engine/tutorials/tutorial_api_06)
