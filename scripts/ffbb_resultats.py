"""Met a jour public/resultats.json avec les classements et scores FFBB du club CANSA.

Lance automatiquement par GitHub Actions (.github/workflows/resultats.yml).
Ecrit aussi ffbb-debug/last-run.json pour diagnostiquer en cas de souci.
"""
import json
import os
import re
import traceback
import unicodedata
from datetime import datetime, timezone

CLUB_CODE = "sud0006049"
CLUB_URL = "https://competitions.ffbb.com/ligues/sud/comites/0006/clubs/sud0006049"
OUT = os.environ.get("RESULTS_OUT", "public/resultats.json")
DEBUG_DIR = os.environ.get("RESULTS_DEBUG_DIR", "ffbb-debug")
os.makedirs(DEBUG_DIR, exist_ok=True)
debug = {"steps": []}


def note(*a):
    msg = " ".join(str(x) for x in a)
    print(msg)
    debug["steps"].append(msg)


def norm(s):
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def g(obj, *names, default=None):
    """Lit un attribut ou une cle, quel que soit le format renvoye par la bibliotheque."""
    for n in names:
        if obj is None:
            return default
        v = obj.get(n) if isinstance(obj, dict) else getattr(obj, n, None)
        if v is not None:
            return v
    return default


def iso(d):
    if d is None:
        return None
    if isinstance(d, datetime):
        return d.isoformat()
    return str(d)


def to_int(x):
    try:
        return int(str(x).strip())
    except Exception:
        return None


PARTICULES = {"de", "du", "des", "la", "le", "les", "d", "l", "sur", "et", "en", "a"}


def joli_nom(nom):
    """'US GRASSOISE - 2' -> 'US Grassoise 2', '... (SPCOC BASKET)' -> 'SPCOC Basket'."""
    nom = str(nom or "").strip()
    m = re.search(r"\(([^)]+)\)", nom)
    if m:
        nom = m.group(1)
    num = ""
    m = re.search(r"\s*-\s*(\d+)\s*$", nom)
    if m:
        nom = nom[: m.start()]
        num = "" if m.group(1) == "1" else " " + m.group(1)
    if not nom.isupper():
        return nom + num
    mots = []
    for i, w in enumerate(nom.split()):
        lw = w.lower()
        if i > 0 and lw in PARTICULES:
            mots.append(lw)
        elif lw in PARTICULES:
            mots.append(w.capitalize())
        elif len(w) <= 2 or (len(w) <= 5 and not re.search(r"[AEIOUY]", w)) or w.isdigit():
            mots.append(w)
        elif w in ("CANSA", "SPCOC", "CDA", "ASPTT", "ESBNB"):
            mots.append(w)
        else:
            mots.append("-".join(x.capitalize() for x in w.split("-")))
    return " ".join(mots) + num


def main():
    from ffbb_data_client import FFBBDataClient

    client = FFBBDataClient.create()

    # 1. Retrouver l'identifiant interne du club a partir de son code
    club = None
    for q in ("CANSA", "Chateauneuf", "Châteauneuf"):
        res = client.search_organismes(q, limit=40)
        hits = g(res, "hits", default=[]) or []
        note("recherche", q, "->", [(g(h, "nom"), g(h, "code"), g(h, "id")) for h in hits][:15])
        for h in hits:
            if str(g(h, "code", default="")).lower() == CLUB_CODE:
                club = h
                break
        if club:
            break
    if not club:
        raise RuntimeError("Club " + CLUB_CODE + " introuvable")
    club_id = int(g(club, "id"))
    club_nom = g(club, "nom", default="")
    note("club", club_id, club_nom)

    # 2. Equipes engagees
    org = client.get_organisme(club_id)
    engagements = g(org, "engagements", default=[]) or []
    note("engagements", len(engagements))

    by_key = {}
    for e in engagements:
        comp = g(e, "idCompetition")
        poule_id = g(g(e, "idPoule"), "id")
        comp_nom = g(comp, "nom", default="") or ""
        cat_code = g(g(comp, "categorie"), "code", default="") or ""
        num = str(g(e, "numeroEquipe", default="1") or "1")
        note("engagement", comp_nom, "| cat", cat_code, "| equipe", num, "| poule", poule_id)
        if not poule_id:
            continue
        try:
            poule = client.get_poule(int(poule_id))
        except Exception as ex:
            note("  poule KO", poule_id, ex)
            continue

        # Classement de notre equipe dans la poule
        rank = None
        my_names = set()
        standings = []
        for r in g(poule, "classements", "classement", default=[]) or []:
            us = str(g(r, "organisme_id", default="")) == str(club_id)
            if us and rank is None:
                rank = r
                my_names.add(norm(g(r, "organisme_nom", default="")))
            standings.append({
                "pos": to_int(g(r, "position")),
                "team": joli_nom(g(r, "organisme_nom", default="")),
                "played": to_int(g(r, "match_joues")),
                "won": to_int(g(r, "gagnes")),
                "lost": to_int(g(r, "perdus")),
                "points": to_int(g(r, "points")),
                "diff": to_int(g(r, "difference")),
                "us": us,
            })
        standings.sort(key=lambda x: (x["pos"] is None, x["pos"] or 0))

        def is_us(name):
            n = norm(name)
            if not n:
                return False
            if any(m and n.startswith(m) for m in my_names):
                return True
            return "cansa" in n or "chateauneuf" in n

        last, nxt = None, None
        matches = []
        for m in g(poule, "rencontres", default=[]) or []:
            n1, n2 = g(m, "nomEquipe1", default=""), g(m, "nomEquipe2", default="")
            home, away = is_us(n1), is_us(n2)
            if not (home or away):
                continue
            d = g(m, "date_rencontre")
            s1, s2 = to_int(g(m, "resultatEquipe1")), to_int(g(m, "resultatEquipe2"))
            played = bool(to_int(g(m, "joue"))) and s1 is not None and s2 is not None
            item = {"date": iso(d), "home": joli_nom(n1), "away": joli_nom(n2), "cansaHome": bool(home)}
            if played:
                item.update(scoreHome=s1, scoreAway=s2)
            matches.append(dict(item, day=to_int(g(m, "numeroJournee"))))
            if played:
                if last is None or str(item["date"]) > str(last["date"]):
                    last = item
            else:
                if item["date"] and (nxt is None or str(item["date"]) < str(nxt["date"])):
                    if str(item["date"]) >= datetime.now().isoformat()[:10]:
                        nxt = item

        if "loisir" in norm(comp_nom):
            label = "Loisirs"
        elif re.match(r"^u\d+", cat_code.lower()):
            label = cat_code.upper() + ("" if num in ("1", "") else "-" + num)
        else:
            label = "Séniors" + ("" if num in ("1", "") else " " + num)

        team = {
            "cat": label,
            "competition": re.sub(r"\bu(\d+)", lambda m: "U" + m.group(1), comp_nom.strip().capitalize()) if comp_nom.isupper() else comp_nom.strip(),
            "poule": g(poule, "nom", default="") or "",
            "rank": to_int(g(rank, "position")) if rank else None,
            "points": to_int(g(rank, "points")) if rank else None,
            "won": to_int(g(rank, "gagnes")) if rank else None,
            "lost": to_int(g(rank, "perdus")) if rank else None,
            "last": last,
            "next": nxt,
            "standings": standings,
            "matches": sorted(matches, key=lambda x: str(x["date"] or "")),
        }
        # Pour une meme equipe (phase 1 puis phase 2), on garde la phase en cours
        activity = (nxt["date"] if nxt else "") or (last["date"] if last else "") or ""
        key = label
        prev = by_key.get(key)
        if prev is None or (bool(nxt), activity) > (bool(prev[1]["next"]), prev[0]):
            by_key[key] = (activity, team)
        note("  ->", label, "rang", team["rank"], "dernier", bool(last), "prochain", bool(nxt))

    order = ["U7", "U9", "U11", "U11-2", "U13", "U13-2", "U15", "U15-2", "U17", "U18", "U18-2", "U20", "Séniors", "Séniors 2", "Loisirs"]
    teams = [t for _, t in by_key.values()]
    teams.sort(key=lambda t: (order.index(t["cat"]) if t["cat"] in order else 99, t["cat"]))
    if not teams:
        raise RuntimeError("Aucune equipe trouvee, fichier non modifie")

    old = {}
    if os.path.exists(OUT):
        with open(OUT, encoding="utf-8") as fh:
            old = json.load(fh)
    if old.get("teams") == teams:
        note("Aucun changement")
        return
    data = {"updated": datetime.now(timezone.utc).isoformat(timespec="seconds"), "source": "FFBB", "clubUrl": CLUB_URL, "teams": teams}
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=1)
    note("resultats.json mis a jour,", len(teams), "equipes")


if __name__ == "__main__":
    ok = True
    try:
        main()
    except Exception:
        ok = False
        debug["error"] = traceback.format_exc()
        print(debug["error"])
    with open(os.path.join(DEBUG_DIR, "last-run.json"), "w", encoding="utf-8") as fh:
        json.dump(debug, fh, ensure_ascii=False, indent=1)
    if not ok:
        raise SystemExit(1)
