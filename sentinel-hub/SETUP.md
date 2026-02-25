# Satteli Sentinel Hub Setup Guide

## Why Sentinel Hub?

| Platform | Monthly Cost | Free Tier |
|----------|-------------|-----------|
| Google Earth Engine | $500+ | None (commercial) |
| **Copernicus Data Space** | **€0** | **10,000 PUs/month** |
| CREODIAS | €30+ | None |

Copernicus Data Space offers free access to Sentinel Hub with 10,000 Processing Units per month - enough for ~300 customers with weekly monitoring.

---

## Step 1: Create Copernicus Account

1. Go to https://dataspace.copernicus.eu/
2. Click "Register" in the top right
3. Fill in your details and verify email
4. This gives you access to the free tier (10,000 PUs/month)

---

## Step 2: Get API Credentials

1. Log in to https://dataspace.copernicus.eu/
2. Go to your Dashboard → User Settings
3. Navigate to "OAuth clients"
4. Click "Create new" OAuth client
5. Note down:
   - **Client ID**: `sh-xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx`
   - **Client Secret**: `xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx`

---

## Step 3: Local Setup

### 3.1 Clone and install

```bash
cd /path/to/satteli/sentinel-hub
pip install -r requirements.txt
```

### 3.2 Configure environment

```bash
cp .env.example .env
```

Edit `.env` with your credentials:
```
SH_CLIENT_ID=sh-xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
SH_CLIENT_SECRET=your-secret-here
```

### 3.3 Test the connection

```bash
python -c "
from sentinelhub import SHConfig, SentinelHubRequest, DataCollection, BBox, CRS
import os

config = SHConfig()
config.sh_client_id = os.getenv('SH_CLIENT_ID')
config.sh_client_secret = os.getenv('SH_CLIENT_SECRET')
config.sh_base_url = 'https://sh.dataspace.copernicus.eu'
config.sh_token_url = 'https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token'

print('✅ Credentials configured')
print(f'Client ID: {config.sh_client_id[:20]}...')
"
```

---

## Step 4: Run Analysis

### 4.1 Single boundary test

```bash
python deforestation_detection.py
```

Expected output:
```
============================================================
SATTELI - Deforestation Detection (Sentinel Hub)
============================================================
Analyzing: Block A - Riau North
  Previous period: 2024-01-15 to 2024-02-14
  Recent period: 2024-02-14 to 2024-03-15
  Boundary area: 1100.0 ha
  Fetching previous period NDVI...
  Fetching recent period NDVI...
  NDVI Previous: 0.682
  NDVI Recent: 0.675
  NDVI Change: 0.007

✅ No significant deforestation detected
```

### 4.2 Batch scan (dry run)

```bash
python batch_scanner.py --dry-run
```

### 4.3 Full batch scan

```bash
python batch_scanner.py
```

---

## Step 5: NASA FIRMS Setup (Fire Detection)

Fire detection uses NASA FIRMS API (separate from Sentinel Hub).

1. Register at https://firms.modaps.eosdis.nasa.gov/api/
2. Get your MAP_KEY from the email
3. Add to `.env`:
   ```
   NASA_FIRMS_KEY=your-map-key
   ```

Note: FIRMS has a free tier with generous limits for fire hotspot queries.

---

## Step 6: Production Deployment (Railway)

### 6.1 Create Railway project

```bash
railway login
railway init
```

### 6.2 Add environment variables

In Railway dashboard or via CLI:
```bash
railway variables set SH_CLIENT_ID=xxx
railway variables set SH_CLIENT_SECRET=xxx
railway variables set SUPABASE_URL=xxx
railway variables set SUPABASE_SERVICE_KEY=xxx
railway variables set FONNTE_TOKEN=xxx
railway variables set RESEND_API_KEY=xxx
```

### 6.3 Create railway.json

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

### 6.4 Deploy

```bash
railway up
```

---

## Processing Unit (PU) Usage

### Understanding PUs

1 PU ≈ processing a 512×512 pixel image with 3 bands

### Estimated usage per analysis:

| Area Size | Single NDVI | Change Detection |
|-----------|-------------|------------------|
| 100 ha (1 km²) | ~1 PU | ~2 PU |
| 1,000 ha (10 km²) | ~8 PU | ~16 PU |
| 10,000 ha (100 km²) | ~80 PU | ~160 PU |

### Monthly capacity (10,000 PU free tier):

| Frequency | Max Customers (10,000 ha each) |
|-----------|-------------------------------|
| Weekly | ~30 customers |
| Bi-weekly | ~60 customers |
| Monthly | ~120 customers |

### Monitoring usage

Check your usage at: https://dataspace.copernicus.eu/ → Dashboard → Usage

---

## Upgrading

If you exceed 10,000 PUs, options:

1. **CREODIAS Exploration** (€30/month) - 30,000 PUs
2. **CREODIAS Basic** (€100/month) - 70,000 PUs
3. **Direct Copernicus upgrade** - Contact support

---

## Troubleshooting

### "401 Unauthorized"
- Check client ID and secret are correct
- Verify OAuth client is active in dashboard
- Ensure token URL is correct for Copernicus Data Space

### "No data available"
- Check date range has cloud-free imagery
- Expand time window (increase `days_back`)
- Verify coordinates are correct (lon, lat order)

### "Rate limit exceeded"
- Free tier: 300 requests/minute
- Add delays between requests: `time.sleep(0.5)`

### "Insufficient PUs"
- Check usage in dashboard
- Reduce resolution (20m instead of 10m)
- Reduce analysis frequency
- Upgrade plan

---

## API Reference

### Sentinel Hub Python

- Documentation: https://sentinelhub-py.readthedocs.io/
- Examples: https://github.com/sentinel-hub/sentinelhub-py/tree/master/examples

### Copernicus Data Space

- Portal: https://dataspace.copernicus.eu/
- API docs: https://documentation.dataspace.copernicus.eu/

### Evalscripts

- Custom scripts: https://custom-scripts.sentinel-hub.com/
- NDVI: https://custom-scripts.sentinel-hub.com/sentinel-2/ndvi/

---

## Support

- Copernicus forum: https://forum.dataspace.copernicus.eu/
- Sentinel Hub forum: https://forum.sentinel-hub.com/
- Satteli: info@satteli.com
