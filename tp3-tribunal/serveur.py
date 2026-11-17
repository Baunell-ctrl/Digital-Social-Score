"""Page dédiée du Tribunal : on soumet un message et on voit les agents travailler en direct.

    uvicorn serveur:app --port 8080      puis http://localhost:8080

POST /juger renvoie un flux SSE (text/event-stream) : un événement par étape du graphe
(pièces du Greffier, texte des agents au fil de l'eau, appels d'outils, verdict de l'Huissier).
"""
from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from google.adk.agents.run_config import RunConfig, StreamingMode
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types
from pydantic import BaseModel, Field

from audience import evenement_en_dict
from tribunal.agent import MODELE, dossier_redacteur, root_agent
from tribunal.outils import CASIER, DOSSIER, instruire

RACINE = Path(__file__).resolve().parent
POLICES = RACINE.parent / "src" / "api" / "static" / "fonts"   # mêmes polices que la page du Digital Social Score

app = FastAPI(title="Tribunal de la Bienséance", docs_url=None, redoc_url=None)
service = InMemorySessionService()
runner = Runner(agent=root_agent, app_name="tribunal", session_service=service)
AFFAIRES = json.loads((DOSSIER / "affaires.json").read_text(encoding="utf-8"))
JURIS = json.loads((DOSSIER / "jurisprudence.json").read_text(encoding="utf-8"))


class Saisine(BaseModel):
    texte: str = Field(min_length=1, max_length=5000)
    ref: str = Field(default="AFF-WEB", pattern=r"^(AFF|JUR)-[\w-]{1,12}$")
    auteur: str = Field(default="C-0000", pattern=r"^C-\d{4}$")
    destinataire: str | None = Field(default=None, pattern=r"^G-\d{2}$")
    date_signalement: str = Field(default_factory=lambda: date.today().isoformat(), pattern=r"^\d{4}-\d{2}-\d{2}$")


@app.get("/", include_in_schema=False)
def page() -> FileResponse:
    return FileResponse(RACINE / "static" / "tribunal.html", media_type="text/html; charset=utf-8")


@app.get("/dossier")
def dossier() -> dict:
    """Les affaires et la jurisprudence chargeables dans la page, et les identifiants connus (sans pseudo)."""
    return {
        "modele": MODELE,
        "affaires": [{k: a[k] for k in ("ref", "auteur", "destinataire", "date_signalement", "page", "texte")}
                     for a in AFFAIRES],
        "jurisprudence": [{"ref": j["ref"], "titre": j["titre"], "texte": j["texte"],
                           "attendu": j["decision"]} for j in JURIS],
        "gardiens": sorted(i for i, f in CASIER.items() if f["role"] == "gardien"),
    }


def _sse(type_: str, donnees: dict) -> str:
    return f"event: {type_}\ndata: {json.dumps(donnees, ensure_ascii=False, default=str)}\n\n"


def _noeud(chemin: str | None) -> str | None:
    """« tribunal@1/procureur@1 » → « procureur »."""
    return re.sub(r"@\d+$", "", chemin.split("/")[-1]) if chemin else None


@app.post("/juger")
async def juger(saisine: Saisine) -> StreamingResponse:
    affaire = saisine.model_dump()
    try:
        pieces = instruire(affaire)
    except RuntimeError as erreur:          # Digital Social Score injoignable
        raise HTTPException(status_code=502, detail=str(erreur)) from erreur

    async def flux():
        yield _sse("pieces", pieces)
        etat0 = {"pieces": json.dumps(pieces, ensure_ascii=False), "affaire": affaire,
                 "dossier_redacteur": dossier_redacteur(pieces), "dossier_sortie": "sortie/web"}
        session = await service.create_session(app_name="tribunal", user_id="page", state=etat0)
        msg = types.Content(role="user", parts=[types.Part(
            text=f"Affaire {affaire['ref']}.\n<message_a_juger>\n{affaire['texte']}\n</message_a_juger>")])
        try:
            async for event in runner.run_async(user_id="page", session_id=session.id, new_message=msg,
                                                run_config=RunConfig(streaming_mode=StreamingMode.SSE)):
                e = evenement_en_dict(affaire["ref"], event)
                e["agent"] = _noeud(e["noeud"]) or e["auteur"]
                e["partiel"] = bool(event.partial)
                yield _sse("etape", e)
            fin = await service.get_session(app_name="tribunal", user_id="page", session_id=session.id)
            yield _sse("verdict", fin.state["verdict"])
        except Exception as erreur:  # noqa: BLE001 : l'erreur est affichée dans la page
            yield _sse("erreur", {"message": f"{type(erreur).__name__} : {str(erreur)[:300]}"})

    return StreamingResponse(flux(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


if POLICES.is_dir():
    app.mount("/fonts", StaticFiles(directory=POLICES), name="fonts")
