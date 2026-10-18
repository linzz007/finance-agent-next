"""Offline configuration checks; never invokes a model or changes the source project."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from policy_impact.company_wiki.loader import company_dir
from policy_impact.harness.artifacts import project_root
from policy_impact.runtime.plugin_config import (
    read_plugin_prompt_modules,
    read_plugin_skill_modules,
)
from policy_impact.skills.registry import SkillRegistry
from run_finance import configure_environment


def hashes(root: Path, *, markdown_only: bool = False) -> dict[str, str]:
    paths = sorted(root.rglob("*.md" if markdown_only else "*"))
    result = {}
    for path in paths:
        if path.is_symlink():
            raise ValueError(f"Linked file is not an isolated copy: {path}")
        if path.is_file():
            result[path.relative_to(root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, help="Optional original project, read-only copy check")
    parser.add_argument("--corpus-source", type=Path, help="Optional original article root, read-only")
    parser.add_argument("--output", type=Path, help="Save a private machine-readable report under data/")
    args = parser.parse_args()
    checks = []

    def check(name: str, passed: bool) -> None:
        checks.append({"check": name, "passed": bool(passed)})

    corpus = configure_environment()
    check("imports_resolve_to_new_project", project_root() == ROOT)
    check("corpus_is_inside_new_project", corpus.is_relative_to(ROOT))
    workspace = company_dir("finance_observer")
    check("workspace_is_inside_new_project", workspace.is_relative_to(ROOT))
    check("only_finance_workspace_copied", {
        p.name for p in workspace.parent.iterdir() if p.is_dir()
    } == {"finance_observer"})
    check("profile_and_wiki_present", (workspace / "company.yaml").is_file()
          and any((workspace / "wiki").rglob("*.md")))
    check("holdings_and_policy_present", all(
        (corpus / filename).is_file() for filename in ("current_holdings.md", "research_policy.md")
    ))
    author_counts = {
        p.name: len(list((p / "articles").glob("*.md")))
        for p in corpus.iterdir() if p.is_dir() and (p / "articles").is_dir()
    }
    check("article_corpus_present", sum(author_counts.values()) > 0)
    manifest = SkillRegistry().get_manifest("general_chat")
    check("system_prompt_resolves", bool(read_plugin_prompt_modules(manifest.prompt_modules)))
    check("skill_modules_resolve", bool(read_plugin_skill_modules(manifest.skill_modules)))
    check("financial_tools_bound", {"article.list", "article.read", "research.plan"}
          .issubset(manifest.allowed_tools))
    source_head = None
    if args.source:
        source = args.source.resolve()
        if source == ROOT or source in ROOT.parents or ROOT in source.parents:
            raise ValueError("Source must be a separate project, not the destination or an ancestor")
        check("wiki_copy_matches_source", hashes(source / "data/companies/finance_observer/wiki")
              == hashes(workspace / "wiki"))
        check("profile_copy_matches_source", (source / "data/companies/finance_observer/company.yaml")
              .read_bytes() == (workspace / "company.yaml").read_bytes())
        # These directories were intentionally not edited by the product separation.
        for directory in ("plugins", "prompts", "src/policy_impact/domains/finance",
                          "src/policy_impact/harness", "src/policy_impact/runtime"):
            def code_hashes(base: Path) -> dict[str, str]:
                return {key: value for key, value in hashes(base).items()
                        if "__pycache__" not in Path(key).parts and not key.endswith(".pyc")}

            check(f"copied_without_changes:{directory}",
                  code_hashes(source / directory) == code_hashes(ROOT / directory))
        source_head = subprocess.check_output(
            ["git", "-C", str(source), "rev-parse", "HEAD"], text=True
        ).strip()
    if args.corpus_source:
        check("article_copy_matches_source", hashes(args.corpus_source.resolve()) == hashes(corpus))
    passed = all(item["passed"] for item in checks)
    report = {
        "schema_version": "finance_next.baseline.v1",
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "passed": passed, "checks": checks, "author_article_counts": author_counts,
        "source_head": source_head, "model_calls": 0,
        "scope": "Offline separation/configuration checks, not financial analysis acceptance",
    }
    if args.output:
        output = args.output if args.output.is_absolute() else ROOT / args.output
        output = output.resolve()
        if not output.is_relative_to(ROOT / "data"):
            raise ValueError("Baseline reports must stay in this project's private data directory")
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=True, indent=2))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
