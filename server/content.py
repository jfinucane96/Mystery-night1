"""Loads versioned content (assessment + mystery packages), validates it, and
builds the privacy projections that are the *only* way content reaches an API
response. Nothing in here knows about HTTP or the database."""

import hashlib
import json
import os
import re
import threading

CONTENT_ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "content")

PHASES = ["DRAFT", "CASTING", "TRAINING", "ARRIVAL", "INVESTIGATION", "ACCUSATION", "REVEAL", "COMPLETE"]
DIMENSIONS = ["spotlight", "performance", "deception", "investigation", "mischief", "commitment"]
_SAFE_ID = re.compile(r"^[A-Za-z0-9_.-]+$")


class ContentError(Exception):
    pass


def phase_reached(current, gate):
    return PHASES.index(current) >= PHASES.index(gate)


# ---------------------------------------------------------------- assessment

_assessments = {}


def load_assessment(version="assessment-v1"):
    if version not in _assessments:
        path = os.path.join(CONTENT_ROOT, "assessment", "v1.json")
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if data["assessment_version"] != version:
            raise ContentError(f"assessment version {version} not found")
        _validate_assessment(data)
        _assessments[version] = data
    return _assessments[version]


def _validate_assessment(a):
    errors = []
    ids = [q["id"] for q in a["questions"]]
    if len(ids) != len(set(ids)):
        errors.append("duplicate question ids")
    for dim in DIMENSIONS:
        n = sum(1 for q in a["questions"] if q["dimension"] == dim)
        if n != 2:
            errors.append(f"dimension {dim} has {n} questions, expected 2")
    for q in a["questions"]:
        if sorted(o["id"] for o in q["options"]) != sorted(a["scoring_map"]):
            errors.append(f"{q['id']}: options must match scoring_map keys")
    if errors:
        raise ContentError("; ".join(errors))


# ---------------------------------------------------------------- packages

class Package:
    def __init__(self, root, manifest, files, digest):
        self.root = root
        self.manifest = manifest
        self.digest = digest
        chars = files["characters"]
        self.content_tags = {t["id"]: t for t in chars["content_tags"]}
        self.roles = {r["id"]: r for r in chars["roles"]}
        self.relationships = {r["id"]: r for r in chars["relationships"]}
        self.victim = chars.get("victim")
        self.knowledge = {k["id"]: k for k in files["knowledge"]["knowledge_items"]}
        self.disclosure_rules = {d["id"]: d for d in files["disclosure_rules"]["disclosure_rules"]}
        self.timeline = files["timeline"]["canonical_facts"]
        self.solution = files["solution"]
        self._raw = files

    @property
    def package_id(self):
        return self.manifest["package_id"]

    @property
    def version(self):
        return self.manifest["content_version"]

    @property
    def title(self):
        return self.manifest["metadata"]["title"]


def _package_dir(package_id, version):
    if not (_SAFE_ID.match(package_id) and _SAFE_ID.match(version)):
        raise ContentError("invalid package reference")
    return os.path.join(CONTENT_ROOT, "packages", package_id, version)


def _digest_dir(root):
    h = hashlib.sha256()
    for name in sorted(os.listdir(root)):
        if name.endswith(".json"):
            h.update(name.encode())
            with open(os.path.join(root, name), "rb") as f:
                h.update(hashlib.sha256(f.read()).digest())
    return h.hexdigest()


_cache = {}
_cache_lock = threading.Lock()


def load_package(package_id, version, expected_digest=None):
    """Load and validate a package. If expected_digest is given (the digest
    recorded when a game was created) the files on disk must still match."""
    root = _package_dir(package_id, version)
    digest = _digest_dir(root)
    if expected_digest and digest != expected_digest:
        raise ContentError(
            f"package {package_id}@{version} changed on disk since this game was created; "
            "publish edits as a new content_version")
    with _cache_lock:
        cached = _cache.get((package_id, version))
        if cached and cached.digest == digest:
            return cached
    with open(os.path.join(root, "package.json"), encoding="utf-8") as f:
        manifest = json.load(f)
    files = {}
    for key, rel in manifest["references"].items():
        if not _SAFE_ID.match(rel):
            raise ContentError(f"bad reference path {rel}")
        with open(os.path.join(root, rel), encoding="utf-8") as f:
            files[key] = json.load(f)
    errors = validate_package(manifest, files, package_id, version)
    if errors:
        raise ContentError(f"package {package_id}@{version} invalid:\n  - " + "\n  - ".join(errors))
    pkg = Package(root, manifest, files, digest)
    with _cache_lock:
        _cache[(package_id, version)] = pkg
    return pkg


def list_packages():
    out = []
    base = os.path.join(CONTENT_ROOT, "packages")
    for pid in sorted(os.listdir(base)):
        versions = os.listdir(os.path.join(base, pid))
        for ver in sorted(versions, key=lambda v: [int(x) if x.isdigit() else x for x in v.split(".")], reverse=True):
            pkg = load_package(pid, ver)
            cfg = pkg.manifest["configuration"]
            out.append({
                "package_id": pid, "content_version": ver, "title": pkg.title,
                "tagline": pkg.manifest["metadata"].get("tagline", ""),
                "min_players": cfg["min_players"], "max_players": cfg["max_players"],
            })
    return out


def validate_package(manifest, files, package_id=None, version=None):
    errors = []
    for field in ("package_id", "schema_version", "content_version", "metadata", "configuration", "references"):
        if field not in manifest:
            errors.append(f"package.json missing {field}")
    if errors:
        return errors
    if package_id and manifest["package_id"] != package_id:
        errors.append("package_id does not match directory")
    if version and manifest["content_version"] != version:
        errors.append("content_version does not match directory")
    for key in ("characters", "knowledge", "disclosure_rules", "timeline", "solution"):
        if key not in files:
            errors.append(f"missing reference: {key}")
    if errors:
        return errors

    seen = {}

    def claim_id(i, where):
        if not isinstance(i, str) or not _SAFE_ID.match(i):
            errors.append(f"{where}: invalid id {i!r}")
        elif i in seen:
            errors.append(f"duplicate id {i} ({seen[i]} and {where})")
        else:
            seen[i] = where

    chars = files["characters"]
    tags = {t["id"] for t in chars["content_tags"]}
    for t in chars["content_tags"]:
        claim_id(t["id"], "content_tags")
        if not isinstance(t.get("sensitive"), bool):
            errors.append(f"tag {t['id']}: sensitive must be true/false")
    roles = {r["id"]: r for r in chars["roles"]}
    for r in chars["roles"]:
        claim_id(r["id"], "roles")
    rels = {r["id"]: r for r in chars["relationships"]}
    for r in chars["relationships"]:
        claim_id(r["id"], "relationships")
    knowledge = {k["id"]: k for k in files["knowledge"]["knowledge_items"]}
    for k in files["knowledge"]["knowledge_items"]:
        claim_id(k["id"], "knowledge")
    rules = {d["id"]: d for d in files["disclosure_rules"]["disclosure_rules"]}
    for d in files["disclosure_rules"]["disclosure_rules"]:
        claim_id(d["id"], "disclosure_rules")
    facts = {f["id"]: f for f in files["timeline"]["canonical_facts"]}
    for f in files["timeline"]["canonical_facts"]:
        claim_id(f["id"], "timeline")

    for r in roles.values():
        where = f"role {r['id']}"
        for field in ("name", "public_bio", "required", "content_tags", "dimension_targets", "load_budget", "private"):
            if field not in r:
                errors.append(f"{where}: missing {field}")
        for t in r.get("content_tags", []):
            if t not in tags:
                errors.append(f"{where}: unknown content tag {t}")
        dt = r.get("dimension_targets", {})
        for dim in DIMENSIONS:
            v = dt.get(dim)
            if not isinstance(v, (int, float)) or not 0 <= v <= 100:
                errors.append(f"{where}: dimension_targets.{dim} must be 0-100")
        lb = r.get("load_budget", {})
        for field in ("preparation_minutes", "secrets", "memories", "missions", "mandatory_performance_moments"):
            if not isinstance(lb.get(field), int) or lb[field] < 0:
                errors.append(f"{where}: load_budget.{field} must be a non-negative integer")
        priv = r.get("private", {})
        if len(priv.get("secrets", [])) != lb.get("secrets"):
            errors.append(f"{where}: load_budget.secrets does not match number of secrets")
        for rid in r.get("relationships", []):
            if rid not in rels:
                errors.append(f"{where}: unknown relationship {rid}")
            elif rels[rid]["from"] != r["id"]:
                errors.append(f"{where}: relationship {rid} belongs to {rels[rid]['from']}")
        for kid in r.get("permitted_memory_ids", []):
            if kid not in knowledge:
                errors.append(f"{where}: unknown memory {kid}")
            elif knowledge[kid]["character_id"] != r["id"]:
                errors.append(f"{where}: memory {kid} belongs to another character")
            elif not (knowledge[kid]["pre_party"] and knowledge[kid]["private_eligible"]):
                errors.append(f"{where}: memory {kid} is not a pre-party private-eligible item")
        for did in r.get("disclosure_rule_ids", []):
            if did not in rules:
                errors.append(f"{where}: unknown disclosure rule {did}")

    for rel in rels.values():
        if rel["from"] not in roles:
            errors.append(f"relationship {rel['id']}: unknown from-role {rel['from']}")
        if rel.get("to") is not None and rel["to"] not in roles:
            errors.append(f"relationship {rel['id']}: unknown to-role {rel['to']}")

    for k in knowledge.values():
        if k["character_id"] not in roles:
            errors.append(f"knowledge {k['id']}: unknown character {k['character_id']}")
        for ref in k.get("canonical_fact_refs", []):
            if ref not in facts:
                errors.append(f"knowledge {k['id']}: unknown canonical fact {ref}")

    for d in rules.values():
        where = f"disclosure rule {d['id']}"
        if d.get("phase_gate") not in PHASES:
            errors.append(f"{where}: invalid phase_gate {d.get('phase_gate')}")
        for rid in d.get("eligible_roles", []):
            if rid not in roles:
                errors.append(f"{where}: unknown role {rid}")
        for cid in d.get("approved_claim_ids", []):
            if cid not in knowledge:
                errors.append(f"{where}: unknown claim {cid}")
            elif knowledge[cid]["character_id"] not in d.get("eligible_roles", []):
                errors.append(f"{where}: claim {cid} belongs to a character outside eligible_roles")
        for pid in d.get("prerequisite_ids", []):
            if pid not in seen:
                errors.append(f"{where}: unknown prerequisite {pid}")

    for f in facts.values():
        for src in f.get("source_event_ids", []):
            if src not in facts:
                errors.append(f"fact {f['id']}: unknown source event {src}")

    sol = files["solution"]
    if sol.get("killer_role_id") not in roles:
        errors.append("solution: killer_role_id is not a role")
    elif not roles[sol["killer_role_id"]].get("required"):
        errors.append("solution: killer role must be required so it is always cast")
    for rid in sol.get("accomplice_role_ids", []):
        if rid not in roles:
            errors.append(f"solution: unknown accomplice {rid}")
    for fid in sol.get("key_evidence", []):
        if fid not in facts:
            errors.append(f"solution: unknown key_evidence {fid}")

    cfg = manifest["configuration"]
    required = sum(1 for r in roles.values() if r.get("required"))
    if cfg.get("max_players", 0) > len(roles):
        errors.append("configuration.max_players exceeds number of roles")
    if required > cfg.get("min_players", 0):
        errors.append("more required roles than configuration.min_players")
    return errors


# ---------------------------------------------------------------- projections
# Every API response that contains package content must be built here.

def public_role(pkg, role_id):
    r = pkg.roles[role_id]
    return {"id": r["id"], "name": r["name"], "public_bio": r["public_bio"]}


def casting_role(pkg, role_id):
    """Role requirements for the host's casting screen (host-only)."""
    r = pkg.roles[role_id]
    return {
        "id": r["id"], "name": r["name"], "public_bio": r["public_bio"],
        "required": r["required"], "importance": r.get("importance"),
        "content_tags": [{"id": t, "label": pkg.content_tags[t]["label"],
                          "sensitive": pkg.content_tags[t]["sensitive"]} for t in r["content_tags"]],
        "dimension_targets": r["dimension_targets"], "load_budget": r["load_budget"],
    }


def player_boundary_tags(pkg):
    """The tag list a guest is asked about. Does not reveal which roles use them."""
    return [{"id": t["id"], "label": t["label"], "description": t["description"],
             "sensitive": t["sensitive"]} for t in pkg.content_tags.values()]


def onboarding_claims(pkg, role_id, phase):
    claims = []
    rules = sorted((d for d in pkg.disclosure_rules.values()
                    if d["channel"] == "onboarding" and role_id in d["eligible_roles"]
                    and phase_reached(phase, d["phase_gate"]) and not d["prerequisite_ids"]),
                   key=lambda d: d["priority"])
    for d in rules:
        for cid in d["approved_claim_ids"]:
            k = pkg.knowledge[cid]
            claims.append({"claim_id": cid, "rule_id": d["id"], "claim": k["claim"],
                           "source_type": k["source_type"], "certainty": k["certainty"],
                           "time": k["source_time"]})
    return claims


def private_role_for_player(pkg, role_id, phase):
    """The full private dashboard for the player *assigned* to role_id.
    Caller must already have verified that assignment and its approval."""
    r = pkg.roles[role_id]
    rels = []
    for rid in r.get("relationships", []):
        rel = pkg.relationships[rid]
        other = pkg.roles[rel["to"]]["name"] if rel.get("to") else rel.get("about")
        rels.append({"with": other, "description": rel["description"]})
    return {
        "id": r["id"], "name": r["name"], "public_bio": r["public_bio"],
        "backstory": r["private"]["backstory"],
        "secrets": list(r["private"]["secrets"]),
        "goal": r["private"].get("goal"),
        "relationships": rels,
        "briefing": onboarding_claims(pkg, role_id, phase),
        "preparation_minutes": r["load_budget"]["preparation_minutes"],
    }


def host_solution(pkg):
    sol = pkg.solution
    return {
        "killer": public_role(pkg, sol["killer_role_id"]),
        "accomplices": [public_role(pkg, a) for a in sol.get("accomplice_role_ids", [])],
        "motive": sol["motive"], "method": sol["method"], "summary": sol.get("summary"),
        "timeline": [{"id": f["id"], "time": f["event_time"], "statement": f["statement"],
                      "criticality": f["criticality"]} for f in pkg.timeline],
    }
