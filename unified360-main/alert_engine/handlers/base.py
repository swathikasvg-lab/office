# alert_engine/handlers/base.py

class BaseMonitoringHandler:
    monitoring_type = None  # must override

    def fetch_metrics(self, rule):
        """
        Must return a dict of metrics.
        Example:
            { 'port_status': 'DOWN', 'response_time_ms': 12 }
        """
        raise NotImplementedError()

