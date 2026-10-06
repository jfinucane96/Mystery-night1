"""Assessment scoring (spec §4). Scores estimate desired participation; they
are never shown to players."""

BOUNDARY_VALUES = ("allowed", "blocked", "unspecified")


def score(assessment, answers):
    """answers: {question_id: option_id}. Returns {dimension: 0-100} plus
    'involvement'. Raises ValueError if incomplete or invalid."""
    smap = assessment["scoring_map"]
    by_dim = {d: [] for d in assessment["dimensions"]}
    for q in assessment["questions"]:
        opt = answers.get(q["id"])
        if opt not in smap:
            raise ValueError(f"question {q['id']} unanswered")
        by_dim[q["dimension"]].append(smap[opt])
    scores = {d: round(sum(v) / len(v)) for d, v in by_dim.items()}
    scores["involvement"] = involvement(scores)
    return scores


def involvement(s):
    return round(0.4 * s["commitment"]
                 + 0.3 * max(s["investigation"], s["mischief"])
                 + 0.3 * max(s["spotlight"], s["performance"], s["deception"]))


def validate_answer(assessment, question_id, option_id):
    for q in assessment["questions"]:
        if q["id"] == question_id:
            if option_id not in {o["id"] for o in q["options"]}:
                raise ValueError("invalid option")
            return
    raise ValueError("unknown question")


def validate_preferences(assessment, tag_ids, boundaries, preparation_minutes, assistance, stretch):
    fields = assessment["preference_fields"]
    if set(boundaries) != set(tag_ids):
        raise ValueError("boundaries must cover exactly the package's content tags")
    for v in boundaries.values():
        if v not in BOUNDARY_VALUES:
            raise ValueError("boundary values must be allowed, blocked or unspecified")
    pm = fields["preparation_minutes"]
    if not isinstance(preparation_minutes, int) or not pm["min"] <= preparation_minutes <= pm["max"]:
        raise ValueError(f"preparation_minutes must be {pm['min']}-{pm['max']}")
    if assistance not in fields["assistance"]:
        raise ValueError("invalid assistance preference")
    if stretch not in fields["stretch_preference"]:
        raise ValueError("invalid stretch preference")
