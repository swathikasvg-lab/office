import requests
from datetime import datetime

PROM_URL = "http://localhost:9090"
INTERVAL = 60  # Alloy pushes 1/min


# ----------- Human Readable Downtime ----------------
def humanize_minutes(m):
    days = m // 1440
    m %= 1440
    hours = m // 60
    minutes = m % 60

    parts = []
    if days > 0: parts.append(f"{days} day{'s' if days>1 else ''}")
    if hours > 0: parts.append(f"{hours} hr{'s' if hours>1 else ''}")
    if minutes > 0: parts.append(f"{minutes} min{'s' if minutes>1 else ''}")

    return " ".join(parts) if parts else "0 mins"


class ServerAvailabilityReport:

    # ----------------------------------------------------
    # Fetch all instances + hostnames + customer name
    # ----------------------------------------------------
    def get_all_instances(self):
        inst_map = {}   # instance → {hostname, customer, type}

        # Linux hostname + customer
        q_linux = "node_uname_info"
        r = requests.get(f"{PROM_URL}/api/v1/query", params={"query": q_linux}).json()
        for row in r["data"]["result"]:
            inst = row["metric"].get("instance")
            hostname = row["metric"].get("nodename")
            customer = row["metric"].get("CustomerName", "Backend")

            inst_map[inst] = {
                "hostname": hostname or inst,
                "customer": customer,
                "type": "linux"
            }

        # Windows hostname + customer
        q_win = "windows_cs_hostname"
        r = requests.get(f"{PROM_URL}/api/v1/query", params={"query": q_win}).json()
        for row in r["data"]["result"]:
            inst = row["metric"].get("instance")
            hostname = row["metric"].get("hostname")
            customer = row["metric"].get("CustomerName", "Backend")

            inst_map[inst] = {
                "hostname": hostname or inst,
                "customer": customer,
                "type": "windows"
            }

        return inst_map  # instance → {hostname, customer, type}

    # ----------------------------------------------------
    # Sample count (Linux or Windows)
    # ----------------------------------------------------
    def count_samples(self, inst, minutes, os_type):
        metric = "node_time_seconds" if os_type == "linux" else "windows_os_time"
        query = f'count_over_time({metric}{{instance="{inst}"}}[{minutes}m])'

        r = requests.get(f"{PROM_URL}/api/v1/query", params={"query": query}).json()
        try:
            return int(float(r["data"]["result"][0]["value"][1]))
        except:
            return 0

    # ----------------------------------------------------
    # Main Report Logic
    # ----------------------------------------------------
    def run(self, instance, start, end, fmt, customer=None):
        start_ts = datetime.fromisoformat(start)
        end_ts = datetime.fromisoformat(end)

        minutes = int((end_ts - start_ts).total_seconds() / 60)
        expected = minutes

        all_map = self.get_all_instances()

        # Filter by customer if selected
        if customer and customer != "ALL":
            all_map = {k: v for k, v in all_map.items() if v["customer"] == customer}

        # Single instance?
        if instance != "ALL":
            if instance in all_map:
                all_map = {instance: all_map[instance]}
            else:
                return None  # no matching host

        results = []

        for inst, meta in all_map.items():
            rec = self.count_samples(inst, minutes, meta["type"])
            availability = (rec / expected) * 100 if expected else 0
            downtime = expected - rec

            results.append({
                "instance": meta["hostname"],
                "customer": meta["customer"],
                "availability": round(availability, 2),
                "downtime": humanize_minutes(downtime)
            })

        # Output File
        if fmt == "excel":
            from .generator_excel import build_excel
            return build_excel(results, start_ts, end_ts, report_name="Server Availability")

        from .generator_pdf import build_pdf
        return build_pdf(results, start_ts, end_ts, report_name="Server Availability")

