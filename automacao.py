"""
Falaw Advogados — Automação de Gmail com IA (núcleo)
=====================================================
Classifica emails recebidos e aplica marcadores automáticos
na caixa de todos os usuários do domínio falaw.com.br.

Toda a configuração vem de variáveis de ambiente (ver README_DEPLOY.md).
"""

import os
import base64
import logging
import time
from datetime import datetime, timezone, timedelta

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
    "MAX_EMAILS_PER_RUN": int(os.environ.get("MAX_EMAILS_PER_RUN", "20")),
    "HOURS_LOOKBACK": int(os.environ.get("HOURS_LOOKBACK", "2")),
    "RUN_FOR_ALL_USERS": os.environ.get("RUN_FOR_ALL_USERS", "false").lower() == "true",
}

# ──────────────────────────────────────────────
# ESTRUTURA DE MARCADORES
# ──────────────────────────────────────────────

# Mapeamento: endereço do grupo → nome do marcador no Gmail
GRUPOS = {
    "advogados@falaw.com.br":            "Falaw/Advogados Falaw",
    "buser-trabalhista@falaw.com.br":    "Falaw/Buser Trabalhista",
    "consultivo@falaw.com.br":           "Falaw/Consultivo Geral",
    "loft-trabalhista@falaw.com.br":     "Falaw/Contencioso LOFT Trabalhista",
    "controladoria@falaw.com.br":        "Falaw/Controladoria FALAW",
    "fa--geral@falaw.com.br":            "Falaw/FA GERAL",
    "frete-trabalhista@falaw.com.br":    "Falaw/Frete Trabalhista",
    "ifood-trabalhista@falaw.com.br":    "Falaw/Ifood",
    "indrive@falaw.com.br":              "Falaw/INDRIVE Litigation",
    "interpag@falaw.com.br":             "Falaw/Interpag Trabalhista",
    "jurimetria@falaw.com.br":           "Falaw/Jurimetria",
    "lalamove@falaw.com.br":             "Falaw/Lalamove",
    "newslatter@falaw.com.br":           "Falaw/Newsletter",
    "pravaler-trabalhista@falaw.com.br": "Falaw/Pravaler",
    "sindical-fa@falaw.com.br":          "Falaw/Sindical FA",
}

# Usuário que NÃO deve receber marcadores automáticos
USUARIO_EXCLUIDO = "tatiana@falaw.com.br"

# Lista de todos os marcadores a criar (inclui pais gerados em garantir_marcadores)
TODOS_MARCADORES = list(GRUPOS.values())

# Marcadores criados por versões anteriores do código (para limpeza completa)
MARCADORES_LEGADOS = [
    "Falaw/⚠️ URGENTE",
    "Falaw/Audiências",
    "Falaw/Propaganda",
    "Falaw/Interno",
    "Falaw/Consultivo",
    "Falaw/Outros",
    "Falaw/Clientes/Outros Clientes",
    "Falaw/Clientes/Apdata",
    "Falaw/Clientes/Baymetrics",
    "Falaw/Clientes/Bipa",
    "Falaw/Clientes/Buser",
    "Falaw/Clientes/Cuidar.me - Dr. Consulta",
    "Falaw/Clientes/Digibee",
    "Falaw/Clientes/Frete - CargoX",
    "Falaw/Clientes/GFT",
    "Falaw/Clientes/Grupo Dória",
    "Falaw/Clientes/Gupy",
    "Falaw/Clientes/Hubees",
    "Falaw/Clientes/Ifood",
    "Falaw/Clientes/KPG",
    "Falaw/Clientes/Lemon",
    "Falaw/Clientes/Musa",
    "Falaw/Clientes/Nuvemshop",
    "Falaw/Clientes/Peg&Pet",
    "Falaw/Clientes/Pier",
    "Falaw/Clientes/Inter",
    "Falaw/Clientes/Pravaler",
    "Falaw/Clientes/Quero",
    "Falaw/Clientes/Rabbot",
    "Falaw/Clientes/Safira",
    "Falaw/Clientes/Solinftec",
    "Falaw/Clientes",
    "Falaw/Newslatter",  # typo antigo
    "Newslatter",        # marcador criado sem prefixo Falaw
    # novos marcadores atuais (caso já existam e precisem ser removidos)
    *TODOS_MARCADORES,
    "Falaw",  # pai raiz
]

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
    """Retorna emails não lidos das últimas X horas, sem marcador Falaw/Grupos."""
    depois = datetime.now(timezone.utc) - timedelta(hours=CONFIG["HOURS_LOOKBACK"])
    timestamp = int(depois.timestamp())

    query = f'is:unread after:{timestamp} -label:"Falaw/Grupos"'

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
            "cc": headers.get("Cc", ""),
            "data": headers.get("Date", ""),
            "corpo": body[:3000],  # limita para não estourar contexto
        }
    except Exception as e:
        log.error(f"Erro ao ler email {msg_id}: {e}")
        return {}


# Palavras-chave que indicam email de audiência
_KW_AUDIENCIAS = [
    "audiência", "audiencia", "pauta de audiência", "pauta de audiencia",
    "designação de audiência", "designacao de audiencia",
    "intimação para audiência", "intimacao para audiencia",
    "ata de audiência", "ata de audiencia", "audiência una",
    "audiencia una", "audiência inicial", "audiencia inicial",
    "audiência de instrução", "audiencia de instrucao",
    "adiamento de audiência", "adiamento de audiencia",
    "conciliação", "conciliacao",
]

# Fragmentos de texto no assunto/corpo → endereço do grupo correspondente
_TEXTO_PARA_GRUPO = {
    "buser":        "buser-trabalhista@falaw.com.br",
    "loft":         "loft-trabalhista@falaw.com.br",
    "frete":        "frete-trabalhista@falaw.com.br",
    "cargox":       "frete-trabalhista@falaw.com.br",
    "ifood":        "ifood-trabalhista@falaw.com.br",
    "indrive":      "indrive@falaw.com.br",
    "interpag":     "interpag@falaw.com.br",
    "pravaler":     "pravaler-trabalhista@falaw.com.br",
    "sindical":     "sindical-fa@falaw.com.br",
}


# ──────────────────────────────────────────────
# CLASSIFICAÇÃO POR GRUPO DESTINATÁRIO
# ──────────────────────────────────────────────

def classificar_email(email: dict) -> str | None:
    """
    Retorna o nome do marcador (label) a aplicar, ou None.
    Prioridade:
      1. Endereço de grupo em To ou Cc
      2. Email de audiência com cliente identificável pelo assunto/corpo
    """
    destinatarios = (email.get("para", "") + " " + email.get("cc", "")).lower()

    # 1. Verifica To + Cc para endereço de grupo
    for endereco, nome_marcador in GRUPOS.items():
        if endereco in destinatarios:
            return nome_marcador

    # 2. Email de audiência: tenta identificar o cliente pelo assunto/corpo
    assunto = email.get("assunto", "").lower()
    corpo = email.get("corpo", "")[:1000].lower()
    texto = assunto + " " + corpo

    if any(kw in texto for kw in _KW_AUDIENCIAS):
        for fragmento, endereco_grupo in _TEXTO_PARA_GRUPO.items():
            if fragmento in texto:
                return GRUPOS.get(endereco_grupo)

    return None


# ──────────────────────────────────────────────
# APLICAR MARCADORES NO EMAIL
# ──────────────────────────────────────────────
def aplicar_marcadores(service, msg_id: str, nome_marcador: str, label_ids: dict):
    """Aplica o marcador do grupo ao email."""
    try:
        label_id = label_ids.get(nome_marcador)
        if label_id:
            service.users().messages().modify(
                userId="me",
                id=msg_id,
                body={"addLabelIds": [label_id]},
            ).execute()
    except Exception as e:
        log.error(f"Erro ao aplicar marcadores no email {msg_id}: {e}")


# ──────────────────────────────────────────────
# PROCESSAR UM USUÁRIO
# ──────────────────────────────────────────────
def processar_usuario(user_email: str) -> dict:
    """Processa os emails de um usuário. Retorna estatísticas."""
    stats = {"usuario": user_email, "processados": 0, "ignorados": 0, "erros": 0}

    if user_email.lower() == USUARIO_EXCLUIDO.lower():
        log.info(f"Ignorando {user_email} (usuário excluído da automação).")
        return stats

    log.info(f"Processando: {user_email}")

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

            nome_marcador = classificar_email(email)
            if nome_marcador:
                aplicar_marcadores(service, msg_ref["id"], nome_marcador, label_ids)
                stats["processados"] += 1
                log.info(
                    f"  ✅ {email.get('assunto', '')[:60]} → {nome_marcador}"
                )
            else:
                stats["ignorados"] += 1
                log.info(
                    f"  — sem grupo: {email.get('assunto', '')[:60]}"
                )

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
        time.sleep(1)  # pausa mínima entre usuários (sem IA, não há rate limit externo)

    resumo = {
        "executado_em": inicio.isoformat(),
        "duracao_segundos": (datetime.now() - inicio).total_seconds(),
        "usuarios_processados": len(usuarios),
        "total_emails": sum(r["processados"] for r in resultados),
        "total_erros": sum(r["erros"] for r in resultados),
        "detalhes": resultados,
    }

    log.info(f"Concluído: {resumo['total_emails']} emails marcados.")
    return resumo


# ──────────────────────────────────────────────
# REMOVER TODOS OS MARCADORES DE TODOS OS EMAILS
# ──────────────────────────────────────────────
def remover_todos_marcadores_usuario(user_email: str) -> dict:
    """Exclui da conta todos os marcadores criados pelo código (atuais + legados).
    A API do Gmail remove automaticamente o marcador de todos os emails ao excluí-lo."""
    log.info(f"Removendo marcadores de: {user_email}")
    stats = {"usuario": user_email, "marcadores_excluidos": 0, "erros": 0}

    try:
        service = get_gmail_service(user_email)

        # Obtém IDs apenas dos marcadores que o código criou (atuais + legados)
        nomes_para_remover = set(MARCADORES_LEGADOS)
        result = service.users().labels().list(userId="me").execute()
        labels_encontradas = {
            l["name"]: l["id"] for l in result.get("labels", [])
            if l["name"] in nomes_para_remover
        }

        if not labels_encontradas:
            log.info(f"  [{user_email}] Nenhum marcador do código encontrado.")
            return stats

        log.info(f"  [{user_email}] {len(labels_encontradas)} marcador(es) encontrado(s).")

        # Exclui cada marcador diretamente — a API remove automaticamente dos emails
        for nome, label_id in labels_encontradas.items():
            try:
                service.users().labels().delete(userId="me", id=label_id).execute()
                stats["marcadores_excluidos"] += 1
                log.info(f"  [{user_email}] ✓ Excluído: {nome}")
            except HttpError as e:
                if e.resp.status == 400:
                    log.info(f"  [{user_email}] Ignorado (sistema): {nome}")
                else:
                    log.error(f"  [{user_email}] Erro ao excluir '{nome}': {e}")
                    stats["erros"] += 1

    except HttpError as e:
        log.error(f"Erro HTTP para {user_email}: {e}")
        stats["erros"] += 1
    except Exception as e:
        log.error(f"Erro inesperado para {user_email}: {e}")
        stats["erros"] += 1

    log.info(f"  [{user_email}] {stats['marcadores_excluidos']} marcador(es) excluído(s).")
    return stats


def remover_todos_marcadores() -> dict:
    """Remove todos os marcadores Falaw de todos os emails de todos os usuários."""
    inicio = datetime.now()
    log.info("=" * 60)
    log.info(f"Removendo marcadores — {inicio.strftime('%d/%m/%Y %H:%M')}")
    log.info("=" * 60)

    usuarios = listar_usuarios()
    resultados = []

    for user_email in usuarios:
        resultados.append(remover_todos_marcadores_usuario(user_email))
        time.sleep(0.5)

    resumo = {
        "executado_em": inicio.isoformat(),
        "duracao_segundos": (datetime.now() - inicio).total_seconds(),
        "usuarios_processados": len(usuarios),
        "total_marcadores_excluidos": sum(r["marcadores_excluidos"] for r in resultados),
        "total_erros": sum(r["erros"] for r in resultados),
        "detalhes": resultados,
    }

    log.info(f"Concluído: {resumo['total_emails_modificados']} email(s) sem marcadores.")
    return resumo


if __name__ == "__main__":
    executar()