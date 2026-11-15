import json

import pytest
from swda_agent_gateway.toolbox import ToolBox, ToolNotPermitted


def run(gw, **req):
    la = gw.runtime.agents["service-recommendation-agent"]
    return la.agent.run({"request_id": "t", **req}, ToolBox(la.agent.tools, la.record.allowed_tools))


def test_classification_and_sensitive_flag(gw):
    r = run(gw, text="I was laid off and need interview help", employment_status="unemployed")
    assert r.category == "job_seeker_support" and "sensitive_category" in r.flags and r.needs_human_review


def test_unclassified_returns_no_recommendations_and_flags(gw):
    r = run(gw, text="hello there")
    assert r.category == "unclassified" and r.recommendations == [] and "low_confidence" in r.flags


def test_eligibility_filters_come_from_config(gw):
    r = run(gw, text="digital skills python coding", employment_status="retired", age=62)
    assert r.recommendations and all(x.programme_id in {"P012"} for x in r.recommendations)   # only retired-eligible programme


def test_deterministic(gw):
    a = run(gw, text="data analytics career change", age=29).model_dump(exclude={"tool_trace"})
    b = run(gw, text="data analytics career change", age=29).model_dump(exclude={"tool_trace"})
    assert json.dumps(a) == json.dumps(b)


def test_toolbox_denies_unlisted_tool(gw):
    la = gw.runtime.agents["service-recommendation-agent"]
    tb = ToolBox(la.agent.tools, ["classify_request"])
    with pytest.raises(ToolNotPermitted):
        tb.call("search_programmes", request={})
    assert tb.trace[-1].ok is False
