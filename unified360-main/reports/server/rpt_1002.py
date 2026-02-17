from prometheus_api_client import PrometheusConnect, MetricRangeDataFrame
from datetime import datetime
import pandas as pd
import requests

PROM_URL = "http://localhost:9090"

class ServerPerformanceReport:

    def __init__(self):
        self.prom = PrometheusConnect(url=PROM_URL, disable_ssl=True)

    # ------------------------------
    # Helper: Convert iso → datetime
    # ------------------------------
    def to_dt(self, s):
        return datetime.fromisoformat(s)

    # ------------------------------
    # Helper: safe average from dataframe
    # ------------------------------
    def safe_avg(self, df):
        try:
            return float(df['value'].mean())
        except:
            return 0.0

    # ------------------------------
    # Detect available instances
    # ------------------------------
    def get_all_instances(self, customer):
        inst_map = {}

        # ----------------------------------
        # Linux instances via raw /series
        # ----------------------------------
        try:
            if customer == 'Backend':
                params={
                    # Matches node_uname_info where CustomerName is either missing or empty
                    "match[]": 'node_uname_info{CustomerName!~".+"}'
                }
            else:
                params={
                    "match[]": f'node_uname_info{{CustomerName="{customer}"}}'
                }
            r = requests.get(
                f"{PROM_URL}/api/v1/series",
                params=params).json()

            for row in r.get("data", []):
                inst = row.get("instance")
                if inst:
                    inst_map[inst] = {"type": "linux"}

        except Exception as e:
            print("Linux series error:", e)

        # ----------------------------------
        # Windows instances
        # ----------------------------------
        try:
            if customer == 'Backend':
                params={
                    # Matches node_uname_info where CustomerName is either missing or empty
                    "match[]": 'windows_os_info{CustomerName!~".+"}'
                }
            else:
                params={
                    "match[]": f'windows_os_info{{CustomerName="{customer}"}}'
                }
            r = requests.get(
                f"{PROM_URL}/api/v1/series",
                params=params
            ).json()

            for row in r.get("data", []):
                inst = row.get("instance")
                if inst:
                    inst_map[inst] = {"type": "windows"}

        except Exception as e:
            print("Windows series error:", e)

        return inst_map


    # ------------------------------
    # Run a range query and return dataframe
    # ------------------------------
    def q(self, query, start, end):
        try:
            step = self.choose_step(start, end)
            data = self.prom.custom_query_range(
                query=query,
                start_time=start,
                end_time=end,
                step=step
            )
            df = MetricRangeDataFrame(data)
            print(df)
            return df
        except Exception as e:
            print(str(e))
            return pd.DataFrame()

    def choose_step(self, start_dt, end_dt):
        seconds = (end_dt - start_dt).total_seconds()
    
        if seconds <= 12 * 3600:
            return "60s"     # 1 min
        elif seconds <= 2 * 86400:
            return "5m"      # 5 min
        elif seconds <= 7 * 86400:
            return "15m"     # 15 min
        elif seconds <= 30 * 86400:
            return "1h"      # 1 hour
        else:
            return "3h"      # 3 hours
    # ------------------------------
    # Disk usage per mount (Linux)
    # ------------------------------
    def linux_disk_usage(self, inst, start, end):
        query = (
            f'(100 - ((node_filesystem_avail_bytes{{instance="{inst}",fstype!~"tmpfs|overlay"}} * 100)'
            f' / node_filesystem_size_bytes{{instance="{inst}",fstype!~"tmpfs|overlay"}}))'
        )

        df = self.q(query, start, end)
        if df.empty:
            return "N/A"

        disk_map = {}
        for mnt in df['mountpoint'].unique():
            mdf = df[df['mountpoint'] == mnt]
            disk_map[mnt] = round(self.safe_avg(mdf), 2)

        return ", ".join([f"{k}={v}%" for k, v in disk_map.items()])

    # ------------------------------
    # Disk usage per volume (Windows)
    # ------------------------------
    def windows_disk_usage(self, inst, start, end):
        query = (
            f'(100 - ((windows_logical_disk_free_megabytes{{instance="{inst}"}} * 1048576 * 100)'
            f' / windows_logical_disk_size_bytes{{instance="{inst}"}}))'
        )

        df = self.q(query, start, end)
        if df.empty:
            return "N/A"

        disk_map = {}
        for vol in df['metric.volume'].unique():
            mdf = df[df['metric.volume'] == vol]
            disk_map[vol] = round(self.safe_avg(mdf), 2)

        return ", ".join([f"{k}={v}%" for k, v in disk_map.items()])

    # ------------------------------
    # Disk R/W
    # ------------------------------
    def disk_rw(self, inst, start, end, os_type):
        if os_type == "linux":
            read_q = f'rate(node_disk_read_bytes_total{{instance="{inst}"}}[5m])'
            write_q = f'rate(node_disk_written_bytes_total{{instance="{inst}"}}[5m])'
        else:
            read_q = f'rate(windows_logical_disk_read_bytes_total{{instance="{inst}"}}[5m])'
            write_q = f'rate(windows_logical_disk_write_bytes_total{{instance="{inst}"}}[5m])'

        df_r = self.q(read_q, start, end)
        df_w = self.q(write_q, start, end)

        read_kb = self.safe_avg(df_r) / 1024
        write_kb = self.safe_avg(df_w) / 1024

        return round(read_kb, 2), round(write_kb, 2)

    # ------------------------------
    # Network R/T
    # ------------------------------
    def net_io(self, inst, start, end):
        rx_q = f'rate(node_network_receive_bytes_total{{instance="{inst}"}}[5m])'
        tx_q = f'rate(node_network_transmit_bytes_total{{instance="{inst}"}}[5m])'

        df_rx = self.q(rx_q, start, end)
        df_tx = self.q(tx_q, start, end)

        rx = self.safe_avg(df_rx) / 1024
        tx = self.safe_avg(df_tx) / 1024

        return round(rx, 2), round(tx, 2)

    # ------------------------------
    # CPU / Memory
    # ------------------------------
    def cpu_mem(self, inst, start, end, os_type):
        if os_type == "linux":
            cpu_q = (
                f'100 - (avg(rate(node_cpu_seconds_total{{instance="{inst}",mode="idle"}}[5m])) * 100)'
            )
            mem_q = (
                f'(1 - (node_memory_MemAvailable_bytes{{instance="{inst}"}} / '
                f'node_memory_MemTotal_bytes{{instance="{inst}"}})) * 100'
            )
        else:
            cpu_q = (
                f'100 - (avg(rate(windows_cpu_time_total{{instance="{inst}",mode="idle"}}[5m])) * 100)'
            )
            mem_q = (
                f'(1 - (windows_memory_available_bytes{{instance="{inst}"}} / '
                f'windows_memory_commit_limit_bytes{{instance="{inst}"}})) * 100'
            )

        df_cpu = self.q(cpu_q, start, end)
        df_mem = self.q(mem_q, start, end)

        cpu = round(self.safe_avg(df_cpu), 2)
        mem = round(self.safe_avg(df_mem), 2)

        return cpu, mem

    # ------------------------------
    # MAIN ENTRY
    # ------------------------------
    def run(self, instance, start, end, customer, fmt):
        start_dt = self.to_dt(start)
        end_dt = self.to_dt(end)

        inst_map = self.get_all_instances(customer)

        # Filter customer (from your API inside routes)
        # Customer filtering happens outside Prometheus

        # Filter instance selection
        if instance != "ALL":
            if instance in inst_map:
                inst_map = {instance: inst_map[instance]}
            else:
                # cannot find exact instance
                return None

        results = []
        for inst, meta in inst_map.items():

            os_type = meta["type"]

            cpu, mem = self.cpu_mem(inst, start_dt, end_dt, os_type)

            if os_type == "linux":
                disk = self.linux_disk_usage(inst, start_dt, end_dt)
            else:
                disk = self.windows_disk_usage(inst, start_dt, end_dt)

            r_kb, w_kb = self.disk_rw(inst, start_dt, end_dt, os_type)

            rx, tx = self.net_io(inst, start_dt, end_dt)

            results.append({
                "instance": inst,
                "cpu": cpu,
                "mem": mem,
                "disk": disk,
                "disk_read": r_kb,
                "disk_write": w_kb,
                "net_in": rx,
                "net_out": tx,
            })

        if fmt == "excel":
            from .performance_excel import build_excel
            return build_excel(results, start_dt, end_dt)

        from .performance_pdf import build_pdf
        return build_pdf(results, start_dt, end_dt, customer)

