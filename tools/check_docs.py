"""Validate document links and S/P -> W/T coverage; no external services."""
from pathlib import Path
import re
import sys
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[1]
errors = []


def read(relative):
    return (ROOT / relative).read_text("utf-8-sig")


def rows(text, pattern):
    result = {}
    for line in text.splitlines():
        if re.match(pattern, line):
            cells = [cell.strip() for cell in line.strip("|").split("|")]
            key = cells[0]
            if key in result:
                errors.append(f"duplicate ID: {key}")
            result[key] = cells
    return result


product = read("docs/product/product-spec.md")
scopes = rows(product, r"^\| S\d{2} \|")
scenarios = rows(product, r"^\| P\d{2} \|")
work = rows(read("docs/blueprint/work-items.md"), r"^\| W\d{3} \|")
tests = rows(read("docs/blueprint/acceptance.md"), r"^\| T\d{2} \|")
decisions = rows(read("docs/architecture/decisions.md"), r"^\| A\d{2} \|")
trace = read("docs/blueprint/traceability.md")
scope_map = rows(trace, r"^\| S\d{2} \|")
scenario_map = rows(trace, r"^\| P\d{2} \|")
if set(scopes) != set(scope_map):
    errors.append("scope coverage mismatch")
if set(scenarios) != set(scenario_map):
    errors.append("product scenario coverage mismatch")

for sid, row in scope_map.items():
    wids = re.findall(r"\bW\d{3}\b", row[1])
    tids = re.findall(r"\bT\d{2}\b", row[2])
    if scopes.get(sid, ["", "", ""])[2] != "مرحلهٔ بعد" and (not wids or not tids):
        errors.append(f"scope without owner/test: {sid}")
    errors.extend(f"{sid}: unknown work {w}" for w in wids if w not in work)
    errors.extend(f"{sid}: unknown test {t}" for t in tids if t not in tests)
for pid, row in scenario_map.items():
    tids = re.findall(r"\bT\d{2}\b", row[1])
    if not tids:
        errors.append(f"scenario without test: {pid}")
    errors.extend(f"{pid}: unknown test {t}" for t in tids if t not in tests)

# All concrete references must resolve, even when appearing outside the matrix.
for path in (ROOT / "docs").rglob("*.md"):
    if "archive" in path.relative_to(ROOT).parts:
        continue
    text = path.read_text("utf-8-sig")
    for token in re.findall(r"\b(?:W\d{3}|T\d{2}|A\d{2}|S\d{2}|P\d{2})\b", text):
        registry = {"W": work, "T": tests, "A": decisions,
                    "S": scopes, "P": scenarios}[token[0]]
        if token not in registry:
            errors.append(f"{path.relative_to(ROOT)}: unknown {token}")

docs = [ROOT / "README.md", *(ROOT / "docs").rglob("*.md")]
for path in docs:
    text = path.read_text("utf-8-sig")
    if text.count("~~~") % 2:
        errors.append(f"unclosed fence: {path.relative_to(ROOT)}")
    for target in re.findall(r"\]\(([^)]+)\)", text):
        if re.match(r"^(?:https?://|mailto:|#)", target):
            continue
        destination = unquote(target.split("#", 1)[0])
        if not (path.parent / destination).is_file():
            errors.append(f"broken local link: {path.relative_to(ROOT)} -> {target}")

# Explicit W dependencies must not contain a directed cycle.
graph = {key: re.findall(r"\bW\d{3}\b", row[3]) for key, row in work.items()}
active, done = set(), set()


def visit(key):
    if key in active:
        errors.append(f"dependency cycle at {key}")
        return
    if key in done:
        return
    active.add(key)
    for dependency in graph.get(key, []):
        if dependency not in work:
            errors.append(f"unknown dependency: {key} -> {dependency}")
        else:
            visit(dependency)
    active.remove(key)
    done.add(key)


for key in work:
    visit(key)
if errors:
    print("\n".join(errors))
    sys.exit(1)
print(f"OK: {len(scopes)} scopes, {len(scenarios)} product scenarios, "
      f"{len(work)} work items, {len(tests)} acceptance scenarios, "
      f"{len(decisions)} technical decisions; links and dependency graph valid.")
