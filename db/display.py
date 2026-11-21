"""
db/display.py  -  how PDL text is shown. PDL sends names, titles and companies in lowercase.
The database keeps exactly what PDL sent, and the rules read titles in lowercase anyway, so
this only changes what people see. Text that already has capitals (e.g. a title typed in the
app) is left as it is.
"""
import re

SMALL_WORDS = {"a", "an", "and", "at", "by", "for", "from", "in", "of", "on", "or", "per",
               "the", "to", "via", "with"}
SPECIAL = {w: w.upper() for w in (
    "abm ai alm amer anz apac apm avp bp ceo cfo chro cio ciso cmo coo cpo cro cto cx dei emea "
    "esg evp ge gm gtm gui hr ic ii iii itsm latam mba mfa ml mts pmo qa rvp sdr sem svp tlm "
    "ui us ux vp ww").split()}
SPECIAL.update({"bps": "BPs", "sdrs": "SDRs", "devops": "DevOps", "r&d": "R&D", "g&a": "G&A",
                "u.s.": "U.S."})
TOKEN = re.compile(r"[a-z0-9.'&]*[a-z0-9&][a-z0-9.'&]*(?:-[a-z0-9.'&]+)*")


def _word(w):
    core = w.rstrip("'")
    if core in SPECIAL:
        return SPECIAL[core] + w[len(core):]
    return core[:1].upper() + core[1:] + w[len(core):]


def proper_title(text):
    """'chief people and ai enablement officer' -> 'Chief People and AI Enablement Officer'.
    Also used for company and industry names."""
    if not isinstance(text, str) or text != text.lower():
        return text
    first = True

    def fix(m):
        nonlocal first
        w = m.group(0)
        out = w if w in SMALL_WORDS and not first else "-".join(map(_word, w.split("-")))
        first = False
        return out

    out = TOKEN.sub(fix, text)            # only the case changes, so the rules read it the same
    return re.sub(r"\bG & a\b", "G & A", out)


def proper_name(text):
    """'mike cannon-brookes' -> 'Mike Cannon-Brookes', "o'brien" -> "O'Brien"."""
    if not isinstance(text, str) or text != text.lower():
        return text
    out = re.sub(r"(^|[\s'-])([a-z])", lambda m: m.group(1) + m.group(2).upper(), text.strip())
    return re.sub(r"\bMc([a-z])", lambda m: "Mc" + m.group(1).upper(), out)


def apply(series, is_pdl, fn):
    """A copy of series with fn applied to the rows where is_pdl is true."""
    out = series.astype(object).copy()
    out[is_pdl] = series[is_pdl].map(fn)
    return out
