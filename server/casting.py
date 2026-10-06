"""Casting (spec §4): eliminate incompatible pairs, score the rest, and find a
complete assignment across all guests at once (Hungarian algorithm).

Inputs are plain dicts so this module has no database or HTTP knowledge.
A player dict has: id, name, scores, boundaries, preparation_minutes,
assistance, stretch_preference. Roles come from the package."""

from .content import DIMENSIONS

STRETCH_TOLERANCE = {"none": 0, "modest": 8, "high": 15}
SHORTFALL_WEIGHT = 1.5
EXCESS_WEIGHT = 0.75
BURDEN_WEIGHT = 0.3
ASSISTANCE_PENALTY = 10
REQUIRED_BONUS = 10_000      # fill required roles before optimising fit
INFEASIBLE = 1_000_000

# Forced casting (host override). Costs are ordered so the solver prefers, in turn:
# filling every required role > forcing as few guests as possible > forcing the
# most willing guest > overall fit.
FORCED_REQUIRED_BONUS = 1_000_000_000
FORCE_PENALTY = 1_000_000

# Willingness multipliers (host-only score, 0-100).
WILL_BLOCKED = 0.1           # per "No thanks"
WILL_SENSITIVE_UNSURE = 0.6  # per "Not sure" on sensitive content
WILL_UNSURE = 0.9            # per "Not sure" on ordinary content


def hard_conflicts(player, role, tags):
    """Reasons this player does not fit this role. Empty list = compatible.
    By default these always win (spec §4); only an explicit host force can
    override them, and that override is recorded."""
    reasons = []
    for t in role["content_tags"]:
        v = player["boundaries"].get(t, "unspecified")
        label = tags[t]["label"]
        if v == "blocked":
            reasons.append(f"said No thanks to: {label}")
        elif v == "unspecified" and tags[t]["sensitive"]:
            reasons.append(f"said Not sure to sensitive content: {label}")
    need = role["load_budget"]["preparation_minutes"]
    if need > player["preparation_minutes"]:
        reasons.append(f"needs {need} min preparation; guest offered {player['preparation_minutes']}")
    return reasons


def willingness(player, role, tags, cost):
    """Host-only 0-100 estimate of how happy this guest would be in this role.
    Starts from questionnaire fit, then discounts for each boundary or
    preparation problem. Returns (score, level, reasons)."""
    score = max(0.0, min(100.0, 100.0 - cost))
    reasons, level = [], "fits"
    for t in role["content_tags"]:
        v = player["boundaries"].get(t, "unspecified")
        label = tags[t]["label"]
        if v == "blocked":
            score *= WILL_BLOCKED
            level = "refused"
            reasons.append(f"No thanks: {label}")
        elif v == "unspecified":
            score *= WILL_SENSITIVE_UNSURE if tags[t]["sensitive"] else WILL_UNSURE
            if tags[t]["sensitive"] and level == "fits":
                level = "stretch"
            reasons.append(f"Not sure: {label}")
    need, offered = role["load_budget"]["preparation_minutes"], player["preparation_minutes"]
    if need > offered:
        score *= (offered / need) if need else 1
        if level == "fits":
            level = "stretch"
        reasons.append(f"prep {offered} of {need} min")
    return round(score), level, reasons


def role_burden(role):
    """0-100 index of how much a role asks of its player."""
    lb = role["load_budget"]
    raw = (min(lb["preparation_minutes"], 60) / 60 * 35
           + min(lb["secrets"] + lb["memories"] + lb["missions"], 8) / 8 * 35
           + min(lb["mandatory_performance_moments"], 3) / 3 * 30)
    return round(raw)


def fit(player, role):
    """Soft cost (lower is better) and a human-readable breakdown."""
    tol = STRETCH_TOLERANCE[player["stretch_preference"]]
    weights = role.get("dimension_weights", {})
    total_w, total, notes = 0.0, 0.0, []
    for d in DIMENSIONS:
        w = weights.get(d, 1.0)
        target, have = role["dimension_targets"][d], player["scores"][d]
        gap = target - have
        if gap > 0:
            pen = max(0, gap - tol) * SHORTFALL_WEIGHT
            if gap >= 25:
                notes.append(f"role asks for more {d} ({target}) than the guest prefers ({have})")
        else:
            pen = -gap * EXCESS_WEIGHT
            if -gap >= 40:
                notes.append(f"guest would enjoy more {d} ({have}) than this role offers ({target})")
        total += w * pen
        total_w += w
    dim_cost = total / total_w
    burden = role_burden(role)
    burden_cost = abs(player["scores"]["involvement"] - burden) * BURDEN_WEIGHT
    assist_cost = 0
    if player["assistance"] == "full" and role["load_budget"]["mandatory_performance_moments"] >= 2:
        assist_cost = ASSISTANCE_PENALTY
        notes.append("guest wants full acting help; role has several mandatory performance moments")
    cost = round(dim_cost + burden_cost + assist_cost, 1)
    return cost, {"dimension_mismatch": round(dim_cost, 1), "burden_mismatch": round(burden_cost, 1),
                  "assistance_penalty": assist_cost, "role_burden": burden,
                  "guest_involvement": player["scores"]["involvement"], "notes": notes}


def hungarian(cost):
    """Minimum-cost assignment for an n x m matrix with n <= m.
    Returns a list: row index -> column index."""
    n, m = len(cost), len(cost[0])
    INF = float("inf")
    u, v = [0.0] * (n + 1), [0.0] * (m + 1)
    p, way = [0] * (m + 1), [0] * (m + 1)
    for i in range(1, n + 1):
        p[0] = i
        j0 = 0
        minv = [INF] * (m + 1)
        used = [False] * (m + 1)
        while True:
            used[j0] = True
            i0, delta, j1 = p[j0], INF, 0
            for j in range(1, m + 1):
                if not used[j]:
                    cur = cost[i0 - 1][j - 1] - u[i0] - v[j]
                    if cur < minv[j]:
                        minv[j], way[j] = cur, j0
                    if minv[j] < delta:
                        delta, j1 = minv[j], j
            for j in range(m + 1):
                if used[j]:
                    u[p[j]] += delta
                    v[j] -= delta
                else:
                    minv[j] -= delta
            j0 = j1
            if p[j0] == 0:
                break
        while True:
            j1 = way[j0]
            p[j0] = p[j1]
            j0 = j1
            if j0 == 0:
                break
    result = [-1] * n
    for j in range(1, m + 1):
        if p[j]:
            result[p[j] - 1] = j - 1
    return result


def compatibility(players, pkg):
    """{player_id: {role_id: {"compatible", "conflicts", "cost", "breakdown",
    "willingness", "level", "willingness_reasons"}}}"""
    out = {}
    for p in players:
        row = {}
        for rid, role in pkg.roles.items():
            conflicts = hard_conflicts(p, role, pkg.content_tags)
            cost, breakdown = fit(p, role)
            will, level, why = willingness(p, role, pkg.content_tags, cost)
            row[rid] = {"compatible": not conflicts, "conflicts": conflicts, "cost": cost, "breakdown": breakdown,
                        "willingness": will, "level": level, "willingness_reasons": why}
        out[p["id"]] = row
    return out


def recommend(players, pkg, force=False):
    """Returns {"assignments": {player_id: role_id}, "forced": {player_id: [conflicts]},
    "problems": [str], "matrix": ...}. problems non-empty means no cast was produced.
    With force=True (host override) required roles are filled even when nobody
    fits, choosing the fewest and most willing guests to override."""
    roles = list(pkg.roles.values())
    matrix = compatibility(players, pkg)
    problems = []
    cfg = pkg.manifest["configuration"]
    if len(players) < cfg["min_players"]:
        problems.append(f"{len(players)} guest(s) ready; this package needs at least {cfg['min_players']}")
    if len(players) > len(roles):
        problems.append(f"{len(players)} guests but only {len(roles)} roles")
        return {"assignments": {}, "forced": {}, "problems": problems, "matrix": matrix}
    if force:
        if not players:
            return {"assignments": {}, "forced": {}, "problems": problems or ["no guests ready"], "matrix": matrix}
        return _recommend_forced(players, roles, matrix, problems)
    unfillable = set()
    for role in roles:
        if role["required"] and not any(matrix[p["id"]][role["id"]]["compatible"] for p in players):
            unfillable.add(role["id"])
            why = "; ".join(f"{p['name']}: {', '.join(matrix[p['id']][role['id']]['conflicts'])}" for p in players)
            problems.append(f"Required role {role['name']} can't be cast. Nobody fits it. {why}")
    for p in players:
        if not any(c["compatible"] for c in matrix[p["id"]].values()):
            problems.append(f"{p['name']} is compatible with no role")
    if not players:
        return {"assignments": {}, "forced": {}, "problems": problems or ["no guests ready"], "matrix": matrix}

    cost = []
    for p in players:
        row = []
        for role in roles:
            cell = matrix[p["id"]][role["id"]]
            c = cell["cost"] if cell["compatible"] else INFEASIBLE
            if role["required"]:
                c -= REQUIRED_BONUS
            row.append(c)
        cost.append(row)
    picks = hungarian(cost)
    assignments = {}
    for p, j in zip(players, picks):
        role = roles[j]
        if not matrix[p["id"]][role["id"]]["compatible"]:
            problems.append(f"no complete compatible cast: {p['name']} cannot be placed")
        assignments[p["id"]] = role["id"]
    filled = set(assignments.values())
    for role in roles:
        if role["required"] and role["id"] not in filled and role["id"] not in unfillable:
            problems.append(f"required role {role['name']} could not be filled")
    return {"assignments": assignments, "forced": {}, "problems": list(dict.fromkeys(problems)), "matrix": matrix}


def _recommend_forced(players, roles, matrix, problems):
    cost = []
    for p in players:
        row = []
        for role in roles:
            cell = matrix[p["id"]][role["id"]]
            c = cell["cost"]
            if not cell["compatible"]:
                c += FORCE_PENALTY + (100 - cell["willingness"]) * 100
            if role["required"]:
                c -= FORCED_REQUIRED_BONUS
            row.append(c)
        cost.append(row)
    picks = hungarian(cost)
    assignments, forced = {}, {}
    for p, j in zip(players, picks):
        rid = roles[j]["id"]
        assignments[p["id"]] = rid
        if not matrix[p["id"]][rid]["compatible"]:
            forced[p["id"]] = matrix[p["id"]][rid]["conflicts"]
    return {"assignments": assignments, "forced": forced, "problems": problems, "matrix": matrix}


def validate_cast(players, assignments, pkg, forced=None):
    """Full revalidation used for overrides and approval. Returns problems.
    forced = {player_id: [conflicts the host explicitly overrode]}. Any conflict
    not in that list (e.g. the guest changed an answer since) is still a problem."""
    forced = forced or {}
    problems = []
    ids = {p["id"] for p in players}
    for p in players:
        if p["id"] not in assignments:
            problems.append(f"{p['name']} has no role")
    for pid in assignments:
        if pid not in ids:
            problems.append("assignment for a guest who is not ready for casting")
    held = list(assignments.values())
    if len(held) != len(set(held)):
        problems.append("a role is assigned to more than one guest")
    for p in players:
        rid = assignments.get(p["id"])
        if rid is None:
            continue
        if rid not in pkg.roles:
            problems.append(f"{p['name']}: unknown role")
            continue
        accepted = set(forced.get(p["id"]) or [])
        for c in hard_conflicts(p, pkg.roles[rid], pkg.content_tags):
            if c not in accepted:
                problems.append(f"{p['name']} as {pkg.roles[rid]['name']}: {c}")
    for role in pkg.roles.values():
        if role["required"] and role["id"] not in held:
            problems.append(f"required role {role['name']} is not cast")
    cfg = pkg.manifest["configuration"]
    if not cfg["min_players"] <= len(players) <= cfg["max_players"]:
        problems.append(f"cast size {len(players)} outside {cfg['min_players']}-{cfg['max_players']}")
    return problems
