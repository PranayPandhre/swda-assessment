import yaml
from swda_agent_gateway.auth import Client
from swda_agent_gateway.registry import AgentRegistry

PORTAL = Client("portal-app", frozenset({"agent:service-recommendation-agent:invoke"}))
ANALYST = Client("analyst-console", frozenset())
AGENT = "service-recommendation-agent"
GOOD_ARGS = {"request_id": "r-1", "text": "I want to switch career into data analytics with python",
             "employment_status": "employed", "age": 30, "preferred_mode": "online"}


def edit_registry(path, fn):
    """Edit the first agent entry, then re-seal (as CI would) so only the intended defect remains."""
    raw = yaml.safe_load(open(path))
    fn(raw["agents"][0], raw)
    yaml.safe_dump(raw, open(path, "w"), sort_keys=False)


def call(gw, client, name=AGENT, args=None, req_id=1):
    return gw.handle({"jsonrpc": "2.0", "id": req_id, "method": "tools/call",
                      "params": {"name": name, "arguments": GOOD_ARGS if args is None else args}}, client)


def seal(path, settings):
    AgentRegistry(path, settings.limits.allowed_entrypoint_prefix).seal_file()
