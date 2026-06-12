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
# CLASSIFICAÇÃO POR REGRAS PYTHON (sem IA)
# ──────────────────────────────────────────────

# Palavras-chave por categoria (assunto + corpo, case-insensitive)
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

_KW_CONSULTIVO = [
    "parecer", "consulta jurídica", "consulta juridica",
    "opinião jurídica", "opiniao juridica", "análise jurídica",
    "analise juridica", "esclarecimento", "orientação jurídica",
    "orientacao juridica", "dúvida jurídica", "duvida juridica",
    "me informe", "gostaria de saber", "poderia me informar",
    "preciso de orientação", "preciso de orientacao",
]

_KW_PROPAGANDA = [
    "unsubscribe", "descadastrar", "cancelar inscrição",
    "newsletter", "promoção", "promocao", "oferta especial",
    "clique aqui", "saiba mais", "acesse agora", "curso",
    "webinar", "workshop", "evento gratuito", "evento pago",
    "patrocinado", "publicidade", "marketing",
    "não responda este e-mail", "nao responda este e-mail",
    "este é um e-mail automático", "este e um e-mail automatico",
    "email marketing",
]

_KW_URGENTE = [
    "urgente", "urgência", "urgencia", "prazo fatal", "penhora",
    "bloqueio", "liminar", "tutela", "bacenjud", "renajud",
    "citação", "citacao", "intimação com prazo", "intimacao com prazo",
    "mandado", "multa", "execução", "execucao", "arresto",
    "sequestro de bens", "leilão", "leilao", "hasta pública", "hasta publica",
    "amanhã", "amanha", "hoje", "agora",
]

# Domínios/textos associados a clientes conhecidos (ordem importa: mais específico primeiro)
# Chave: fragmento que pode aparecer no campo "De:" ou no assunto/corpo
# Valor: nome canônico em CLIENTES_CONHECIDOS
_DOMINIOS_CLIENTES = {
    # por domínio de email
    "apdata.com":       "Apdata",
    "baymetrics.com":   "Baymetrics",
    "bipa.com":         "Bipa",
    "buser.com":        "Buser",
    "cuidar.me":        "Cuidar.me",
    "drconsulta":       "Cuidar.me",
    "dr.consulta":      "Cuidar.me",
    "digibee.com":      "Digibee",
    "cargox.com":       "CargoX",
    "gft.com":          "GFT",
    "grupodoria.com":   "Grupo Dória",
    "gupy.io":          "Gupy",
    "hubees.com":       "Hubees",
    "ifood.com":        "Ifood",
    "kpg.com":          "KPG",
    "lemon.energy":     "Lemon",
    "musa.com":         "Musa",
    "nuvemshop.com":    "Nuvemshop",
    "pegepet.com":      "Peg&Pet",
    "pier.digital":     "Pier",
    "pier.finance":     "Pier",
    "bancointer.com":   "Inter",
    "inter.co":         "Inter",
    "@inter.":          "Inter",
    "pravaler.com":     "Pravaler",
    "quero.com":        "Quero",
    "quero.education":  "Quero",
    "rabbot.com":       "Rabbot",
    "safira.com":       "Safira",
    "solinftec.com":    "Solinftec",
}

# Fragmentos de texto (assunto/corpo) para detectar clientes
_TEXTO_CLIENTES = {
    "apdata":       "Apdata",
    "baymetrics":   "Baymetrics",
    "bipa":         "Bipa",
    "buser":        "Buser",
    "cuidar.me":    "Cuidar.me",
    "dr. consulta": "Cuidar.me",
    "dr consulta":  "Cuidar.me",
    "digibee":      "Digibee",
    "cargox":       "CargoX",
    "frete.com":    "CargoX",
    " gft ":        "GFT",
    "grupo dória":  "Grupo Dória",
    "grupo doria":  "Grupo Dória",
    "gupy":         "Gupy",
    "hubees":       "Hubees",
    "ifood":        "Ifood",
    " kpg ":        "KPG",
    "lemon energy": "Lemon",
    " musa ":       "Musa",
    "nuvemshop":    "Nuvemshop",
    "peg&pet":      "Peg&Pet",
    "peg e pet":    "Peg&Pet",
    "pier.digital": "Pier",
    "banco inter":  "Inter",
    "pravaler":     "Pravaler",
    "quero edu":    "Quero",
    "rabbot":       "Rabbot",
    "safira":       "Safira",
    "solinftec":    "Solinftec",
}


def _contem(texto: str, palavras: list) -> bool:
    t = texto.lower()
    return any(p in t for p in palavras)


def _detectar_cliente(de: str, assunto: str, corpo: str) -> str | None:
    """Retorna o nome canônico do cliente detectado, ou None."""
    de_lower = de.lower()
    for fragmento, nome in _DOMINIOS_CLIENTES.items():
        if fragmento in de_lower:
            return nome

    texto = (assunto + " " + corpo[:1000]).lower()
    for fragmento, nome in _TEXTO_CLIENTES.items():
        if fragmento in texto:
            return nome

    return None


def classificar_email(email: dict) -> dict:
    """Classifica o email usando regras Python puras (sem IA)."""
    de = email.get("de", "")
    para = email.get("para", "")
    assunto = email.get("assunto", "")
    corpo = email.get("corpo", "")
    texto_completo = assunto + " " + corpo

    # ── 1. INTERNO ──────────────────────────────────
    de_interno = "@falaw.com.br" in de.lower()
    para_interno = "@falaw.com.br" in para.lower()
    if de_interno and para_interno:
        categoria = "Interno"
        cliente = _detectar_cliente(de, assunto, corpo)
        urgente = _contem(texto_completo, _KW_URGENTE)
        return {
            "categoria": categoria,
            "urgente": urgente,
            "motivo_urgencia": "detectado por palavras-chave" if urgente else None,
            "cliente": cliente,
            "resumo": assunto[:120],
        }

    # ── 2. AUDIÊNCIAS ───────────────────────────────
    if _contem(texto_completo, _KW_AUDIENCIAS):
        cliente = _detectar_cliente(de, assunto, corpo)
        urgente = _contem(texto_completo, _KW_URGENTE)
        return {
            "categoria": "Audiencias",
            "urgente": urgente,
            "motivo_urgencia": "detectado por palavras-chave" if urgente else None,
            "cliente": cliente,
            "resumo": assunto[:120],
        }

    # ── 3. CLIENTE CONHECIDO ────────────────────────
    cliente = _detectar_cliente(de, assunto, corpo)
    if cliente:
        urgente = _contem(texto_completo, _KW_URGENTE)
        # Se o remetente externo usa linguagem consultiva → Consultivo
        if not de_interno and _contem(texto_completo, _KW_CONSULTIVO):
            return {
                "categoria": "Consultivo",
                "urgente": urgente,
                "motivo_urgencia": "detectado por palavras-chave" if urgente else None,
                "cliente": cliente,
                "resumo": assunto[:120],
            }
        return {
            "categoria": "Cliente",
            "urgente": urgente,
            "motivo_urgencia": "detectado por palavras-chave" if urgente else None,
            "cliente": cliente,
            "resumo": assunto[:120],
        }

    # ── 4. CONSULTIVO (sem cliente identificado) ────
    if not de_interno and _contem(texto_completo, _KW_CONSULTIVO):
        urgente = _contem(texto_completo, _KW_URGENTE)
        return {
            "categoria": "Consultivo",
            "urgente": urgente,
            "motivo_urgencia": "detectado por palavras-chave" if urgente else None,
            "cliente": None,
            "resumo": assunto[:120],
        }

    # ── 5. PROPAGANDA ───────────────────────────────
    if _contem(texto_completo, _KW_PROPAGANDA):
        return {
            "categoria": "Propaganda",
            "urgente": False,
            "motivo_urgencia": None,
            "cliente": None,
            "resumo": assunto[:120],
        }

    # ── 6. OUTRO ────────────────────────────────────
    urgente = _contem(texto_completo, _KW_URGENTE)
    return {
        "categoria": "Outro",
        "urgente": urgente,
        "motivo_urgencia": "detectado por palavras-chave" if urgente else None,
        "cliente": None,
        "resumo": assunto[:120],
    }


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
            nome_marcador = MARCADORES["OutrosClientes"]
        else:
            nome_marcador = MARCADORES.get(categoria, MARCADORES["Outro"])

        labels_para_adicionar = []

        if nome_marcador in label_ids:
            labels_para_adicionar.append(label_ids[nome_marcador])

        # Adiciona também o marcador do cliente se identificado em qualquer categoria
        if cliente and cliente in CLIENTES_CONHECIDOS and categoria != "Cliente":
            marcador_cliente = CLIENTES_CONHECIDOS[cliente]
            if marcador_cliente in label_ids and label_ids[marcador_cliente] not in labels_para_adicionar:
                labels_para_adicionar.append(label_ids[marcador_cliente])

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
