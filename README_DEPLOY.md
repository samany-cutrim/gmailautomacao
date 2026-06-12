# FA Law — Gmail Automação — Deploy no Render

## Arquitetura

```
cron-job.org (a cada 30 min)
      │  POST /run  (com header X-Run-Token)
      ▼
Render Web Service (FastAPI — plano Free)
      │
      ├─→ Gmail API (Service Account + delegação de domínio)
      ├─→ GitHub Models (gpt-4o) — classificação
      └─→ Google Chat — notificação de urgentes
```

O plano Free do Render hiberna após 15 min sem tráfego, mas o próprio
ping do cron-job.org acorda o serviço — funciona perfeitamente para
execuções a cada 30 minutos. (A primeira chamada após hibernar demora
~50s para responder; o cron-job.org aguarda.)

---

## Estrutura do projeto

```
falaw-gmail-render/
├── main.py            ← API FastAPI (endpoints /, /health, /run)
├── automacao.py       ← lógica da automação
├── requirements.txt   ← dependências
├── .gitignore         ← protege credenciais
└── README_DEPLOY.md   ← este arquivo
```

---

## PASSO 1 — Subir o código no GitHub

```bash
cd falaw-gmail-render
git init
git add .
git commit -m "FA Law Gmail Automacao"
git branch -M main
git remote add origin https://github.com/SEU_USUARIO/falaw-gmail-automacao.git
git push -u origin main
```

⚠️ **IMPORTANTE:** o `.gitignore` já bloqueia `service_account.json`.
NUNCA suba esse arquivo para o GitHub — ele vai como Secret File no Render.

---

## PASSO 2 — Criar o Web Service no Render

1. Acesse [dashboard.render.com](https://dashboard.render.com)
2. **New → Web Service**
3. Conecte o repositório `falaw-gmail-automacao`
4. Configure:
   - **Name:** `falaw-gmail-automacao`
   - **Region:** Oregon (ou mais próxima)
   - **Branch:** main
   - **Runtime:** Python 3
   - **Build Command:** `pip install -r requirements.txt`
   - **Start Command:** `uvicorn main:app --host 0.0.0.0 --port $PORT`
   - **Instance Type:** Free

---

## PASSO 3 — Variáveis de ambiente

No Render → seu serviço → **Environment** → adicione:

| Chave | Valor |
|---|---|
| `GITHUB_TOKEN` | seu PAT do GitHub (`ghp_...`) |
| `GOOGLE_CHAT_WEBHOOK` | URL do webhook do espaço no Chat |
| `RUN_TOKEN` | crie uma senha forte qualquer (ex: gere em 1password ou `openssl rand -hex 24`) |
| `WORKSPACE_DOMAIN` | `falaw.com.br` |
| `ADMIN_USER` | `samany@falaw.com.br` |
| `RUN_FOR_ALL_USERS` | `false` (mude para `true` depois de testar) |
| `MAX_EMAILS_PER_RUN` | `20` |
| `HOURS_LOOKBACK` | `2` |

---

## PASSO 4 — Secret File (service_account.json)

No Render → seu serviço → **Environment → Secret Files**:

- **Filename:** `service_account.json`
  *(o Render salva em `/etc/secrets/service_account.json` — o código já aponta para lá)*
- **Contents:** cole o conteúdo completo do arquivo JSON da Service Account

---

## PASSO 5 — Deploy e teste manual

1. Clique em **Manual Deploy → Deploy latest commit**
2. Aguarde o build terminar
3. Teste o status: abra `https://falaw-gmail-automacao.onrender.com/` no navegador
4. Teste a execução (PowerShell):

```powershell
Invoke-RestMethod -Method Post `
  -Uri "https://falaw-gmail-automacao.onrender.com/run" `
  -Headers @{"X-Run-Token"="SEU_RUN_TOKEN"}
```

Ou com curl:
```bash
curl -X POST https://falaw-gmail-automacao.onrender.com/run \
  -H "X-Run-Token: SEU_RUN_TOKEN"
```

A resposta traz o resumo: emails processados, urgentes, erros.

---

## PASSO 6 — Agendar com cron-job.org (gratuito)

1. Crie conta em [cron-job.org](https://cron-job.org)
2. **Create cronjob:**
   - **Title:** FA Law Gmail Automação
   - **URL:** `https://falaw-gmail-automacao.onrender.com/run`
   - **Schedule:** a cada 30 minutos
   - **Request method:** POST
3. Em **Advanced → Headers**, adicione:
   - `X-Run-Token` = o mesmo valor do RUN_TOKEN
4. Em **Advanced → Timeout:** 300 segundos
   *(importante: dá tempo do Render acordar da hibernação)*
5. Salvar e ativar

---

## PASSO 7 — Ativar para todos os colaboradores

Depois de alguns dias testando só na sua conta:

1. Render → Environment → `RUN_FOR_ALL_USERS` = `true`
2. O serviço redeploya automaticamente

---

## Como adicionar novos clientes

Edite `automacao.py` no GitHub (pode ser direto pelo site):

```python
CLIENTES_CONHECIDOS = {
    "Ifood": "FA Law/Clientes/Ifood",
    "Inter": "FA Law/Clientes/Inter",
    "NovoCliente": "FA Law/Clientes/NovoCliente",   # ← adicione assim
}
```

Commit → o Render redeploya sozinho → os marcadores são criados
automaticamente na próxima execução, em todas as contas.

---

## Estrutura de marcadores criada

```
FA Law/
├── ⚠️ URGENTE          ← palavra "urgente" / termos críticos + estrela + Chat
├── Consultivo          ← consultas jurídicas e pedidos de parecer
├── Audiências          ← tudo sobre audiências
├── Propaganda          ← marketing, promoções, spam
├── Clientes/
│   ├── Ifood
│   ├── Inter
│   └── Outros Clientes ← clientes sem marcador próprio
└── Outros
```

---

## Monitoramento

- **Logs em tempo real:** Render → seu serviço → aba **Logs**
- **Histórico de execuções:** cron-job.org → seu job → **Execution history**
- **Falhas:** o cron-job.org pode enviar email automático se a chamada falhar
  (ative em Settings → Notifications)

---

## Solução de problemas

| Sintoma | Causa provável | Solução |
|---|---|---|
| 401 no /run | Token errado | Confira X-Run-Token = RUN_TOKEN |
| `unauthorized_client` nos logs | Delegação não propagou | Aguarde até 24h ou confira Client ID/escopos no Admin |
| `RateLimitReached` do GitHub Models | Limite diário do plano free atingido | Reduza MAX_EMAILS_PER_RUN ou aumente o intervalo do cron |
| Primeira execução do dia demora | Hibernação do plano Free | Normal — timeout de 300s no cron resolve |
