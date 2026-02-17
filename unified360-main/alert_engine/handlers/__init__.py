# alert_engine/handlers/__init__.py

from .port_handler import PortHandler
from .url_handler import UrlHandler
from .ping_handler import PingHandler
from .fortigate.parent import FortigateParentHandler
from .snmp_interface import SNMPInterfaceHandler
from .service_down_handler import ServiceDownHandler 
from .oracle_handler import OracleHandler
from .server_handler import ServerHandler

HANDLER_REGISTRY = {
    "server": ServerHandler(),
    "port": PortHandler(),
    "url": UrlHandler(),
    "ping": PingHandler(),
    "fortigate": FortigateParentHandler(),
    "SNMP_Interface": SNMPInterfaceHandler(),
    "service_down": ServiceDownHandler(),
    "oracle": OracleHandler(),
}
