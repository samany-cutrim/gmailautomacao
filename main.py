"""
FA Law — Gmail Automação — API (Render)
========================================
Expõe a automação via HTTP para ser disparada por agendador externo
(cron-job.org) a cada 30 minutos.

Endpoints:
    GET  /             → status do serviço
    GET  /health       → healthcheck
    POST /run          → executa a automação (protegido por token)
"""

import os
import logging

from fastapi import FastAPI, HTTPException, Header
from fastapi.responses import JSONResponse

import automacao

log = logging.getLogger(__name__)

app = FastAPI(
    title="FA Law — Gmail Automação",
    description="Classificação automática de emails com IA",
    version="1.0.0",
)

RUN_TOKEN = os.environ.get("RUN_TOKEN", "")


@app.get("/")
def root():
    return {
        "servico": "FA Law — Gmail Automação",
        "status": "online",
        "modo": "todos os usuários" if automacao.CONFIG["RUN_FOR_ALL_USERS"] else "apenas admin (teste)",
    }


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/run")
def run(x_run_token: str = Header(default="")):
    """
    Executa a automação. Protegido por token no header X-Run-Token.
    Configure o cron-job.org para chamar este endpoint com o header.
    """
    if not RUN_TOKEN:
        raise HTTPException(status_code=500, detail="RUN_TOKEN não configurado no servidor.")

    if x_run_token != RUN_TOKEN:
        raise HTTPException(status_code=401, detail="Token inválido.")

    try:
        resumo = automacao.executar()
        return JSONResponse(content=resumo)
    except Exception as e:
        log.error(f"Erro na execução: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/remove-labels")
def remove_labels(x_run_token: str = Header(default="")):
    """
    Remove todos os marcadores Falaw de todos os emails de todos os usuários.
    Protegido pelo mesmo token X-Run-Token.
    """
    if not RUN_TOKEN:
        raise HTTPException(status_code=500, detail="RUN_TOKEN não configurado no servidor.")

    if x_run_token != RUN_TOKEN:
        raise HTTPException(status_code=401, detail="Token inválido.")

    try:
        resumo = automacao.remover_todos_marcadores()
        return JSONResponse(content=resumo)
    except Exception as e:
        log.error(f"Erro ao remover marcadores: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/remove-labels/{token}")
def remove_labels_get(token: str):
    """
    Remove marcadores via GET — pode ser aberto direto no navegador (Safari/Chrome).
    URL: /remove-labels/SEU_TOKEN
    """
    if not RUN_TOKEN:
        raise HTTPException(status_code=500, detail="RUN_TOKEN não configurado no servidor.")

    if token != RUN_TOKEN:
        raise HTTPException(status_code=401, detail="Token inválido.")

    try:
        resumo = automacao.remover_todos_marcadores()
        return JSONResponse(content=resumo)
    except Exception as e:
        log.error(f"Erro ao remover marcadores: {e}")
        raise HTTPException(status_code=500, detail=str(e))

