"""Constants for the Home Assistant Gateway integration."""

DOMAIN = "ha_gateway"
VERSION = "0.2.0"

CONF_ADDRESS = "address"
CONF_TOKEN = "token"
CONF_USE_TLS = "use_tls"
CONF_LOCAL_URL = "local_url"
CONF_FORWARD_CLIENT_IP = "forward_client_ip"

DEFAULT_LOCAL_URL = "http://127.0.0.1:8123"

# Dispatcher signal sent with the entry id when the tunnel goes up or down.
SIGNAL_STATE = f"{DOMAIN}_state"
