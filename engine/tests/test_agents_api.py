"""Offline tests for the MCP + A2A surface (service/agents_api.py). The engine is mocked: what is worth
testing here is the protocol routing, the validation at the public boundary, and the honesty of the shapes."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "service"))
import agents_api as api  # noqa: E402


class FakeRegion:
    def __init__(self, name, title, bounds, presets, official=None, live=None):
        self.name = name
        self.manifest = {"title": title, "official": official, "live_rainfall": live}
        self.bounds = bounds
        self.presets = presets

    def covers(self, box):
        w, s, e, n = box
        W, S, E, N = self.bounds
        return W <= w and e <= E and S <= s and n <= N


SG = FakeRegion("singapore", "Singapore", (103.6, 1.2, 104.1, 1.5),
                [{"label": "Geylang / Kallang", "bbox": [103.86, 1.305, 103.885, 1.325]}], live="nea_sg")
KO = FakeRegion("kochi", "Kochi, Kerala, India", (76.1, 9.8, 76.5, 10.2),
                [{"label": "Edappally", "bbox": [76.295, 10.01, 76.325, 10.04]}], official=[10, 50])


def regions():
    return {"singapore": SG, "kochi": KO}


# ---- the shared area resolver (the public boundary) ----------------------------------------------

def test_an_area_name_resolves_to_its_preset_box():
    r, box = api._resolve(regions, "singapore", None, "Geylang")
    assert r is SG and box == (103.86, 1.305, 103.885, 1.325)


def test_an_area_name_is_found_without_the_region():
    r, box = api._resolve(regions, None, None, "Edappally")
    assert r is KO


def test_a_bbox_without_a_region_is_matched_to_the_one_that_covers_it():
    r, box = api._resolve(regions, None, [76.3, 10.02, 76.32, 10.03], None)
    assert r is KO


@pytest.mark.parametrize("bad", [[1, 2, 3], [103.9, 1.3, 103.86, 1.325], "nope", [104.0, 1.3, 104.2, 1.32]])
def test_a_malformed_or_inverted_or_outside_box_is_a_tool_error_not_a_crash(bad):
    with pytest.raises(api.ToolError):
        api._resolve(regions, "singapore", bad, None)


def test_a_box_over_the_area_limit_is_refused():
    with pytest.raises(api.ToolError, match="limit"):
        api._resolve(regions, "singapore", [103.6, 1.2, 104.1, 1.5], None)   # the whole region, way over 120 km2


def test_an_unknown_region_is_a_tool_error():
    with pytest.raises(api.ToolError, match="unknown region"):
        api._resolve(regions, "tokyo", None, "somewhere")


# ---- MCP JSON-RPC routing ------------------------------------------------------------------------

def test_initialize_announces_the_protocol_and_the_tools_capability():
    out = api._handle_mcp(regions, {"jsonrpc": "2.0", "id": 1, "method": "initialize"})
    assert out["result"]["protocolVersion"] == api.MCP_PROTOCOL
    assert "tools" in out["result"]["capabilities"]


def test_a_notification_gets_no_response():
    assert api._handle_mcp(regions, {"jsonrpc": "2.0", "method": "notifications/initialized"}) is None


def test_tools_list_exposes_every_tool_with_a_schema():
    out = api._handle_mcp(regions, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    tools = {t["name"]: t for t in out["result"]["tools"]}
    assert set(tools) == set(api.TOOLS)
    assert all("inputSchema" in t and t["description"] for t in tools.values())


def test_list_regions_tool_names_the_areas_and_what_each_city_has():
    out = api._handle_mcp(regions, {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                                    "params": {"name": "list_regions", "arguments": {}}})
    import json
    data = json.loads(out["result"]["content"][0]["text"])
    sg = next(r for r in data["regions"] if r["region"] == "singapore")
    assert "Geylang / Kallang" in sg["areas"] and sg["has_live_rain_gauges"] is True
    assert next(r for r in data["regions"] if r["region"] == "kochi")["has_official_flood_map"] is True


def test_an_unknown_tool_is_reported_as_a_tool_error_not_a_crash():
    out = api._handle_mcp(regions, {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                                    "params": {"name": "no_such_tool", "arguments": {}}})
    assert out["result"]["isError"] is True


def test_an_unknown_method_is_a_jsonrpc_error():
    out = api._handle_mcp(regions, {"jsonrpc": "2.0", "id": 5, "method": "frobnicate"})
    assert out["error"]["code"] == -32601


def test_flood_screening_returns_counts_and_never_a_depth(monkeypatch):
    import bih_engine
    fake = {"screening": {"depressions": [{"id": "d1", "pooling_index": 5}, {"id": "d2", "pooling_index": 3}],
                          "routes_affected": [{"road": "Pipeline Road", "highway": "primary", "length_m": 120}]},
            "geojson": {"features": [{"properties": {"id": "d1", "fills_at_rainfall": True}},
                                     {"properties": {"id": "d2", "fills_at_rainfall": False}}]},
            "grid": {"area_km2": 7.1}, "exposure": {"available": True, "people_in_worst_spots": 40, "people_in_box": 9000}}
    monkeypatch.setattr(bih_engine, "analyse_area", lambda *a, **k: dict(fake))
    out = api._call_tool(regions, "flood_screening", {"region": "kochi", "area": "Edappally", "rainfall_mm": 60})
    assert out["low_spots_found"] == 2 and out["low_spots_that_fill_at_this_rainfall"] == 1
    assert out["roughly_people_in_worst_spots_that_fill"] == 40
    assert out["has_official_depth_map"] is False                 # a flag about the authority's map, not a depth of ours
    assert "max_depth_m" not in out and "depth_m" not in out      # the engine's own depth never surfaces


def test_flood_screening_refuses_rainfall_out_of_range():
    with pytest.raises(api.ToolError):
        api._call_tool(regions, "flood_screening", {"region": "kochi", "area": "Edappally", "rainfall_mm": 5000})


# ---- A2A -----------------------------------------------------------------------------------------

def test_the_agent_card_has_a_url_a_skill_and_the_protocol():
    card = api._agent_card("https://example.run.app")
    assert card["url"] == "https://example.run.app/a2a"
    assert card["protocolVersion"] == api.A2A_PROTOCOL
    assert card["skills"][0]["id"] == "screen_place" and card["skills"][0]["examples"]


def test_a_message_names_its_own_place_from_the_text():
    r, box, label = api._resolve_place_from_text(regions, "Will Edappally flood tonight?", None)
    assert r is KO and label == "Edappally"


def test_a_message_with_no_known_place_lists_the_areas_it_can_screen(monkeypatch):
    monkeypatch.setattr(api.llm, "available", lambda: True)
    out = api._handle_a2a(regions, {"jsonrpc": "2.0", "id": 1, "method": "message/send",
                                    "params": {"message": {"parts": [{"kind": "text", "text": "will it flood?"}]}}})
    text = out["result"]["parts"][0]["text"]
    assert "Edappally" in text and "Geylang / Kallang" in text


def test_message_send_runs_the_full_verified_pipeline_and_returns_the_briefing(monkeypatch):
    monkeypatch.setattr(api.llm, "available", lambda: True)
    seen = {}

    def fake_run(**kw):
        seen.update(kw)
        return {"briefing": {"headline": "Not on this forecast.", "bullets": ["Only 1.4 mm is due."],
                             "caveat": "A 10 m screening; drains are not in the data."}}
    monkeypatch.setattr(api.coordinator, "run", fake_run)
    out = api._handle_a2a(regions, {"jsonrpc": "2.0", "id": 7, "method": "message/send",
                                    "params": {"message": {"parts": [{"kind": "text", "text": "Will Edappally flood?"}]}}})
    text = out["result"]["parts"][0]["text"]
    assert text.startswith("Not on this forecast.") and "drains are not in the data" in text
    assert seen["area_label"] == "Edappally" and seen["place_resolved"] == "Edappally"   # it did not need a geocoder


def test_message_send_without_a_text_part_is_a_jsonrpc_error(monkeypatch):
    monkeypatch.setattr(api.llm, "available", lambda: True)
    out = api._handle_a2a(regions, {"jsonrpc": "2.0", "id": 8, "method": "message/send",
                                    "params": {"message": {"parts": []}}})
    assert out["error"]["code"] == -32602


def test_an_engine_crash_in_a2a_never_leaks_a_stack_trace(monkeypatch):
    monkeypatch.setattr(api.llm, "available", lambda: True)
    monkeypatch.setattr(api.coordinator, "run", lambda **k: (_ for _ in ()).throw(RuntimeError("boom secret")))
    out = api._handle_a2a(regions, {"jsonrpc": "2.0", "id": 9, "method": "message/send",
                                    "params": {"message": {"parts": [{"kind": "text", "text": "Will Edappally flood?"}]}}})
    assert out["error"]["code"] == -32603 and "boom secret" not in out["error"]["message"]
