-- ============================================================
-- SATTELI DATABASE SCHEMA
-- ============================================================
-- Run this in Supabase SQL Editor to set up all tables
-- https://supabase.com/dashboard/project/YOUR_PROJECT/sql

-- Enable UUID extension
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ============================================================
-- CUSTOMERS TABLE
-- ============================================================
CREATE TABLE IF NOT EXISTS customers (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),

    -- Auth link
    auth_user_id UUID REFERENCES auth.users(id) ON DELETE SET NULL,

    -- Profile
    name TEXT NOT NULL,
    email TEXT NOT NULL UNIQUE,
    phone TEXT,                         -- WhatsApp number (e.g., 6281234567890)
    company TEXT,

    -- Subscription
    plan TEXT DEFAULT 'trial',          -- trial, starter, professional, enterprise
    status TEXT DEFAULT 'active',       -- active, suspended, cancelled
    total_hectares INTEGER DEFAULT 0,

    -- Notification preferences
    notify_whatsapp BOOLEAN DEFAULT true,
    notify_email BOOLEAN DEFAULT true,
    notify_weekly_digest BOOLEAN DEFAULT true,

    -- Alert thresholds
    threshold_deforestation_ha NUMERIC DEFAULT 0.5,
    threshold_ndvi_change NUMERIC DEFAULT 0.3,

    -- Timestamps
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Index for auth lookup
CREATE INDEX IF NOT EXISTS idx_customers_auth_user ON customers(auth_user_id);
CREATE INDEX IF NOT EXISTS idx_customers_email ON customers(email);
CREATE INDEX IF NOT EXISTS idx_customers_status ON customers(status);

-- ============================================================
-- BOUNDARIES TABLE
-- ============================================================
CREATE TABLE IF NOT EXISTS boundaries (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    customer_id UUID NOT NULL REFERENCES customers(id) ON DELETE CASCADE,

    -- Boundary info
    name TEXT NOT NULL,
    description TEXT,

    -- Geometry (stored as GeoJSON)
    geojson JSONB NOT NULL,

    -- Calculated fields
    hectares NUMERIC,
    centroid_lat NUMERIC,
    centroid_lon NUMERIC,

    -- Status
    status TEXT DEFAULT 'active',       -- active, paused, deleted
    last_scan_at TIMESTAMPTZ,
    last_alert_at TIMESTAMPTZ,

    -- Health metrics (updated by scans)
    current_ndvi NUMERIC,
    health_status TEXT DEFAULT 'unknown', -- healthy, warning, alert, unknown

    -- Timestamps
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_boundaries_customer ON boundaries(customer_id);
CREATE INDEX IF NOT EXISTS idx_boundaries_status ON boundaries(status);

-- ============================================================
-- ALERTS TABLE
-- ============================================================
CREATE TABLE IF NOT EXISTS alerts (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    customer_id UUID NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
    boundary_id UUID REFERENCES boundaries(id) ON DELETE SET NULL,

    -- Alert details
    type TEXT NOT NULL,                 -- deforestation, fire, crop_stress, encroachment
    severity TEXT NOT NULL,             -- low, medium, high, critical
    title TEXT NOT NULL,
    description TEXT,

    -- Affected area
    affected_hectares NUMERIC,
    coordinates JSONB,                  -- {lat, lon} of alert centroid

    -- Evidence
    satellite_image_url TEXT,
    before_image_url TEXT,
    after_image_url TEXT,

    -- Status workflow
    status TEXT DEFAULT 'new',          -- new, acknowledged, investigating, resolved, false_positive
    acknowledged_at TIMESTAMPTZ,
    acknowledged_by UUID REFERENCES auth.users(id),
    resolved_at TIMESTAMPTZ,
    resolved_by UUID REFERENCES auth.users(id),
    resolution_notes TEXT,

    -- Detection info
    detected_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    detection_method TEXT,              -- sentinel_hub, firms, manual
    confidence_score NUMERIC,           -- 0-1 confidence level

    -- Timestamps
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_alerts_customer ON alerts(customer_id);
CREATE INDEX IF NOT EXISTS idx_alerts_boundary ON alerts(boundary_id);
CREATE INDEX IF NOT EXISTS idx_alerts_status ON alerts(status);
CREATE INDEX IF NOT EXISTS idx_alerts_type ON alerts(type);
CREATE INDEX IF NOT EXISTS idx_alerts_detected ON alerts(detected_at DESC);

-- ============================================================
-- NOTIFICATIONS TABLE
-- ============================================================
CREATE TABLE IF NOT EXISTS notifications (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    alert_id UUID REFERENCES alerts(id) ON DELETE CASCADE,
    customer_id UUID NOT NULL REFERENCES customers(id) ON DELETE CASCADE,

    -- Delivery info
    channel TEXT NOT NULL,              -- whatsapp, email, sms
    recipient TEXT NOT NULL,            -- phone number or email
    message_preview TEXT,

    -- Status
    status TEXT DEFAULT 'pending',      -- pending, sent, delivered, failed
    sent_at TIMESTAMPTZ,
    delivered_at TIMESTAMPTZ,
    error_message TEXT,

    -- External reference
    external_id TEXT,                   -- ID from Fonnte/Resend

    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_notifications_alert ON notifications(alert_id);
CREATE INDEX IF NOT EXISTS idx_notifications_customer ON notifications(customer_id);
CREATE INDEX IF NOT EXISTS idx_notifications_status ON notifications(status);

-- ============================================================
-- NDVI HISTORY TABLE
-- ============================================================
CREATE TABLE IF NOT EXISTS ndvi_history (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    boundary_id UUID NOT NULL REFERENCES boundaries(id) ON DELETE CASCADE,

    -- Reading data
    date DATE NOT NULL,
    mean_ndvi NUMERIC,
    min_ndvi NUMERIC,
    max_ndvi NUMERIC,
    std_ndvi NUMERIC,

    -- Quality info
    cloud_cover_pct NUMERIC,
    valid_pixel_pct NUMERIC,
    observations INTEGER,               -- Number of satellite passes used

    -- Processing info
    processing_units_used NUMERIC,

    created_at TIMESTAMPTZ DEFAULT NOW(),

    -- Unique constraint per boundary per date
    UNIQUE(boundary_id, date)
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_ndvi_history_boundary ON ndvi_history(boundary_id);
CREATE INDEX IF NOT EXISTS idx_ndvi_history_date ON ndvi_history(date DESC);

-- ============================================================
-- SCAN LOGS TABLE
-- ============================================================
CREATE TABLE IF NOT EXISTS scan_logs (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),

    -- Scan info
    scan_type TEXT NOT NULL,            -- weekly, daily_fire, manual, on_demand
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ,

    -- Results
    status TEXT DEFAULT 'running',      -- running, completed, failed
    customers_scanned INTEGER DEFAULT 0,
    boundaries_scanned INTEGER DEFAULT 0,
    alerts_generated INTEGER DEFAULT 0,

    -- Resource usage
    processing_units_used NUMERIC,

    -- Error tracking
    error_message TEXT,
    error_details JSONB,

    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Index
CREATE INDEX IF NOT EXISTS idx_scan_logs_status ON scan_logs(status);
CREATE INDEX IF NOT EXISTS idx_scan_logs_started ON scan_logs(started_at DESC);

-- ============================================================
-- ROW LEVEL SECURITY (RLS)
-- ============================================================

-- Enable RLS on all tables
ALTER TABLE customers ENABLE ROW LEVEL SECURITY;
ALTER TABLE boundaries ENABLE ROW LEVEL SECURITY;
ALTER TABLE alerts ENABLE ROW LEVEL SECURITY;
ALTER TABLE notifications ENABLE ROW LEVEL SECURITY;
ALTER TABLE ndvi_history ENABLE ROW LEVEL SECURITY;
ALTER TABLE scan_logs ENABLE ROW LEVEL SECURITY;

-- Customers: Users can only see their own customer record
CREATE POLICY "Users can view own customer record"
    ON customers FOR SELECT
    USING (auth.uid() = auth_user_id);

CREATE POLICY "Users can update own customer record"
    ON customers FOR UPDATE
    USING (auth.uid() = auth_user_id);

-- Boundaries: Users can only see their own boundaries
CREATE POLICY "Users can view own boundaries"
    ON boundaries FOR SELECT
    USING (customer_id IN (
        SELECT id FROM customers WHERE auth_user_id = auth.uid()
    ));

CREATE POLICY "Users can insert own boundaries"
    ON boundaries FOR INSERT
    WITH CHECK (customer_id IN (
        SELECT id FROM customers WHERE auth_user_id = auth.uid()
    ));

CREATE POLICY "Users can update own boundaries"
    ON boundaries FOR UPDATE
    USING (customer_id IN (
        SELECT id FROM customers WHERE auth_user_id = auth.uid()
    ));

CREATE POLICY "Users can delete own boundaries"
    ON boundaries FOR DELETE
    USING (customer_id IN (
        SELECT id FROM customers WHERE auth_user_id = auth.uid()
    ));

-- Alerts: Users can only see their own alerts
CREATE POLICY "Users can view own alerts"
    ON alerts FOR SELECT
    USING (customer_id IN (
        SELECT id FROM customers WHERE auth_user_id = auth.uid()
    ));

CREATE POLICY "Users can update own alerts"
    ON alerts FOR UPDATE
    USING (customer_id IN (
        SELECT id FROM customers WHERE auth_user_id = auth.uid()
    ));

-- Notifications: Users can only see their own notifications
CREATE POLICY "Users can view own notifications"
    ON notifications FOR SELECT
    USING (customer_id IN (
        SELECT id FROM customers WHERE auth_user_id = auth.uid()
    ));

-- NDVI History: Users can only see their own boundary history
CREATE POLICY "Users can view own ndvi history"
    ON ndvi_history FOR SELECT
    USING (boundary_id IN (
        SELECT b.id FROM boundaries b
        JOIN customers c ON b.customer_id = c.id
        WHERE c.auth_user_id = auth.uid()
    ));

-- Scan logs: Only service role can access (for admin/backend)
CREATE POLICY "Service role can access scan logs"
    ON scan_logs FOR ALL
    USING (auth.role() = 'service_role');

-- ============================================================
-- SERVICE ROLE POLICIES
-- ============================================================
-- Allow service role (backend) full access for batch processing

CREATE POLICY "Service role full access to customers"
    ON customers FOR ALL
    USING (auth.role() = 'service_role');

CREATE POLICY "Service role full access to boundaries"
    ON boundaries FOR ALL
    USING (auth.role() = 'service_role');

CREATE POLICY "Service role full access to alerts"
    ON alerts FOR ALL
    USING (auth.role() = 'service_role');

CREATE POLICY "Service role full access to notifications"
    ON notifications FOR ALL
    USING (auth.role() = 'service_role');

CREATE POLICY "Service role full access to ndvi_history"
    ON ndvi_history FOR ALL
    USING (auth.role() = 'service_role');

-- ============================================================
-- FUNCTIONS
-- ============================================================

-- Function to update updated_at timestamp
CREATE OR REPLACE FUNCTION update_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Apply to tables
CREATE TRIGGER update_customers_updated_at
    BEFORE UPDATE ON customers
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at();

CREATE TRIGGER update_boundaries_updated_at
    BEFORE UPDATE ON boundaries
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at();

CREATE TRIGGER update_alerts_updated_at
    BEFORE UPDATE ON alerts
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at();

-- Function to calculate boundary hectares from GeoJSON
CREATE OR REPLACE FUNCTION calculate_boundary_area()
RETURNS TRIGGER AS $$
DECLARE
    coords JSONB;
    min_lon NUMERIC;
    max_lon NUMERIC;
    min_lat NUMERIC;
    max_lat NUMERIC;
    lat_mid NUMERIC;
    km_per_deg_lon NUMERIC;
    km_per_deg_lat NUMERIC;
    area_km2 NUMERIC;
BEGIN
    -- Extract coordinates from GeoJSON
    coords := NEW.geojson->'coordinates'->0;

    -- Calculate bounding box
    SELECT
        MIN((c->0)::NUMERIC),
        MAX((c->0)::NUMERIC),
        MIN((c->1)::NUMERIC),
        MAX((c->1)::NUMERIC)
    INTO min_lon, max_lon, min_lat, max_lat
    FROM jsonb_array_elements(coords) AS c;

    -- Calculate centroid
    NEW.centroid_lon := (min_lon + max_lon) / 2;
    NEW.centroid_lat := (min_lat + max_lat) / 2;

    -- Approximate area calculation
    lat_mid := (min_lat + max_lat) / 2;
    km_per_deg_lon := 111.32 * COS(RADIANS(lat_mid));
    km_per_deg_lat := 110.574;

    area_km2 := (max_lon - min_lon) * km_per_deg_lon * (max_lat - min_lat) * km_per_deg_lat;
    NEW.hectares := area_km2 * 100;

    RETURN NEW;
EXCEPTION
    WHEN OTHERS THEN
        -- If calculation fails, leave hectares as provided
        RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Apply area calculation trigger
CREATE TRIGGER calculate_boundary_area_trigger
    BEFORE INSERT OR UPDATE OF geojson ON boundaries
    FOR EACH ROW
    EXECUTE FUNCTION calculate_boundary_area();

-- Function to update customer total hectares
CREATE OR REPLACE FUNCTION update_customer_hectares()
RETURNS TRIGGER AS $$
BEGIN
    -- Update total hectares for the customer
    UPDATE customers
    SET total_hectares = (
        SELECT COALESCE(SUM(hectares), 0)
        FROM boundaries
        WHERE customer_id = COALESCE(NEW.customer_id, OLD.customer_id)
        AND status = 'active'
    )
    WHERE id = COALESCE(NEW.customer_id, OLD.customer_id);

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Apply to boundaries
CREATE TRIGGER update_customer_hectares_trigger
    AFTER INSERT OR UPDATE OR DELETE ON boundaries
    FOR EACH ROW
    EXECUTE FUNCTION update_customer_hectares();

-- ============================================================
-- VIEWS
-- ============================================================

-- Customer dashboard summary view
CREATE OR REPLACE VIEW customer_dashboard AS
SELECT
    c.id AS customer_id,
    c.name,
    c.company,
    c.plan,
    c.total_hectares,
    COUNT(DISTINCT b.id) AS boundary_count,
    COUNT(DISTINCT CASE WHEN a.status = 'new' THEN a.id END) AS open_alerts,
    COUNT(DISTINCT CASE WHEN a.status = 'new' AND a.severity IN ('high', 'critical') THEN a.id END) AS critical_alerts,
    AVG(b.current_ndvi) AS avg_ndvi,
    MAX(a.detected_at) AS last_alert_at,
    MAX(b.last_scan_at) AS last_scan_at
FROM customers c
LEFT JOIN boundaries b ON c.id = b.customer_id AND b.status = 'active'
LEFT JOIN alerts a ON c.id = a.customer_id
GROUP BY c.id;

-- Alert summary view
CREATE OR REPLACE VIEW alert_summary AS
SELECT
    a.*,
    b.name AS boundary_name,
    b.hectares AS boundary_hectares,
    c.name AS customer_name,
    c.email AS customer_email,
    c.phone AS customer_phone
FROM alerts a
LEFT JOIN boundaries b ON a.boundary_id = b.id
LEFT JOIN customers c ON a.customer_id = c.id;

-- ============================================================
-- SAMPLE DATA (for testing)
-- ============================================================

-- Uncomment to insert sample data for testing
/*
INSERT INTO customers (id, name, email, phone, company, plan, status)
VALUES
    ('a1b2c3d4-0000-0000-0000-000000000001', 'PT Sawit Makmur', 'ops@sawitmakmur.co.id', '6281234567890', 'Sawit Makmur Group', 'professional', 'active'),
    ('a1b2c3d4-0000-0000-0000-000000000002', 'Golden Palm Plantation', 'manager@goldenpalm.com', '6289876543210', 'Golden Palm Ltd', 'starter', 'active');

INSERT INTO boundaries (customer_id, name, geojson)
VALUES
    ('a1b2c3d4-0000-0000-0000-000000000001', 'Block A - Riau North', '{"type": "Polygon", "coordinates": [[[102.05, 1.45], [102.15, 1.45], [102.15, 1.55], [102.05, 1.55], [102.05, 1.45]]]}'),
    ('a1b2c3d4-0000-0000-0000-000000000001', 'Block B - Riau South', '{"type": "Polygon", "coordinates": [[[102.10, 1.30], [102.20, 1.30], [102.20, 1.40], [102.10, 1.40], [102.10, 1.30]]]}'),
    ('a1b2c3d4-0000-0000-0000-000000000002', 'Estate 1 - Jambi', '{"type": "Polygon", "coordinates": [[[103.50, -1.60], [103.60, -1.60], [103.60, -1.50], [103.50, -1.50], [103.50, -1.60]]]}');

INSERT INTO alerts (customer_id, boundary_id, type, severity, title, description, affected_hectares, status)
SELECT
    'a1b2c3d4-0000-0000-0000-000000000001',
    id,
    'deforestation',
    'high',
    'Deforestation detected in Block A',
    '2.3 hectares of vegetation loss detected in the northeast corner.',
    2.3,
    'new'
FROM boundaries WHERE name = 'Block A - Riau North';
*/

-- ============================================================
-- DONE
-- ============================================================
-- Schema setup complete. Next steps:
-- 1. Create Supabase project at https://supabase.com
-- 2. Run this SQL in the SQL Editor
-- 3. Copy your project URL and anon/service keys
-- 4. Update your .env files with the credentials
