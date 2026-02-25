"""
Satteli - Batch Scanner (Sentinel Hub)
======================================

Scans all customer boundaries and sends alerts for detected changes.
Run this as a weekly cron job.

Usage:
    python batch_scanner.py                    # Scan all customers
    python batch_scanner.py --customer CUST001 # Scan specific customer
    python batch_scanner.py --dry-run          # Test without sending alerts

"""

import os
import json
import argparse
from datetime import datetime
from typing import Optional
import requests
from dataclasses import asdict

from deforestation_detection import (
    detect_deforestation,
    detect_fire_hotspots,
    DeforestationResult,
    config as sh_config
)

# Configuration
SUPABASE_URL = os.getenv('SUPABASE_URL')
SUPABASE_KEY = os.getenv('SUPABASE_SERVICE_KEY')
FONNTE_TOKEN = os.getenv('FONNTE_TOKEN')
RESEND_API_KEY = os.getenv('RESEND_API_KEY')

# Supabase client setup
supabase = None
try:
    if SUPABASE_URL and SUPABASE_KEY:
        from supabase import create_client
        supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
except ImportError:
    pass


def get_active_customers() -> list:
    """Fetch all active customers with boundaries from database."""

    if not supabase:
        print("⚠️  Supabase not configured, using sample data")
        return get_sample_customers()

    response = supabase.table('customers').select(
        '*, boundaries(*)'
    ).eq('status', 'active').execute()

    return response.data


def get_sample_customers() -> list:
    """Sample customers for testing without database."""

    return [
        {
            'id': 'CUST001',
            'name': 'PT Sawit Makmur',
            'email': 'ops@sawitmakmur.co.id',
            'phone': '6281234567890',
            'boundaries': [
                {
                    'id': 'BND001',
                    'name': 'Block A - Riau North',
                    'geojson': {
                        "type": "Polygon",
                        "coordinates": [[
                            [102.05, 1.45],
                            [102.15, 1.45],
                            [102.15, 1.55],
                            [102.05, 1.55],
                            [102.05, 1.45]
                        ]]
                    },
                    'hectares': 1100
                },
                {
                    'id': 'BND002',
                    'name': 'Block B - Riau South',
                    'geojson': {
                        "type": "Polygon",
                        "coordinates": [[
                            [102.10, 1.30],
                            [102.20, 1.30],
                            [102.20, 1.40],
                            [102.10, 1.40],
                            [102.10, 1.30]
                        ]]
                    },
                    'hectares': 1200
                }
            ]
        },
        {
            'id': 'CUST002',
            'name': 'Golden Palm Plantation',
            'email': 'manager@goldenpalm.com',
            'phone': '6289876543210',
            'boundaries': [
                {
                    'id': 'BND003',
                    'name': 'Estate 1 - Jambi',
                    'geojson': {
                        "type": "Polygon",
                        "coordinates": [[
                            [103.50, -1.60],
                            [103.60, -1.60],
                            [103.60, -1.50],
                            [103.50, -1.50],
                            [103.50, -1.60]
                        ]]
                    },
                    'hectares': 950
                }
            ]
        }
    ]


def save_alert_to_db(alert: dict) -> Optional[str]:
    """Save alert to Supabase database."""

    if not supabase:
        print(f"   [DB] Would save alert: {alert['type']} - {alert['boundary_name']}")
        return 'mock-alert-id'

    response = supabase.table('alerts').insert({
        'customer_id': alert['customer_id'],
        'boundary_id': alert.get('boundary_id'),
        'type': alert['type'],
        'severity': alert['severity'],
        'title': alert['title'],
        'description': alert['description'],
        'affected_hectares': alert.get('affected_hectares'),
        'coordinates': alert.get('coordinates'),
        'detected_at': datetime.now().isoformat(),
        'status': 'new'
    }).execute()

    return response.data[0]['id'] if response.data else None


def save_ndvi_reading(boundary_id: str, ndvi_data: dict) -> None:
    """Save NDVI reading to history table."""

    if not supabase:
        return

    supabase.table('ndvi_history').insert({
        'boundary_id': boundary_id,
        'date': datetime.now().date().isoformat(),
        'mean_ndvi': ndvi_data.get('mean_ndvi_recent'),
        'min_ndvi': None,
        'max_ndvi': None,
        'cloud_cover_pct': None
    }).execute()


def send_whatsapp_alert(phone: str, alert: dict, dry_run: bool = False) -> bool:
    """Send WhatsApp alert via Fonnte."""

    severity_emoji = {
        'low': '⚠️',
        'medium': '🟠',
        'high': '🔴',
        'critical': '🚨'
    }

    emoji = severity_emoji.get(alert.get('severity', ''), '⚠️')

    message = f"""{emoji} *SATTELI ALERT*

*{alert['type'].upper()}* detected

📍 *Location:* {alert['boundary_name']}
📐 *Affected:* {alert.get('affected_hectares', 'N/A'):.1f} ha
⏰ *Detected:* {datetime.now().strftime('%Y-%m-%d %H:%M')}
📊 *Severity:* {alert.get('severity', 'unknown').upper()}

{alert.get('description', '')}

View details: https://satteli.com/dashboard/"""

    if dry_run:
        print(f"   [DRY RUN] Would send WhatsApp to {phone}:")
        print(f"   {message[:100]}...")
        return True

    if not FONNTE_TOKEN:
        print(f"   [SKIP] Fonnte not configured")
        return False

    try:
        response = requests.post(
            'https://api.fonnte.com/send',
            headers={'Authorization': FONNTE_TOKEN},
            data={
                'target': phone,
                'message': message,
                'countryCode': '62'
            },
            timeout=30
        )
        return response.status_code == 200
    except Exception as e:
        print(f"   [ERROR] WhatsApp send failed: {e}")
        return False


def send_email_alert(email: str, alert: dict, dry_run: bool = False) -> bool:
    """Send email alert via Resend."""

    if dry_run:
        print(f"   [DRY RUN] Would send email to {email}")
        return True

    if not RESEND_API_KEY:
        print(f"   [SKIP] Resend not configured")
        return False

    try:
        import resend
        resend.api_key = RESEND_API_KEY

        severity_color = {
            'low': '#3b82f6',
            'medium': '#eab308',
            'high': '#f97316',
            'critical': '#ef4444'
        }.get(alert.get('severity', ''), '#6b7280')

        html_content = f"""
        <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
            <div style="background: #0c1425; color: white; padding: 20px; text-align: center;">
                <h1 style="margin: 0;">🛰️ SATTELI ALERT</h1>
            </div>
            <div style="padding: 20px; background: #f5f5f5;">
                <div style="background: {severity_color}; color: white; padding: 12px 16px; border-radius: 8px; margin-bottom: 20px;">
                    <strong>{alert.get('severity', '').upper()}</strong>: {alert['type'].upper()} Detected
                </div>
                <p><strong>Location:</strong> {alert['boundary_name']}</p>
                <p><strong>Affected Area:</strong> {alert.get('affected_hectares', 'N/A'):.1f if alert.get('affected_hectares') else 'N/A'} hectares</p>
                <p><strong>Detected:</strong> {datetime.now().strftime('%Y-%m-%d %H:%M')}</p>
                <p style="margin-top: 16px;">{alert.get('description', '')}</p>
                <a href="https://satteli.com/dashboard/"
                   style="display: inline-block; background: #0ea5e9; color: white;
                          padding: 12px 24px; text-decoration: none; border-radius: 8px; margin-top: 20px;">
                    View Dashboard
                </a>
            </div>
            <div style="padding: 15px; text-align: center; color: #666; font-size: 12px;">
                Satteli - Satellite Intelligence for Sustainable Land Management
            </div>
        </div>
        """

        resend.Emails.send({
            "from": "alerts@satteli.com",
            "to": email,
            "subject": f"🚨 {alert['type'].title()} Alert - {alert['boundary_name']}",
            "html": html_content
        })
        return True
    except Exception as e:
        print(f"   [ERROR] Email send failed: {e}")
        return False


def scan_customer(customer: dict, dry_run: bool = False) -> dict:
    """Scan all boundaries for a single customer."""

    print(f"\n👤 Scanning customer: {customer['name']} ({customer['id']})")
    print(f"   Boundaries: {len(customer.get('boundaries', []))}")

    results = {
        'customer_id': customer['id'],
        'customer_name': customer['name'],
        'boundaries_scanned': 0,
        'alerts_triggered': 0,
        'alerts': [],
        'pu_used': 0  # Track Processing Units
    }

    for boundary in customer.get('boundaries', []):
        print(f"\n   📍 Analyzing: {boundary['name']}")

        # Estimate PU usage (rough: ~8 PU per 100 km²)
        hectares = boundary.get('hectares', 1000)
        estimated_pu = max(1, int(hectares / 100 * 0.8))
        results['pu_used'] += estimated_pu * 2  # x2 for two time periods

        # Run deforestation detection
        try:
            deforest_result = detect_deforestation(
                boundary_geojson=boundary['geojson'],
                customer_id=customer['id'],
                boundary_name=boundary['name'],
                days_back=30,
                ndvi_threshold=0.3,
                min_area_ha=0.5
            )

            results['boundaries_scanned'] += 1

            # Save NDVI reading to history
            if deforest_result.mean_ndvi_recent:
                save_ndvi_reading(boundary['id'], asdict(deforest_result))

            if deforest_result.alert_triggered:
                alert = {
                    'customer_id': customer['id'],
                    'boundary_id': boundary['id'],
                    'boundary_name': boundary['name'],
                    'type': 'deforestation',
                    'severity': deforest_result.severity,
                    'title': f"Deforestation detected in {boundary['name']}",
                    'description': f"Approximately {deforest_result.deforestation_area_ha:.1f} hectares of vegetation loss detected. NDVI dropped from {deforest_result.mean_ndvi_previous:.2f} to {deforest_result.mean_ndvi_recent:.2f}.",
                    'affected_hectares': deforest_result.deforestation_area_ha,
                    'coordinates': deforest_result.coordinates
                }

                # Save to database
                alert_id = save_alert_to_db(alert)
                alert['alert_id'] = alert_id

                # Send notifications
                if customer.get('phone'):
                    send_whatsapp_alert(customer['phone'], alert, dry_run)
                if customer.get('email'):
                    send_email_alert(customer['email'], alert, dry_run)

                results['alerts_triggered'] += 1
                results['alerts'].append(alert)

                print(f"   ⚠️  DEFORESTATION ALERT: {deforest_result.deforestation_area_ha:.1f} ha")
            else:
                print(f"   ✅ No deforestation detected")

        except Exception as e:
            print(f"   ❌ Deforestation scan failed: {e}")

        # Run fire detection (uses NASA FIRMS, not Sentinel Hub)
        try:
            fire_result = detect_fire_hotspots(
                boundary_geojson=boundary['geojson'],
                customer_id=customer['id'],
                boundary_name=boundary['name'],
                days_back=7
            )

            if fire_result['alert_triggered']:
                alert = {
                    'customer_id': customer['id'],
                    'boundary_id': boundary['id'],
                    'boundary_name': boundary['name'],
                    'type': 'fire',
                    'severity': fire_result['severity'],
                    'title': f"Fire hotspots detected in {boundary['name']}",
                    'description': f"{fire_result['fire_detections']} active fire hotspot(s) detected in the last 7 days.",
                    'affected_hectares': None,
                    'coordinates': None
                }

                alert_id = save_alert_to_db(alert)
                alert['alert_id'] = alert_id

                if customer.get('phone'):
                    send_whatsapp_alert(customer['phone'], alert, dry_run)
                if customer.get('email'):
                    send_email_alert(customer['email'], alert, dry_run)

                results['alerts_triggered'] += 1
                results['alerts'].append(alert)

                print(f"   🔥 FIRE ALERT: {fire_result['fire_detections']} hotspots")
            else:
                print(f"   ✅ No fire hotspots detected")

        except Exception as e:
            print(f"   ❌ Fire scan failed: {e}")

    return results


def run_batch_scan(customer_id: Optional[str] = None, dry_run: bool = False):
    """Run batch scan for all customers or a specific customer."""

    print("=" * 60)
    print("🛰️  SATTELI BATCH SCANNER (Sentinel Hub)")
    print(f"📅  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    if dry_run:
        print("🔵  DRY RUN MODE - No alerts will be sent")
    print("=" * 60)

    # Check Sentinel Hub credentials
    if not sh_config.sh_client_id:
        print("\n⚠️  Sentinel Hub credentials not configured!")
        print("   Running with sample data only.\n")

    customers = get_active_customers()

    if customer_id:
        customers = [c for c in customers if c['id'] == customer_id]
        if not customers:
            print(f"❌ Customer {customer_id} not found")
            return

    print(f"\n📊 Customers to scan: {len(customers)}")
    total_boundaries = sum(len(c.get('boundaries', [])) for c in customers)
    print(f"📍 Total boundaries: {total_boundaries}")

    all_results = []
    total_alerts = 0
    total_pu = 0

    for customer in customers:
        result = scan_customer(customer, dry_run)
        all_results.append(result)
        total_alerts += result['alerts_triggered']
        total_pu += result['pu_used']

    # Summary
    print("\n" + "=" * 60)
    print("📊 SCAN SUMMARY")
    print("=" * 60)
    print(f"Customers scanned: {len(customers)}")
    print(f"Boundaries analyzed: {sum(r['boundaries_scanned'] for r in all_results)}")
    print(f"Total alerts triggered: {total_alerts}")
    print(f"Estimated PUs used: ~{total_pu}")
    print(f"PU quota remaining: ~{10000 - total_pu}/10,000 (free tier)")

    if total_alerts > 0:
        print("\n⚠️  ALERTS:")
        for result in all_results:
            for alert in result['alerts']:
                print(f"   - [{alert.get('severity', '?').upper()}] {alert['type']}: {alert['boundary_name']}")

    print("\n✅ Batch scan complete")

    return all_results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Satteli Batch Scanner (Sentinel Hub)')
    parser.add_argument('--customer', type=str, help='Scan specific customer ID')
    parser.add_argument('--dry-run', action='store_true', help='Test without sending alerts')
    args = parser.parse_args()

    run_batch_scan(
        customer_id=args.customer,
        dry_run=args.dry_run
    )
