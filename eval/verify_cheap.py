"""真实验证：新链路（Agent，便宜模式 max_revisions=1）在 Haiku 上的
实际 token / 费用 / critic 行为。只跑新链路，不跑旧链路。

复用 eval/run_eval.py 的 surrogate patch（已修复重复发送 bug）、
run_new、load_inputs、PRICING。
"""
import json
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__)))
import run_eval as E

MODEL = "claude-haiku-4-5-20251001"
RESUMES = ["R01", "R02", "R03"]

PRICING = E.PRICING[MODEL]  # (input, output, cache_read, cache_write)


def cost_of(usage):
    inp, out, cr, cw = PRICING
    regular = usage["input"] - usage["cache_creation"] - usage["cache_read"]
    dollars = (regular * inp + usage["cache_creation"] * cw
               + usage["cache_read"] * cr + usage["output"] * out) / 1e6
    return round(dollars, 4)


def main():
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    outdir = os.path.join(E.EVAL, "results", ts + "_verify")
    os.makedirs(outdir, exist_ok=True)

    texts, human = E.load_inputs(only=RESUMES)
    field, _, _ = E.scoring.resolve_field(E.scoring.load_framework(), E.FIELD_ID)
    graph = E.graph_mod.build_graph(api_key="eval-dummy", model=MODEL,
                                    max_revisions=1)
    print(f"resumes={RESUMES} model={MODEL} max_revisions=1", flush=True)

    results = []
    for idx, rid in enumerate(sorted(texts), 1):
        print(f"[{idx}/{len(texts)}] {rid} 新链路...", flush=True)
        r = E._run_with_retry(lambda: E.run_new(rid, texts[rid], graph, field, MODEL),
                              rid, "新链路")
        if "error" in r:
            print(f"  FAILED: {r['error']}", flush=True)
            results.append(r)
            with open(os.path.join(outdir, "verify.json"), "w") as fh:
                json.dump(results, fh, ensure_ascii=False, indent=1)
            continue
        r["cost_usd"] = cost_of(r["measured_usage"])
        results.append(r)
        with open(os.path.join(outdir, "verify.json"), "w") as fh:
            json.dump(results, fh, ensure_ascii=False, indent=1)
        u = r["measured_usage"]
        print(f"  calls={r['api_calls']} in={u['input']} out={u['output']} "
              f"cache_write={u['cache_creation']} cache_read={u['cache_read']} "
              f"cost=${r['cost_usd']} critic_pass={r['critic_pass']} "
              f"corrections={len(r.get('critic_corrections') or [])} "
              f"sec={r['seconds']}", flush=True)

    print("RESULTS_DIR=" + outdir, flush=True)


if __name__ == "__main__":
    main()
