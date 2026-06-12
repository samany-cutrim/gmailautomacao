"""
FA Law — Gmail Automação — API (Render)
========================================
Expõe a automação via HTTP para ser disparada por agendador externo
(cron-job.org) a cada 30 minutos.

Endpoints:
    GET  /             → status do serviço
    GET  /health       → healthcheck
    POST /run          → executa a automação (protegido por token)
    GET  /relatorio    → relatório de leitura por usuário (protegido por token)
"""

import os
import logging
from datetime import datetime

from fastapi import FastAPI, HTTPException, Header, Query
from fastapi.responses import JSONResponse, HTMLResponse

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


@app.get("/relatorio", response_class=HTMLResponse)
def relatorio(
    x_run_token: str = Header(default=""),
    horas: int = Query(default=24, ge=1, le=168),
):
    """
    Relatório de emails classificados vs lidos por usuário.
    Parâmetro ?horas=24 (padrão) — janela de tempo analisada.
    """
    if not RUN_TOKEN:
        raise HTTPException(status_code=500, detail="RUN_TOKEN não configurado no servidor.")
    if x_run_token != RUN_TOKEN:
        raise HTTPException(status_code=401, detail="Token inválido.")

    try:
        dados = automacao.gerar_relatorio(horas=horas)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    gerado_em = datetime.now().strftime("%d/%m/%Y %H:%M")

    linhas_usuarios = ""
    for u in dados:
        usuario = u.get("usuario", "")
        erro = u.get("erro")

        if erro:
            linhas_usuarios += f"""
            <tr class="erro">
                <td>{usuario}</td>
                <td colspan="5">Erro: {erro}</td>
            </tr>"""
            continue

        total      = u.get("total_classificados", 0)
        lidos      = u.get("total_lidos", 0)
        nao_lidos  = u.get("total_nao_lidos", 0)
        urgentes   = u.get("total_urgentes_nao_lidos", 0)
        cor_urgente = ' style="color:#c0392b;font-weight:bold"' if urgentes > 0 else ""

        linhas_usuarios += f"""
        <tr class="cabecalho-usuario">
            <td><b>{usuario}</b></td>
            <td style="text-align:center">{total}</td>
            <td style="text-align:center;color:#27ae60">{lidos}</td>
            <td style="text-align:center;color:#e67e22">{nao_lidos}</td>
            <td style="text-align:center"{cor_urgente}>{urgentes}</td>
            <td></td>
        </tr>"""

        for email in u.get("emails", []):
            urgente_badge = ' 🚨' if email.get("urgente") else ""
            lido = email.get("lido", False)
            status_icon = "✅" if lido else "📭"
            status_cor = "#27ae60" if lido else "#e67e22"

            leituras = email.get("leituras", [])
            if leituras:
                leituras_html = "<br>".join(
                    f'<span style="color:#2980b9">{l["usuario"]}</span>'
                    f' <span style="color:#999;font-size:0.85em">'
                    f'{l["horario"][:16].replace("T"," ")}</span>'
                    for l in leituras
                )
            elif lido:
                leituras_html = '<span style="color:#999;font-size:0.85em">lido (audit pendente)</span>'
            else:
                leituras_html = '<span style="color:#ccc;font-size:0.85em">—</span>'

            linhas_usuarios += f"""
        <tr class="detalhe">
            <td style="padding-left:2rem;font-size:0.9em" colspan="2">
                <span style="color:{status_cor}">{status_icon}</span>
                {email.get("assunto", "")}{urgente_badge}
            </td>
            <td style="color:#555;font-size:0.85em">{email.get("de", "")}</td>
            <td style="color:#888;font-size:0.85em">{email.get("categoria", "")}</td>
            <td style="color:#888;font-size:0.85em">{email.get("data", "")[:22]}</td>
            <td style="font-size:0.85em">{leituras_html}</td>
        </tr>"""

    html = f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Falaw — Relatório de Emails</title>
<style>
  body {{ font-family: Arial, sans-serif; margin: 2rem; background: #f5f5f5; color: #333; }}
  h1 {{ color: #2c3e50; }}
  p.sub {{ color: #777; margin-top: -0.5rem; }}
  p.aviso {{ color: #e67e22; font-size:0.88em; background:#fff8f0;
             border-left:3px solid #e67e22; padding:0.4rem 0.8rem; border-radius:4px; }}
  table {{ border-collapse: collapse; width: 100%; background: #fff; border-radius: 8px;
           overflow: hidden; box-shadow: 0 1px 4px rgba(0,0,0,.1); }}
  th {{ background: #2c3e50; color: #fff; padding: 0.7rem 1rem; text-align: left; }}
  td {{ padding: 0.5rem 1rem; border-bottom: 1px solid #eee; vertical-align:top; }}
  tr.cabecalho-usuario {{ background: #ecf0f1; }}
  tr.detalhe {{ background: #fafafa; }}
  tr.erro {{ background: #fdecea; }}
  tr:hover {{ background: #f0f4f8; }}
</style>
</head>
<body>
<h1>📬 Relatório de Emails — Falaw Advogados</h1>
<p class="sub">Gerado em {gerado_em} &nbsp;|&nbsp; Janela: últimas {horas} horas</p>
<p class="aviso">⚠️ Dados de leitura (quem leu / horário) têm atraso de 1–3 horas na API do Google.</p>
<table>
  <thead>
    <tr>
      <th>Usuário / Email</th>
      <th style="text-align:center">Classificados</th>
      <th style="text-align:center">Lidos</th>
      <th style="text-align:center">Não lidos</th>
      <th style="text-align:center">Urgentes n/lidos</th>
      <th>Quem leu / Horário</th>
    </tr>
  </thead>
  <tbody>
    {linhas_usuarios}
  </tbody>
</table>
</body>
</html>"""

    return HTMLResponse(content=html)
