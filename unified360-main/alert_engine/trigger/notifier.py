# alert_engine/trigger/notifier.py
# FINAL VERSION – NO CIRCULAR IMPORTS + FULLY WORKING IN DAEMON

import os
import smtplib
import threading
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta
from jinja2 import Environment, FileSystemLoader

# -------------------------------------------------------------------
# LAZY IMPORTS – only when needed (breaks the circular import)
# -------------------------------------------------------------------
def _get_app_and_db():
    from app import app
    from extensions import db
    return app, db

# -------------------------------------------------------------------
# TEMPLATE DIRECTORY
# -------------------------------------------------------------------
TEMPLATE_DIR = os.path.join(os.path.dirname(__file__), "templates")
env = Environment(loader=FileSystemLoader(TEMPLATE_DIR))

# -------------------------------------------------------------------
# SUBJECT MAP
# -------------------------------------------------------------------
SUBJECT_MAP = {
    "url_down": "URL Down Alert for {hostname}",
    "url_slow": "URL Slow Response for {hostname}",
    "url_recovery": "URL Recovery for {hostname}",
    "port_alert": "Port Down Alert for {hostname}:{port}",
    "port_slow": "Port Latency Alert for {hostname}:{port}",
    "port_recovery": "Port Recovery Alert for {hostname}:{port}",
    "fortigate_vpn_down": "Fortigate VPN Down: {hostname} / {vpn_name}",
    "fortigate_vpn_recovery": "Fortigate VPN Recovery: {hostname} / {vpn_name}",
    "fortigate_vpn_alert":  "Fortigate VPN Traffic Alert: {hostname} / {vpn_name}",
    "fortigate_vpn_recovery_traffic": "Fortigate VPN Traffic Recovery: {hostname} / {vpn_name}",
    "ping_latency": "Ping Latency Alert for {hostname}",
    "ping_packetloss": "Ping Packet Loss Alert for {hostname}",
    "ping_recovery": "Ping Recovery for {hostname}",
    "fortigate_sdwan_alert": "SDWAN Link Alert: {hostname} / {link_name}",
    "fortigate_sdwan_recovery": "SDWAN Link Recovery: {hostname} / {link_name}",
    "fortigate_sys_alert": "Fortigate System Alert: {hostname}",
    "fortigate_sys_recovery": "Fortigate System Recovery: {hostname}",
    "service_down": "Service Down Alert in {hostname}",
    "service_recovery": "Service Recovery Alert in {hostname}",
    "oracle_db_down": "Oracle Database Down Alert",
    "oracle_recovery": "Oracle Database Recovery Alert",
    "oracle_threshold_alert": "Oracle Database Threshold Alert",

    # ✅ Server subjects (optional, but nice)
    "server_cpu_high": "CPU High Alert for {hostname}",
    "server_cpu_recovery": "CPU Recovery for {hostname}",
    "server_mem_high": "Memory High Alert for {hostname}",
    "server_mem_recovery": "Memory Recovery for {hostname}",
    "server_disk_high": "Disk Alert for {hostname}",
    "server_disk_recovery": "Disk Recovery for {hostname}",
    "server_net_high": "Network Alert for {hostname}",
    "server_net_recovery": "Network Recovery for {hostname}",
}

# -------------------------------------------------------------------
# THREAD-SAFE EMAIL SENDER
# -------------------------------------------------------------------
def _send_email_thread(smtp_cfg, to_list, subject, html_body):
    app, db = _get_app_and_db()
    with app.app_context():
        try:
            msg = MIMEMultipart("alternative")
            msg["From"] = smtp_cfg.sender
            msg["To"] = ", ".join(to_list)
            msg["Subject"] = subject
            msg.attach(MIMEText(html_body, "html"))

            if smtp_cfg.security == "TLS":
                server = smtplib.SMTP(smtp_cfg.host, smtp_cfg.port, timeout=15)
                server.starttls()
            elif smtp_cfg.security == "SSL":
                server = smtplib.SMTP_SSL(smtp_cfg.host, smtp_cfg.port, timeout=15)
            else:
                server = smtplib.SMTP(smtp_cfg.host, smtp_cfg.port, timeout=15)

            if smtp_cfg.username:
                server.login(smtp_cfg.username, smtp_cfg.password)

            server.sendmail(smtp_cfg.sender, to_list, msg.as_string())
            server.quit()
            print(f"[Notifier] Email sent → {to_list}")
        except Exception as e:
            print(f"[Notifier] Email FAILED → {to_list}: {e}")

# -------------------------------------------------------------------
# MAIN send_notification – 100% safe
# -------------------------------------------------------------------
def send_notification(template, rule=None, **kwargs):
    # ✅ Inject "rule" into template context (THIS FIXES: 'rule' is undefined)
    if rule is not None:
        kwargs.setdefault("rule", rule)
        kwargs.setdefault("rule_name", getattr(rule, "name", ""))
        try:
            kwargs.setdefault("customer_name", rule.customer.name if rule.customer else "")
        except Exception:
            kwargs.setdefault("customer_name", "")

    now = datetime.utcnow()
    ist = now + timedelta(hours=5, minutes=30)
    kwargs["alert_time_utc"] = now.isoformat() + "Z"
    kwargs["alert_time_ist"] = ist.strftime("%Y-%m-%d %H:%M:%S IST")

    if "downtime" in kwargs and kwargs["downtime"] is not None:
        secs = int(kwargs["downtime"])
        hrs, mins = divmod(secs // 60, 60)
        kwargs["downtime_human"] = f"{hrs}h {mins}m {secs%60}s"

    subject = SUBJECT_MAP.get(template, template)
    try:
        subject = subject.format(**kwargs)
    except Exception:
        pass

    template_file = f"{template}.html"
    if not os.path.exists(os.path.join(TEMPLATE_DIR, template_file)):
        print(f"[Notifier] Template missing: {template_file}")
        return

    try:
        body = env.get_template(template_file).render(**kwargs)
    except Exception as e:
        print(f"[Notifier] Render error: {e}")
        return

    emails = [c.email for c in (rule.contact_group.contacts if rule and rule.contact_group else []) if c.email]
    if not emails:
        return

    def _load_and_send():
        app, db = _get_app_and_db()
        with app.app_context():
            from models.smtp import SmtpConfig
            smtp_cfg = db.session.query(SmtpConfig).first()
            if smtp_cfg:
                _send_email_thread(smtp_cfg, emails, subject, body)

    threading.Thread(target=_load_and_send, daemon=True).start()

