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
    analyze_plant_health,
    DeforestationResult,
    PlantHealthResult,
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


def send_whatsapp_health_report(phone: str, report: PlantHealthResult, dry_run: bool = False) -> bool:
    """Send plant health report via WhatsApp."""

    health_emoji = {
        'healthy': '🌿',
        'moderate': '🌱',
        'stressed': '⚠️',
        'critical': '🚨',
        'unknown': '❓'
    }

    emoji = health_emoji.get(report.health_status, '❓')
    score_bar = '█' * (report.health_score // 10) + '░' * (10 - report.health_score // 10) if report.health_score else '░' * 10

    message = f"""{emoji} *SATTELI HEALTH REPORT*

📍 *{report.boundary_name}*
📅 {report.analysis_date}

*Health Status:* {report.health_status.upper()}
*Score:* {report.health_score}/100
[{score_bar}]

📊 *NDVI Metrics:*
• Mean: {report.mean_ndvi:.3f if report.mean_ndvi else 'N/A'}
• Range: {report.min_ndvi:.2f if report.min_ndvi else 'N/A'} - {report.max_ndvi:.2f if report.max_ndvi else 'N/A'}
"""

    if report.stressed_area_ha:
        message += f"\n⚠️ *Stressed Area:* {report.stressed_area_ha:.1f} ha ({report.stressed_percentage:.0f}%)"

    if report.recommendations:
        message += "\n\n💡 *Recommendations:*"
        for i, rec in enumerate(report.recommendations[:3], 1):
            message += f"\n{i}. {rec}"

    message += "\n\nView details: https://satteli.com/dashboard/"

    if dry_run:
        print(f"   [DRY RUN] Would send health report to {phone}:")
        print(f"   {message[:150]}...")
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


def send_email_health_report(email: str, report: PlantHealthResult, dry_run: bool = False) -> bool:
    """Send plant health report via email."""

    if dry_run:
        print(f"   [DRY RUN] Would send health report email to {email}")
        return True

    if not RESEND_API_KEY:
        print(f"   [SKIP] Resend not configured")
        return False

    try:
        import resend
        resend.api_key = RESEND_API_KEY

        status_color = {
            'healthy': '#10b981',
            'moderate': '#f59e0b',
            'stressed': '#f97316',
            'critical': '#ef4444',
            'unknown': '#6b7280'
        }.get(report.health_status, '#6b7280')

        # Build recommendations HTML
        recs_html = ""
        if report.recommendations:
            recs_html = "<ul style='margin: 0; padding-left: 20px;'>"
            for rec in report.recommendations:
                recs_html += f"<li style='margin: 4px 0;'>{rec}</li>"
            recs_html += "</ul>"

        html_content = f"""
        <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
            <div style="background: #0c1425; color: white; padding: 20px; text-align: center;">
                <h1 style="margin: 0;">🌿 SATTELI HEALTH REPORT</h1>
            </div>
            <div style="padding: 20px; background: #f5f5f5;">
                <h2 style="margin-top: 0;">{report.boundary_name}</h2>
                <p style="color: #666;">Analysis Date: {report.analysis_date}</p>

                <div style="background: {status_color}; color: white; padding: 16px; border-radius: 8px; margin: 16px 0; text-align: center;">
                    <div style="font-size: 14px; opacity: 0.9;">Health Status</div>
                    <div style="font-size: 28px; font-weight: bold;">{report.health_status.upper()}</div>
                    <div style="font-size: 18px;">Score: {report.health_score}/100</div>
                </div>

                <div style="background: white; padding: 16px; border-radius: 8px; margin: 16px 0;">
                    <h3 style="margin-top: 0; color: #333;">📊 NDVI Metrics</h3>
                    <table style="width: 100%; border-collapse: collapse;">
                        <tr>
                            <td style="padding: 8px 0; border-bottom: 1px solid #eee;"><strong>Mean NDVI</strong></td>
                            <td style="padding: 8px 0; border-bottom: 1px solid #eee; text-align: right;">{report.mean_ndvi:.3f if report.mean_ndvi else 'N/A'}</td>
                        </tr>
                        <tr>
                            <td style="padding: 8px 0; border-bottom: 1px solid #eee;"><strong>Min NDVI</strong></td>
                            <td style="padding: 8px 0; border-bottom: 1px solid #eee; text-align: right;">{report.min_ndvi:.3f if report.min_ndvi else 'N/A'}</td>
                        </tr>
                        <tr>
                            <td style="padding: 8px 0; border-bottom: 1px solid #eee;"><strong>Max NDVI</strong></td>
                            <td style="padding: 8px 0; border-bottom: 1px solid #eee; text-align: right;">{report.max_ndvi:.3f if report.max_ndvi else 'N/A'}</td>
                        </tr>
                        <tr>
                            <td style="padding: 8px 0;"><strong>Area</strong></td>
                            <td style="padding: 8px 0; text-align: right;">{report.boundary_area_ha:.1f} ha</td>
                        </tr>
                    </table>
                </div>

                {"<div style='background: #fef3c7; border: 1px solid #f59e0b; padding: 16px; border-radius: 8px; margin: 16px 0;'><h3 style='margin-top: 0; color: #92400e;'>⚠️ Stressed Area</h3><p style='margin: 0;'><strong>" + f"{report.stressed_area_ha:.1f}" + " ha</strong> (" + f"{report.stressed_percentage:.0f}" + "%) showing signs of stress</p></div>" if report.stressed_area_ha and report.stressed_area_ha > 0 else ""}

                <div style="background: white; padding: 16px; border-radius: 8px; margin: 16px 0;">
                    <h3 style="margin-top: 0; color: #333;">💡 Recommendations</h3>
                    {recs_html}
                </div>

                <a href="https://satteli.com/dashboard/"
                   style="display: inline-block; background: #0ea5e9; color: white;
                          padding: 12px 24px; text-decoration: none; border-radius: 8px; margin-top: 16px;">
                    View Full Report
                </a>
            </div>
            <div style="padding: 15px; text-align: center; color: #666; font-size: 12px;">
                Satteli - Satellite Intelligence for Sustainable Land Management
            </div>
        </div>
        """

        resend.Emails.send({
            "from": "reports@satteli.com",
            "to": email,
            "subject": f"🌿 Plant Health Report - {report.boundary_name} ({report.health_status.upper()})",
            "html": html_content
        })
        return True
    except Exception as e:
        print(f"   [ERROR] Email send failed: {e}")
        return False


def save_health_report_to_db(report: PlantHealthResult) -> Optional[str]:
    """Save health report and update boundary health status in database."""

    if not supabase:
        print(f"   [DB] Would save health report: {report.boundary_name} - {report.health_status}")
        return 'mock-health-id'

    # Update boundary health status
    supabase.table('boundaries').update({
        'current_ndvi': report.mean_ndvi,
        'health_status': report.health_status,
        'last_scan_at': datetime.now().isoformat()
    }).eq('id', report.boundary_id).execute()

    # Save NDVI reading to history
    supabase.table('ndvi_history').insert({
        'boundary_id': report.boundary_id,
        'date': report.analysis_date,
        'mean_ndvi': report.mean_ndvi,
        'min_ndvi': report.min_ndvi,
        'max_ndvi': report.max_ndvi,
        'std_ndvi': report.ndvi_std
    }).execute()

    # If alert triggered, save as alert
    if report.alert_triggered:
        response = supabase.table('alerts').insert({
            'customer_id': report.customer_id,
            'boundary_id': report.boundary_id,
            'type': 'crop_stress',
            'severity': report.severity,
            'title': f"Plant stress detected in {report.boundary_name}",
            'description': f"Health status: {report.health_status.upper()}. Mean NDVI: {report.mean_ndvi:.3f}. {report.stressed_area_ha:.1f} ha showing stress signs." if report.stressed_area_ha else f"Health status: {report.health_status.upper()}. Mean NDVI: {report.mean_ndvi:.3f}.",
            'affected_hectares': report.stressed_area_ha,
            'detected_at': datetime.now().isoformat(),
            'status': 'new'
        }).execute()
        return response.data[0]['id'] if response.data else None

    return None


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


def scan_customer(customer: dict, dry_run: bool = False, include_health: bool = True) -> dict:
    """Scan all boundaries for a single customer."""

    print(f"\n👤 Scanning customer: {customer['name']} ({customer['id']})")
    print(f"   Boundaries: {len(customer.get('boundaries', []))}")

    results = {
        'customer_id': customer['id'],
        'customer_name': customer['name'],
        'boundaries_scanned': 0,
        'alerts_triggered': 0,
        'alerts': [],
        'health_reports': [],
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

        # Run plant health analysis
        if include_health:
            try:
                health_result = analyze_plant_health(
                    boundary_geojson=boundary['geojson'],
                    customer_id=customer['id'],
                    boundary_id=boundary['id'],
                    boundary_name=boundary['name'],
                    baseline_ndvi=boundary.get('baseline_ndvi'),  # Optional baseline
                    stress_threshold=customer.get('threshold_ndvi_change', 0.4)
                )

                # Add extra PU for health analysis
                results['pu_used'] += estimated_pu

                # Save to database
                save_health_report_to_db(health_result)

                # Store in results
                results['health_reports'].append({
                    'boundary_name': boundary['name'],
                    'health_status': health_result.health_status,
                    'health_score': health_result.health_score,
                    'mean_ndvi': health_result.mean_ndvi,
                    'stressed_area_ha': health_result.stressed_area_ha,
                    'recommendations': health_result.recommendations
                })

                # Send notifications for stressed/critical status
                if health_result.alert_triggered:
                    if customer.get('phone'):
                        send_whatsapp_health_report(customer['phone'], health_result, dry_run)
                    if customer.get('email'):
                        send_email_health_report(customer['email'], health_result, dry_run)

                    results['alerts_triggered'] += 1
                    results['alerts'].append({
                        'type': 'crop_stress',
                        'boundary_name': boundary['name'],
                        'severity': health_result.severity,
                        'health_status': health_result.health_status
                    })

                    print(f"   🌿 HEALTH: {health_result.health_status.upper()} (Score: {health_result.health_score}/100)")
                else:
                    print(f"   🌿 Health: {health_result.health_status} (Score: {health_result.health_score}/100)")

            except Exception as e:
                print(f"   ❌ Health analysis failed: {e}")

    return results


def run_batch_scan(customer_id: Optional[str] = None, dry_run: bool = False, include_health: bool = True):
    """Run batch scan for all customers or a specific customer."""

    print("=" * 60)
    print("🛰️  SATTELI BATCH SCANNER (Sentinel Hub)")
    print(f"📅  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    if dry_run:
        print("🔵  DRY RUN MODE - No alerts will be sent")
    if include_health:
        print("🌿  Including plant health analysis")
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
        result = scan_customer(customer, dry_run, include_health)
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

    # Health summary
    all_health = []
    for result in all_results:
        all_health.extend(result.get('health_reports', []))

    if all_health:
        print("\n🌿 PLANT HEALTH SUMMARY:")
        healthy = sum(1 for h in all_health if h['health_status'] == 'healthy')
        moderate = sum(1 for h in all_health if h['health_status'] == 'moderate')
        stressed = sum(1 for h in all_health if h['health_status'] == 'stressed')
        critical = sum(1 for h in all_health if h['health_status'] == 'critical')

        print(f"   Healthy:   {healthy} boundaries")
        print(f"   Moderate:  {moderate} boundaries")
        print(f"   Stressed:  {stressed} boundaries")
        print(f"   Critical:  {critical} boundaries")

        # Calculate average health score
        scores = [h['health_score'] for h in all_health if h['health_score'] is not None]
        if scores:
            avg_score = sum(scores) / len(scores)
            print(f"\n   Average Health Score: {avg_score:.0f}/100")

    if total_alerts > 0:
        print("\n⚠️  ALERTS:")
        for result in all_results:
            for alert in result['alerts']:
                alert_type = alert.get('type', 'unknown')
                severity = alert.get('severity', '?')
                boundary = alert.get('boundary_name', 'Unknown')

                if alert_type == 'crop_stress':
                    health_status = alert.get('health_status', '')
                    print(f"   - [{severity.upper()}] {alert_type}: {boundary} ({health_status})")
                else:
                    print(f"   - [{severity.upper()}] {alert_type}: {boundary}")

    print("\n✅ Batch scan complete")

    return all_results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Satteli Batch Scanner (Sentinel Hub)')
    parser.add_argument('--customer', type=str, help='Scan specific customer ID')
    parser.add_argument('--dry-run', action='store_true', help='Test without sending alerts')
    parser.add_argument('--no-health', action='store_true', help='Skip plant health analysis')
    args = parser.parse_args()

    run_batch_scan(
        customer_id=args.customer,
        dry_run=args.dry_run,
        include_health=not args.no_health
    )
