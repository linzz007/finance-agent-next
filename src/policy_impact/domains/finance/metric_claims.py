"""Narrow deterministic guards for explicit metric claims, not arbitrary NLP."""

import re


ALIASES = {"ixic": ("纳斯达克", "纳指"), "xauusd": ("黄金", "金银"),
           "xagusd": ("白银", "金银")}
DATE = r"(?:(20\d{2})[-/])?(\d{1,2})[-/月](\d{1,2})日?"


def research_claim_issues(answer: str, reports: list[dict]) -> list[str]:
    issues = []
    def asserted(pattern):
        return any(not re.search(r"(?:不能|不可|不应|并非|不是).{0,12}$", answer[max(0, m.start()-18):m.start()])
                   for m in re.finditer(pattern, answer))
    if asserted(r"文章(?:只|仅)有发布日期"):
        issues.append("只能说结构化字段目前只保留日期，原文可能有时分但系统尚未抽取核验；不能断言文章本身只有日期。")
    for sentence in re.split(r"[。；;\n]", answer):
        inference = re.search(r"(?:反向波动|跌幅差|跌幅.{0,10}小于).{0,60}(?:表明|说明|提示).{0,25}(?:单因素.{0,10}不足|驱动.{0,8}不同|驱动不完全相同)", sentence)
        if inference and not re.search("不能|不足以|无法", sentence[:inference.end()]):
            issues.append("反向波动或资产跌幅差异不能推出单因素解释不足或驱动不同；只能说需要检验、现有证据无法识别或排除共同驱动。")
    discounting = any(a.get("symbol") == "ixic" and a.get("mechanism") == "rates_discounting"
                      for report in reports for a in (report.get("analysis") or {}).get("asset_assessments", []))
    if discounting and asserted(r"加息(?:导致|推动|带动)(?:了)?(?:纳指|纳斯达克)(?:上行|上涨)"):
        issues.append("保存的rates_discounting假设是加息可能压制估值，而纳指上涨与该负向通道不一致；不得改述为保存分析认为加息导致纳指上行，即便加上待验证也改变了机制方向。")
    return issues


def metric_claim_issues(answer: str, reports: list[dict]) -> list[str]:
    issues = []
    # Ignore code/formatting markers, but do not erase the text of a claim.
    lines = re.split(r"[。；;\n]", answer.replace("**", "").replace("`", ""))
    for report in reports:
        metrics = report.get("metrics", [])
        drawdowns = [m for m in metrics if m.get("max_drawdown_pct") is not None]
        max_dd = max((m["max_drawdown_pct"] for m in drawdowns), default=None)
        winners = "、".join(m["name"] for m in drawdowns if m["max_drawdown_pct"] == max_dd)
        table_basis = False
        for line in lines:
            if "|" in line and re.search("基准日|计算起|收益起", line):
                table_basis = True
            elif "|" not in line and line.strip():
                table_basis = False
            for metric in metrics:
                aliases = tuple(dict.fromkeys((metric["name"], metric["symbol"], *ALIASES.get(metric["symbol"], ()))))
                match = re.search("|".join(map(re.escape, aliases)), line, re.I)
                if not match:
                    continue
                # Only bind a claim to the asset immediately before it. Later
                # assets in a comparison are independently evaluated.
                segment = line[match.end():]
                other_names = [alias for other in metrics if other is not metric
                               for alias in (other["name"], *ALIASES.get(other["symbol"], ()))
                               if alias not in aliases]
                if other_names:
                    segment = re.split("|".join(map(re.escape, other_names)), segment, maxsplit=1)[0]
                denied = bool(re.search("并非|不是|不能称|不可称|不应称|并不|并未|没有|错误|误述|削弱|不支持|否定|非单边", segment))
                if not denied and re.search("单边|一路", segment):
                    if metric.get("up_days", 0) > 0 and metric.get("down_days", 0) > 0:
                        issues.append(f"{metric['name']}窗口内同时有上涨和下跌，不能笼统称单边或一路涨跌；应描述为整体/震荡走势，子区间路径需另外核对逐日数据。")
                if (not denied and re.search(r"(?:最大)?回撤(?:幅度)?(?:最深|最大|居首|最多)", segment)
                        and metric.get("max_drawdown_pct") is not None and metric["max_drawdown_pct"] < max_dd):
                    issues.append(f"窗口内日末价最大回撤排名错误：{winners}为{max_dd:.6f}%，高于{metric['name']}的{metric['max_drawdown_pct']:.6f}%。")
                basis = re.search(r"(?:基准(?:日|日期|价)?|计算起点|收益起点)(?:[^\d。；|]{0,14})" + DATE, segment)
                if not basis and table_basis and "|" in line:
                    basis = re.search(DATE, segment)
                if basis and not denied and metric.get("start_date"):
                    year, month, day = basis.groups()
                    expected = tuple(map(int, metric["start_date"].split("-")))
                    actual = (int(year) if year else expected[0], int(month), int(day))
                    if actual != expected:
                        issues.append(f"{metric['name']}收益基准日期错误：计算实际使用{metric['start_date']}，不是请求起日或首个窗口内观测日；期末为{metric.get('end_date')}。")
    return list(dict.fromkeys(issues))
