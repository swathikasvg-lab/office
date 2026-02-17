from .vpn import FortigateVpnHandler
from .sdwan import FortigateSdwanHandler
from .sys import FortigateSystemHandler

class FortigateParentHandler:

    def __init__(self):
        self.vpn = FortigateVpnHandler()
        self.sdwan = FortigateSdwanHandler()
        self.sys = FortigateSystemHandler()

    def execute(self, rule, state=None):
        """
        Master dispatcher for Fortigate rules.
        We do NOT rely on UI-provided hostname.
        We fetch all Fortigate devices automatically.
        """

        # 1. VPN Tunnel rules
        rule_name = (getattr(rule, "name", "") or "").lower()

        if "vpn" in rule_name:
            return self.vpn.execute(rule, state)

        # 2. SDWAN rules
        if "sdwan" in rule_name:
            return self.sdwan.execute(rule, state)

        # 3. System rules (CPU, Memory, HA, Session count, Disk)
        return self.sys.execute(rule, state)
