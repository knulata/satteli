# Satteli Technical Architecture

## Overview

Satteli is a satellite monitoring platform for palm oil plantations and agriculture. It ingests free Sentinel-2 imagery, processes it through change detection algorithms, and delivers alerts via WhatsApp/email.

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              SATTELI ARCHITECTURE                           │
└─────────────────────────────────────────────────────────────────────────────┘

┌──────────────┐    ┌──────────────┐    ┌──────────────┐    ┌──────────────┐
│  Sentinel-2  │    │   Landsat    │    │    FIRMS     │    │   Planet     │
│   (Free)     │    │   (Free)     │    │ (Fire/Free)  │    │ (Paid/Later) │
└──────┬───────┘    └──────┬───────┘    └──────┬───────┘    └──────┬───────┘
       │                   │                   │                   │
       └───────────────────┴───────────────────┴───────────────────┘
                                    │
                                    ▼
                    ┌───────────────────────────────┐
                    │    GOOGLE EARTH ENGINE        │
                    │  ┌─────────────────────────┐  │
                    │  │ • Image compositing     │  │
                    │  │ • Cloud masking         │  │
                    │  │ • NDVI calculation      │  │
                    │  │ • Change detection      │  │
                    │  └─────────────────────────┘  │
                    └───────────────┬───────────────┘
                                    │
                                    ▼
                    ┌───────────────────────────────┐
                    │      PROCESSING LAYER         │
                    │         (Railway)             │
                    │  ┌─────────────────────────┐  │
                    │  │ • Scheduled triggers    │  │
                    │  │ • Alert classification  │  │
                    │  │ • Report generation     │  │
                    │  │ • Customer matching     │  │
                    │  └─────────────────────────┘  │
                    └───────────────┬───────────────┘
                                    │
              ┌─────────────────────┼─────────────────────┐
              │                     │                     │
              ▼                     ▼                     ▼
    ┌─────────────────┐   ┌─────────────────┐   ┌─────────────────┐
    │    SUPABASE     │   │    WHATSAPP     │   │     EMAIL       │
    │   (Database)    │   │   (Fonnte)      │   │   (Resend)      │
    │                 │   │                 │   │                 │
    │ • Customers     │   │ • Alerts        │   │ • Reports       │
    │ • Boundaries    │   │ • Updates       │   │ • Summaries     │
    │ • Alerts        │   │                 │   │                 │
    │ • History       │   │                 │   │                 │
    └────────┬────────┘   └─────────────────┘   └─────────────────┘
             │
             ▼
    ┌─────────────────────────────────────────┐
    │           CUSTOMER DASHBOARD            │
    │              (Vercel)                   │
    │  ┌───────────────────────────────────┐  │
    │  │ • Map view with boundaries        │  │
    │  │ • Alert timeline                  │  │
    │  │ • Historical imagery comparison   │  │
    │  │ • Report downloads                │  │
    │  │ • Settings & notifications        │  │
    │  └───────────────────────────────────┘  │
    └─────────────────────────────────────────┘
```

---

## Component Details

### 1. Data Sources

#### Sentinel-2 (Primary - FREE)
- **Resolution:** 10m (RGB, NIR), 20m (Red Edge, SWIR)
- **Frequency:** Every 5 days
- **Access:** Google Earth Engine or Copernicus Open Access Hub
- **Use cases:** Deforestation, crop health (NDVI), land use change

#### NASA FIRMS (Fire - FREE)
- **Resolution:** 375m (VIIRS), 1km (MODIS)
- **Frequency:** Near real-time (3-4 hours)
- **Access:** FIRMS API (https://firms.modaps.eosdis.nasa.gov)
- **Use cases:** Fire/hotspot detection

#### Landsat 8/9 (Backup - FREE)
- **Resolution:** 30m
- **Frequency:** Every 16 days
- **Use cases:** Historical analysis, thermal bands

---

### 2. Google Earth Engine (Processing)

GEE handles all heavy satellite processing for free (within limits).

```javascript
// Example: Deforestation detection script
var geometry = ee.Geometry.Polygon([customer_boundary]);

// Get Sentinel-2 imagery for last 2 periods
var recent = ee.ImageCollection('COPERNICUS/S2_SR_HARMONIZED')
    .filterBounds(geometry)
    .filterDate('2024-02-01', '2024-02-15')
    .filter(ee.Filter.lt('CLOUDY_PIXEL_PERCENTAGE', 20))
    .median();

var previous = ee.ImageCollection('COPERNICUS/S2_SR_HARMONIZED')
    .filterBounds(geometry)
    .filterDate('2024-01-01', '2024-01-15')
    .filter(ee.Filter.lt('CLOUDY_PIXEL_PERCENTAGE', 20))
    .median();

// Calculate NDVI
var ndvi_recent = recent.normalizedDifference(['B8', 'B4']);
var ndvi_previous = previous.normalizedDifference(['B8', 'B4']);

// Detect significant decrease (potential deforestation)
var ndvi_change = ndvi_previous.subtract(ndvi_recent);
var deforestation = ndvi_change.gt(0.3); // Threshold

// Get affected area in hectares
var area = deforestation.multiply(ee.Image.pixelArea())
    .reduceRegion({
        reducer: ee.Reducer.sum(),
        geometry: geometry,
        scale: 10,
        maxPixels: 1e9
    });

// Export results
Export.table.toDrive({
    collection: ee.FeatureCollection([ee.Feature(null, {
        'customer_id': 'CUST001',
        'deforestation_ha': ee.Number(area.get('nd')).divide(10000),
        'date': '2024-02-15'
    })]),
    description: 'deforestation_alert',
    fileFormat: 'CSV'
});
```

#### GEE Scheduled Tasks
- Run via Earth Engine Python API + cron job on Railway
- Process each customer's boundaries weekly
- Export alerts to Cloud Storage → trigger webhook

---

### 3. Backend Processing (Railway)

**Tech Stack:** Python + FastAPI

```
/satteli-backend
├── main.py                 # FastAPI app
├── services/
│   ├── gee_processor.py    # Earth Engine integration
│   ├── alert_service.py    # Alert classification & delivery
│   ├── fire_service.py     # FIRMS API integration
│   └── report_service.py   # PDF/Excel generation
├── models/
│   ├── customer.py
│   ├── boundary.py
│   └── alert.py
├── jobs/
│   ├── weekly_scan.py      # Scheduled deforestation check
│   ├── daily_fire.py       # Fire hotspot check
│   └── monthly_report.py   # Summary reports
└── requirements.txt
```

#### Key Endpoints

```python
# API Routes
POST /api/customers              # Create customer
POST /api/boundaries             # Upload KML/GeoJSON boundary
GET  /api/alerts/{customer_id}   # Get alerts for customer
POST /api/scan/{customer_id}     # Trigger manual scan
GET  /api/report/{customer_id}   # Generate report
POST /webhooks/gee-complete      # GEE export completion webhook
```

#### Scheduled Jobs (Cron)

| Job | Frequency | Description |
|-----|-----------|-------------|
| `weekly_scan` | Every Monday 6 AM | Process all customer boundaries for deforestation |
| `daily_fire` | Every 6 hours | Check FIRMS API for hotspots in customer areas |
| `monthly_report` | 1st of month | Generate monthly summary for each customer |

---

### 4. Database Schema (Supabase)

```sql
-- Customers
CREATE TABLE customers (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name TEXT NOT NULL,
    email TEXT NOT NULL,
    phone TEXT,                    -- WhatsApp number
    company TEXT,
    plan TEXT DEFAULT 'starter',   -- starter, professional, enterprise
    total_hectares INTEGER,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Plantation boundaries (GeoJSON)
CREATE TABLE boundaries (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    customer_id UUID REFERENCES customers(id),
    name TEXT,                     -- e.g., "Block A", "Concession North"
    geojson JSONB NOT NULL,        -- GeoJSON polygon
    hectares NUMERIC,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Alerts
CREATE TABLE alerts (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    customer_id UUID REFERENCES customers(id),
    boundary_id UUID REFERENCES boundaries(id),
    type TEXT NOT NULL,            -- deforestation, fire, crop_stress
    severity TEXT,                 -- low, medium, high, critical
    title TEXT,
    description TEXT,
    affected_hectares NUMERIC,
    coordinates JSONB,             -- Centroid or polygon of affected area
    satellite_image_url TEXT,      -- Link to before/after image
    status TEXT DEFAULT 'new',     -- new, acknowledged, resolved
    detected_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Alert notifications sent
CREATE TABLE notifications (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    alert_id UUID REFERENCES alerts(id),
    channel TEXT,                  -- whatsapp, email
    sent_at TIMESTAMPTZ DEFAULT NOW(),
    status TEXT                    -- sent, delivered, failed
);

-- Historical NDVI readings (for trends)
CREATE TABLE ndvi_history (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    boundary_id UUID REFERENCES boundaries(id),
    date DATE,
    mean_ndvi NUMERIC,
    min_ndvi NUMERIC,
    max_ndvi NUMERIC,
    cloud_cover_pct NUMERIC
);

-- Indexes
CREATE INDEX idx_alerts_customer ON alerts(customer_id);
CREATE INDEX idx_alerts_status ON alerts(status);
CREATE INDEX idx_boundaries_customer ON boundaries(customer_id);
```

---

### 5. Alert Delivery

#### WhatsApp (via Fonnte)
```python
import requests

def send_whatsapp_alert(phone: str, alert: dict):
    message = f"""🛰️ *SATTELI ALERT*

⚠️ *{alert['type'].upper()}* detected in {alert['boundary_name']}

📍 Location: {alert['coordinates']}
📐 Affected area: {alert['affected_hectares']:.1f} ha
📅 Detected: {alert['detected_at']}

View details: https://app.satteli.com/alerts/{alert['id']}

Reply STOP to unsubscribe."""

    response = requests.post(
        'https://api.fonnte.com/send',
        headers={'Authorization': FONNTE_TOKEN},
        data={
            'target': phone,
            'message': message,
            'countryCode': '62'
        }
    )
    return response.json()
```

#### Email (via Resend)
```python
import resend

def send_email_alert(email: str, alert: dict):
    resend.api_key = RESEND_API_KEY

    resend.Emails.send({
        "from": "alerts@satteli.com",
        "to": email,
        "subject": f"🚨 {alert['type']} Alert - {alert['boundary_name']}",
        "html": render_alert_email(alert)  # HTML template
    })
```

---

### 6. Customer Dashboard (Vercel)

**Tech Stack:** Next.js + Tailwind + Mapbox/Leaflet

```
/satteli-dashboard
├── app/
│   ├── page.tsx                 # Landing (redirect to /dashboard)
│   ├── login/page.tsx           # Auth (Supabase Auth)
│   ├── dashboard/
│   │   ├── page.tsx             # Main dashboard with map
│   │   ├── alerts/page.tsx      # Alert list
│   │   ├── reports/page.tsx     # Historical reports
│   │   └── settings/page.tsx    # Notification preferences
│   └── api/
│       └── [...routes]          # API routes (proxy to Railway)
├── components/
│   ├── Map.tsx                  # Leaflet map with boundaries
│   ├── AlertCard.tsx
│   ├── BoundaryUploader.tsx     # KML/GeoJSON upload
│   └── Timeline.tsx
└── lib/
    ├── supabase.ts
    └── api.ts
```

#### Dashboard Features
1. **Map View** - All boundaries with color-coded status
2. **Alert Feed** - Real-time alerts with acknowledge/resolve actions
3. **Compare View** - Before/after satellite imagery slider
4. **Reports** - Monthly PDF summaries, NDVI trends
5. **Boundary Management** - Upload KML, edit boundaries
6. **Settings** - WhatsApp/email preferences, alert thresholds

---

### 7. Infrastructure & Costs

| Component | Service | Monthly Cost |
|-----------|---------|--------------|
| Satellite Processing | Google Earth Engine | FREE (non-commercial limits) |
| Backend | Railway (Pro) | $20 |
| Database | Supabase (Pro) | $25 |
| Dashboard Hosting | Vercel (Pro) | $20 |
| WhatsApp API | Fonnte | ~$10 |
| Email | Resend | FREE (up to 3K/month) |
| Map Tiles | Mapbox | FREE (up to 50K loads) |
| File Storage | Supabase Storage | Included |
| **Total** | | **~$75/month** |

At $75/month fixed cost + $0 marginal cost per hectare (Sentinel-2 is free), margins are excellent.

---

## Implementation Phases

### Phase 1: MVP (2-3 weeks)
- [ ] Set up GEE account + basic deforestation script
- [ ] Supabase database + customer table
- [ ] Manual boundary upload (admin enters GeoJSON)
- [ ] Weekly cron job for scanning
- [ ] WhatsApp alert delivery
- [ ] Basic dashboard (map + alert list)

### Phase 2: Self-Service (2-3 weeks)
- [ ] Customer signup flow
- [ ] KML/GeoJSON boundary upload UI
- [ ] FIRMS fire integration
- [ ] Email alerts + digest
- [ ] Monthly report generation

### Phase 3: Scale (ongoing)
- [ ] Multi-spectral analysis (crop health)
- [ ] Historical trend charts
- [ ] API for enterprise integration
- [ ] Higher resolution imagery (Planet)
- [ ] Mobile app

---

## Key Technical Decisions

### Why Google Earth Engine?
- Free processing of petabytes of satellite data
- Pre-loaded Sentinel-2, Landsat, MODIS
- Python + JavaScript APIs
- Handles cloud masking, compositing, analysis
- No egress fees for exports

### Why Supabase?
- Postgres with PostGIS for geospatial queries
- Built-in auth
- Real-time subscriptions (for live alerts)
- Storage for images/reports
- Generous free tier

### Why Railway?
- Easy Python deployment
- Built-in cron jobs
- Cheap for background processing
- Good logging

---

## Risk Mitigation

| Risk | Mitigation |
|------|------------|
| GEE quota limits | Monitor usage, batch processing, upgrade to commercial if needed |
| Cloud cover gaps | Use multiple dates, SAR radar backup (Sentinel-1) |
| False positives | Human QA review before sending alerts, tunable thresholds |
| WhatsApp blocking | Rate limiting, business verification, fallback to email |

---

## Next Steps

1. **Create GEE project** and test basic script on sample area
2. **Set up Supabase** with schema above
3. **Build Railway backend** with `/api/scan` endpoint
4. **Test end-to-end** with one real customer boundary
5. **Iterate on dashboard** based on customer feedback
