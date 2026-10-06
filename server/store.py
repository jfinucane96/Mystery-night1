"""Game rules for the casting slice. Every function takes the authenticated
user and decides, on the server, what that user may see or change.
Package content leaves this module only through content.py projections."""

import json
import re
import secrets
from datetime import datetime, timedelta, timezone

from . import assessment as scoring
from . import auth, casting, content
from .db import transaction

INVITE_DAYS = 30
ASSESSMENT_VERSION = "assessment-v1"
EDITABLE_PHASES = ("DRAFT", "CASTING")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class ApiError(Exception):
    def __init__(self, status, message, **extra):
        super().__init__(message)
        self.status, self.message, self.extra = status, message, extra


def _audit(conn, game_id, user, action, **detail):
    conn.execute("INSERT INTO audit_log (game_id, actor_user_id, action, detail, at) VALUES (?,?,?,?,?)",
                 (game_id, user["id"] if user else None, action, json.dumps(detail), auth.now()))


def _require_user(user):
    if not user:
        raise ApiError(401, "Please sign in.")
    return user


# ------------------------------------------------------------------ accounts

def register(conn, email, password, display_name):
    email = (email or "").strip().lower()
    display_name = (display_name or "").strip()
    if not EMAIL_RE.match(email) or len(email) > 200:
        raise ApiError(400, "Enter a valid email address.")
    if not 1 <= len(display_name) <= 60:
        raise ApiError(400, "Display name must be 1-60 characters.")
    if not isinstance(password, str) or len(password) < 8:
        raise ApiError(400, "Password must be at least 8 characters.")
    uid = auth.new_id()
    with transaction(conn):
        if conn.execute("SELECT 1 FROM users WHERE email = ?", (email,)).fetchone():
            raise ApiError(409, "An account with that email already exists. Sign in instead.")
        conn.execute("INSERT INTO users (id, email, display_name, password_hash, created_at) VALUES (?,?,?,?,?)",
                     (uid, email, display_name, auth.hash_password(password), auth.now()))
        token = auth.create_session(conn, uid)
    return token


def login(conn, email, password):
    uid = auth.check_login(conn, (email or "").strip().lower(), password or "")
    if not uid:
        raise ApiError(401, "Email or password is incorrect.")
    return auth.create_session(conn, uid)


# ------------------------------------------------------------------ helpers

def _game(conn, gid):
    return conn.execute("SELECT * FROM games WHERE id = ?", (gid,)).fetchone()


def _player_for(conn, gid, user_id):
    return conn.execute("SELECT * FROM players WHERE game_id = ? AND identity_id = ?", (gid, user_id)).fetchone()


def _access(conn, user, gid):
    """Returns (game, 'host'|'player', player_row). Non-members get 404 so the
    existence of a game id is not confirmed to strangers."""
    _require_user(user)
    g = _game(conn, gid)
    if g is None:
        raise ApiError(404, "Game not found.")
    if g["host_user_id"] == user["id"]:
        return g, "host", None
    p = _player_for(conn, gid, user["id"])
    if p is not None and p["rsvp"] == "accepted":
        return g, "player", p
    raise ApiError(404, "Game not found.")


def _require_host(conn, user, gid):
    g, rel, _ = _access(conn, user, gid)
    if rel != "host":
        raise ApiError(403, "Only the host can do that.")
    return g


def _require_player(conn, user, gid):
    g, rel, p = _access(conn, user, gid)
    if rel != "player":
        raise ApiError(403, "Only guests in this game can do that.")
    return g, p


def _package(g):
    try:
        return content.load_package(g["package_id"], g["package_version"], g["package_hash"])
    except content.ContentError as e:
        raise ApiError(500, str(e))


def _ready_players(conn, gid):
    rows = conn.execute(
        "SELECT p.*, u.display_name FROM players p JOIN users u ON u.id = p.identity_id "
        "WHERE p.game_id = ? AND p.rsvp = 'accepted' AND p.assessment_status = 'complete' "
        "ORDER BY p.completed_at", (gid,)).fetchall()
    return [{
        "id": r["id"], "name": r["display_name"], "scores": json.loads(r["scores"]),
        "boundaries": json.loads(r["boundaries"]), "preparation_minutes": r["preparation_minutes"],
        "assistance": r["assistance"], "stretch_preference": r["stretch_preference"],
        "updated_at": r["updated_at"],
    } for r in rows]


def _approved_assignment(conn, gid, player_id):
    return conn.execute(
        "SELECT * FROM assignments WHERE game_id = ? AND player_id = ? AND approval_state = 'approved'",
        (gid, player_id)).fetchone()


# ------------------------------------------------------------------ games

def list_packages():
    return content.list_packages()


def create_game(conn, user, title, package_id, package_version):
    _require_user(user)
    title = (title or "").strip()
    if not 1 <= len(title) <= 100:
        raise ApiError(400, "Give the game a title (1-100 characters).")
    try:
        pkg = content.load_package(package_id or "", package_version or "")
    except (content.ContentError, FileNotFoundError):
        raise ApiError(400, "Unknown mystery package.")
    gid = auth.new_id()
    with transaction(conn):
        conn.execute("INSERT INTO games (id, title, host_user_id, package_id, package_version, package_hash, created_at) "
                     "VALUES (?,?,?,?,?,?,?)",
                     (gid, title, user["id"], pkg.package_id, pkg.version, pkg.digest, auth.now()))
        _audit(conn, gid, user, "game.create", package=f"{pkg.package_id}@{pkg.version}")
    return {"id": gid}


def list_games(conn, user):
    _require_user(user)
    hosted = conn.execute("SELECT id, title, phase, created_at FROM games WHERE host_user_id = ? "
                          "ORDER BY created_at DESC", (user["id"],)).fetchall()
    joined = conn.execute("SELECT g.id, g.title, g.phase, g.created_at FROM players p JOIN games g ON g.id = p.game_id "
                          "WHERE p.identity_id = ? AND p.rsvp = 'accepted' ORDER BY g.created_at DESC",
                          (user["id"],)).fetchall()
    return {"hosting": [dict(r) for r in hosted], "playing": [dict(r) for r in joined]}


def game_view(conn, user, gid):
    g, rel, p = _access(conn, user, gid)
    pkg = _package(g)
    host = conn.execute("SELECT display_name FROM users WHERE id = ?", (g["host_user_id"],)).fetchone()
    base = {"id": g["id"], "title": g["title"], "phase": g["phase"], "role": rel,
            "host_name": host["display_name"],
            "package": {"title": pkg.title, "tagline": pkg.manifest["metadata"].get("tagline", ""),
                        "version": pkg.version}}
    if rel == "host":
        base.update(_host_view(conn, g, pkg))
    else:
        answered = conn.execute("SELECT COUNT(*) FROM assessment_answers WHERE player_id = ?",
                                (p["id"],)).fetchone()[0]
        released = _approved_assignment(conn, gid, p["id"]) is not None
        base["me"] = {"player_id": p["id"], "assessment_status": p["assessment_status"],
                      "answered": answered, "character_released": released}
    return base


def _host_view(conn, g, pkg):
    invs = conn.execute(
        "SELECT i.id, i.label, i.status, i.created_at, i.expires_at, u.display_name AS claimed_by "
        "FROM invitations i LEFT JOIN users u ON u.id = i.claimed_by_user_id "
        "WHERE i.game_id = ? ORDER BY i.created_at", (g["id"],)).fetchall()
    players = conn.execute(
        "SELECT p.id, p.rsvp, p.assessment_status, p.updated_at, u.display_name, u.email, "
        "(SELECT COUNT(*) FROM assessment_answers a WHERE a.player_id = p.id) AS answered, "
        "(SELECT role_id FROM assignments a WHERE a.player_id = p.id) AS role_id, "
        "(SELECT approval_state FROM assignments a WHERE a.player_id = p.id) AS approval_state, "
        "(SELECT COUNT(*) FROM disclosure_receipts r WHERE r.player_id = p.id AND r.acknowledged_at IS NOT NULL) AS acknowledged "
        "FROM players p JOIN users u ON u.id = p.identity_id WHERE p.game_id = ? ORDER BY p.updated_at",
        (g["id"],)).fetchall()
    cfg = pkg.manifest["configuration"]
    return {
        "invitations": [dict(r) for r in invs],
        "players": [{**dict(r), "role_name": pkg.roles[r["role_id"]]["name"] if r["role_id"] else None}
                    for r in players],
        "question_count": len(content.load_assessment(ASSESSMENT_VERSION)["questions"]),
        "player_range": [cfg["min_players"], cfg["max_players"]],
        "assignment_version": g["assignment_version"],
    }


# ------------------------------------------------------------------ invitations

def create_invitation(conn, user, gid, label):
    g = _require_host(conn, user, gid)
    if g["phase"] not in EDITABLE_PHASES:
        raise ApiError(409, "The cast is already approved; invitations are closed.")
    label = (label or "").strip()[:60]
    token = secrets.token_urlsafe(24)
    iid = auth.new_id()
    expires = (datetime.now(timezone.utc) + timedelta(days=INVITE_DAYS)).isoformat(timespec="seconds")
    with transaction(conn):
        conn.execute("INSERT INTO invitations (id, game_id, label, token_hash, created_at, expires_at) "
                     "VALUES (?,?,?,?,?,?)", (iid, gid, label, auth.token_hash(token), auth.now(), expires))
        _audit(conn, gid, user, "invitation.create", invitation_id=iid, label=label)
    # The raw token is returned exactly once; only its hash is stored.
    return {"id": iid, "label": label, "token": token, "path": f"/#/invite/{token}"}


def revoke_invitation(conn, user, gid, iid):
    _require_host(conn, user, gid)
    with transaction(conn):
        row = conn.execute("SELECT status FROM invitations WHERE id = ? AND game_id = ?", (iid, gid)).fetchone()
        if not row:
            raise ApiError(404, "Invitation not found.")
        if row["status"] != "pending":
            raise ApiError(409, f"Invitation is already {row['status']}.")
        conn.execute("UPDATE invitations SET status = 'revoked' WHERE id = ?", (iid,))
        _audit(conn, gid, user, "invitation.revoke", invitation_id=iid)
    return {"ok": True}


def _invitation_by_token(conn, token):
    if not token or len(token) > 100:
        raise ApiError(404, "This invitation link is not valid.")
    inv = conn.execute("SELECT * FROM invitations WHERE token_hash = ?", (auth.token_hash(token),)).fetchone()
    if not inv:
        raise ApiError(404, "This invitation link is not valid.")
    return inv


def preview_invitation(conn, user, token):
    inv = _invitation_by_token(conn, token)
    g = _game(conn, inv["game_id"])
    pkg = _package(g)
    host = conn.execute("SELECT display_name FROM users WHERE id = ?", (g["host_user_id"],)).fetchone()
    status = inv["status"]
    if status == "pending" and inv["expires_at"] < auth.now():
        status = "expired"
    mine = bool(user and inv["claimed_by_user_id"] == user["id"])
    return {"game_title": g["title"], "host_name": host["display_name"], "label": inv["label"],
            "package_title": pkg.title, "tagline": pkg.manifest["metadata"].get("tagline", ""),
            "status": status, "claimed_by_you": mine,
            "game_id": g["id"] if mine and inv["status"] == "accepted" else None}


def respond_invitation(conn, user, token, response):
    _require_user(user)
    if response not in ("accept", "decline"):
        raise ApiError(400, "Response must be accept or decline.")
    with transaction(conn):
        inv = _invitation_by_token(conn, token)
        g = _game(conn, inv["game_id"])
        if g["host_user_id"] == user["id"]:
            raise ApiError(409, "You're the host of this game; hosts don't play a character. "
                                "Open this link in a different account to test as a guest.")
        if inv["claimed_by_user_id"] and inv["claimed_by_user_id"] != user["id"]:
            raise ApiError(409, "This invitation has already been used by someone else.")
        if inv["status"] in ("revoked", "closed"):
            raise ApiError(410, "This invitation is no longer active.")
        if inv["status"] == "pending" and inv["expires_at"] < auth.now():
            raise ApiError(410, "This invitation has expired. Ask the host for a new link.")
        if g["phase"] not in EDITABLE_PHASES:
            raise ApiError(409, "Casting for this game is already complete.")
        existing = _player_for(conn, g["id"], user["id"])
        if existing and existing["invitation_id"] != inv["id"]:
            raise ApiError(409, "You've already joined this game with another invitation.")
        rsvp = "accepted" if response == "accept" else "declined"
        if rsvp == "accepted" and not (existing and existing["rsvp"] == "accepted"):
            max_players = _package(g).manifest["configuration"]["max_players"]
            accepted = conn.execute("SELECT COUNT(*) FROM players WHERE game_id = ? AND rsvp = 'accepted'",
                                    (g["id"],)).fetchone()[0]
            if accepted >= max_players:
                raise ApiError(409, f"This game is full ({max_players} guests). Ask the host about a spare place.")
        if existing:
            if existing["rsvp"] == "accepted" and rsvp == "declined":
                conn.execute("DELETE FROM assignments WHERE player_id = ? AND approval_state = 'draft'",
                             (existing["id"],))
            conn.execute("UPDATE players SET rsvp = ?, updated_at = ? WHERE id = ?", (rsvp, auth.now(), existing["id"]))
        else:
            conn.execute("INSERT INTO players (id, game_id, identity_id, invitation_id, rsvp, assessment_version, updated_at) "
                         "VALUES (?,?,?,?,?,?,?)",
                         (auth.new_id(), g["id"], user["id"], inv["id"], rsvp, ASSESSMENT_VERSION, auth.now()))
        conn.execute("UPDATE invitations SET status = ?, claimed_by_user_id = ?, responded_at = ? WHERE id = ?",
                     (rsvp, user["id"], auth.now(), inv["id"]))
        _audit(conn, g["id"], user, "invitation.respond", invitation_id=inv["id"], rsvp=rsvp)
    return {"game_id": g["id"], "rsvp": rsvp}


# ------------------------------------------------------------------ assessment

def get_assessment(conn, user, gid):
    g, p = _require_player(conn, user, gid)
    a = content.load_assessment(p["assessment_version"])
    pkg = _package(g)
    answers = {r["question_id"]: r["option_id"] for r in
               conn.execute("SELECT question_id, option_id FROM assessment_answers WHERE player_id = ?", (p["id"],))}
    prefs = a["preference_fields"]
    return {
        "version": a["assessment_version"],
        # No dimension labels or scoring map are sent to players.
        "questions": [{"id": q["id"], "prompt": q["prompt"],
                       "options": [{"id": o["id"], "text": o["text"]} for o in q["options"]]}
                      for q in a["questions"]],
        "answers": answers,
        "tags": content.player_boundary_tags(pkg),
        "boundaries": json.loads(p["boundaries"]) if p["boundaries"] else {},
        "preparation_minutes": p["preparation_minutes"] if p["preparation_minutes"] is not None
        else prefs["preparation_minutes"]["default"],
        "assistance": p["assistance"] or "some",
        "stretch_preference": p["stretch_preference"] or "modest",
        "status": p["assessment_status"],
        "editable": g["phase"] in EDITABLE_PHASES,
    }


def _require_editable(g):
    if g["phase"] not in EDITABLE_PHASES:
        raise ApiError(409, "Your character has been approved, so the questionnaire is closed.")


def save_answer(conn, user, gid, question_id, option_id):
    g, p = _require_player(conn, user, gid)
    _require_editable(g)
    a = content.load_assessment(p["assessment_version"])
    try:
        scoring.validate_answer(a, question_id, option_id)
    except ValueError as e:
        raise ApiError(400, str(e))
    with transaction(conn):
        conn.execute("INSERT INTO assessment_answers (player_id, question_id, option_id, saved_at) VALUES (?,?,?,?) "
                     "ON CONFLICT (player_id, question_id) DO UPDATE SET option_id = excluded.option_id, "
                     "saved_at = excluded.saved_at", (p["id"], question_id, option_id, auth.now()))
        status = "in_progress" if p["assessment_status"] == "not_started" else p["assessment_status"]
        if status == "complete":
            _rescore(conn, p, a)
        conn.execute("UPDATE players SET assessment_status = ?, updated_at = ? WHERE id = ?",
                     (status, auth.now(), p["id"]))
    return {"ok": True}


def save_preferences(conn, user, gid, body):
    g, p = _require_player(conn, user, gid)
    _require_editable(g)
    a = content.load_assessment(p["assessment_version"])
    pkg = _package(g)
    boundaries = body.get("boundaries") or {}
    try:
        scoring.validate_preferences(a, list(pkg.content_tags), boundaries, body.get("preparation_minutes"),
                                     body.get("assistance"), body.get("stretch_preference"))
    except ValueError as e:
        raise ApiError(400, str(e))
    with transaction(conn):
        status = "in_progress" if p["assessment_status"] == "not_started" else p["assessment_status"]
        conn.execute("UPDATE players SET boundaries = ?, preparation_minutes = ?, assistance = ?, "
                     "stretch_preference = ?, assessment_status = ?, updated_at = ? WHERE id = ?",
                     (json.dumps(boundaries), body["preparation_minutes"], body["assistance"],
                      body["stretch_preference"], status, auth.now(), p["id"]))
    return {"ok": True}


def _rescore(conn, p, a):
    answers = {r["question_id"]: r["option_id"] for r in
               conn.execute("SELECT question_id, option_id FROM assessment_answers WHERE player_id = ?", (p["id"],))}
    scores = scoring.score(a, answers)
    conn.execute("UPDATE players SET scores = ? WHERE id = ?", (json.dumps(scores), p["id"]))


def submit_assessment(conn, user, gid):
    g, p = _require_player(conn, user, gid)
    _require_editable(g)
    a = content.load_assessment(p["assessment_version"])
    with transaction(conn):
        p = conn.execute("SELECT * FROM players WHERE id = ?", (p["id"],)).fetchone()
        if not p["boundaries"]:
            raise ApiError(400, "Please complete the boundaries and preferences section.")
        try:
            _rescore(conn, p, a)
        except ValueError:
            raise ApiError(400, "Please answer every question before submitting.")
        conn.execute("UPDATE players SET assessment_status = 'complete', completed_at = COALESCE(completed_at, ?), "
                     "updated_at = ? WHERE id = ?", (auth.now(), auth.now(), p["id"]))
        _audit(conn, gid, user, "assessment.submit", player_id=p["id"])
    # Players are not shown scores, tiers or involvement labels (spec §3).
    return {"status": "complete"}


# ------------------------------------------------------------------ casting (host)

def recommend_cast(conn, user, gid, force=False):
    """force=True is the host's "auto-assign necessary roles" override: required
    roles are filled even when nobody fits, and each override is recorded."""
    g = _require_host(conn, user, gid)
    if g["phase"] not in EDITABLE_PHASES:
        raise ApiError(409, "The cast is already approved.")
    pkg = _package(g)
    with transaction(conn):
        _check_no_incomplete(conn, gid)
        players = _ready_players(conn, gid)
        result = casting.recommend(players, pkg, force=bool(force))
        conn.execute("DELETE FROM assignments WHERE game_id = ? AND approval_state = 'draft'", (gid,))
        if not result["problems"]:
            for pid, rid in result["assignments"].items():
                forced = result["forced"].get(pid)
                conn.execute("INSERT INTO assignments (game_id, player_id, role_id, source, forced_conflicts) "
                             "VALUES (?,?,?,?,?)", (gid, pid, rid, "forced" if forced else "recommended",
                                                    json.dumps(forced) if forced else None))
        conn.execute("UPDATE games SET phase = 'CASTING', casting_problems = ?, casting_computed_at = ? WHERE id = ?",
                     (json.dumps(result["problems"]), auth.now(), gid))
        _audit(conn, gid, user, "casting.recommend.forced" if force else "casting.recommend",
               problems=result["problems"], forced=result["forced"],
               assignments=result["assignments"] if not result["problems"] else {})
    return get_casting(conn, user, gid)


def _current_assignments(conn, gid):
    rows = conn.execute("SELECT player_id, role_id, forced_conflicts FROM assignments WHERE game_id = ?",
                        (gid,)).fetchall()
    return ({r["player_id"]: r["role_id"] for r in rows},
            {r["player_id"]: json.loads(r["forced_conflicts"]) for r in rows if r["forced_conflicts"]})


def _check_no_incomplete(conn, gid):
    waiting = conn.execute(
        "SELECT u.display_name FROM players p JOIN users u ON u.id = p.identity_id "
        "WHERE p.game_id = ? AND p.rsvp = 'accepted' AND p.assessment_status != 'complete'", (gid,)).fetchall()
    if waiting:
        names = ", ".join(r["display_name"] for r in waiting)
        raise ApiError(409, f"Waiting for questionnaires from: {names}.")


def get_casting(conn, user, gid):
    g = _require_host(conn, user, gid)
    pkg = _package(g)
    players = _ready_players(conn, gid)
    matrix = casting.compatibility(players, pkg)
    rows = {r["player_id"]: r for r in conn.execute("SELECT * FROM assignments WHERE game_id = ?", (gid,))}
    out_players = []
    for p in players:
        a = rows.get(p["id"])
        out_players.append({
            "id": p["id"], "name": p["name"], "scores": p["scores"], "boundaries": p["boundaries"],
            "preparation_minutes": p["preparation_minutes"], "assistance": p["assistance"],
            "stretch_preference": p["stretch_preference"],
            "assignment": {"role_id": a["role_id"], "approval_state": a["approval_state"], "source": a["source"],
                           "forced_conflicts": json.loads(a["forced_conflicts"]) if a["forced_conflicts"] else []}
            if a else None,
            "options": [{"role_id": rid, **cell} for rid, cell in matrix[p["id"]].items()],
        })
    computed = g["casting_computed_at"]
    stale = bool(computed) and (any(p["updated_at"] > computed for p in players)
                                or {p["id"] for p in players} != set(rows))
    current, forced = _current_assignments(conn, gid)
    live_problems = casting.validate_cast(players, current, pkg, forced) if rows else []
    return {
        "phase": g["phase"],
        "roles": [content.casting_role(pkg, rid) for rid in pkg.roles],
        "players": out_players,
        "recommendation_problems": json.loads(g["casting_problems"] or "[]"),
        "validation_problems": live_problems,
        "computed_at": computed,
        "stale": stale and g["phase"] == "CASTING",
    }


def override_assignment(conn, user, gid, player_id, role_id, force=False):
    """Move a guest to a role (swapping with whoever holds it). If either guest
    doesn't fit their new role, the server refuses unless force=True, in which
    case the specific conflicts the host accepted are recorded and audited."""
    g = _require_host(conn, user, gid)
    if g["phase"] != "CASTING":
        raise ApiError(409, "Overrides are only possible while reviewing a recommended cast.")
    pkg = _package(g)
    if role_id not in pkg.roles:
        raise ApiError(400, "Unknown role.")
    with transaction(conn):
        players = {p["id"]: p for p in _ready_players(conn, gid)}
        if player_id not in players:
            raise ApiError(404, "That guest isn't ready for casting.")
        current, forced = _current_assignments(conn, gid)
        if player_id not in current:
            raise ApiError(409, "Run the recommendation first.")
        p = players[player_id]
        proposed = dict(current)
        old_role = proposed[player_id]
        holder = next((pid for pid, rid in proposed.items() if rid == role_id and pid != player_id), None)
        proposed[player_id] = role_id
        moved_conflicts = {player_id: casting.hard_conflicts(p, pkg.roles[role_id], pkg.content_tags)}
        if holder:
            proposed[holder] = old_role
            moved_conflicts[holder] = casting.hard_conflicts(players[holder], pkg.roles[old_role], pkg.content_tags)
        needs_force = {pid: c for pid, c in moved_conflicts.items() if c}
        if needs_force and not force:
            parts = [f"{players[pid]['name']} as {pkg.roles[proposed[pid]]['name']}: " + "; ".join(c)
                     for pid, c in needs_force.items()]
            raise ApiError(409, "This doesn't fit: " + " | ".join(parts) + ". You can force it as host.",
                           can_force=True, conflicts={pid: c for pid, c in needs_force.items()})
        for pid in moved_conflicts:
            forced.pop(pid, None)
        forced.update(needs_force)
        problems = casting.validate_cast(list(players.values()), proposed, pkg, forced)
        if problems:
            raise ApiError(409, "That change would leave the cast invalid: " + "; ".join(problems))
        # Clear the moved rows first so UNIQUE(game_id, role_id) holds mid-swap.
        moved = list(moved_conflicts)
        conn.executemany("DELETE FROM assignments WHERE game_id = ? AND player_id = ?", [(gid, x) for x in moved])
        for x in moved:
            conn.execute("INSERT INTO assignments (game_id, player_id, role_id, source, forced_conflicts) "
                         "VALUES (?,?,?,?,?)", (gid, x, proposed[x], "forced" if x in needs_force else "override",
                                                json.dumps(needs_force[x]) if x in needs_force else None))
        _audit(conn, gid, user, "casting.override.forced" if needs_force else "casting.override",
               player_id=player_id, role_id=role_id, swapped_with=holder, forced=needs_force)
    return get_casting(conn, user, gid)


def approve_cast(conn, user, gid):
    g = _require_host(conn, user, gid)
    if g["phase"] != "CASTING":
        raise ApiError(409, "There is no recommended cast to approve.")
    pkg = _package(g)
    with transaction(conn):
        _check_no_incomplete(conn, gid)
        players = _ready_players(conn, gid)
        assignments, forced = _current_assignments(conn, gid)
        problems = casting.validate_cast(players, assignments, pkg, forced)
        if problems:
            raise ApiError(409, "Can't approve yet: " + "; ".join(problems), problems=problems)
        version = g["assignment_version"] + 1
        conn.execute("UPDATE assignments SET approval_state = 'approved', approved_by = ?, approved_at = ?, "
                     "assignment_version = ? WHERE game_id = ?", (user["id"], auth.now(), version, gid))
        conn.execute("UPDATE games SET phase = 'TRAINING', assignment_version = ? WHERE id = ?", (version, gid))
        conn.execute("UPDATE invitations SET status = 'closed' WHERE game_id = ? AND status = 'pending'", (gid,))
        _audit(conn, gid, user, "casting.approve", assignment_version=version, assignments=assignments,
               forced=forced)
    return {"phase": "TRAINING", "assignment_version": version}


# ------------------------------------------------------------------ characters

def my_character(conn, user, gid):
    g, p = _require_player(conn, user, gid)
    a = _approved_assignment(conn, gid, p["id"])
    if not a or not content.phase_reached(g["phase"], "TRAINING"):
        raise ApiError(409, "Your character hasn't been released yet. The host will approve the cast soon.")
    pkg = _package(g)
    view = content.private_role_for_player(pkg, a["role_id"], g["phase"])
    with transaction(conn):
        for c in view["briefing"]:
            conn.execute("INSERT INTO disclosure_receipts (game_id, player_id, claim_id, channel, "
                         "request_or_event_id, delivered_at) VALUES (?,?,?,?,?,?) ON CONFLICT DO NOTHING",
                         (gid, p["id"], c["claim_id"], "onboarding", c["rule_id"], auth.now()))
        acked = conn.execute("SELECT COUNT(*) FROM disclosure_receipts WHERE game_id = ? AND player_id = ? "
                             "AND acknowledged_at IS NOT NULL", (gid, p["id"])).fetchone()[0]
    for c in view["briefing"]:
        c.pop("rule_id")
    view["acknowledged"] = bool(view["briefing"]) and acked >= len(view["briefing"])
    view["assignment_version"] = a["assignment_version"]
    return view


def acknowledge_briefing(conn, user, gid):
    g, p = _require_player(conn, user, gid)
    if not _approved_assignment(conn, gid, p["id"]):
        raise ApiError(409, "Your character hasn't been released yet.")
    with transaction(conn):
        conn.execute("UPDATE disclosure_receipts SET acknowledged_at = ? WHERE game_id = ? AND player_id = ? "
                     "AND acknowledged_at IS NULL", (auth.now(), gid, p["id"]))
    return {"ok": True}


def cast_list(conn, user, gid):
    g, rel, _ = _access(conn, user, gid)
    if not content.phase_reached(g["phase"], "TRAINING"):
        raise ApiError(409, "Characters are revealed after the host approves the cast.")
    pkg = _package(g)
    rows = conn.execute("SELECT a.role_id, u.display_name FROM assignments a JOIN players p ON p.id = a.player_id "
                        "JOIN users u ON u.id = p.identity_id WHERE a.game_id = ? AND a.approval_state = 'approved'",
                        (gid,)).fetchall()
    return {"victim": pkg.victim,
            "cast": [{**content.public_role(pkg, r["role_id"]), "played_by": r["display_name"]} for r in rows]}


def character(conn, user, gid, role_id):
    """Private character sheet. Only that character's approved player (or the
    host, who knows the solution) may read it."""
    g, rel, p = _access(conn, user, gid)
    pkg = _package(g)
    if role_id not in pkg.roles:
        raise ApiError(404, "Character not found.")
    if rel == "player":
        a = _approved_assignment(conn, gid, p["id"])
        if not a or a["role_id"] != role_id:
            _audit(conn, gid, user, "denied.character", role_id=role_id)
            raise ApiError(403, "That character's file is private.")
        return my_character(conn, user, gid)
    return content.private_role_for_player(pkg, role_id, g["phase"])


def solution(conn, user, gid):
    g, rel, _ = _access(conn, user, gid)
    if rel != "host":
        _audit(conn, gid, user, "denied.solution")
        raise ApiError(403, "Only the host can see the solution.")
    return content.host_solution(_package(g))
