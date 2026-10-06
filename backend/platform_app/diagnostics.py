"""Evidence-grounded diagnosis shared by every registered product.

The model explains a captured failure; it cannot change the test verdict or
claim that a commit introduced a bug without a reproducible version bisect.
"""

import hashlib
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from .config import Settings
from .filestore import FileStore
from .product_catalog import discover_products


class AIUnavailable(RuntimeError):
    pass


class Finding(BaseModel):
    text: str
    evidence_ids: list[str]


class CandidateCommit(BaseModel):
    revision: str
    reason: str
    evidence_ids: list[str]


class Diagnosis(BaseModel):
    summary: str
    failure_category: Literal["product", "environment", "framework", "unknown"]
    facts: list[Finding]
    code_logic: list[Finding]
    likely_causes: list[Finding]
    candidate_commits: list[CandidateCommit]
    solutions: list[Finding]
    unknowns: list[str]


_SECRET = re.compile(r"(?i)((?:password|passwd|api[_-]?key|secret|token)\s*[:=]\s*)\S+")
_JSON_SECRET = re.compile(r'(?i)("(?:password|passwd|api[_-]?key|secret|token)"\s*:\s*)"[^"]*"')
_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z_0-9]{4,}")
_SKIP = {"error", "failed", "actual", "expected", "status", "false", "true", "postgres"}


def _redact(value: str) -> str:
    return _SECRET.sub(r"\1[REDACTED]", _JSON_SECRET.sub(r'\1"[REDACTED]"', value))


def _source_root(settings: Settings, product_id: str) -> Path | None:
    product = discover_products(settings.products_root).get(product_id)
    return product.source_root if product else None


def _git(root: Path, *args: str) -> str | None:
    try:
        process = subprocess.run(
            ["git", "-C", str(root), *args], capture_output=True, text=True,
            timeout=6, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return process.stdout.strip() if process.returncode == 0 else None


def _file_evidence(path: Path, evidence_id: str, max_lines: int = 100) -> dict | None:
    if not path.is_file():
        return None
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            lines = [line.rstrip("\n") for _, line in zip(range(max_lines), handle)]
    except OSError:
        return None
    return {
        "id": evidence_id, "kind": "artifact", "path": str(path),
        "line_start": 1, "line_end": len(lines),
        "content": _redact("\n".join(lines))[:12000],
    }


def _code_matches(root: Path, terms: list[str]) -> list[dict]:
    evidence = []
    seen = set()
    for term in terms[:4]:
        try:
            process = subprocess.run(
                ["rg", "-n", "-F", "--max-count", "1", "--max-columns", "240",
                 "--max-filesize", "512K", "--glob", "*.c",
                 "--glob", "*.h", "--glob", "*.py", term, str(root)],
                capture_output=True, text=True, timeout=8, check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            break
        for line in process.stdout.splitlines():
            if len(evidence) >= 12:
                break
            match = re.match(r"^(.*?):(\d+):(.*)$", line)
            if not match:
                continue
            path, number, _source = match.groups()
            identity = (path, number)
            if identity in seen:
                continue
            seen.add(identity)
            source_path = Path(path).resolve()
            if not source_path.is_relative_to(root):
                continue
            match_line = int(number)
            try:
                lines = source_path.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                continue
            first = max(1, match_line - 8)
            last = min(len(lines), match_line + 12)
            evidence.append({
                "id": f"code:{len(evidence) + 1}", "kind": "source",
                "path": str(source_path.relative_to(root)), "line_start": first,
                "line_end": last, "matched_line": match_line,
                "content": _redact("\n".join(
                    f"{index}: {lines[index - 1]}" for index in range(first, last + 1)
                )),
            })
    return evidence


def build_evidence_bundle(settings: Settings, result: dict) -> dict:
    """Bound evidence size and keep source checkout separate from binary identity."""
    evidence = [{
        "id": "result", "kind": "result", "path": None,
        "content": json.dumps({
            "target": result["target"], "status": result["status"],
            "reason": result["reason"], "updated_at": result["updated_at"],
        }, ensure_ascii=False),
    }]
    artifact = Path(result["artifact_dir"]).resolve() if result.get("artifact_dir") else None
    if artifact and artifact.is_file():
        item = _file_evidence(artifact, "operation_log", 160)
        if item:
            evidence.append(item)
    elif artifact and artifact.is_dir():
        for filename in ("summary.json", "steps.json", "report.txt"):
            item = _file_evidence(artifact / filename, filename, 120)
            if item:
                evidence.append(item)
        for index, path in enumerate(sorted(artifact.rglob("*.log"))[:3]):
            item = _file_evidence(path, f"log:{index + 1}", 100)
            if item:
                evidence.append(item)

    root = _source_root(settings, result["product_id"])
    checkout_revision = _git(root, "rev-parse", "HEAD") if root and root.is_dir() else None
    search_text = str(result.get("reason") or "") + " " + " ".join(
        item["content"][:1500] for item in evidence[1:]
    )
    terms = [word for word in dict.fromkeys(_IDENTIFIER.findall(search_text))
             if word.lower() not in _SKIP and len(word) <= 64]
    code = _code_matches(root, terms) if root and root.is_dir() else []
    candidate_revisions = set()
    if root and checkout_revision:
        for item in code[:6]:
            blamed = _git(root, "blame", "-L", f"{item['matched_line']},{item['matched_line']}",
                           "--porcelain", "--", item["path"])
            if blamed:
                revision = blamed.splitlines()[0].split()[0]
                if re.fullmatch(r"[0-9a-f]{40}", revision) and revision != '0'*40:
                    item["last_modified_commit"] = revision
                    candidate_revisions.add(revision)
    evidence.extend(code)
    return {
        "product_id": result["product_id"], "environment_id": result["environment_id"],
        "target": result["target"], "profile": result["profile"],
        "source_checkout": str(root) if root and root.is_dir() else None,
        "source_checkout_revision": checkout_revision,
        "running_binary_revision": None,
        "commit_attribution": "unverified",
        "candidate_revisions": sorted(candidate_revisions),
        "evidence": evidence,
    }


def diagnose(store: FileStore, settings: Settings, result: dict) -> dict:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise AIUnavailable("未配置 OPENAI_API_KEY，无法调用 AI 诊断")
    model = os.environ.get("PRODUCT_PLATFORM_AI_MODEL", "gpt-6-astra")
    bundle = build_evidence_bundle(settings, result)
    digest = hashlib.sha256(json.dumps(bundle, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    from openai import OpenAI

    client = OpenAI(api_key=api_key, timeout=90.0, max_retries=1)
    response = client.responses.parse(
        model=model, store=False, text_format=Diagnosis,
        input=[
            {"role": "system", "content": (
                "你是产品回归故障诊断助手。只依据给定证据陈述事实；区分产品缺陷、环境问题和框架问题。"
                "解释相关代码路径，提出可验证的修复方案。每项事实、代码逻辑、原因及方案填写证据 ID。"
                "git blame 的提交只能列为候选；没有可重复的 good/bad 版本二分就不得称为引入提交。"
                "运行二进制提交未知时必须指出源码检出版本未必等于运行版本。"
            )},
            {"role": "user", "content": json.dumps(bundle, ensure_ascii=False)[:50000]},
        ],
    )
    diagnosis = response.output_parsed
    if diagnosis is None:
        raise RuntimeError("模型未返回可解析的诊断")
    allowed = {item["id"] for item in bundle["evidence"]}
    findings = diagnosis.facts + diagnosis.code_logic + diagnosis.likely_causes + diagnosis.solutions
    for finding in [*findings, *diagnosis.candidate_commits]:
        if not finding.evidence_ids or set(finding.evidence_ids) - allowed:
            raise ValueError("AI 诊断引用了不存在的证据")
    if any(item.revision not in bundle["candidate_revisions"] for item in diagnosis.candidate_commits):
        raise ValueError("AI 诊断引用了未经检索的候选提交")
    content = {"analysis": diagnosis.model_dump(), "evidence": bundle["evidence"],
               "source_checkout_revision": bundle["source_checkout_revision"],
               "running_binary_revision": None, "commit_attribution": "unverified"}
    store.diagnoses.put_diagnosis(result, digest, model, content)
    return content
