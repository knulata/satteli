"""
Satteli - Deforestation Detection using Sentinel Hub
=====================================================

This script detects potential deforestation by comparing NDVI values
between two time periods using Sentinel-2 imagery via Sentinel Hub API.

Setup:
1. Create account at https://dataspace.copernicus.eu/
2. Get OAuth credentials from dashboard
3. Install: pip install sentinelhub
4. Set environment variables (see .env.example)

"""

import os
import numpy as np
from datetime import datetime, timedelta
from typing import Optional, Tuple
from dataclasses import dataclass

from sentinelhub import (
    SHConfig,
    SentinelHubRequest,
    SentinelHubStatistical,
    DataCollection,
    MimeType,
    CRS,
    BBox,
    Geometry,
    bbox_to_dimensions,
)

# Load configuration from environment
config = SHConfig()
config.sh_client_id = os.getenv('SH_CLIENT_ID', '')
config.sh_client_secret = os.getenv('SH_CLIENT_SECRET', '')
config.sh_base_url = os.getenv('SH_BASE_URL', 'https://sh.dataspace.copernicus.eu')
config.sh_token_url = os.getenv('SH_TOKEN_URL', 'https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token')


@dataclass
class DeforestationResult:
    """Result of deforestation analysis."""
    customer_id: str
    boundary_name: str
    analysis_date: str
    period_previous: str
    period_recent: str
    boundary_area_ha: float
    mean_ndvi_previous: Optional[float]
    mean_ndvi_recent: Optional[float]
    ndvi_change: Optional[float]
    deforestation_area_ha: Optional[float]
    deforestation_percentage: Optional[float]
    alert_triggered: bool
    severity: Optional[str]
    coordinates: Optional[dict]


# Evalscript for NDVI calculation
NDVI_EVALSCRIPT = """
//VERSION=3
function setup() {
    return {
        input: [{
            bands: ["B04", "B08", "SCL"],
            units: "DN"
        }],
        output: [
            { id: "ndvi", bands: 1, sampleType: "FLOAT32" },
            { id: "mask", bands: 1, sampleType: "UINT8" }
        ]
    };
}

function evaluatePixel(sample) {
    // Cloud masking using SCL band
    // SCL: 4=vegetation, 5=bare soil, 6=water - these are valid
    // SCL: 3=cloud shadow, 8=cloud medium, 9=cloud high, 10=cirrus - mask these
    let validPixel = (sample.SCL != 3 && sample.SCL != 8 && sample.SCL != 9 && sample.SCL != 10);

    // Calculate NDVI
    let ndvi = (sample.B08 - sample.B04) / (sample.B08 + sample.B04);

    return {
        ndvi: [validPixel ? ndvi : -9999],
        mask: [validPixel ? 1 : 0]
    };
}
"""

# Evalscript for change detection (returns difference between two dates)
CHANGE_DETECTION_EVALSCRIPT = """
//VERSION=3
function setup() {
    return {
        input: [{
            bands: ["B04", "B08", "SCL"],
            units: "DN"
        }],
        output: { bands: 1, sampleType: "FLOAT32" },
        mosaicking: "ORBIT"
    };
}

function preProcessScenes(collections) {
    // Sort by date
    collections.scenes.orbits.sort((a, b) => new Date(a.dateFrom) - new Date(b.dateFrom));
    return collections;
}

function evaluatePixel(samples) {
    if (samples.length < 2) return [-9999];

    // Get first (older) and last (recent) valid samples
    let older = null;
    let recent = null;

    for (let i = 0; i < samples.length; i++) {
        let s = samples[i];
        let valid = (s.SCL != 3 && s.SCL != 8 && s.SCL != 9 && s.SCL != 10);
        if (valid) {
            if (!older) older = s;
            recent = s;
        }
    }

    if (!older || !recent || older === recent) return [-9999];

    let ndvi_old = (older.B08 - older.B04) / (older.B08 + older.B04);
    let ndvi_new = (recent.B08 - recent.B04) / (recent.B08 + recent.B04);

    // Positive value = vegetation loss
    return [ndvi_old - ndvi_new];
}
"""

# Statistical evalscript for aggregated NDVI stats
STATS_EVALSCRIPT = """
//VERSION=3
function setup() {
    return {
        input: [{
            bands: ["B04", "B08", "SCL"]
        }],
        output: [
            { id: "ndvi", bands: 1, sampleType: "FLOAT32" },
            { id: "valid", bands: 1, sampleType: "UINT8" }
        ]
    };
}

function evaluatePixel(sample) {
    let valid = (sample.SCL >= 4 && sample.SCL <= 6);
    let ndvi = valid ? (sample.B08 - sample.B04) / (sample.B08 + sample.B04) : 0;
    return {
        ndvi: [ndvi],
        valid: [valid ? 1 : 0]
    };
}
"""


def geojson_to_bbox(geojson: dict) -> Tuple[BBox, float]:
    """Convert GeoJSON polygon to Sentinel Hub BBox and calculate area."""
    coords = geojson['coordinates'][0]
    lons = [c[0] for c in coords]
    lats = [c[1] for c in coords]

    bbox = BBox([min(lons), min(lats), max(lons), max(lats)], crs=CRS.WGS84)

    # Approximate area calculation (rough estimate)
    # More accurate would use pyproj for proper projection
    lat_mid = (min(lats) + max(lats)) / 2
    lon_diff = max(lons) - min(lons)
    lat_diff = max(lats) - min(lats)

    # Approximate degrees to km at this latitude
    km_per_deg_lon = 111.32 * np.cos(np.radians(lat_mid))
    km_per_deg_lat = 110.574

    area_km2 = (lon_diff * km_per_deg_lon) * (lat_diff * km_per_deg_lat)
    area_ha = area_km2 * 100

    return bbox, area_ha


def get_ndvi_stats(
    bbox: BBox,
    geometry: dict,
    start_date: str,
    end_date: str,
    config: SHConfig
) -> dict:
    """Get NDVI statistics for a geometry and time period using Statistical API."""

    request = SentinelHubStatistical(
        aggregation=SentinelHubStatistical.aggregation(
            evalscript=STATS_EVALSCRIPT,
            time_interval=(start_date, end_date),
            aggregation_interval='P1D',
            resolution=(10, 10)
        ),
        input_data=[
            SentinelHubStatistical.input_data(
                DataCollection.SENTINEL2_L2A,
                maxcc=0.3
            )
        ],
        geometry=Geometry(geometry, crs=CRS.WGS84),
        config=config
    )

    try:
        response = request.get_data()[0]

        # Aggregate stats across all dates
        ndvi_values = []
        for interval in response.get('data', []):
            outputs = interval.get('outputs', {})
            ndvi_stats = outputs.get('ndvi', {}).get('bands', {}).get('B0', {}).get('stats', {})
            if ndvi_stats.get('mean') is not None:
                ndvi_values.append(ndvi_stats['mean'])

        if ndvi_values:
            return {
                'mean': np.mean(ndvi_values),
                'min': np.min(ndvi_values),
                'max': np.max(ndvi_values),
                'count': len(ndvi_values)
            }
    except Exception as e:
        print(f"Stats request failed: {e}")

    return {'mean': None, 'min': None, 'max': None, 'count': 0}


def detect_deforestation(
    boundary_geojson: dict,
    customer_id: str,
    boundary_name: str = "Unknown",
    days_back: int = 30,
    ndvi_threshold: float = 0.3,
    min_area_ha: float = 0.5,
    resolution: int = 10
) -> DeforestationResult:
    """
    Detect deforestation within a boundary by comparing NDVI between two periods.

    Args:
        boundary_geojson: GeoJSON polygon of the area to monitor
        customer_id: Customer identifier for tracking
        boundary_name: Human-readable name of the boundary
        days_back: Number of days for each comparison period (default 30)
        ndvi_threshold: Minimum NDVI decrease to flag as deforestation (default 0.3)
        min_area_ha: Minimum affected area to trigger alert (default 0.5 ha)
        resolution: Output resolution in meters (default 10)

    Returns:
        DeforestationResult with detection results
    """

    # Convert GeoJSON to BBox
    bbox, boundary_area_ha = geojson_to_bbox(boundary_geojson)

    # Calculate dimensions at given resolution
    size = bbox_to_dimensions(bbox, resolution=resolution)

    # Define time periods
    today = datetime.now()
    recent_end = today
    recent_start = today - timedelta(days=days_back)
    previous_end = recent_start
    previous_start = previous_end - timedelta(days=days_back)

    recent_start_str = recent_start.strftime('%Y-%m-%d')
    recent_end_str = recent_end.strftime('%Y-%m-%d')
    previous_start_str = previous_start.strftime('%Y-%m-%d')
    previous_end_str = previous_end.strftime('%Y-%m-%d')

    print(f"Analyzing: {boundary_name}")
    print(f"  Previous period: {previous_start_str} to {previous_end_str}")
    print(f"  Recent period: {recent_start_str} to {recent_end_str}")
    print(f"  Boundary area: {boundary_area_ha:.1f} ha")

    # Get NDVI stats for both periods
    print("  Fetching previous period NDVI...")
    stats_previous = get_ndvi_stats(
        bbox, boundary_geojson,
        previous_start_str, previous_end_str,
        config
    )

    print("  Fetching recent period NDVI...")
    stats_recent = get_ndvi_stats(
        bbox, boundary_geojson,
        recent_start_str, recent_end_str,
        config
    )

    # Calculate change
    ndvi_change = None
    deforestation_area_ha = None
    deforestation_pct = None
    alert_triggered = False
    severity = None

    if stats_previous['mean'] is not None and stats_recent['mean'] is not None:
        ndvi_change = stats_previous['mean'] - stats_recent['mean']

        print(f"  NDVI Previous: {stats_previous['mean']:.3f}")
        print(f"  NDVI Recent: {stats_recent['mean']:.3f}")
        print(f"  NDVI Change: {ndvi_change:.3f}")

        # If significant decrease and area was previously vegetated
        if ndvi_change > ndvi_threshold and stats_previous['mean'] > 0.4:
            # Estimate affected area (rough - assumes uniform change)
            # In production, you'd use pixel-level analysis
            deforestation_pct = min(100, (ndvi_change / stats_previous['mean']) * 100)
            deforestation_area_ha = boundary_area_ha * (deforestation_pct / 100)

            if deforestation_area_ha >= min_area_ha:
                alert_triggered = True
                severity = classify_severity(deforestation_area_ha)
                print(f"  ⚠️ ALERT: {deforestation_area_ha:.2f} ha deforestation detected!")

    return DeforestationResult(
        customer_id=customer_id,
        boundary_name=boundary_name,
        analysis_date=today.strftime('%Y-%m-%d'),
        period_previous=f"{previous_start_str} to {previous_end_str}",
        period_recent=f"{recent_start_str} to {recent_end_str}",
        boundary_area_ha=boundary_area_ha,
        mean_ndvi_previous=stats_previous['mean'],
        mean_ndvi_recent=stats_recent['mean'],
        ndvi_change=ndvi_change,
        deforestation_area_ha=deforestation_area_ha,
        deforestation_percentage=deforestation_pct,
        alert_triggered=alert_triggered,
        severity=severity,
        coordinates=None  # Would need pixel-level analysis for precise location
    )


def get_ndvi_image(
    bbox: BBox,
    start_date: str,
    end_date: str,
    resolution: int = 10
) -> np.ndarray:
    """Download NDVI image for visualization."""

    size = bbox_to_dimensions(bbox, resolution=resolution)

    request = SentinelHubRequest(
        evalscript=NDVI_EVALSCRIPT,
        input_data=[
            SentinelHubRequest.input_data(
                data_collection=DataCollection.SENTINEL2_L2A,
                time_interval=(start_date, end_date),
                maxcc=0.3
            )
        ],
        responses=[
            SentinelHubRequest.output_response('ndvi', MimeType.TIFF)
        ],
        bbox=bbox,
        size=size,
        config=config
    )

    return request.get_data()[0]


def classify_severity(area_ha: float) -> str:
    """Classify alert severity based on affected area."""
    if area_ha >= 10:
        return 'critical'
    elif area_ha >= 5:
        return 'high'
    elif area_ha >= 1:
        return 'medium'
    else:
        return 'low'


def detect_fire_hotspots(
    boundary_geojson: dict,
    customer_id: str,
    boundary_name: str = "Unknown",
    days_back: int = 7
) -> dict:
    """
    Detect fire hotspots using NASA FIRMS API.

    Note: This uses the free NASA FIRMS API, not Sentinel Hub.
    FIRMS provides near-real-time fire data from MODIS and VIIRS.

    Args:
        boundary_geojson: GeoJSON polygon
        customer_id: Customer identifier
        boundary_name: Human-readable name
        days_back: Number of days to check (default 7, max 10)

    Returns:
        dict with fire detection results
    """
    import requests

    # NASA FIRMS API - free, no auth required for basic access
    # For production, register for a MAP_KEY at https://firms.modaps.eosdis.nasa.gov/api/
    FIRMS_URL = "https://firms.modaps.eosdis.nasa.gov/api/country/csv"
    MAP_KEY = os.getenv('NASA_FIRMS_KEY', 'DEMO_KEY')

    # Get bounding box
    coords = boundary_geojson['coordinates'][0]
    lons = [c[0] for c in coords]
    lats = [c[1] for c in coords]

    min_lon, max_lon = min(lons), max(lons)
    min_lat, max_lat = min(lats), max(lats)

    # FIRMS area API
    area_url = f"https://firms.modaps.eosdis.nasa.gov/api/area/csv/{MAP_KEY}/VIIRS_SNPP_NRT/{min_lon},{min_lat},{max_lon},{max_lat}/{days_back}"

    fire_count = 0
    try:
        response = requests.get(area_url, timeout=30)
        if response.status_code == 200:
            lines = response.text.strip().split('\n')
            fire_count = max(0, len(lines) - 1)  # Subtract header row
    except Exception as e:
        print(f"FIRMS API error: {e}")

    result = {
        'customer_id': customer_id,
        'boundary_name': boundary_name,
        'analysis_date': datetime.now().strftime('%Y-%m-%d'),
        'period': f"Last {days_back} days",
        'fire_detections': fire_count,
        'alert_triggered': fire_count > 0,
        'severity': 'critical' if fire_count >= 5 else 'high' if fire_count > 0 else None
    }

    return result


def calculate_ndvi_trend(
    boundary_geojson: dict,
    customer_id: str,
    months_back: int = 6
) -> list:
    """
    Calculate monthly NDVI trend for crop health monitoring.

    Args:
        boundary_geojson: GeoJSON polygon
        customer_id: Customer identifier
        months_back: Number of months of history

    Returns:
        List of monthly NDVI readings
    """

    bbox, _ = geojson_to_bbox(boundary_geojson)
    today = datetime.now()
    results = []

    for i in range(months_back):
        month_end = today - timedelta(days=30 * i)
        month_start = month_end - timedelta(days=30)

        start_str = month_start.strftime('%Y-%m-%d')
        end_str = month_end.strftime('%Y-%m-%d')

        stats = get_ndvi_stats(bbox, boundary_geojson, start_str, end_str, config)

        results.append({
            'customer_id': customer_id,
            'month': month_start.strftime('%Y-%m'),
            'mean_ndvi': stats['mean'],
            'min_ndvi': stats['min'],
            'max_ndvi': stats['max'],
            'observations': stats['count']
        })

    return results


# ============================================================================
# EXAMPLE USAGE
# ============================================================================

if __name__ == "__main__":

    # Check configuration
    if not config.sh_client_id or not config.sh_client_secret:
        print("⚠️  Sentinel Hub credentials not configured!")
        print("   Set SH_CLIENT_ID and SH_CLIENT_SECRET environment variables")
        print("   Get credentials from: https://dataspace.copernicus.eu/")
        print("\nRunning in demo mode with sample output...\n")

        # Demo output
        print("=" * 60)
        print("SATTELI - Deforestation Detection (DEMO)")
        print("=" * 60)
        print("\nSample analysis for Block A - Riau:")
        print("  Previous NDVI: 0.72")
        print("  Recent NDVI: 0.68")
        print("  Change: 0.04 (within normal range)")
        print("  ✅ No significant deforestation detected")
        print("\n" + "=" * 60)
        exit(0)

    # Example: Palm oil plantation boundary in Riau, Indonesia
    sample_boundary = {
        "type": "Polygon",
        "coordinates": [[
            [102.05, 1.45],
            [102.15, 1.45],
            [102.15, 1.55],
            [102.05, 1.55],
            [102.05, 1.45]
        ]]
    }

    print("=" * 60)
    print("SATTELI - Deforestation Detection (Sentinel Hub)")
    print("=" * 60)

    # Run deforestation detection
    result = detect_deforestation(
        boundary_geojson=sample_boundary,
        customer_id="CUST001",
        boundary_name="Block A - Riau North",
        days_back=30,
        ndvi_threshold=0.3,
        min_area_ha=0.5
    )

    print("\n📊 ANALYSIS RESULTS:")
    print("-" * 40)
    print(f"Customer: {result.customer_id}")
    print(f"Boundary: {result.boundary_name}")
    print(f"Analysis Date: {result.analysis_date}")
    print(f"Boundary Area: {result.boundary_area_ha:.1f} ha")

    if result.mean_ndvi_previous:
        print(f"\nNDVI Previous: {result.mean_ndvi_previous:.3f}")
        print(f"NDVI Recent: {result.mean_ndvi_recent:.3f}")
        print(f"NDVI Change: {result.ndvi_change:.3f}")

    if result.alert_triggered:
        print(f"\n⚠️  ALERT TRIGGERED!")
        print(f"   Severity: {result.severity.upper()}")
        print(f"   Affected Area: {result.deforestation_area_ha:.2f} ha")
    else:
        print(f"\n✅ No significant deforestation detected")

    # Run fire detection
    print("\n" + "=" * 60)
    print("🔥 FIRE DETECTION")
    print("-" * 40)

    fire_result = detect_fire_hotspots(
        boundary_geojson=sample_boundary,
        customer_id="CUST001",
        boundary_name="Block A - Riau North",
        days_back=7
    )

    print(f"Fire detections in last 7 days: {fire_result['fire_detections']}")
    if fire_result['alert_triggered']:
        print(f"⚠️  FIRE ALERT! Severity: {fire_result['severity'].upper()}")
    else:
        print("✅ No fire hotspots detected")

    print("\n" + "=" * 60)
    print("Analysis complete.")
