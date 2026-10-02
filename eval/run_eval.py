#!/usr/bin/env python3
"""三方对比 eval: 人工 ground truth vs 旧单次调用链路 vs 新 LangGraph agent.

field_id 固定为 data_analyst(21/30 简历主攻数据方向, 见 DATASET_NOTES 适用边界).
模型两边统一用 --model 指定(默认 DEFAULT_MODEL；省钱可用 Haiku),
旧链路 runs=1(生产默认).

用法:
    .venv/bin/python eval/run_eval.py [--limit N] [--only R01,R02]
输出:
    eval/results/<timestamp>/old.json      旧链路逐份原始结果
    eval/results/<timestamp>/new.json      新链路逐份原始结果(含每轮 scorer 快照)
    eval/results/<timestamp>/metrics.json  对比指标
    eval/results/<timestamp>/report.md     人读版报告

认证: 通过 httpx 补丁把 vault surrogate 注入所有 api.anthropic.com 请求,
两条链路的代码零修改(api_key 传占位串即可).
"""
import argparse
import csv
import json
import os
import sys
import time
from datetime import datetime

# ---- 1. vault 认证补丁(必须在 import anthropic 相关之前) ----
# 1a. httpx2 解析 no_proxy 里的 "[::1]" 这类括号 IPv6 会崩, 先清掉(不影响外网请求)
for _var in ("no_proxy", "NO_PROXY"):
    _v = os.environ.get(_var, "")
    os.environ[_var] = ",".join(p for p in _v.split(",") if "[" not in p and "::" not in p)

import sys  # noqa: F401  (已在文件头导入, 此处仅强调顺序)
sys.path.insert(0, "/opt/hatch/skills/skill-creator/bin")
from dynamic_credentials import dynamic_credential_entry  # noqa: E402

CALL_COUNT = {"n": 0}
_USAGE = {"input": 0, "output": 0, "cache_creation": 0, "cache_read": 0}


def _install_surrogate_patch(mod):
    _orig_send = mod.Client.send

    def _patched_send(self, request, **kwargs):
        url = str(request.url)
        is_anthropic = "api.anthropic.com" in url
        if is_anthropic:
            entry = dynamic_credential_entry("custom.anthropic")
            request.headers["x-api-key"] = entry["surrogate"]
            CALL_COUNT["n"] += 1
        resp = _orig_send(self, request, **kwargs)
        if is_anthropic:
            # 累计量 token：只处理非流式响应；resp.read() 可重复读，
            # 不影响 SDK 后续自己的读取。
            try:
                if "text/event-stream" not in resp.headers.get("content-type", ""):
                    resp.read()
                    u = resp.json().get("usage") or {}
                    _USAGE["input"] += u.get("input_tokens", 0)
                    _USAGE["output"] += u.get("output_tokens", 0)
                    _USAGE["cache_creation"] += u.get("cache_creation_input_tokens", 0)
                    _USAGE["cache_read"] += u.get("cache_read_input_tokens", 0)
            except Exception:
                pass
        return resp

    mod.Client.send = _patched_send


def _usage_delta(before):
    return {k: _USAGE[k] - before[k] for k in _USAGE}


import httpx  # noqa: E402

_install_surrogate_patch(httpx)
try:
    import httpx2  # noqa: E402  (anthropic SDK 1.9+ 实际用的 http 层)

    _install_surrogate_patch(httpx2)
except ImportError:
    pass

# ---- 2. 业务 import ----
# 注意: sys.path 只加 REPO/app, 不加 REPO —— 否则顶层模块名 `app` 会撞上 app/app.py。
# agents/ 是正常包 (有 __init__.py), scoring 是 app/ 下的顶层模块。
HOME = os.path.expanduser("~")
REPO = os.path.join(HOME, "workspace", "resume-compass")
sys.path.insert(0, os.path.join(REPO, "app"))
EVAL = os.path.join(REPO, "eval")

import scoring  # noqa: E402
import agents.graph as graph_mod  # noqa: E402
import agents.nodes as nodes_mod  # noqa: E402

DIMS = ["edu", "exp", "proj", "skill", "cert", "lead", "present"]
FIELD_ID = "data_analyst"
EVAL = os.path.join(REPO, "eval")

# 模型定价: (input, output, cache_read, cache_creation) $/MTok
PRICING = {
    "claude-sonnet-5": (2.0, 10.0, 0.2, 2.5),
    "claude-haiku-4-5-20251001": (1.0, 5.0, 0.1, 1.25),
}

# ---- 3. 记录 critic 修订前后: 给 score_node 加快照 ----
_orig_score_node = nodes_mod.score_node


def _recording_score_node(state, api_key, model, scorer_agent=None):
    out = _orig_score_node(state, api_key=api_key, model=model,
                           scorer_agent=scorer_agent)
    snaps = list(state.get("eval_snapshots") or [])
    snaps.append(out.get("scorer_output"))
    out["eval_snapshots"] = snaps
    return out


graph_mod.score_node = _recording_score_node


def load_inputs(only=None, limit=None):
    texts = {}
    for i in range(1, 31):
        rid = f"R{i:02d}"
        if only and rid not in only:
            continue
        p = os.path.join(EVAL, "resumes_text", f"{rid}.txt")
        with open(p, encoding="utf-8") as fh:
            texts[rid] = fh.read()
        if limit and len(texts) >= limit:
            break
    human = {}
    with open(os.path.join(EVAL, "human_scores.csv"), encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            human[row["resume_id"]] = {d: int(row[d]) for d in DIMS}
    return texts, human


def _is_transient(exc):
    """网络类抖动值得重试一次。"""
    name = type(exc).__name__
    return "Connection" in name or "Timeout" in name


def _run_with_retry(fn, rid, label):
    for attempt in (1, 2):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001
            if attempt == 1 and _is_transient(exc):
                print(f"  {rid} {label} 网络抖动, 20秒后重试...", flush=True)
                time.sleep(20)
                continue
            return {"resume_id": rid,
                    "error": f"{type(exc).__name__}: {exc}"}


def run_old(rid, text, model):
    t0 = time.time()
    calls_before = CALL_COUNT["n"]
    usage_before = dict(_USAGE)
    result = scoring.score_resume(
        resume_text=text, field_id=FIELD_ID,
        api_key="eval-dummy", model=model, runs=1,
    )
    return {
        "resume_id": rid,
        "model": model,
        "dimension_scores": result.get("dimension_scores", {}),
        "total": result.get("total"),
        "tier_label": result.get("tier_label"),
        "usage": result.get("usage", {}),
        "measured_usage": _usage_delta(usage_before),
        "api_calls": CALL_COUNT["n"] - calls_before,
        "seconds": round(time.time() - t0, 1),
    }


def run_new(rid, text, graph, field, model):
    t0 = time.time()
    calls_before = CALL_COUNT["n"]
    usage_before = dict(_USAGE)
    final_state = graph.invoke({
        "resume_text": text,
        "field_id": FIELD_ID,
        "field": field,  # plan_node 直接读 state["field"], 调用方负责 resolve
    })
    fr = final_state.get("final_result") or {}
    snaps = final_state.get("eval_snapshots") or []

    def dim_of(snap):
        if not snap:
            return {}
        dims = snap.get("dimensions") or {}
        return {d: (dims.get(d) or {}).get("score") for d in DIMS}

    return {
        "resume_id": rid,
        "model": model,
        "dimension_scores": {d: fr.get("scores", {}).get(d)
                             for d in DIMS} if isinstance(fr.get("scores"), dict)
        else {d: (fr.get("dimension_scores") or {}).get(d) for d in DIMS},
        "total": fr.get("total"),
        "tier_label": fr.get("tier_label"),
        "revision_rounds": final_state.get("revision_round", 1),
        "critic_pass": final_state.get("critic_pass"),
        "critic_feedback": final_state.get("critic_feedback", []),
        "critic_corrections": final_state.get("critic_corrections", []),
        "round1_scores": dim_of(snaps[0] if snaps else None),
        "final_snap_scores": dict(fr.get("dimension_scores") or {}),
        "n_snapshots": len(snaps),
        "api_calls": CALL_COUNT["n"] - calls_before,
        "measured_usage": _usage_delta(usage_before),
        "seconds": round(time.time() - t0, 1),
    }


def spearman(xs, ys):
    n = len(xs)
    if n < 2:
        return None

    def ranks(v):
        order = sorted(range(n), key=lambda i: v[i])
        r = [0] * n
        i = 0
        while i < n:
            j = i
            while j + 1 < n and v[order[j + 1]] == v[order[i]]:
                j += 1
            avg = (i + j) / 2 + 1
            for k in range(i, j + 1):
                r[order[k]] = avg
            i = j + 1
        return r

    rx, ry = ranks(xs), ranks(ys)
    mx, my = sum(rx) / n, sum(ry) / n
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    den = (sum((a - mx) ** 2 for a in rx) * sum((b - my) ** 2 for b in ry)) ** 0.5
    return round(num / den, 4) if den else None


def compute_metrics(human, old_res, new_res, default_model):
    fw = scoring.load_framework()
    field = next(f for f in fw["fields"] if f["id"] == FIELD_ID)
    weights = field["weights"]

    def model_of(r):
        # 混跑前的旧结果没有 model 字段；写死回填为 Sonnet——
        # 唯一缺字段的就是 20260928_192435 目录，它 100% 是 Sonnet 跑的。
        # 注意不能用 default_model（混跑时它是 Haiku，会把 Sonnet 结果误标）。
        return r.get("model") or "claude-sonnet-5"

    def human_total(hs):
        # 与 report_node 同款确定性公式, 保证人工总分与模型总分口径一致
        base = sum(weights[d] * hs[d] / 5 for d in DIMS)
        return round(min(scoring._score_cap(hs, fw), base))

    def dim_series(res_list):
        return {d: [(human[r["resume_id"]][d], r["dimension_scores"].get(d))
                    for r in res_list
                    if r["dimension_scores"].get(d) is not None]
                for d in DIMS}

    def pipeline_metrics(res_list):
        """单条链路的指标；空列表时返回 None 占位而不是炸。"""
        out = {"per_dimension": {}, "total": None, "tier": None, "n": len(res_list)}
        if not res_list:
            return out
        ds = dim_series(res_list)
        for d in DIMS:
            pairs = ds[d]
            if not pairs:
                out["per_dimension"][d] = {"mae": None, "exact_match": None, "n": 0}
                continue
            mae = sum(abs(h - m) for h, m in pairs) / len(pairs)
            em = sum(1 for h, m in pairs if h == m) / len(pairs)
            out["per_dimension"][d] = {
                "mae": round(mae, 3),
                "exact_match": round(em, 3),
                "n": len(pairs),
            }
        htotals, mtotals = [], []
        for r in res_list:
            if r["total"] is not None:
                htotals.append(human_total(human[r["resume_id"]]))
                mtotals.append(r["total"])
        if mtotals:
            mae_t = sum(abs(h - m) for h, m in zip(htotals, mtotals)) / len(mtotals)
            out["total"] = {
                "mae": round(mae_t, 3),
                "spearman": spearman(htotals, mtotals),
                "n": len(mtotals),
                # 口径说明: 人工总分不含 bonus(人工表没有 bonus 信息),
                # 模型总分可能含最多 10 分 bonus, 因此 total/tier 对比天然口径不一致,
                # 以维度级 MAE 为主要结论依据。
            }
            tiers = sorted(fw["tiers"], key=lambda t: t["min"], reverse=True)

            def tier_of(total):
                return next(t["label"] for t in tiers if total >= t["min"])

            agree = sum(
                1 for r in res_list
                if r["total"] is not None
                and r.get("tier_label") == tier_of(human_total(human[r["resume_id"]]))
            )
            out["tier"] = {
                "agreement": round(agree / len(mtotals), 3),
                "n": len(mtotals),
            }
        return out

    metrics = {"per_dimension": {}, "total": {}, "tier": {}, "critic": {},
               "by_model": {}, "paired": {}}
    for name, res_list in (("old", old_res), ("new", new_res)):
        pm = pipeline_metrics(res_list)
        for d in DIMS:
            metrics["per_dimension"].setdefault(d, {})[name] = pm["per_dimension"][d]
        metrics["total"][name] = pm["total"]
        metrics["tier"][name] = pm["tier"]
    # critic 修订效果: 只看被打回过的简历, 修订前后与人工分的距离变化
    improved, worsened, same = 0, 0, 0
    deltas = []
    for r in new_res:
        if r["revision_rounds"] > 1 and r["round1_scores"]:
            h = human[r["resume_id"]]
            d1 = sum(abs(h[d] - (r["round1_scores"].get(d) or 0)) for d in DIMS)
            d2 = sum(abs(h[d] - (r["dimension_scores"].get(d) or 0)) for d in DIMS)
            delta = d1 - d2  # >0 说明修订后更接近人工
            deltas.append(delta)
            if delta > 0:
                improved += 1
            elif delta < 0:
                worsened += 1
            else:
                same += 1
    metrics["critic"] = {
        "revised_count": len(deltas),
        "improved": improved, "worsened": worsened, "same": same,
        "mean_delta": round(sum(deltas) / len(deltas), 3) if deltas else None,
        # 修订轮跑完但分数一个没变 = critic 的打回没起作用
        "revision_noop_count": sum(
            1 for r in new_res
            if r["revision_rounds"] > 1
            and r.get("round1_scores") == r.get("final_snap_scores")),
    }
    # 分层: 按模型分别汇总（混跑时每层 n 较小，仅作参考，不跨模型比绝对 MAE）
    for name, res_list in (("old", old_res), ("new", new_res)):
        by_m = {}
        for r in res_list:
            by_m.setdefault(model_of(r), []).append(r)
        for m, sub in by_m.items():
            pm = pipeline_metrics(sub)
            maes = [v["mae"] for v in pm["per_dimension"].values()
                    if v["mae"] is not None]
            metrics["by_model"].setdefault(m, {})[name] = {
                "n": len(sub),
                "mean_mae": round(sum(maes) / len(maes), 3) if maes else None,
                "per_dimension": {d: pm["per_dimension"][d]["mae"] for d in DIMS},
            }
    # 配对差值: 每份简历内部新旧链路的维度 MAE 之差（<0 表示新链路更接近人工）。
    # 配对天然控制了"简历难度"和"模型"，是混跑下最干净的比较口径。
    new_by_id = {r["resume_id"]: r for r in new_res}
    diffs = []
    for r in old_res:
        nr = new_by_id.get(r["resume_id"])
        if nr is None:
            continue
        h = human[r["resume_id"]]
        d_old = sum(abs(h[d] - (r["dimension_scores"].get(d) or 0))
                    for d in DIMS) / len(DIMS)
        d_new = sum(abs(h[d] - (nr["dimension_scores"].get(d) or 0))
                    for d in DIMS) / len(DIMS)
        diffs.append({"resume_id": r["resume_id"], "model": model_of(r),
                      "delta": round(d_new - d_old, 3)})
    if diffs:
        vals = [x["delta"] for x in diffs]
        metrics["paired"] = {
            "n": len(diffs),
            "mean_delta": round(sum(vals) / len(vals), 3),
            "new_better": sum(1 for v in vals if v < 0),
            "old_better": sum(1 for v in vals if v > 0),
            "tie": sum(1 for v in vals if v == 0),
            "by_resume": diffs,
        }
    # 耗时 / 调用 / 费用统计（按每份结果实际用的模型分别计价）
    for name, res_list in (("old", old_res), ("new", new_res)):
        tok = {"input": 0, "output": 0, "cache_creation": 0, "cache_read": 0}
        cost = 0.0
        for r in res_list:
            m = model_of(r)
            pi, po, pcr, pcw = PRICING.get(m, PRICING["claude-sonnet-5"])
            u = r.get("measured_usage") or {}
            for k in tok:
                tok[k] += u.get(k, 0)
            cost += (u.get("input", 0) / 1e6 * pi + u.get("output", 0) / 1e6 * po
                     + u.get("cache_creation", 0) / 1e6 * pcw
                     + u.get("cache_read", 0) / 1e6 * pcr)
        metrics.setdefault("cost", {})[name] = {
            "total_seconds": round(sum(r["seconds"] for r in res_list), 1),
            "total_api_calls": sum(r["api_calls"] for r in res_list),
            "measured_tokens": tok,
            "estimated_usd": round(cost, 2),
            "n": len(res_list),
        }
    metrics["cost"]["pricing_note"] = (
        "按每份结果实际模型计价；sonnet-5: $2/$10/MTok, haiku-4-5: $1/$5/MTok "
        "(input/output), cache_read 0.1x, cache_creation 按1.25x估算)")
    models_used = sorted({model_of(r) for r in old_res + new_res})
    metrics["models_used"] = models_used
    if len(models_used) > 1:
        metrics["mixed_model_note"] = (
            "本次 eval 混用了多个模型（Sonnet 跑完的部分保留，剩余用 Haiku 接上）。"
            "每份简历内部新旧链路模型一致，paired 差值不受模型干扰；"
            "分层的 per_dimension/by_model 仅作参考，不直接跨模型对比绝对 MAE。")
    return metrics
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--only", type=str, default=None)
    ap.add_argument("--model", type=str, default=scoring.DEFAULT_MODEL,
                    help="两条链路统一用的模型；省钱可用 claude-haiku-4-5-20251001")
    ap.add_argument("--resume", type=str, default=None,
                    help="复用已有结果目录做断点续跑：跳过已成功的简历，失败的会重试")
    args = ap.parse_args()
    only = args.only.split(",") if args.only else None
    model = args.model
    if model not in PRICING:
        print(f"警告: {model} 无定价表, 费用按 sonnet-5 估算", flush=True)

    if args.resume:
        outdir = args.resume
        print(f"断点续跑: {outdir}", flush=True)
    else:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        outdir = os.path.join(EVAL, "results", ts)
    os.makedirs(outdir, exist_ok=True)

    texts, human = load_inputs(only=only, limit=args.limit)
    print(f"简历 {len(texts)} 份, field={FIELD_ID}, model={model}", flush=True)

    graph = graph_mod.build_graph(api_key="eval-dummy", model=model)
    field, _, _ = scoring.resolve_field(scoring.load_framework(), FIELD_ID)

    # 已有成功结果直接复用
    old_res, new_res = [], []
    old_ok, new_ok = set(), set()
    for fname, res_list, ok_ids in (("old.json", old_res, old_ok),
                                   ("new.json", new_res, new_ok)):
        p = os.path.join(outdir, fname)
        if os.path.exists(p):
            with open(p, encoding="utf-8") as fh:
                for r in json.load(fh):
                    if "error" not in r:
                        res_list.append(r)
                        ok_ids.add(r["resume_id"])
    if old_ok or new_ok:
        print(f"已跳过: 旧链路 {len(old_ok)} 份, 新链路 {len(new_ok)} 份", flush=True)

    for idx, rid in enumerate(sorted(texts), 1):
        if rid not in old_ok:
            print(f"[{idx}/{len(texts)}] {rid} 旧链路...", flush=True)
            ro = _run_with_retry(lambda: run_old(rid, texts[rid], model),
                                 rid, "旧链路")
            old_res.append(ro)
            with open(os.path.join(outdir, "old.json"), "w") as fh:
                json.dump(old_res, fh, ensure_ascii=False, indent=1)
        else:
            print(f"[{idx}/{len(texts)}] {rid} 旧链路已跑过，跳过", flush=True)

        if rid not in new_ok:
            print(f"[{idx}/{len(texts)}] {rid} 新链路...", flush=True)
            rn = _run_with_retry(lambda: run_new(rid, texts[rid], graph, field, model),
                                 rid, "新链路")
            new_res.append(rn)
            with open(os.path.join(outdir, "new.json"), "w") as fh:
                json.dump(new_res, fh, ensure_ascii=False, indent=1)
        else:
            print(f"[{idx}/{len(texts)}] {rid} 新链路已跑过，跳过", flush=True)

    ok_old = [r for r in old_res if "error" not in r]
    ok_new = [r for r in new_res if "error" not in r]
    metrics = compute_metrics(human, ok_old, ok_new, model)
    metrics["errors"] = {
        "old": [r["resume_id"] for r in old_res if "error" in r],
        "new": [r["resume_id"] for r in new_res if "error" in r],
    }
    with open(os.path.join(outdir, "metrics.json"), "w") as fh:
        json.dump(metrics, fh, ensure_ascii=False, indent=1)
    print(json.dumps(metrics, ensure_ascii=False, indent=1))
    print(f"结果已存: {outdir}")


if __name__ == "__main__":
    main()
