"""Import a locally reviewed profile patch and Wiki pages through loopback APIs."""

import argparse
import json
from pathlib import Path
import re
import sys
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, ProxyHandler

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from policy_impact.company_wiki.loader import parse_frontmatter


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-dir", type=Path, required=True)
    parser.add_argument("--company-id", required=True)
    parser.add_argument("--base-url", default="http://127.0.0.1:8503")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    parsed = urlsplit(args.base_url)
    if (parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
            or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment):
        raise ValueError("Personal profile import only permits a loopback HTTP origin")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", args.company_id):
        raise ValueError("Invalid workspace ID")
    opener = build_opener(ProxyHandler({}))

    def call(path, body=None):
        request = Request(args.base_url + path,
                          data=None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8"),
                          headers={"Content-Type": "application/json"})
        with opener.open(request, timeout=30) as response:
            return json.load(response)

    root = args.input_dir.resolve()

    def read(name):
        target = (root / name).resolve()
        if not target.is_relative_to(root):
            raise ValueError("Bundle path leaves input directory")
        return target.read_text(encoding="utf-8")

    patch = json.loads(read("profile_patch.json"))
    entries = json.loads(read("manifest.json"))["pages"]
    base = f"/companies/{args.company_id}/workbench"
    before = call(base + "/finance/profile")["profile"]
    if before is None:
        raise ValueError("Create the target financial profile first")
    existing = {p["path"]: p for p in call(base + "/wiki")["pages"]}
    pending = []
    for entry in entries:
        _, body = parse_frontmatter(read(entry["file"]))
        old = existing.get(entry["path"])
        if old and old["body"].strip() != body.strip():
            raise ValueError("Existing Wiki differs; refusing to overwrite: " + entry["path"])
        if not old:
            pending.append({"path": entry["path"], "body": body, "create": True})
    changed = any(before.get(key) != value for key, value in patch.items())
    print(json.dumps({"mode": "apply" if args.apply else "preview", "pages_to_create": len(pending),
                      "profile_patch_needed": changed, "expected_version": before["version"]}))
    if not args.apply:
        return
    for page in pending:
        call(base + "/wiki/pages", page)
    if changed:
        call(base + "/finance/profile", {"profile": patch, "expected_version": before["version"]})
    current = call(base + "/finance/profile")["profile"]
    assert all(current.get(key) == value for key, value in patch.items()), "Profile read-back mismatch"
    pages = {p["path"]: p for p in call(base + "/wiki")["pages"]}
    for entry in entries:
        _, body = parse_frontmatter(read(entry["file"]))
        assert pages[entry["path"]]["body"].strip() == body.strip(), "Wiki read-back mismatch"
    print(json.dumps({"verified": True, "version": current["version"],
                      "pages": [{"path": entry["path"], "facts": pages[entry["path"]]["fact_count"]}
                                for entry in entries]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
