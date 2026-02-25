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


@dataclass
class PlantHealthResult:
    """Result of plant health analysis."""
    customer_id: str
    boundary_id: str
    boundary_name: str
    analysis_date: str
    boundary_area_ha: float
    # Current NDVI metrics
    mean_ndvi: Optional[float]
    min_ndvi: Optional[float]
    max_ndvi: Optional[float]
    ndvi_std: Optional[float]
    # Health classification
    health_status: str  # healthy, moderate, stressed, critical, unknown
    health_score: Optional[int]  # 0-100
    # Comparison to baseline
    baseline_ndvi: Optional[float]
    ndvi_change_from_baseline: Optional[float]
    # Stress zones
    stressed_area_ha: Optional[float]
    stressed_percentage: Optional[float]
    # Recommendations
    recommendations: list
    # Alert
    alert_triggered: bool
    severity: Optional[str]


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


def classify_plant_health(ndvi: float) -> Tuple[str, int]:
    """
    Classify plant health based on NDVI value.

    For palm oil plantations:
    - Healthy mature palms: NDVI 0.6 - 0.9
    - Moderate/young palms: NDVI 0.4 - 0.6
    - Stressed: NDVI 0.2 - 0.4
    - Critical/dead: NDVI < 0.2

    Returns:
        Tuple of (health_status, health_score 0-100)
    """
    if ndvi is None:
        return 'unknown', 0

    if ndvi >= 0.7:
        return 'healthy', int(min(100, 70 + (ndvi - 0.7) * 100))
    elif ndvi >= 0.5:
        return 'healthy', int(50 + (ndvi - 0.5) * 100)
    elif ndvi >= 0.4:
        return 'moderate', int(40 + (ndvi - 0.4) * 100)
    elif ndvi >= 0.3:
        return 'stressed', int(25 + (ndvi - 0.3) * 150)
    elif ndvi >= 0.2:
        return 'stressed', int(15 + (ndvi - 0.2) * 100)
    else:
        return 'critical', int(max(0, ndvi * 75))


def get_health_recommendations(health_status: str, ndvi: float, ndvi_change: Optional[float]) -> list:
    """Generate actionable recommendations based on plant health status."""

    recommendations = []

    if health_status == 'critical':
        recommendations.append("URGENT: Immediate field inspection required")
        recommendations.append("Check for disease outbreak (Ganoderma, Basal Stem Rot)")
        recommendations.append("Assess irrigation/drainage issues")
        recommendations.append("Consider soil nutrient analysis")

    elif health_status == 'stressed':
        recommendations.append("Schedule field inspection within 1 week")
        recommendations.append("Check soil moisture levels")
        recommendations.append("Review fertilization program")
        if ndvi_change and ndvi_change < -0.1:
            recommendations.append("Rapid decline detected - investigate pest/disease")

    elif health_status == 'moderate':
        recommendations.append("Monitor closely over next 2 weeks")
        recommendations.append("Consider targeted fertilizer application")
        if ndvi and ndvi < 0.45:
            recommendations.append("Young palms may need additional nutrients")

    elif health_status == 'healthy':
        recommendations.append("Continue current management practices")
        if ndvi and ndvi > 0.8:
            recommendations.append("Excellent vegetation density")

    return recommendations


def analyze_plant_health(
    boundary_geojson: dict,
    customer_id: str,
    boundary_id: str,
    boundary_name: str = "Unknown",
    baseline_ndvi: Optional[float] = None,
    stress_threshold: float = 0.4
) -> PlantHealthResult:
    """
    Analyze plant health for a boundary using NDVI metrics.

    Args:
        boundary_geojson: GeoJSON polygon of the area
        customer_id: Customer identifier
        boundary_id: Boundary identifier
        boundary_name: Human-readable name
        baseline_ndvi: Expected NDVI for healthy vegetation (optional)
        stress_threshold: NDVI below this is considered stressed (default 0.4)

    Returns:
        PlantHealthResult with health analysis
    """

    bbox, boundary_area_ha = geojson_to_bbox(boundary_geojson)
    today = datetime.now()

    # Get current NDVI (last 14 days for cloud-free composite)
    recent_start = (today - timedelta(days=14)).strftime('%Y-%m-%d')
    recent_end = today.strftime('%Y-%m-%d')

    print(f"  Analyzing plant health for: {boundary_name}")
    print(f"  Period: {recent_start} to {recent_end}")

    # Get NDVI statistics
    stats = get_ndvi_stats(bbox, boundary_geojson, recent_start, recent_end, config)

    mean_ndvi = stats.get('mean')
    min_ndvi = stats.get('min')
    max_ndvi = stats.get('max')

    # Calculate standard deviation estimate from min/max
    ndvi_std = None
    if min_ndvi is not None and max_ndvi is not None:
        ndvi_std = (max_ndvi - min_ndvi) / 4  # Rough estimate

    # Classify health
    health_status, health_score = classify_plant_health(mean_ndvi)

    # Compare to baseline
    ndvi_change_from_baseline = None
    if baseline_ndvi and mean_ndvi:
        ndvi_change_from_baseline = mean_ndvi - baseline_ndvi

    # Estimate stressed area
    stressed_area_ha = None
    stressed_percentage = None
    if min_ndvi is not None and mean_ndvi is not None:
        # Rough estimate: assume normal distribution
        if min_ndvi < stress_threshold:
            # Estimate percentage below threshold
            if ndvi_std and ndvi_std > 0:
                z_score = (stress_threshold - mean_ndvi) / ndvi_std
                # Simple approximation of CDF
                if z_score > 0:
                    stressed_percentage = min(100, max(0, 50 + z_score * 30))
                else:
                    stressed_percentage = max(0, 50 + z_score * 30)
            else:
                stressed_percentage = 10 if mean_ndvi > stress_threshold else 50
            stressed_area_ha = boundary_area_ha * (stressed_percentage / 100)

    # Generate recommendations
    recommendations = get_health_recommendations(health_status, mean_ndvi, ndvi_change_from_baseline)

    # Determine if alert should be triggered
    alert_triggered = health_status in ['stressed', 'critical']
    severity = None
    if alert_triggered:
        if health_status == 'critical':
            severity = 'critical'
        elif stressed_area_ha and stressed_area_ha > 10:
            severity = 'high'
        else:
            severity = 'medium'

    # Log results
    if mean_ndvi:
        print(f"  Mean NDVI: {mean_ndvi:.3f}")
        print(f"  Health Status: {health_status.upper()} (Score: {health_score}/100)")
        if alert_triggered:
            print(f"  ⚠️ HEALTH ALERT: {severity.upper()}")
    else:
        print(f"  ⚠️ Could not retrieve NDVI data")

    return PlantHealthResult(
        customer_id=customer_id,
        boundary_id=boundary_id,
        boundary_name=boundary_name,
        analysis_date=today.strftime('%Y-%m-%d'),
        boundary_area_ha=boundary_area_ha,
        mean_ndvi=mean_ndvi,
        min_ndvi=min_ndvi,
        max_ndvi=max_ndvi,
        ndvi_std=ndvi_std,
        health_status=health_status,
        health_score=health_score,
        baseline_ndvi=baseline_ndvi,
        ndvi_change_from_baseline=ndvi_change_from_baseline,
        stressed_area_ha=stressed_area_ha,
        stressed_percentage=stressed_percentage,
        recommendations=recommendations,
        alert_triggered=alert_triggered,
        severity=severity
    )


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
