"""Exercise public APIs with a real configured model; persist every response/trace."""

import argparse
import json
from pathlib import Path
import sys
import time
from urllib.request import Request, urlopen
from urllib.error import HTTPError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from policy_impact.domains.finance.profile import default_profile


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8503")
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--single-turn", action="store_true")
    parser.add_argument("--label", default="financial-delivery")
    parser.add_argument("--request-timeout", type=int, default=420)
    parser.add_argument("--query", default="/report 请结合我的金融观察档案，生成最近30天的金融观察报告。包括上证指数、沪深300、创业板指的区间表现，以及本期官方宏观发布和机构公开观点；说明和我的学习关注有什么关系，区分事实与解释，附上来源和数据日期。没有的数据请明确标注。")
    args = parser.parse_args()
    cid = "finance_observer"
    base = f"/companies/{cid}/workbench"
    results = {"base_url": args.base_url, "company_id": cid, "label": args.label, "cases": []}
    output = ROOT / "data/acceptance" / (args.label + ".json")

    def save():
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")

    def call(path, body=None, *, timeout=None):
        request = Request(args.base_url + path, data=json.dumps(body, ensure_ascii=False).encode() if body is not None else None,
                          headers={"Content-Type": "application/json"})
        try:
            with urlopen(request, timeout=timeout or args.request_timeout) as response:
                return json.load(response)
        except HTTPError as exc:
            raise RuntimeError(f"HTTP {exc.code}: {exc.read().decode('utf-8')[:1800]}") from exc

    if cid not in call("/companies")["company_ids"]:
        call("/companies", {"company_id": cid, "name": "个人金融观察", "profile_type": "personal"})
    profile = call(base + "/finance/profile")["profile"]
    if profile is None:
        profile = call(base + "/finance/profile", {"profile": default_profile(), "expected_version": 0})["profile"]
    results["profile"] = profile
    results["wiki_search"] = call(base + "/wiki/search?query=%E9%87%91%E8%9E%8D&top_k=8")
    results["models"] = call(base + "/models")
    save()
    print(json.dumps({"profile_version": profile["version"], "status": profile["status"], "models": results["models"]}, ensure_ascii=False), flush=True)
    if args.prepare_only:
        return
    session = call(base + "/sessions", {"title": "金融观察 · 报告与追问", "model_id": "deepseek-v4-flash", "mode": "auto"})
    sid = session.get("session_id") or session.get("session", {}).get("session_id")
    if not sid:
        raise RuntimeError(f"session id missing: {session}")
    results["session_id"] = sid
    save()
    queries = [args.query,
               "刚才报告里沪深300的区间涨跌是怎么算的？请列出基准日、截止交易日、两个收盘价和公式，并说明这是不是实时行情。",
               "基于刚才报告，我接下来最值得关注哪两件事？请分别讲事实依据、可能的另一种解释和还缺什么证据，不要给我买卖建议。"]
    for query in queries[:1] if args.single_turn else queries:
        case = {"query": query}
        results["cases"].append(case)
        started = time.monotonic()
        try:
            response = call(f"{base}/sessions/{sid}/messages", {"message": query})
            case["response"] = response
            run_id = response.get("run_id") or response.get("metadata", {}).get("run_id")
            case["trace"] = call(f"{base}/sessions/{sid}/runs/{run_id}/trace") if run_id else call(f"{base}/sessions/{sid}/trace")
            case["elapsed_seconds"] = round(time.monotonic() - started, 2)
            print(json.dumps({"run_id": run_id, "seconds": case["elapsed_seconds"], "answer": response.get("answer", ""), "artifacts": response.get("artifacts", {})}, ensure_ascii=False), flush=True)
        except Exception as exc:
            case["error"] = str(exc)
            save()
            raise
        save()
    results["reports"] = call(base + "/finance/reports?limit=3")
    results["status"] = call(base + "/finance/status")
    save()
    print(str(output), flush=True)


if __name__ == "__main__":
    main()
