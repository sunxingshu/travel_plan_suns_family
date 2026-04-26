from __future__ import annotations

import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from src.models import FamilyConfig, TravelPlan, DreamPlan

logger = logging.getLogger(__name__)

_GMAIL_HOST = "smtp.gmail.com"
_GMAIL_PORT = 587


def send_travel_email(
    gmail_email: str,
    gmail_app_password: str,
    recipient_emails: list[str],
    travel_plans: list[TravelPlan],
    dream_plans: list[DreamPlan],
    transfer_bonuses: list = None,
    evergreen_tips: list = None,
    config: FamilyConfig = None,
    run_date: str = "",
) -> None:
    html = render_email_html(travel_plans, dream_plans, transfer_bonuses or [], evergreen_tips or [], config, run_date)
    plain = _render_plain_text(travel_plans, config)

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"✈ Sun Family Travel Ideas — {run_date}"
    msg["From"] = gmail_email
    msg["To"] = ", ".join(recipient_emails)
    msg.attach(MIMEText(plain, "plain"))
    msg.attach(MIMEText(html, "html"))

    try:
        with smtplib.SMTP(_GMAIL_HOST, _GMAIL_PORT, timeout=30) as server:
            server.ehlo()
            server.starttls()
            server.ehlo()
            server.login(gmail_email, gmail_app_password)
            server.sendmail(gmail_email, recipient_emails, msg.as_string())
        logger.info("Email sent successfully to %s", ", ".join(recipient_emails))
    except smtplib.SMTPAuthenticationError as e:
        raise EmailSendError(
            "Gmail authentication failed. Check GMAIL_EMAIL and GMAIL_APP_PASSWORD. "
            "Ensure 2FA is enabled and you're using an App Password, not your account password."
        ) from e
    except smtplib.SMTPException as e:
        raise EmailSendError(f"SMTP error: {e}") from e
    except Exception as e:
        raise EmailSendError(f"Unexpected error sending email: {e}") from e


def render_email_html(
    travel_plans: list[TravelPlan],
    dream_plans: list[DreamPlan],
    transfer_bonuses: list = None,
    evergreen_tips: list = None,
    config: FamilyConfig = None,
    run_date: str = "",
    template_dir: str = "templates",
    template_name: str = "email_template.html",
) -> str:
    template_path = Path(template_dir)
    if not template_path.exists():
        # Try relative to project root
        template_path = Path(__file__).parent.parent / "templates"

    env = Environment(loader=FileSystemLoader(str(template_path)), autoescape=True)
    template = env.get_template(template_name)
    return template.render(
        plans=travel_plans,
        dream_plans=dream_plans,
        transfer_bonuses=transfer_bonuses or [],
        evergreen_tips=evergreen_tips or [],
        config=config,
        run_date=run_date,
    )


def send_failure_email(
    gmail_email: str,
    gmail_app_password: str,
    recipient_emails: list[str],
    error_message: str,
    run_date: str,
) -> None:
    """Send a plain-text failure notification when the main pipeline breaks."""
    msg = MIMEText(
        f"Sun Family Travel Planner — run failed on {run_date}\n\n"
        f"Error details:\n{error_message}\n\n"
        "Check GitHub Actions logs for more information.",
        "plain",
    )
    msg["Subject"] = f"⚠ Travel Planner Failed — {run_date}"
    msg["From"] = gmail_email
    msg["To"] = ", ".join(recipient_emails)

    try:
        with smtplib.SMTP(_GMAIL_HOST, _GMAIL_PORT, timeout=30) as server:
            server.ehlo()
            server.starttls()
            server.ehlo()
            server.login(gmail_email, gmail_app_password)
            server.sendmail(gmail_email, recipient_emails, msg.as_string())
    except Exception as e:
        logger.error("Failed to send failure notification email: %s", e)


def _render_plain_text(travel_plans: list[TravelPlan], config: FamilyConfig) -> str:
    lines = [
        "Sun Family Weekly Travel Ideas",
        "=" * 40,
        "",
    ]
    for plan in travel_plans:
        dp = plan.destination_plan
        lines.append(f"#{plan.rank} — {dp.destination_city}, {dp.country} (score: {plan.overall_score}/10)")
        if plan.best_for:
            lines.append(f"   Best for: {plan.best_for}")
        if dp.flight:
            f = dp.flight
            lines.append(
                f"   ✈ Flight: ${f.price_usd:.0f} | {f.airline} | {f.duration_hours}h | "
                f"{'Nonstop' if f.stops == 0 else f'{f.stops} stop(s)'} | {f.departure_date}→{f.return_date}"
            )
        if dp.hotel:
            h = dp.hotel
            lines.append(f"   🏨 Hotel:  {h.name} ({h.star_rating:.0f}★) | ${h.price_per_night_usd:.0f}/nt | ${h.total_price_usd:.0f} total")
        if dp.weather:
            w = dp.weather
            lines.append(f"   🌤 Weather: {w.avg_temp_celsius}°C | {w.conditions} | {w.precipitation_chance_pct:.0f}% rain")
        if dp.flight_points_options:
            best = dp.flight_points_options[0]
            lines.append(f"   💳 Pts:    {best.program_name} | need {best.points_required:,} / have {best.points_available:,}")
        lines.append(f"   💰 Total:  ${dp.total_cash_cost_usd:.0f} cash")
        if plan.ai_recommendation_text:
            lines.append(f"   📝 {plan.ai_recommendation_text}")
        if plan.pros:
            lines.append(f"   ✓ {' / '.join(plan.pros)}")
        if plan.cons:
            lines.append(f"   ✗ {' / '.join(plan.cons)}")
        lines.append("")

    lines += [
        "-" * 40,
        f"Budget: ${config.budget_usd:,.0f} | Home: {config.home_airport}",
        "Automated by travel_plan_suns_family (GitHub Actions)",
    ]
    return "\n".join(lines)


class EmailSendError(Exception):
    pass
