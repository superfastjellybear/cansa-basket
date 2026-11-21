"""Etape 1 : exploration de l'API FFBB pour le club CANSA (sud0006049).

Ce script ne touche pas au site. Il enregistre ce que renvoie l'API dans
ffbb-debug/ pour qu'on puisse ecrire ensuite le vrai script de resultats.
"""
import inspect
import json
import os
import traceback

OUT = "ffbb-debug"
os.makedirs(OUT, exist_ok=True)
log = {}


def dump(obj, depth=0):
    """Convertit n'importe quel objet renvoye par la bibliotheque en JSON."""
    if depth > 8:
        return repr(obj)[:200]
    if obj is None or isinstance(obj, (str, int, float, bool)):
        return obj
    if isinstance(obj, dict):
        return {str(k): dump(v, depth + 1) for k, v in list(obj.items())[:200]}
    if isinstance(obj, (list, tuple, set)):
        return [dump(v, depth + 1) for v in list(obj)[:60]]
    for meth in ("model_dump", "to_dict", "dict"):
        f = getattr(obj, meth, None)
        if callable(f):
            try:
                return dump(f(), depth + 1)
            except Exception:
                pass
    if hasattr(obj, "__dict__"):
        return {k: dump(v, depth + 1) for k, v in vars(obj).items() if not k.startswith("_")}
    return repr(obj)[:300]


def attempt(name, fn):
    try:
        res = fn()
        log[name] = {"ok": True, "result": dump(res)}
        return res
    except Exception as e:
        log[name] = {"ok": False, "error": f"{type(e).__name__}: {e}", "trace": traceback.format_exc()[-1500:]}
        return None


import ffbb_api_client_v3 as lib  # noqa: E402

log["lib_version"] = getattr(lib, "__version__", "?")
log["lib_exports"] = sorted(n for n in dir(lib) if not n.startswith("_"))

tokens = attempt("tokens", lambda: lib.TokenManager.get_tokens())
client = None
if tokens is not None:
    client = attempt("client", lambda: lib.FFBBAPIClientV3.create(
        api_bearer_token=tokens.api_token,
        meilisearch_bearer_token=tokens.meilisearch_token,
    ))

if client is not None:
    methods = {}
    for n in dir(client):
        if n.startswith("_"):
            continue
        f = getattr(client, n, None)
        if callable(f):
            try:
                methods[n] = str(inspect.signature(f))
            except Exception:
                methods[n] = "?"
    log["client_methods"] = methods

    # sous-clients eventuels (REST / Meilisearch)
    for attr in dir(client):
        if attr.startswith("_"):
            continue
        sub = getattr(client, attr, None)
        if sub is None or callable(sub) or isinstance(sub, (str, int, float, bool, dict, list)):
            continue
        sm = {}
        for n in dir(sub):
            if n.startswith("_"):
                continue
            f = getattr(sub, n, None)
            if callable(f):
                try:
                    sm[n] = str(inspect.signature(f))
                except Exception:
                    sm[n] = "?"
        if sm:
            log["sub_" + attr + "_methods"] = sm

    for q in ("CANSA", "Chateauneuf", "Châteauneuf"):
        attempt("search_organismes_" + q, lambda q=q: client.search_organismes(q))
    attempt("multi_search_CANSA", lambda: client.multi_search("CANSA"))
    attempt("search_rencontres_CANSA", lambda: client.search_rencontres("CANSA"))

    # Essais directs avec l'identifiant du club, selon les methodes disponibles
    for n in sorted(log.get("client_methods", {})):
        low = n.lower()
        if low.startswith("get_") and any(k in low for k in ("organisme", "club", "equipe", "engagement")):
            for arg in ("sud0006049", "SUD0006049", "0006049"):
                attempt(f"{n}({arg})", lambda n=n, arg=arg: getattr(client, n)(arg))

with open(os.path.join(OUT, "discovery.json"), "w", encoding="utf-8") as fh:
    json.dump(log, fh, ensure_ascii=False, indent=1, default=str)
print("ok, cles :", list(log))
