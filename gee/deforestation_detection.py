"""
Satteli - Deforestation Detection using Google Earth Engine
============================================================

This script detects potential deforestation by comparing NDVI values
between two time periods using Sentinel-2 imagery.

Setup:
1. Create a GEE account: https://earthengine.google.com/signup/
2. Install: pip install earthengine-api
3. Authenticate: earthengine authenticate
4. Run: python deforestation_detection.py

"""

import ee
import json
from datetime import datetime, timedelta
from typing import Optional

# Initialize Earth Engine
# For first run, use: ee.Authenticate()
ee.Initialize(project='your-gee-project-id')  # Replace with your project ID


def detect_deforestation(
    boundary_geojson: dict,
    customer_id: str,
    boundary_name: str = "Unknown",
    days_back: int = 30,
    ndvi_threshold: float = 0.3,
    min_area_ha: float = 0.5,
    cloud_cover_max: int = 20
) -> dict:
    """
    Detect deforestation within a boundary by comparing NDVI between two periods.

    Args:
        boundary_geojson: GeoJSON polygon of the area to monitor
        customer_id: Customer identifier for tracking
        boundary_name: Human-readable name of the boundary
        days_back: Number of days for each comparison period (default 30)
        ndvi_threshold: Minimum NDVI decrease to flag as deforestation (default 0.3)
        min_area_ha: Minimum affected area to trigger alert (default 0.5 ha)
        cloud_cover_max: Maximum cloud cover percentage (default 20%)

    Returns:
        dict with detection results
    """

    # Convert GeoJSON to Earth Engine geometry
    geometry = ee.Geometry(boundary_geojson)

    # Define time periods
    today = datetime.now()

    # Recent period (last N days)
    recent_end = today
    recent_start = today - timedelta(days=days_back)

    # Previous period (N to 2N days ago)
    previous_end = recent_start
    previous_start = previous_end - timedelta(days=days_back)

    # Format dates for GEE
    recent_start_str = recent_start.strftime('%Y-%m-%d')
    recent_end_str = recent_end.strftime('%Y-%m-%d')
    previous_start_str = previous_start.strftime('%Y-%m-%d')
    previous_end_str = previous_end.strftime('%Y-%m-%d')

    print(f"Comparing periods:")
    print(f"  Previous: {previous_start_str} to {previous_end_str}")
    print(f"  Recent:   {recent_start_str} to {recent_end_str}")

    # Get Sentinel-2 Surface Reflectance imagery
    def get_sentinel2_composite(start_date: str, end_date: str) -> ee.Image:
        """Get cloud-masked Sentinel-2 composite for a date range."""

        collection = (ee.ImageCollection('COPERNICUS/S2_SR_HARMONIZED')
            .filterBounds(geometry)
            .filterDate(start_date, end_date)
            .filter(ee.Filter.lt('CLOUDY_PIXEL_PERCENTAGE', cloud_cover_max)))

        # Cloud masking function using SCL band
        def mask_clouds(image):
            scl = image.select('SCL')
            # SCL values: 4=vegetation, 5=bare soil, 6=water, 7=cloud low prob
            # Exclude: 3=cloud shadow, 8=cloud medium, 9=cloud high, 10=cirrus
            mask = scl.neq(3).And(scl.neq(8)).And(scl.neq(9)).And(scl.neq(10))
            return image.updateMask(mask)

        # Apply cloud masking and create median composite
        masked = collection.map(mask_clouds)
        composite = masked.median()

        return composite

    # Get composites for both periods
    recent_composite = get_sentinel2_composite(recent_start_str, recent_end_str)
    previous_composite = get_sentinel2_composite(previous_start_str, previous_end_str)

    # Calculate NDVI for both periods
    # NDVI = (NIR - Red) / (NIR + Red)
    # Sentinel-2: B8 = NIR, B4 = Red
    ndvi_recent = recent_composite.normalizedDifference(['B8', 'B4']).rename('ndvi_recent')
    ndvi_previous = previous_composite.normalizedDifference(['B8', 'B4']).rename('ndvi_previous')

    # Calculate NDVI change (positive = vegetation loss)
    ndvi_change = ndvi_previous.subtract(ndvi_recent).rename('ndvi_change')

    # Identify deforestation pixels (significant NDVI decrease)
    # Also filter to areas that were previously vegetated (NDVI > 0.4)
    deforestation_mask = (ndvi_change.gt(ndvi_threshold)
        .And(ndvi_previous.gt(0.4)))  # Was vegetated before

    # Calculate affected area in hectares
    pixel_area = ee.Image.pixelArea()
    deforestation_area = deforestation_mask.multiply(pixel_area)

    total_area = deforestation_area.reduceRegion(
        reducer=ee.Reducer.sum(),
        geometry=geometry,
        scale=10,  # Sentinel-2 resolution
        maxPixels=1e9
    )

    # Get the area value and convert to hectares
    area_m2 = total_area.get('ndvi_change')
    area_ha = ee.Number(area_m2).divide(10000)

    # Get boundary total area for context
    boundary_area_ha = geometry.area().divide(10000)

    # Calculate mean NDVI values for reporting
    mean_ndvi_recent = ndvi_recent.reduceRegion(
        reducer=ee.Reducer.mean(),
        geometry=geometry,
        scale=10,
        maxPixels=1e9
    ).get('ndvi_recent')

    mean_ndvi_previous = ndvi_previous.reduceRegion(
        reducer=ee.Reducer.mean(),
        geometry=geometry,
        scale=10,
        maxPixels=1e9
    ).get('ndvi_previous')

    # Find centroid of largest deforestation cluster (for alert location)
    # This is a simplified approach - for production, use connected components
    deforestation_vectors = deforestation_mask.selfMask().reduceToVectors(
        geometry=geometry,
        scale=10,
        maxPixels=1e9,
        geometryType='polygon',
        eightConnected=True
    )

    # Get centroid of the affected area
    affected_centroid = deforestation_mask.selfMask().reduceRegion(
        reducer=ee.Reducer.centroid(),
        geometry=geometry,
        scale=10,
        maxPixels=1e9
    )

    # Compile results
    results = {
        'customer_id': customer_id,
        'boundary_name': boundary_name,
        'analysis_date': today.strftime('%Y-%m-%d'),
        'period_previous': f"{previous_start_str} to {previous_end_str}",
        'period_recent': f"{recent_start_str} to {recent_end_str}",
        'boundary_area_ha': boundary_area_ha.getInfo(),
        'deforestation_area_ha': area_ha.getInfo(),
        'mean_ndvi_previous': mean_ndvi_previous.getInfo(),
        'mean_ndvi_recent': mean_ndvi_recent.getInfo(),
        'ndvi_change_threshold': ndvi_threshold,
        'alert_triggered': False,
        'centroid': None
    }

    # Check if alert should be triggered
    if results['deforestation_area_ha'] and results['deforestation_area_ha'] >= min_area_ha:
        results['alert_triggered'] = True
        results['severity'] = classify_severity(results['deforestation_area_ha'])

        # Try to get centroid coordinates
        try:
            centroid_coords = affected_centroid.getInfo()
            if centroid_coords and 'ndvi_change' in centroid_coords:
                coords = centroid_coords['ndvi_change']['coordinates']
                results['centroid'] = {'lon': coords[0], 'lat': coords[1]}
        except:
            pass

    return results


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
    Detect fire hotspots within a boundary using FIRMS VIIRS data.

    Args:
        boundary_geojson: GeoJSON polygon of the area to monitor
        customer_id: Customer identifier
        boundary_name: Human-readable name
        days_back: Number of days to check (default 7)

    Returns:
        dict with fire detection results
    """

    geometry = ee.Geometry(boundary_geojson)

    today = datetime.now()
    start_date = (today - timedelta(days=days_back)).strftime('%Y-%m-%d')
    end_date = today.strftime('%Y-%m-%d')

    # Use FIRMS VIIRS active fire data
    fires = (ee.ImageCollection('FIRMS')
        .filterBounds(geometry)
        .filterDate(start_date, end_date)
        .select('T21'))  # Brightness temperature

    # Count fire pixels
    fire_count = fires.count().reduceRegion(
        reducer=ee.Reducer.sum(),
        geometry=geometry,
        scale=375,  # VIIRS resolution
        maxPixels=1e9
    )

    count = fire_count.get('T21')
    count_value = ee.Algorithms.If(count, count, 0)

    results = {
        'customer_id': customer_id,
        'boundary_name': boundary_name,
        'analysis_date': today.strftime('%Y-%m-%d'),
        'period': f"{start_date} to {end_date}",
        'fire_detections': ee.Number(count_value).getInfo(),
        'alert_triggered': False
    }

    if results['fire_detections'] and results['fire_detections'] > 0:
        results['alert_triggered'] = True
        results['severity'] = 'critical' if results['fire_detections'] >= 5 else 'high'

    return results


def calculate_ndvi_trend(
    boundary_geojson: dict,
    customer_id: str,
    months_back: int = 12
) -> list:
    """
    Calculate monthly NDVI trend for a boundary.
    Useful for crop health monitoring and yield prediction.

    Args:
        boundary_geojson: GeoJSON polygon
        customer_id: Customer identifier
        months_back: Number of months of history (default 12)

    Returns:
        List of monthly NDVI readings
    """

    geometry = ee.Geometry(boundary_geojson)

    today = datetime.now()
    results = []

    for i in range(months_back):
        # Calculate month boundaries
        month_end = today - timedelta(days=30 * i)
        month_start = month_end - timedelta(days=30)

        start_str = month_start.strftime('%Y-%m-%d')
        end_str = month_end.strftime('%Y-%m-%d')

        # Get Sentinel-2 composite
        collection = (ee.ImageCollection('COPERNICUS/S2_SR_HARMONIZED')
            .filterBounds(geometry)
            .filterDate(start_str, end_str)
            .filter(ee.Filter.lt('CLOUDY_PIXEL_PERCENTAGE', 30)))

        composite = collection.median()
        ndvi = composite.normalizedDifference(['B8', 'B4'])

        # Calculate statistics
        stats = ndvi.reduceRegion(
            reducer=ee.Reducer.mean().combine(
                ee.Reducer.minMax(), sharedInputs=True
            ),
            geometry=geometry,
            scale=10,
            maxPixels=1e9
        )

        try:
            stats_info = stats.getInfo()
            results.append({
                'customer_id': customer_id,
                'month': month_start.strftime('%Y-%m'),
                'mean_ndvi': stats_info.get('nd_mean'),
                'min_ndvi': stats_info.get('nd_min'),
                'max_ndvi': stats_info.get('nd_max'),
                'image_count': collection.size().getInfo()
            })
        except Exception as e:
            print(f"Error processing {month_start.strftime('%Y-%m')}: {e}")
            results.append({
                'customer_id': customer_id,
                'month': month_start.strftime('%Y-%m'),
                'mean_ndvi': None,
                'error': str(e)
            })

    return results


def export_change_image(
    boundary_geojson: dict,
    output_name: str,
    days_back: int = 30
) -> str:
    """
    Export before/after satellite images for visual comparison.

    Args:
        boundary_geojson: GeoJSON polygon
        output_name: Name for the export task
        days_back: Days for comparison period

    Returns:
        Task ID for the export
    """

    geometry = ee.Geometry(boundary_geojson)

    today = datetime.now()
    recent_start = (today - timedelta(days=days_back)).strftime('%Y-%m-%d')
    recent_end = today.strftime('%Y-%m-%d')
    previous_start = (today - timedelta(days=days_back*2)).strftime('%Y-%m-%d')
    previous_end = recent_start

    # Get composites
    def get_composite(start, end):
        return (ee.ImageCollection('COPERNICUS/S2_SR_HARMONIZED')
            .filterBounds(geometry)
            .filterDate(start, end)
            .filter(ee.Filter.lt('CLOUDY_PIXEL_PERCENTAGE', 20))
            .median()
            .select(['B4', 'B3', 'B2'])  # RGB
            .clip(geometry))

    recent = get_composite(recent_start, recent_end)
    previous = get_composite(previous_start, previous_end)

    # Stack images side by side (for comparison)
    # Export to Google Drive
    task_recent = ee.batch.Export.image.toDrive(
        image=recent,
        description=f"{output_name}_recent",
        folder='satteli_exports',
        region=geometry,
        scale=10,
        maxPixels=1e9
    )

    task_previous = ee.batch.Export.image.toDrive(
        image=previous,
        description=f"{output_name}_previous",
        folder='satteli_exports',
        region=geometry,
        scale=10,
        maxPixels=1e9
    )

    task_recent.start()
    task_previous.start()

    return {
        'recent_task': task_recent.id,
        'previous_task': task_previous.id
    }


# ============================================================================
# EXAMPLE USAGE
# ============================================================================

if __name__ == "__main__":

    # Example: Palm oil plantation boundary in Riau, Indonesia
    # This is a sample polygon - replace with real customer boundaries
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
    print("SATTELI - Deforestation Detection")
    print("=" * 60)

    # Run deforestation detection
    results = detect_deforestation(
        boundary_geojson=sample_boundary,
        customer_id="CUST001",
        boundary_name="Block A - Riau North",
        days_back=30,
        ndvi_threshold=0.3,
        min_area_ha=0.5
    )

    print("\n📊 ANALYSIS RESULTS:")
    print("-" * 40)
    print(f"Customer: {results['customer_id']}")
    print(f"Boundary: {results['boundary_name']}")
    print(f"Analysis Date: {results['analysis_date']}")
    print(f"Previous Period: {results['period_previous']}")
    print(f"Recent Period: {results['period_recent']}")
    print(f"\nBoundary Area: {results['boundary_area_ha']:.1f} ha")
    print(f"Mean NDVI (Previous): {results['mean_ndvi_previous']:.3f}" if results['mean_ndvi_previous'] else "Mean NDVI (Previous): N/A")
    print(f"Mean NDVI (Recent): {results['mean_ndvi_recent']:.3f}" if results['mean_ndvi_recent'] else "Mean NDVI (Recent): N/A")
    print(f"\n🌲 Deforestation Detected: {results['deforestation_area_ha']:.2f} ha" if results['deforestation_area_ha'] else "\n🌲 Deforestation Detected: 0 ha")

    if results['alert_triggered']:
        print(f"\n⚠️  ALERT TRIGGERED!")
        print(f"   Severity: {results['severity'].upper()}")
        if results['centroid']:
            print(f"   Location: {results['centroid']['lat']:.4f}, {results['centroid']['lon']:.4f}")
    else:
        print(f"\n✅ No significant deforestation detected")

    print("\n" + "=" * 60)

    # Run fire detection
    print("\n🔥 FIRE DETECTION")
    print("-" * 40)

    fire_results = detect_fire_hotspots(
        boundary_geojson=sample_boundary,
        customer_id="CUST001",
        boundary_name="Block A - Riau North",
        days_back=7
    )

    print(f"Fire detections in last 7 days: {fire_results['fire_detections']}")
    if fire_results['alert_triggered']:
        print(f"⚠️  FIRE ALERT! Severity: {fire_results['severity'].upper()}")
    else:
        print("✅ No fire hotspots detected")

    print("\n" + "=" * 60)
    print("Analysis complete.")
