"""Service Recommendation Agent: deterministic, config-driven, synthetic data only.

Pipeline (all via the permission-checked ToolBox):
  classify_request -> search_programmes (eligibility filter) -> rank_programmes (weighted features)
The same class backs the hourly batch job and the online MCP tool, so behaviour is identical.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from ..config import load_yaml
from ..models import Recommendation, RecommendationResponse
from ..toolbox import ToolBox

UNCLASSIFIED = "unclassified"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ClassifierCfg(_Strict):
    min_score: float
    saturation: float
    ambiguity_margin: float
    categories: dict[str, dict[str, dict[str, float]]]


class RankingCfg(_Strict):
    top_k: int
    weights: dict[str, float]
    max_duration_weeks_fit: float


class AgentCfg(_Strict):
    catalogue_path: str
    request_schema_path: str
    classifier: ClassifierCfg
    ranking: RankingCfg
    fields: dict
    review: dict


_OPS = {
    "in": lambda req, prog: req in prog,
    "gte": lambda req, prog: req >= prog,
    "lte": lambda req, prog: req <= prog,
}


class ServiceRecommendationAgent:
    def __init__(self, cfg: AgentCfg, catalogue: list[dict], request_schema: dict, agent_id: str, version: str):
        self.cfg, self.catalogue, self.request_schema = cfg, catalogue, request_schema
        self.agent_id, self.version = agent_id, version
        self._patterns = {
            cat: [(kw, w, re.compile(rf"\b{re.escape(kw)}\b")) for kw, w in spec["keywords"].items()]
            for cat, spec in cfg.classifier.categories.items()
        }
        self.tools = {
            "classify_request": self.classify_request,
            "search_programmes": self.search_programmes,
            "rank_programmes": self.rank_programmes,
        }

    # ---- deterministic tools -------------------------------------------------
    def classify_request(self, text: str) -> dict:
        c = self.cfg.classifier
        lowered = text.lower()
        scores = {}
        for cat, pats in self._patterns.items():
            hit_weight = sum(w for _, w, rx in pats if rx.search(lowered))
            scores[cat] = round(min(1.0, hit_weight / c.saturation), 4)
        ordered = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))
        top, top_s = ordered[0]
        second_s = ordered[1][1] if len(ordered) > 1 else 0.0
        if top_s < c.min_score:
            return {"category": UNCLASSIFIED, "scores": scores, "ambiguous": False}
        return {"category": top, "scores": scores, "ambiguous": (top_s - second_s) < c.ambiguity_margin}

    def search_programmes(self, request: dict) -> list[dict]:
        f = self.cfg.fields
        out = []
        for prog in self.catalogue:
            ok = True
            for rule in f["eligibility"]:
                req_v, prog_v = request.get(rule["request_field"]), prog.get(rule["programme_field"])
                if req_v is None or prog_v is None:     # unknown on either side -> rule not applied
                    continue
                if not _OPS[rule["op"]](req_v, prog_v):
                    ok = False
                    break
            if ok:
                out.append(prog)
        return out

    def rank_programmes(self, candidates: list[dict], request: dict, category: str) -> list[dict]:
        r, f = self.cfg.ranking, self.cfg.fields
        cat = f["catalogue"]
        w = r.weights
        ranked = []
        for prog in candidates:
            reasons, score = [], 0.0
            if category in prog.get(cat["categories"], []):
                score += w["category_match"]
                reasons.append(f"matches category '{category}'")
            for pref in f["preferences"]:
                want = request.get(pref["request_field"])
                if want is not None and prog.get(pref["programme_field"]) == want:
                    score += w["mode_match"]
                    reasons.append(f"matches preferred {pref['programme_field']} '{want}'")
            score += w["subsidy"] * float(prog.get(cat["subsidy_rate"], 0.0))
            fit = max(0.0, 1.0 - float(prog.get(cat["duration_weeks"], 0)) / r.max_duration_weeks_fit)
            score += w["duration_fit"] * fit
            ranked.append({"programme_id": prog[cat["id"]], "title": prog[cat["title"]],
                           "score": round(score, 4), "reasons": reasons})
        ranked.sort(key=lambda x: (-x["score"], x["programme_id"]))      # deterministic tie-break
        return ranked[: r.top_k]

    # ---- orchestration of the tools -----------------------------------------
    def run(self, request: dict, toolbox: ToolBox) -> RecommendationResponse:
        text = request[self.cfg.fields["text"]]
        cls = toolbox.call("classify_request", text=text)
        flags: list[str] = []
        recs: list[dict] = []
        if cls["category"] == UNCLASSIFIED:
            flags.append(self.cfg.review["low_confidence_flag"])
        else:
            candidates = toolbox.call("search_programmes", request=request)
            recs = toolbox.call("rank_programmes", candidates=candidates, request=request, category=cls["category"])
            if not recs:
                flags.append("no_eligible_programme")
        if cls["ambiguous"]:
            flags.append("ambiguous_category")
        if cls["category"] in self.cfg.review["sensitive_categories"]:
            flags.append("sensitive_category")
        return RecommendationResponse(
            request_id=request["request_id"], category=cls["category"], category_scores=cls["scores"],
            recommendations=[Recommendation(**r) for r in recs], flags=flags,
            needs_human_review=bool(flags), agent_id=self.agent_id, agent_version=self.version,
            tool_trace=toolbox.trace,
        )


def build_agent(rec, base_dir: Path) -> ServiceRecommendationAgent:
    cfg = AgentCfg(**load_yaml(base_dir / rec.agent_config))
    catalogue = json.loads((base_dir / cfg.catalogue_path).read_text())["programmes"]
    schema = json.loads((base_dir / cfg.request_schema_path).read_text())
    return ServiceRecommendationAgent(cfg, catalogue, schema, rec.agent_id, rec.version)
