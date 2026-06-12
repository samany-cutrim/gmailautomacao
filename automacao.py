"""
Falaw Advogados — Automação de Gmail com IA (núcleo)
=====================================================
Classifica emails recebidos e aplica marcadores automáticos
na caixa de todos os usuários do domínio falaw.com.br.

Toda a configuração vem de variáveis de ambiente (ver README_DEPLOY.md).
"""

import os
import json
import base64
import logging
import time
from datetime import datetime, timezone, timedelta

from openai import OpenAI
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

# ──────────────────────────────────────────────
# CONFIGURAÇÃO — via variáveis de ambiente
# ──────────────────────────────────────────────
CONFIG = {
    # No Render: Secret File em /etc/secrets/service_account.json
    "SERVICE_ACCOUNT_FILE": os.environ.get(
        "SERVICE_ACCOUNT_FILE", "/etc/secrets/service_account.json"
    ),
    "DOMAIN": os.environ.get("WORKSPACE_DOMAIN", "falaw.com.br"),
    "ADMIN_USER": os.environ.get("ADMIN_USER", "samany@falaw.com.br"),
    "GITHUB_TOKEN": os.environ.get("GITHUB_TOKEN", ""),
    "OPENAI_API_KEY": os.environ.get("OPENAI_API_KEY", ""),
    "GEMINI_API_KEY": os.environ.get("GEMINI_API_KEY", ""),
    "MAX_EMAILS_PER_RUN": int(os.environ.get("MAX_EMAILS_PER_RUN", "20")),
    "HOURS_LOOKBACK": int(os.environ.get("HOURS_LOOKBACK", "2")),
    "RUN_FOR_ALL_USERS": os.environ.get("RUN_FOR_ALL_USERS", "false").lower() == "true",
}

# ──────────────────────────────────────────────
# ESTRUTURA DE MARCADORES
# ──────────────────────────────────────────────

# Clientes que têm marcador próprio.
# Para adicionar um novo cliente, basta incluir aqui:
# "NomeDetectadoPelaIA": "Falaw/Clientes/NomeDoMarcador",
# Obs: não use "/" no nome do marcador final — no Gmail, "/" cria subnível.
CLIENTES_CONHECIDOS = {
    "Apdata":       "Falaw/Clientes/Apdata",
    "Baymetrics":   "Falaw/Clientes/Baymetrics",
    "Bipa":         "Falaw/Clientes/Bipa",
    "Buser":        "Falaw/Clientes/Buser",
    "Cuidar.me":    "Falaw/Clientes/Cuidar.me - Dr. Consulta",
    "Digibee":      "Falaw/Clientes/Digibee",
    "CargoX":       "Falaw/Clientes/Frete - CargoX",
    "GFT":          "Falaw/Clientes/GFT",
    "Grupo Dória":  "Falaw/Clientes/Grupo Dória",
    "Gupy":         "Falaw/Clientes/Gupy",
    "Hubees":       "Falaw/Clientes/Hubees",
    "Ifood":        "Falaw/Clientes/Ifood",
    "KPG":          "Falaw/Clientes/KPG",
    "Lemon":        "Falaw/Clientes/Lemon",
    "Musa":         "Falaw/Clientes/Musa",
    "Nuvemshop":    "Falaw/Clientes/Nuvemshop",
    "Peg&Pet":      "Falaw/Clientes/Peg&Pet",
    "Pier":         "Falaw/Clientes/Pier",
    "Inter":        "Falaw/Clientes/Inter",
    "Pravaler":     "Falaw/Clientes/Pravaler",
    "Quero":        "Falaw/Clientes/Quero",
    "Rabbot":       "Falaw/Clientes/Rabbot",
    "Safira":       "Falaw/Clientes/Safira",
    "Solinftec":    "Falaw/Clientes/Solinftec",
}

# Nomes alternativos que a IA pode encontrar → nome canônico da lista acima
APELIDOS_CLIENTES = {
    "Dr. Consulta": "Cuidar.me",
    "Dr Consulta": "Cuidar.me",
    "Frete": "CargoX",
    "Banco Inter": "Inter",
}

MARCADORES = {
    "Consultivo":      "Falaw/Consultivo",
    "Audiencias":      "Falaw/Audiências",
    "Propaganda":      "Falaw/Propaganda",
    "Interno":         "Falaw/Interno",
    "OutrosClientes":  "Falaw/Clientes/Outros Clientes",
    "Outro":           "Falaw/Outros",
    "URGENTE":         "Falaw/⚠️ URGENTE",
}

# Junta tudo para criação automática dos marcadores
TODOS_MARCADORES = list(MARCADORES.values()) + list(CLIENTES_CONHECIDOS.values())

# ──────────────────────────────────────────────
# LOGGING
# ──────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)

# ──────────────────────────────────────────────
# ESCOPOS NECESSÁRIOS
# ──────────────────────────────────────────────
SCOPES = [
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.labels",
    "https://www.googleapis.com/auth/admin.directory.user.readonly",
]


# ──────────────────────────────────────────────
# AUTENTICAÇÃO
# ──────────────────────────────────────────────
def get_credentials(user_email: str):
    """Retorna credenciais delegadas para um usuário específico."""
    creds = service_account.Credentials.from_service_account_file(
        CONFIG["SERVICE_ACCOUNT_FILE"],
        scopes=SCOPES,
    )
    return creds.with_subject(user_email)


def get_gmail_service(user_email: str):
    creds = get_credentials(user_email)
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


def get_admin_service():
    creds = get_credentials(CONFIG["ADMIN_USER"])
    return build("admin", "directory_v1", credentials=creds, cache_discovery=False)


# ──────────────────────────────────────────────
# LISTAR USUÁRIOS DO DOMÍNIO
# ──────────────────────────────────────────────
def listar_usuarios() -> list:
    """Retorna lista de emails de todos os usuários do domínio."""
    if not CONFIG["RUN_FOR_ALL_USERS"]:
        return [CONFIG["ADMIN_USER"]]

    try:
        service = get_admin_service()
        usuarios = []
        request = service.users().list(domain=CONFIG["DOMAIN"], maxResults=200)
        while request is not None:
            resultado = request.execute()
            for u in resultado.get("users", []):
                if not u.get("suspended"):
                    usuarios.append(u["primaryEmail"])
            request = service.users().list_next(request, resultado)
        log.info(f"Encontrados {len(usuarios)} usuários no domínio.")
        return usuarios
    except Exception as e:
        log.error(f"Erro ao listar usuários: {e}")
        return [CONFIG["ADMIN_USER"]]


# ──────────────────────────────────────────────
# MARCADORES — criar se não existir
# ──────────────────────────────────────────────
def garantir_marcadores(service, user_email: str) -> dict:
    """
    Garante que todos os marcadores existem na conta do usuário,
    incluindo marcadores-pai (ex: 'FA Law/Clientes' antes de 'FA Law/Clientes/Ifood').
    Retorna dict {nome_marcador: label_id}
    """
    try:
        result = service.users().labels().list(userId="me").execute()
        existentes = {l["name"]: l["id"] for l in result.get("labels", [])}

        # Monta a lista incluindo todos os pais necessários
        necessarios = set()
        for nome in TODOS_MARCADORES:
            partes = nome.split("/")
            for i in range(1, len(partes) + 1):
                necessarios.add("/".join(partes[:i]))

        ids = {}
        for nome_marcador in sorted(necessarios):  # ordena para criar pais primeiro
            if nome_marcador not in existentes:
                try:
                    novo = service.users().labels().create(
                        userId="me",
                        body={
                            "name": nome_marcador,
                            "labelListVisibility": "labelShow",
                            "messageListVisibility": "show",
                        }
                    ).execute()
                    existentes[nome_marcador] = novo["id"]
                    log.info(f"  [{user_email}] Marcador criado: {nome_marcador}")
                except HttpError as e:
                    if e.resp.status == 409:
                        # Marcador já existe — recarrega lista para obter o ID
                        result = service.users().labels().list(userId="me").execute()
                        existentes = {l["name"]: l["id"] for l in result.get("labels", [])}
                    else:
                        raise
            if nome_marcador in existentes:
                ids[nome_marcador] = existentes[nome_marcador]

        return ids
    except Exception as e:
        log.error(f"Erro ao garantir marcadores para {user_email}: {e}")
        return {}


# ──────────────────────────────────────────────
# LER EMAILS NÃO LIDOS RECENTES
# ──────────────────────────────────────────────
def buscar_emails_nao_lidos(service) -> list:
    """Retorna emails não lidos das últimas X horas, sem marcador FA Law."""
    depois = datetime.now(timezone.utc) - timedelta(hours=CONFIG["HOURS_LOOKBACK"])
    timestamp = int(depois.timestamp())

    query = f'is:unread after:{timestamp} -label:"Falaw"'

    try:
        result = service.users().messages().list(
            userId="me",
            q=query,
            maxResults=CONFIG["MAX_EMAILS_PER_RUN"]
        ).execute()
        return result.get("messages", [])
    except Exception as e:
        log.error(f"Erro ao buscar emails: {e}")
        return []


def ler_email(service, msg_id: str) -> dict:
    """Lê o conteúdo completo de um email."""
    try:
        msg = service.users().messages().get(
            userId="me", id=msg_id, format="full"
        ).execute()

        headers = {h["name"]: h["value"] for h in msg["payload"].get("headers", [])}

        payload = msg["payload"]

        def extrair_texto(part):
            if part.get("mimeType") == "text/plain" and part.get("body", {}).get("data"):
                return base64.urlsafe_b64decode(part["body"]["data"]).decode("utf-8", errors="ignore")
            for subpart in part.get("parts", []):
                texto = extrair_texto(subpart)
                if texto:
                    return texto
            return ""

        body = extrair_texto(payload)

        return {
            "id": msg_id,
            "assunto": headers.get("Subject", "(sem assunto)"),
            "de": headers.get("From", ""),
            "para": headers.get("To", ""),
            "data": headers.get("Date", ""),
            "corpo": body[:3000],  # limita para não estourar contexto
        }
    except Exception as e:
        log.error(f"Erro ao ler email {msg_id}: {e}")
        return {}


# ──────────────────────────────────────────────
# CLASSIFICAÇÃO COM IA (GitHub Models → OpenAI → Gemini)
# ──────────────────────────────────────────────
def _chamar_modelo(client, model: str, prompt: str) -> dict:
    """Chama um modelo via cliente OpenAI-compatível e retorna JSON."""
    response = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=400,
        temperature=0.1,
    )
    texto = response.choices[0].message.content.strip()
    texto = texto.replace("```json", "").replace("```", "").strip()
    return json.loads(texto)


def classificar_email(email: dict) -> dict:
    """
    Tenta classificar o email usando GitHub Models, OpenAI e Gemini como fallback.
    """
    vazio = {"categoria": "Outro", "urgente": False, "motivo_urgencia": None, "cliente": None, "resumo": ""}

    clientes_lista = ", ".join(CLIENTES_CONHECIDOS.keys())

    prompt = f"""Você é um assistente jurídico do escritório Falaw Advogados, especializado em direito do trabalho brasileiro.

Analise o email abaixo e retorne SOMENTE um objeto JSON válido, sem texto adicional, sem markdown.

REGRAS DE CLASSIFICAÇÃO (siga nesta ordem de prioridade):

1. INTERNO: se o email for enviado por alguém do domínio @falaw.com.br para outro(s) do mesmo
   domínio (comunicação interna do escritório) → categoria "Interno"

2. AUDIENCIA: se o email tratar de audiência (designação, pauta, intimação para audiência,
   ata de audiência, adiamento, audiência una, inicial ou de instrução) → categoria "Audiencias"

3. CONSULTIVO: se for uma consulta jurídica (cliente ou colega pedindo parecer, opinião
   jurídica, análise de situação, dúvida sobre legislação/procedimento) → categoria "Consultivo"

4. CLIENTE: se o email for de/sobre um destes clientes do escritório:
   {clientes_lista}
   → categoria "Cliente" e preencha o campo "cliente" com o nome EXATO como está na lista.
   Atenção a variações: "Dr. Consulta" = Cuidar.me | "Frete" = CargoX | "Banco Inter" = Inter

5. OUTROS CLIENTES: se for claramente de um cliente do escritório mas que NÃO está
   na lista acima → categoria "OutrosClientes"

6. PROPAGANDA: marketing, promoções, newsletters comerciais, divulgação de cursos/eventos
   pagos, spam → categoria "Propaganda"

7. OUTRO: tudo que não se encaixar acima → categoria "Outro"

URGÊNCIA — marque urgente: true se:
- A palavra "urgente" (ou "URGENTE", "urgência") aparecer no assunto ou corpo
- Mencionar: prazo fatal, penhora, bloqueio, liminar, tutela, bacenjud,
  citação, intimação com prazo, mandado, multa, execução

JSON esperado:
{{
  "categoria": "Interno | Audiencias | Consultivo | Cliente | OutrosClientes | Propaganda | Outro",
  "urgente": true ou false,
  "motivo_urgencia": "explicação breve se urgente, senão null",
  "cliente": "nome exato da lista se categoria=Cliente, senão null",
  "resumo": "1 frase resumindo o email"
}}

Email:
Assunto: {email.get('assunto', '')}
De: {email.get('de', '')}
Para: {email.get('para', '')}
Corpo: {email.get('corpo', '')}"""

    provedores = []
    if CONFIG["GITHUB_TOKEN"]:
        provedores.append(("GitHub Models", OpenAI(
            base_url="https://models.inference.ai.azure.com",
            api_key=CONFIG["GITHUB_TOKEN"],
        ), "gpt-4o-mini"))
    if CONFIG["OPENAI_API_KEY"]:
        provedores.append(("OpenAI", OpenAI(
            api_key=CONFIG["OPENAI_API_KEY"],
        ), "gpt-4o-mini"))
    if CONFIG["GEMINI_API_KEY"]:
        provedores.append(("Gemini", OpenAI(
            base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
            api_key=CONFIG["GEMINI_API_KEY"],
        ), "gemini-2.0-flash"))

    for nome, client, model in provedores:
        try:
            return _chamar_modelo(client, model, prompt)
        except json.JSONDecodeError as e:
            log.error(f"[{nome}] Erro ao parsear JSON: {e}")
            return vazio
        except Exception as e:
            log.warning(f"[{nome}] Falhou ({e}), tentando próximo provedor...")

    log.error("Todos os provedores de IA falharam.")
    return vazio


# ──────────────────────────────────────────────
# APLICAR MARCADORES NO EMAIL
# ──────────────────────────────────────────────
def aplicar_marcadores(service, msg_id: str, classificacao: dict, label_ids: dict):
    """Aplica marcadores e estrela ao email conforme classificação."""
    try:
        categoria = classificacao.get("categoria", "Outro")
        urgente = classificacao.get("urgente", False)
        cliente = classificacao.get("cliente")

        # Resolve apelidos (ex: "Dr. Consulta" → "Cuidar.me")
        if cliente in APELIDOS_CLIENTES:
            cliente = APELIDOS_CLIENTES[cliente]

        # Resolve o marcador conforme a categoria
        if categoria == "Cliente" and cliente in CLIENTES_CONHECIDOS:
            nome_marcador = CLIENTES_CONHECIDOS[cliente]
        elif categoria == "Cliente":
            # IA disse Cliente mas o nome não está na lista → Outros Clientes
            nome_marcador = MARCADORES["OutrosClientes"]
        else:
            nome_marcador = MARCADORES.get(categoria, MARCADORES["Outro"])

        labels_para_adicionar = []

        if nome_marcador in label_ids:
            labels_para_adicionar.append(label_ids[nome_marcador])

        if urgente and MARCADORES["URGENTE"] in label_ids:
            labels_para_adicionar.append(label_ids[MARCADORES["URGENTE"]])
            labels_para_adicionar.append("STARRED")

        if labels_para_adicionar:
            service.users().messages().modify(
                userId="me",
                id=msg_id,
                body={"addLabelIds": labels_para_adicionar}
            ).execute()

    except Exception as e:
        log.error(f"Erro ao aplicar marcadores no email {msg_id}: {e}")


# ──────────────────────────────────────────────
# PROCESSAR UM USUÁRIO
# ──────────────────────────────────────────────
def processar_usuario(user_email: str) -> dict:
    """Processa os emails de um usuário. Retorna estatísticas."""
    log.info(f"Processando: {user_email}")
    stats = {"usuario": user_email, "processados": 0, "urgentes": 0, "erros": 0}

    try:
        service = get_gmail_service(user_email)
        label_ids = garantir_marcadores(service, user_email)
        emails = buscar_emails_nao_lidos(service)

        if not emails:
            log.info("  Nenhum email novo para processar.")
            return stats

        log.info(f"  {len(emails)} email(s) para classificar.")

        for msg_ref in emails:
            email = ler_email(service, msg_ref["id"])
            if not email:
                stats["erros"] += 1
                continue

            classificacao = classificar_email(email)
            aplicar_marcadores(service, msg_ref["id"], classificacao, label_ids)
            stats["processados"] += 1

            status = "🚨 URGENTE" if classificacao.get("urgente") else "✅ OK"
            log.info(
                f"  [{status}] {email.get('assunto', '')[:60]} "
                f"→ {classificacao.get('categoria')} "
                f"| {classificacao.get('resumo', '')[:80]}"
            )

            if classificacao.get("urgente"):
                stats["urgentes"] += 1

    except HttpError as e:
        log.error(f"Erro HTTP para {user_email}: {e}")
        stats["erros"] += 1
    except Exception as e:
        log.error(f"Erro inesperado para {user_email}: {e}")
        stats["erros"] += 1

    return stats


# ──────────────────────────────────────────────
# EXECUÇÃO COMPLETA
# ──────────────────────────────────────────────
def executar() -> dict:
    """Roda a automação para todos os usuários. Retorna resumo."""
    inicio = datetime.now()
    log.info("=" * 60)
    log.info(f"FA Law Gmail Automação — {inicio.strftime('%d/%m/%Y %H:%M')}")
    log.info("=" * 60)

    usuarios = listar_usuarios()
    resultados = []

    for user_email in usuarios:
        resultados.append(processar_usuario(user_email))
        time.sleep(5)  # pausa entre usuários para não estourar rate limit

    resumo = {
        "executado_em": inicio.isoformat(),
        "duracao_segundos": (datetime.now() - inicio).total_seconds(),
        "usuarios_processados": len(usuarios),
        "total_emails": sum(r["processados"] for r in resultados),
        "total_urgentes": sum(r["urgentes"] for r in resultados),
        "total_erros": sum(r["erros"] for r in resultados),
        "detalhes": resultados,
    }

    log.info(f"Concluído: {resumo['total_emails']} emails, {resumo['total_urgentes']} urgentes.")
    return resumo


if __name__ == "__main__":
    executar()
