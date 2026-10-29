"""Pure scoring and money rules for The Hollow Order. No database, no I/O:
everything here is deterministic and unit-tested.

Money is whole pounds (integers). The estate always pays out exactly
`total` pounds between the surviving families, never a negative amount."""

POINTS = (3, 2, 1)


def tally(ballots):
    """ballots: list of pick lists [first, second, third] -> {character: points}."""
    out = {}
    for picks in ballots:
        for pts, cid in zip(POINTS, picks):
            out[cid] = out.get(cid, 0) + pts
    return out


def influence(inheritance_ballots, suspicion_ballots, order):
    """Hidden Cult Influence per person: inheritance points received minus suspicion
    points received (every suspicion point costs one influence point)."""
    gain, loss = tally(inheritance_ballots), tally(suspicion_ballots)
    return {c: gain.get(c, 0) - loss.get(c, 0) for c in order}


def ranking(scores, order):
    """Highest first; ties keep the stable character order."""
    return sorted(order, key=lambda c: (-scores.get(c, 0), order.index(c)))


def family_influence(infl, families):
    return {f: sum(infl.get(m, 0) for m in fam["members"]) for f, fam in families.items()}


def split(total, weights, order):
    """Share `total` in proportion to non-negative weights. Remainder pounds go to the
    largest fractional parts, ties in `order`. If every weight is zero, share equally."""
    keys = [k for k in order if k in weights]
    w = {k: max(0, weights[k]) for k in keys}
    if not keys:
        return {}
    if sum(w.values()) == 0:
        w = {k: 1 for k in keys}
    s = sum(w.values())
    base = {k: total * w[k] // s for k in keys}
    rest = total - sum(base.values())
    frac = sorted(keys, key=lambda k: (-(total * w[k] % s), keys.index(k)))
    for k in frac[:rest]:
        base[k] += 1
    return base


def apply_transfers(shares, transfers, eligible):
    """transfers: [(payer_family, recipient_family, amount)] already honoured.
    Transfers involving a non-eligible family are void (the surviving payer keeps
    the money). A payer can never pay out more than its own share: if it promised
    more, each of its payments is reduced pro rata. Returns (new shares, settled list)."""
    valid = [(p, r, a) for p, r, a in transfers if p in eligible and r in eligible and p != r and a > 0]
    out = {}
    for p, _, a in valid:
        out[p] = out.get(p, 0) + a
    settled = []
    for p, r, a in valid:
        if out[p] > shares.get(p, 0):
            a = a * shares.get(p, 0) // out[p]
        settled.append((p, r, a))
    result = dict(shares)
    for p, r, a in settled:
        result[p] -= a
        result[r] += a
    return result, settled


def projection(total, families, infl, transfers, order_families):
    """Provisional TV projection before the tribunal: all four families share, honoured
    deals applied, never shown below zero."""
    shares = split(total, family_influence(infl, families), order_families)
    shares, _ = apply_transfers(shares, transfers, set(families))
    return {f: max(0, v) for f, v in shares.items()}


def round_display(amount, step=100000):
    return int((amount + step // 2) // step * step)


def settle_wagers(shares, wagers, family_of, guilty, eligible, order):
    """wagers: {character: (side, amount)}. Losing stakes (capped so no family goes
    below zero) form a pot shared by winners in proportion to their stakes. With no
    winners, nobody pays. Wagers from eliminated families are void."""
    live = {c: (s, a) for c, (s, a) in wagers.items() if family_of[c] in eligible and a > 0}
    win = {c: a for c, (s, a) in live.items() if (s == "guilty") == guilty}
    lose = {c: a for c, (s, a) in live.items() if (s == "guilty") != guilty}
    result = dict(shares)
    if not win or not lose:
        return result, {"pot": 0, "won": {}, "lost": {}}
    # cap each family's losses at what it actually has
    lost = {}
    by_family = {}
    for c, a in lose.items():
        by_family.setdefault(family_of[c], []).append(c)
    for f, members in by_family.items():
        want = sum(lose[c] for c in members)
        have = max(0, result[f])
        for c in members:
            lost[c] = lose[c] if want <= have else lose[c] * have // want
    pot = sum(lost.values())
    won = split(pot, win, [c for c in order if c in win])
    for c, a in lost.items():
        result[family_of[c]] -= a
    for c, a in won.items():
        result[family_of[c]] += a
    return result, {"pot": pot, "won": won, "lost": lost}


def settle(total, families, infl, eliminated, transfers, wagers, family_of, guilty, order):
    """Final payouts: three surviving families share the estate by influence, then
    deals settle, then wagers. Returns {family: pounds} summing exactly to total."""
    fam_order = list(families)
    eligible = [f for f in fam_order if f != eliminated]
    shares = split(total, {f: v for f, v in family_influence(infl, families).items() if f in eligible}, eligible)
    shares, settled = apply_transfers(shares, transfers, set(eligible))
    shares, wager_result = settle_wagers(shares, wagers, family_of, guilty, set(eligible), order)
    shares[eliminated] = 0
    assert sum(shares.values()) == total and min(shares.values()) >= 0
    return {"payouts": {f: shares[f] for f in fam_order}, "deals": settled, "wagers": wager_result}


def eliminate(yes_votes, nominee, family_of, fam_infl, fam_order, threshold=4):
    """4 YES of 8 convicts: the nominee's family is disinherited. Otherwise the family with
    the lowest influence is (ties in family order)."""
    if nominee and yes_votes >= threshold:
        return nominee, family_of[nominee]
    low = sorted(fam_order, key=lambda f: (fam_infl.get(f, 0), fam_order.index(f)))[0]
    return None, low
