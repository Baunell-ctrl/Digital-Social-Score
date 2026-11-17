"""L'audience : juge les 40 affaires (ou les 6 cas de jurisprudence) avec le graphe d'agents.

    python audience.py                       # les 40 affaires → sortie/
    python audience.py --refs AFF-038,AFF-039,AFF-040
    python audience.py --jurisprudence       # les 6 cas tranchés, comparés à la décision publiée
    python audience.py --jurisprudence --scores reference   # avec les scores toxic-bert du dossier
"""
from __future__ import annotations

import argparse
import asyncio
import json
import time
from pathlib import Path

from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from tribunal.agent import dossier_redacteur, root_agent
from tribunal.outils import DOSSIER, SCORES_REFERENCE, instruire

RACINE = Path(__file__).resolve().parent
service = InMemorySessionService()
runner = Runner(agent=root_agent, app_name="tribunal", session_service=service)


def evenement_en_dict(ref: str, event) -> dict:
    """Résumé lisible d'un événement ADK : qui parle, quels outils, quel texte, combien de jetons."""
    parts = (event.content.parts if event.content and event.content.parts else [])
    usage = event.usage_metadata
    return {
        "ref": ref, "t": round(time.time(), 3), "auteur": event.author,
        "noeud": event.node_info.path if getattr(event, "node_info", None) else None,
        "texte": "".join(p.text for p in parts if p.text and not p.thought) or None,
        "appels_outils": [{"nom": p.function_call.name, "args": p.function_call.args} for p in parts if p.function_call],
        "reponses_outils": [{"nom": p.function_response.name, "reponse": p.function_response.response}
                            for p in parts if p.function_response],
        "etat_modifie": sorted((event.actions.state_delta or {}).keys()) if event.actions else [],
        "jetons": {"entree": usage.prompt_token_count, "sortie": usage.candidates_token_count,
                   "total": usage.total_token_count} if usage else None,
    }


async def juger(affaire: dict, sortie: Path, scores: dict | None = None, trace=None) -> dict:
    """Le Greffier instruit, une session est créée avec les pièces, le graphe s'exécute, l'Huissier conclut."""
    pieces = instruire(affaire, scores=scores)
    etat0 = {"pieces": json.dumps(pieces, ensure_ascii=False), "affaire": affaire,
             "dossier_redacteur": dossier_redacteur(pieces), "dossier_sortie": str(sortie.relative_to(RACINE))}
    session = await service.create_session(app_name="tribunal", user_id="csbn", state=etat0)
    texte = f"Affaire {affaire['ref']}.\n<message_a_juger>\n{affaire['texte']}\n</message_a_juger>"
    msg = types.Content(role="user", parts=[types.Part(text=texte)])
    t0 = time.perf_counter()
    async for event in runner.run_async(user_id="csbn", session_id=session.id, new_message=msg):
        if trace is not None:
            trace.write(json.dumps(evenement_en_dict(affaire["ref"], event), ensure_ascii=False, default=str) + "\n")
            trace.flush()
    fin = await service.get_session(app_name="tribunal", user_id="csbn", session_id=session.id)
    return {**fin.state["verdict"], "duree_s": round(time.perf_counter() - t0, 1)}


def affaires_jurisprudence() -> list[dict]:
    jur = json.loads((DOSSIER / "jurisprudence.json").read_text(encoding="utf-8"))
    return [{"ref": j["ref"], "auteur": "C-0000", "date_signalement": "2026-11-13", "destinataire": None,
             "texte": j["texte"], "attendu": j["decision"]} for j in jur]


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--jurisprudence", action="store_true", help="juger les 6 cas de jurisprudence")
    ap.add_argument("--refs", help="références à juger, séparées par des virgules")
    ap.add_argument("--scores", choices=["api", "reference"], default="api",
                    help="api : notre Digital Social Score ; reference : scores toxic-bert du dossier (jurisprudence)")
    args = ap.parse_args()

    if args.jurisprudence:
        affaires, sortie = affaires_jurisprudence(), RACINE / "sortie" / "jurisprudence"
    else:
        affaires = json.loads((DOSSIER / "affaires.json").read_text(encoding="utf-8"))
        sortie = RACINE / "sortie"
    if args.refs:
        voulues = set(args.refs.split(","))
        affaires = [a for a in affaires if a["ref"] in voulues]

    sortie.mkdir(parents=True, exist_ok=True)
    for nom in ("decisions.jsonl", "attente_gardiens.jsonl", "trace.jsonl"):
        (sortie / nom).unlink(missing_ok=True)       # chaque audience repart de fichiers vides

    with open(sortie / "trace.jsonl", "w", encoding="utf-8") as trace:
        for a in affaires:
            scores = ({k: float(v) for k, v in SCORES_REFERENCE[a["ref"]].items()}
                      if args.scores == "reference" and a["ref"] in SCORES_REFERENCE else None)
            v = await juger(a, sortie, scores, trace)
            ok = ""
            if "attendu" in a:
                bon = (v["niveau_retenu"], v["sanction"]) == (a["attendu"]["niveau"], a["attendu"]["sanction"])
                ok = f"  attendu {a['attendu']['niveau']}/{a['attendu']['sanction']} {'✔' if bon else '✘'}"
            print(f"{a['ref']}  présumé {v['niveau_presume']}  {','.join(v['circonstances']) or '-':<14} → "
                  f"{v['niveau_retenu']} {v['sanction']} {','.join(v['mesures']) or '-':<10} {v['statut']:<19}"
                  f" {v['duree_s']:>5} s{ok}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
