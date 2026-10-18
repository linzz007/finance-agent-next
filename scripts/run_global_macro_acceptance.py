"""Real-model, public-API acceptance. Saves private traces locally, never credentials."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import time
from urllib.error import HTTPError
from urllib.parse import urlparse
from urllib.request import ProxyHandler, Request, build_opener


ROOT = Path(__file__).resolve().parents[1]
CASES = [
    ("M01", "普通行情与追问", "分析一下今天美国三大指数的行情，有什么共同变化和分歧？结合近一周的宏观资料解释可能原因，不生成报告。"),
    ("M02", "普通行情与追问", "沿用刚才的数据，标普500的涨跌是怎么算的？请给出两个日期、价格和公式，不要重新联网。"),
    ("M03", "普通行情与追问", "刚才的原因解释最可能被什么事实推翻？请给一个替代解释和能区分它们的观察条件。"),
    ("M04", "全球宏观报告与追问", "/report 请生成今天的全球宏观市场复盘，按我的Wiki关注黄金、白银和美国三大指数。金银用国际现货美元每金衡盎司，区分各市场最近完整日线和近一周的宏观资料，用一至三条主线说明事实、可能解释和反证。"),
    ("M05", "全球宏观报告与追问", "刚才的黄金和白银是什么品种？请列出来源链接、计价单位、原始标识和日线截点，确认不是期货或ETF。不要重新获取。"),
    ("M06", "全球宏观报告与追问", "报告里哪些是实际观察到的，哪些只是可能解释？给我最重要的两条，并附来源。"),
    ("M07", "单资产分析转报告", "今天黄金的行情怎么样？我想理解变化和相关宏观背景，不生成报告，也不要推断我的持仓收益。"),
    ("M08", "单资产分析转报告", "把刚才通过校验的分析生成一份HTML报告，沿用那份快照，不要用期货或ETF替代现货。"),
    ("M09", "周度自然语言报告", "请生成最近一周黄金、白银和美股三大指数的宏观观察报告，用证据解释可能的共同主线和反证，不给买卖建议。"),
    ("M10", "刷新与范围边界", "刷新今天美国三大指数的数据，在聊天里讲变化，不生成报告。"),
    ("M11", "刷新与范围边界", "/report 生成明天美国三大指数完整收盘分析，别拿历史数据替代。"),
    ("M12", "刷新与范围边界", "/report 只看今天比特币完整收盘表现，不用其他资产替代。"),
    ("M13", "知识与档案边界", "国际黄金现货和黄金期货有什么区别？只讲概念，不查今天价格，不生成报告。"),
    ("M14", "知识与档案边界", "Wiki里有我的历史研究偏好，这是否意味着你知道我现在的持仓、仓位和盈亏？请只说明你知道的边界。"),
    ("M15", "月度走势修复", "给我讲一下最近一个月的纳斯达克走势和金银走势，帮我分析一下大致的导致的原因呢？"),
    ("M16", "月度走势修复", "沿用刚才的数据，不重新获取。请列出这三个资产的计算起止日期、基准价格、期末价格和涨跌幅，并说明金银的采样口径与来源。"),
    ("M17", "月度走势修复", "沿用刚才的数据，不重新获取。哪些原因还只是猜测？请分别说说纳指、黄金、白银的证据缺口，以及报告期内有哪些反向表现需要注意；晚于行情截止的文章能解释此前涨跌吗？"),
    ("M18", "月度走势修复", "/report 将刚才的分析生成HTML报告，沿用那份快照，不重新获取。保留逐资产核查、来源链接和未证实的原因说明。"),
]


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8504")
    parser.add_argument("--company-id", default="finance_observer")
    parser.add_argument("--label", default="global-macro-acceptance")
    parser.add_argument("--cases", default="M01,M02,M03")
    parser.add_argument("--configure-profile", action="store_true")
    parser.add_argument("--prepare-only", action="store_true")
    args = parser.parse_args()
    if urlparse(args.base_url).hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("acceptance targets must be local")
    output = ROOT / "data" / "acceptance" / f"{Path(args.label).name}.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    base = f"/companies/{args.company_id}/workbench"
    opener = build_opener(ProxyHandler({}))

    def call(path, body=None):
        request = Request(args.base_url + path, data=json.dumps(body, ensure_ascii=False).encode() if body is not None else None,
                          headers={"Content-Type": "application/json"})
        try:
            with opener.open(request, timeout=480) as response:
                return json.load(response)
        except HTTPError as exc:
            raise RuntimeError(f"HTTP {exc.code}: {exc.read().decode('utf-8')[:1200]}") from exc

    results = json.loads(output.read_text(encoding="utf-8")) if output.exists() else {"cases": [], "sessions": {}}
    results.update(base_url=args.base_url, company_id=args.company_id, updated_at=datetime.now(timezone.utc).isoformat())

    def save():
        output.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")

    if args.configure_profile:
        profile = call(base + "/finance/profile")["profile"]
        if not profile:
            raise ValueError("Create a financial Wiki through the UI/API before running this acceptance")
        patch = {"symbols": ["xauusd", "xagusd", "spx", "ixic", "dji"], "quality_policy": "strict",
                 "default_session_mode": "latest_completed", "document_days": 7,
                 "display_name": "全球宏观与个人关注资产观察",
                 "purpose": "理解黄金、白银与美股的宏观变化及可能原因，以证据和反证区分事实与解释；不推断当前持仓盈亏。"}
        old_boundary = "当前行情接入仍限于配置中的境内指数；黄金、美股、白银、美元和美债序列尚未接入，不得拿A股代替或声称已覆盖。"
        new_boundary = "2026-09-23已验证黄金/白银现货参考价及标普500、纳斯达克综合、道琼斯指数日线；金银按UTC完整采样参考日而非官方收盘，美股为第三方供应商指数收盘日线，按常规交易日历核对，不是交易所直供或价格认证。美元指数、美债收益率、银行积存金成交报价尚未接入；每次查询仍须校验数据时效与完整性。"
        patch["constraints"] = [new_boundary if value == old_boundary else value for value in profile.get("constraints", [])]
        if any(profile.get(k) != v for k, v in patch.items()):
            profile = call(base + "/finance/profile", {"profile": {**profile, **patch}, "expected_version": profile["version"]})["profile"]
        results["profile_version"] = profile["version"]
        old_note = "迁移时的接入边界：现系统有A股主要指数日线、统计局、Fed/BIS公开文章；尚无黄金白银、美股指数、美元、美债的完整行情序列。Wiki偏好已更新不等于这些数据已接通。旧项目有相关采集代码，但需重新实测并区分期货、现货和时区后才能迁移。"
        wiki = call(base + "/wiki")
        for page in wiki.get("pages", []):
            if page["path"].endswith("研究偏好与报告目标.md") and old_note in page["body"]:
                call(base + "/wiki/pages", {"path": page["path"], "body": page["body"].replace(old_note, new_boundary), "create": False})
                results["wiki_capability_note_updated"] = True
        save()
        print(json.dumps({"profile_version": profile["version"], "symbols": profile["symbols"], "quality_policy": profile["quality_policy"]}), flush=True)
    if args.prepare_only:
        return
    selected = set(args.cases.split(",")) if args.cases != "all" else {case[0] for case in CASES}
    for case_id, group, question in CASES:
        if case_id not in selected:
            continue
        sid = results["sessions"].get(group)
        if not sid:
            response = call(base + "/sessions", {"title": "宏观验收 · " + group,
                            "model_id": "deepseek-v4-flash", "mode": "auto"})
            sid = response.get("session_id") or response.get("session", {}).get("session_id")
            if not sid:
                raise ValueError("session API did not return an ID")
            results["sessions"][group] = sid
        case = {"id": case_id, "query": question, "session_id": sid, "started_at": datetime.now(timezone.utc).isoformat()}
        results["cases"].append(case)
        save()
        started = time.monotonic()
        try:
            response = call(f"{base}/sessions/{sid}/messages", {"message": question})
            case["response"] = response
            case["trace"] = call(f"{base}/sessions/{sid}/runs/{response['run_id']}/trace")
        except Exception as exc:
            case["error"] = str(exc)
        case["elapsed_seconds"] = round(time.monotonic() - started, 2)
        save()
        response = case.get("response", {})
        print(json.dumps({"id": case_id, "session_id": sid, "run_id": response.get("run_id"),
              "elapsed_seconds": case["elapsed_seconds"], "error": case.get("error"),
              "answer": response.get("answer", ""), "artifacts": response.get("artifacts", {})}, ensure_ascii=False), flush=True)
    print(str(output), flush=True)


if __name__ == "__main__":
    main()
