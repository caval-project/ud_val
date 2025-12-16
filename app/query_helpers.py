from sqlalchemy import text, bindparam
from .extensions import db


def _fetch_translit_for_arg_lemmas(lemmas, *, vlemma=None, vgloss=None):
    """
    Map language argument lemmas -> transliterated argument lemmas.
    Optionally scope results to a specific head verb sense (vlemma + optional vgloss).
    """
    if not lemmas:
        return {}

    sense_join = ""
    sense_where = []
    params = {"L": list(lemmas)}

    if vlemma:
        sense_join = "JOIN verbs vv ON vv.sent_id = a.sent_id AND vv.token_id = a.head_id"
        sense_where.append("vv.lemma = :vlemma")
        params["vlemma"] = vlemma
        if vgloss:
            sense_where.append("vv.gloss = :vgloss")
            params["vgloss"] = vgloss

    q = text(
        f"""
        SELECT DISTINCT a.lemma, a.translit_lemma
        FROM arguments a
        {sense_join}
        WHERE a.lemma IN :L
        {(' AND ' + ' AND '.join(sense_where)) if sense_where else ''}
        """
    ).bindparams(bindparam("L", expanding=True))

    rows = db.session.execute(q, params).fetchall()
    return {r.lemma: r.translit_lemma for r in rows if r.translit_lemma}


def _fetch_translit_for_dep_bits(dep_bits, *, vlemma=None, vgloss=None):
    """
    Map language dependency "bit" (right-hand side of case_value) -> translit_dep_lemma.
    Optionally scope results to a specific head verb sense (vlemma + optional vgloss).
    """
    if not dep_bits:
        return {}

    sense_join = ""
    sense_where = []
    params = {"L": list(dep_bits)}

    if vlemma:
        sense_join = "JOIN verbs vv ON vv.sent_id = a.sent_id AND vv.token_id = a.head_id"
        sense_where.append("vv.lemma = :vlemma")
        params["vlemma"] = vlemma
        if vgloss:
            sense_where.append("vv.gloss = :vgloss")
            params["vgloss"] = vgloss

    q = text(
        f"""
        SELECT DISTINCT
            TRIM(SUBSTRING_INDEX(a.case_value, '+', -1)) AS dep_bit_arm,
            a.translit_dep_lemma
        FROM arguments a
        {sense_join}
        WHERE TRIM(SUBSTRING_INDEX(a.case_value, '+', -1)) IN :L
        {(' AND ' + ' AND '.join(sense_where)) if sense_where else ''}
        """
    ).bindparams(bindparam("L", expanding=True))

    rows = db.session.execute(q, params).fetchall()
    return {r.dep_bit_arm: r.translit_dep_lemma for r in rows if r.translit_dep_lemma}


def _fetch_arm_for_arg_tlemmas(tlemmas, *, vlemma=None, vgloss=None):
    """
    Map transliterated argument lemmas -> language argument lemmas.
    Optionally scope results to a specific head verb sense (vlemma + optional vgloss).
    """
    if not tlemmas:
        return {}

    sense_join = ""
    sense_where = []
    params = {"L": list(tlemmas)}

    if vlemma:
        sense_join = "JOIN verbs vv ON vv.sent_id = a.sent_id AND vv.token_id = a.head_id"
        sense_where.append("vv.lemma = :vlemma")
        params["vlemma"] = vlemma
        if vgloss:
            sense_where.append("vv.gloss = :vgloss")
            params["vgloss"] = vgloss

    q = text(
        f"""
        SELECT DISTINCT a.translit_lemma, a.lemma
        FROM arguments a
        {sense_join}
        WHERE a.translit_lemma IN :L
        {(' AND ' + ' AND '.join(sense_where)) if sense_where else ''}
        """
    ).bindparams(bindparam("L", expanding=True))

    rows = db.session.execute(q, params).fetchall()
    return {r.translit_lemma: r.lemma for r in rows if r.lemma}


def _fetch_arm_for_dep_tbits(tbits, *, vlemma=None, vgloss=None):
    """
    Map translit_dep_lemma -> Language dependency "bit" (right-hand side of case_value).
    Optionally scope results to a specific head verb sense (vlemma + optional vgloss).
    """
    if not tbits:
        return {}

    sense_join = ""
    sense_where = []
    params = {"L": list(tbits)}

    if vlemma:
        sense_join = "JOIN verbs vv ON vv.sent_id = a.sent_id AND vv.token_id = a.head_id"
        sense_where.append("vv.lemma = :vlemma")
        params["vlemma"] = vlemma
        if vgloss:
            sense_where.append("vv.gloss = :vgloss")
            params["vgloss"] = vgloss

    q = text(
        f"""
        SELECT DISTINCT
            a.translit_dep_lemma,
            TRIM(SUBSTRING_INDEX(a.case_value, '+', -1)) AS dep_bit_arm
        FROM arguments a
        {sense_join}
        WHERE a.translit_dep_lemma IN :L
        {(' AND ' + ' AND '.join(sense_where)) if sense_where else ''}
        """
    ).bindparams(bindparam("L", expanding=True))

    rows = db.session.execute(q, params).fetchall()
    return {r.translit_dep_lemma: r.dep_bit_arm for r in rows if r.dep_bit_arm}


def _fetch_case_values_for_tbits(tbits, *, dep_rel=None, tlemma=None, vlemma=None, vgloss=None):
    """
    For each translit_dep_lemma in tbits, fetch the set of matching case_value strings.
    Optional filters: dep_rel, translit_lemma (tlemma), and head verb sense (vlemma/vgloss).
    """
    if not tbits:
        return {}

    conditions = ["a.translit_dep_lemma IN :L"]
    params = {"L": list(tbits)}
    joins = ""

    if dep_rel:
        conditions.append("a.dep_rel = :dep_rel")
        params["dep_rel"] = dep_rel

    if tlemma:
        conditions.append("a.translit_lemma = :tlemma")
        params["tlemma"] = tlemma

    if vlemma:
        joins += " JOIN verbs vv ON vv.sent_id = a.sent_id AND vv.token_id = a.head_id"
        conditions.append("vv.lemma = :vlemma")
        params["vlemma"] = vlemma
        if vgloss:
            conditions.append("vv.gloss = :vgloss")
            params["vgloss"] = vgloss

    q = text(
        f"""
        SELECT DISTINCT a.translit_dep_lemma, a.case_value
        FROM arguments a
        {joins}
        WHERE {' AND '.join(conditions)}
        """
    ).bindparams(bindparam("L", expanding=True))

    rows = db.session.execute(q, params).fetchall()
    out = {}
    for r in rows:
        out.setdefault(r.translit_dep_lemma, set()).add(r.case_value)
    return out


def _fetch_tbits_from_full_case_values(case_values, *, dep_rel=None, tlemma=None, vlemma=None, vgloss=None):
    """
    Inverse lookup: for each full case_value, fetch the set of matching translit_dep_lemma values.
    Optional filters: dep_rel, translit_lemma (tlemma), and head verb sense (vlemma/vgloss).
    """
    if not case_values:
        return {}

    conds = ["a.case_value IN :L"]
    params = {"L": list(case_values)}
    joins = ""

    if dep_rel:
        conds.append("a.dep_rel = :dep_rel")
        params["dep_rel"] = dep_rel

    if tlemma:
        conds.append("a.translit_lemma = :tlemma")
        params["tlemma"] = tlemma

    if vlemma:
        joins += " JOIN verbs vv ON vv.sent_id = a.sent_id AND vv.token_id = a.head_id"
        conds.append("vv.lemma = :vlemma")
        params["vlemma"] = vlemma
        if vgloss:
            conds.append("vv.gloss = :vgloss")
            params["vgloss"] = vgloss

    q = text(
        f"""
        SELECT DISTINCT a.case_value, a.translit_dep_lemma
        FROM arguments a
        {joins}
        WHERE {' AND '.join(conds)}
        """
    ).bindparams(bindparam("L", expanding=True))

    rows = db.session.execute(q, params).fetchall()
    out = {}
    for r in rows:
        if r.translit_dep_lemma:
            out.setdefault(r.case_value, set()).add(r.translit_dep_lemma)
    return out
