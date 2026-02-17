# routes/tools_routes.py
from flask import Blueprint, render_template, request, jsonify, session, redirect, url_for, current_app
from functools import wraps
import time, socket, telnetlib, ssl, smtplib, requests, asyncio, threading
from ping3 import ping
import dns.resolver
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from models.smtp import SmtpConfig
import ipaddress

tools_bp = Blueprint("tools", __name__)

# ----------------------
# RBAC / Helpers
# ----------------------
def _current_user():
    return session.get("user")

def login_required_page(fn):
    @wraps(fn)
    def wrapper(*a, **kw):
        if "user" not in session:
            return redirect(url_for("auth.login"))
        return fn(*a, **kw)
    return wrapper

def login_required_api(fn):
    @wraps(fn)
    def wrapper(*a, **kw):
        if "user" not in session:
            return jsonify({"ok": False, "error": "Unauthorized"}), 401
        return fn(*a, **kw)
    return wrapper

def admin_required_api(fn):
    @wraps(fn)
    def wrapper(*a, **kw):
        u = _current_user()
        if not u or not u.get("is_admin"):
            return jsonify({"ok": False, "error": "Forbidden"}), 403
        return fn(*a, **kw)
    return wrapper

# ----------------------
# Network safety helpers
# ----------------------
# Deny private/reserved ranges for non-admin users to mitigate SSRF / internal probing.
def _is_private_address(host_or_ip):
    try:
        # try treat as IP
        ip = ipaddress.ip_address(host_or_ip)
        return ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved
    except Exception:
        # if it's a hostname, try resolve and check first answer
        try:
            answers = dns.resolver.resolve(host_or_ip, "A", lifetime=3)
            for r in answers:
                ip = ipaddress.ip_address(r.to_text())
                if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved:
                    return True
            return False
        except Exception:
            # if we can't resolve, be conservative and treat as non-private (allow DNS failure to surface later)
            return False

def _validate_host(host, require_public_for_nonadmin=True):
    if not host or len(host) > 255:
        return False, "Invalid host"
    user = _current_user()
    if require_public_for_nonadmin and user and not user.get("is_admin"):
        if _is_private_address(host):
            return False, "Probing private/internal addresses is not allowed"
    return True, None

def _validate_port(port):
    try:
        p = int(port)
        if 1 <= p <= 65535:
            return True, None
        return False, "Port must be 1-65535"
    except Exception:
        return False, "Invalid port"

# ----------------------
# SMTP helpers (use DB config)
# ----------------------
def get_smtp_config():
    try:
        return SmtpConfig.query.order_by(SmtpConfig.id.desc()).first()
    except Exception:
        return None

def _send_email_with_config(cfg, to_email, subject, body, allow_insecure=False, timeout=15):
    msg = MIMEMultipart()
    msg["From"] = cfg.sender
    msg["To"] = to_email
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain"))

    # default: verify certs; allow_insecure True will skip verification (admin-only use)
    try:
        if (cfg.security or "").upper() == "SSL":
            ctx = ssl.create_default_context()
            if allow_insecure:
                ctx = ssl._create_unverified_context()
            with smtplib.SMTP_SSL(cfg.host, cfg.port, context=ctx, timeout=timeout) as server:
                server.ehlo()
                if cfg.username:
                    server.login(cfg.username, cfg.password or "")
                server.send_message(msg)

        elif (cfg.security or "").upper() == "TLS":
            with smtplib.SMTP(cfg.host, cfg.port, timeout=timeout) as server:
                server.ehlo()
                ctx = ssl.create_default_context()
                if allow_insecure:
                    ctx = ssl._create_unverified_context()
                server.starttls(context=ctx)
                server.ehlo()
                if cfg.username:
                    server.login(cfg.username, cfg.password or "")
                server.send_message(msg)
        else:
            with smtplib.SMTP(cfg.host, cfg.port, timeout=timeout) as server:
                server.ehlo()
                if cfg.username:
                    server.login(cfg.username, cfg.password or "")
                server.send_message(msg)

        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}

# Convenience wrapper used by API (admin may allow insecure option)
def send_email(to_email, subject, body, allow_insecure=False):
    cfg = get_smtp_config()
    if not cfg:
        return {"ok": False, "error": "SMTP configuration not found"}
    return _send_email_with_config(cfg, to_email, subject, body, allow_insecure=allow_insecure)

# ----------------------
# Tool implementations (validated + safe fallbacks)
# ----------------------
def run_ping(target):
    if not target:
        return {"ok": False, "error": "Ping target required"}
    ok, reason = _validate_host(target)
    if not ok:
        return {"ok": False, "error": reason}
    try:
        rtt = ping(target, timeout=2, unit="ms")
        if rtt is None:
            return {"ok": True, "output": f"Ping to {target} timed out"}
        return {"ok": True, "output": f"Ping to {target}: {rtt:.2f} ms"}
    except Exception as e:
        return {"ok": False, "error": str(e)}

def run_telnet(target, port):
    if not target or port is None:
        return {"ok": False, "error": "Telnet target and port required"}
    ok, reason = _validate_host(target)
    if not ok:
        return {"ok": False, "error": reason}
    ok, reason = _validate_port(port)
    if not ok:
        return {"ok": False, "error": reason}

    try:
        start = time.time()
        tn = telnetlib.Telnet(target, int(port), timeout=3)
        tn.close()
        return {"ok": True, "output": f"Telnet {target}:{port} succeeded in {(time.time()-start)*1000:.1f} ms"}
    except Exception as e:
        return {"ok": False, "error": f"Telnet failed: {e}"}

def run_nslookup(target):
    if not target:
        return {"ok": False, "error": "NSLookup target required"}
    ok, reason = _validate_host(target, require_public_for_nonadmin=False)
    if not ok:
        return {"ok": False, "error": reason}
    try:
        answers = dns.resolver.resolve(target, "A", lifetime=3)
        ips = [r.to_text() for r in answers]
        return {"ok": True, "output": "\n".join(ips)}
    except Exception as e:
        return {"ok": False, "error": f"NSLookup failed: {e}"}

def run_url_check(url):
    if not url:
        return {"ok": False, "error": "URL required"}
    # basic length/content checks
    if len(url) > 2048:
        return {"ok": False, "error": "URL too long"}
    user = _current_user()
    # block non-admin probing into private hostnames/IPs
    try:
        parsed = requests.utils.urlparse(url)
        host = parsed.hostname
        if not host:
            return {"ok": False, "error": "Invalid URL"}
        ok, reason = _validate_host(host)
        if not ok:
            return {"ok": False, "error": reason}
    except Exception:
        return {"ok": False, "error": "Invalid URL"}

    try:
        r = requests.head(url, timeout=6, allow_redirects=True)
        headers = "\n".join(f"{k}: {v}" for k, v in r.headers.items())
        return {"ok": True, "output": f"Status {r.status_code} {r.reason}\n\n{headers}"}
    except Exception as e:
        return {"ok": False, "error": f"URL check failed: {e}"}

def run_traceroute(target, hops=16):
    if not target:
        return {"ok": False, "error": "Traceroute target required"}
    # traceroute uses raw sockets — require admin
    user = _current_user()
    if not user or not user.get("is_admin"):
        return {"ok": False, "error": "Traceroute requires admin privileges"}

    ok, reason = _validate_host(target, require_public_for_nonadmin=False)
    if not ok:
        return {"ok": False, "error": reason}

    # Best-effort simple UDP traceroute (no raw ICMP required) — still may fail without privileges on some platforms
    result = [f"Traceroute to {target} (max {hops} hops):"]
    port = 33434
    for ttl in range(1, min(int(hops), 64) + 1):
        try:
            recv = socket.socket(socket.AF_INET, socket.SOCK_RAW, socket.IPPROTO_ICMP)
            send = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
            recv.settimeout(2)
            send.setsockopt(socket.SOL_IP, socket.IP_TTL, ttl)
            recv.bind(("", port))
            start = time.time()
            send.sendto(b"", (target, port))
            try:
                _, addr = recv.recvfrom(512)
                elapsed = (time.time() - start) * 1000
                result.append(f"{ttl}\t{addr[0]}\t{elapsed:.2f} ms")
            except socket.timeout:
                result.append(f"{ttl}\t*\tRequest timed out")
            finally:
                send.close()
                recv.close()
        except PermissionError:
            return {"ok": False, "error": "Traceroute requires root/administrator privileges."}
        except Exception as e:
            result.append(f"{ttl}\tError: {e}")
            break
    return {"ok": True, "output": "\n".join(result)}

# ----------------------
# SNMP (safe execution)
# ----------------------
# Note: original code used async PySNMP API. Running asyncio.run inside Flask can
# cause problems if Flask is running inside an event loop. We run SNMP in a thread
# to isolate potential event-loop interactions.
def _run_snmp_sync(snmp_data):
    # Minimal SNMP GET using pysnmp synchronous API if available (fallback if not installed).
    try:
        # lazy import so app doesn't require pysnmp unless SNMP tool used
        from pysnmp.hlapi import (
            SnmpEngine, CommunityData, UdpTransportTarget, ContextData,
            ObjectType, ObjectIdentity, getCmd, UsmUserData, usmHMACSHAAuthProtocol, usmNoAuthProtocol
        )
    except Exception:
        return {"ok": False, "error": "PySNMP not installed on server"}

    target = snmp_data.get("ip")
    oid = snmp_data.get("oid", "1.3.6.1.2.1.1.1.0")
    version = snmp_data.get("version", "v2c")
    ok, reason = _validate_host(target)
    if not ok:
        return {"ok": False, "error": reason}

    try:
        if version == "v2c":
            community = snmp_data.get("community", "public")
            iterator = getCmd(
                SnmpEngine(),
                CommunityData(community, mpModel=1),
                UdpTransportTarget((target, 161), timeout=2, retries=1),
                ContextData(),
                ObjectType(ObjectIdentity(oid))
            )
        else:
            # SNMPv3 basic support
            user = snmp_data.get("user", "")
            auth = snmp_data.get("auth_pass")
            priv = snmp_data.get("priv_pass")
            user_data = UsmUserData(user, auth, priv)
            iterator = getCmd(
                SnmpEngine(),
                user_data,
                UdpTransportTarget((target, 161), timeout=2, retries=1),
                ContextData(),
                ObjectType(ObjectIdentity(oid))
            )

        errorIndication, errorStatus, errorIndex, varBinds = next(iterator)
        if errorIndication:
            return {"ok": False, "error": str(errorIndication)}
        if errorStatus:
            return {"ok": False, "error": f"{errorStatus.prettyPrint()}"}
        out = "\n".join(f"{x[0].prettyPrint()} = {x[1].prettyPrint()}" for x in varBinds)
        return {"ok": True, "output": out}
    except Exception as e:
        return {"ok": False, "error": str(e)}

def run_snmp(snmp_data):
    # SNMP can be considered sensitive — restrict to admin-only
    user = _current_user()
    if not user or not user.get("is_admin"):
        return {"ok": False, "error": "SNMP queries require admin privileges"}

    # run in a short-lived thread to avoid blocking main thread for long
    result_container = {}
    def target():
        result_container["result"] = _run_snmp_sync(snmp_data)

    th = threading.Thread(target=target, daemon=True)
    th.start()
    th.join(timeout=8)
    return result_container.get("result") or {"ok": False, "error": "SNMP query timed out"}

# ----------------------
# Flask endpoints
# ----------------------
@tools_bp.get("/tools")
@login_required_page
def tools_page():
    return render_template("tools.html")

@tools_bp.post("/api/run-tool")
@login_required_api
def run_tool():
    try:
        data = request.get_json(silent=True) or {}
        tool = data.get("tool")
        if not tool:
            return jsonify({"ok": False, "error": "Tool not specified"}), 400

        # Basic rate-limiting hook (placeholder)
        # TODO: integrate with your rate limiter / redis counters
        # e.g. if is_rate_limited(user_id, tool): return 429

        user = _current_user()
        output = None

        if tool == "ping":
            output = run_ping(data.get("target"))
        elif tool == "telnet":
            # telnet allowed for non-admin but private hosts blocked
            output = run_telnet(data.get("target"), data.get("port"))
        elif tool == "nslookup":
            output = run_nslookup(data.get("target"))
        elif tool == "urlcheck":
            # urlcheck allowed, but private hosts blocked for non-admin
            output = run_url_check(data.get("url"))
        elif tool == "traceroute":
            # admin only
            if not user.get("is_admin"):
                return jsonify({"ok": False, "error": "Traceroute requires admin privileges"}), 403
            output = run_traceroute(data.get("target"), hops=data.get("hops", 16))
        elif tool == "snmp":
            output = run_snmp(data.get("snmp", {}))
        else:
            return jsonify({"ok": False, "error": "Invalid tool"}), 400

        # Optionally email results. Restrict email-to if necessary.
        email_to = data.get("email_to") or data.get("email")
        if email_to:
            # validate basic email length
            if len(email_to) > 254:
                if output and isinstance(output, dict) and output.get("ok"):
                    output = {"ok": True, "output": output.get("output"), "email_status": {"ok": False, "error": "Invalid recipient"}}
                else:
                    output = {"ok": False, "error": "Invalid recipient"}
            else:
                allow_insecure = bool(data.get("allow_insecure_smtp", False)) and user.get("is_admin")
                status = send_email(email_to, f"{tool.upper()} Result", (output.get("output") if isinstance(output, dict) else str(output)), allow_insecure=allow_insecure)
                if not status["ok"]:
                    # attach email failure info
                    if isinstance(output, dict):
                        output["email_status"] = status
                    else:
                        output = {"ok": True, "output": str(output), "email_status": status}
                else:
                    if isinstance(output, dict):
                        output["email_status"] = status
                    else:
                        output = {"ok": True, "output": str(output), "email_status": status}

        # Normalize output shape
        if isinstance(output, dict):
            return jsonify(output)
        else:
            return jsonify({"ok": True, "output": str(output)})

    except Exception as e:
        current_app.logger.exception("run-tool failed")
        return jsonify({"ok": False, "error": str(e)}), 500

