# Falaw Advogados — Gmail Automação — Deploy no Render

## O que este projeto faz

A cada 30 minutos, percorre a caixa de entrada de **todos os usuários
ativos do domínio falaw.com.br**, classifica os emails não lidos com IA
(GitHub Models — gpt-4o) e aplica marcadores automáticos. Emails urgentes
recebem também o marcador `⚠️ URGENTE` + estrela, direto na caixa do
colaborador.

## Arquitetura

```
cron-job.org (a cada 30 min, gratuito)
      │  POST /run  (com header X-Run-Token)
      ▼
Render Web Service (FastAPI — plano Free)
      │
      ├─→ Gmail API (Service Account + delegação de domínio)
      │     └─→ caixa de CADA usuário ativo do falaw.com.br
      └─→ GitHub Models (gpt-4o) — classificação
```

O plano Free do Render hiberna após 15 min sem tráfego, mas o próprio
ping do cron-job.org acorda o serviço. A primeira chamada após hibernar
demora ~50s; o timeout de 300s no cron resolve.

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

## Estrutura de marcadores criada em cada caixa

```
Falaw/
├── ⚠️ URGENTE          ← palavra "urgente" / termos críticos + estrela
├── Consultivo          ← consultas jurídicas e pedidos de parecer
├── Audiências          ← tudo sobre audiências
├── Propaganda          ← marketing, promoções, spam
├── Clientes/
│   ├── Apdata          ├── Hubees           ├── Pier
│   ├── Baymetrics      ├── Ifood            ├── Pravaler
│   ├── Bipa            ├── Inter            ├── Quero
│   ├── Buser           ├── KPG              ├── Rabbot
│   ├── Cuidar.me - Dr. Consulta             ├── Safira
│   ├── Digibee         ├── Lemon            ├── Solinftec
│   ├── Frete - CargoX  ├── Musa             └── Outros Clientes
│   ├── GFT             ├── Nuvemshop
│   ├── Grupo Dória     ├── Peg&Pet
│   └── Gupy
└── Outros
```

**Regra:** cliente identificado mas sem marcador próprio → `Outros Clientes`.

---

## PASSO 1 — Subir o código no GitHub

```bash
cd falaw-gmail-render
git init
git add .
git commit -m "Falaw Gmail Automacao"
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
| `RUN_TOKEN` | crie uma senha forte (ex: `openssl rand -hex 24`) |
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

1. **Manual Deploy → Deploy latest commit**
2. Teste o status: abra `https://falaw-gmail-automacao.onrender.com/` no navegador
3. Teste a execução (PowerShell):

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

A resposta traz o resumo: usuários processados, emails, urgentes, erros.

---

## PASSO 6 — Agendar com cron-job.org (gratuito)

1. Crie conta em [cron-job.org](https://cron-job.org)
2. **Create cronjob:**
   - **Title:** Falaw Gmail Automação
   - **URL:** `https://falaw-gmail-automacao.onrender.com/run`
   - **Schedule:** a cada 30 minutos
   - **Request method:** POST
3. Em **Advanced → Headers**, adicione:
   - `X-Run-Token` = o mesmo valor do RUN_TOKEN
4. Em **Advanced → Timeout:** 300 segundos
5. Salvar e ativar

---

## PASSO 7 — Ativar para todos os colaboradores

Depois de alguns dias testando só na sua conta:

1. Render → Environment → `RUN_FOR_ALL_USERS` = `true`
2. O serviço redeploya automaticamente
3. Na próxima execução, os marcadores são criados na caixa de
   **todos os usuários ativos** do falaw.com.br

---

## Como adicionar novos clientes

Edite `automacao.py` no GitHub (pode ser direto pelo site), no dicionário
`CLIENTES_CONHECIDOS`:

```python
CLIENTES_CONHECIDOS = {
    ...
    "NovoCliente": "Falaw/Clientes/NovoCliente",   # ← adicione assim
}
```

⚠️ Não use "/" no nome final do marcador — no Gmail, "/" cria subnível.
(Por isso "Cuidar.me/dr. consulta" virou "Cuidar.me - Dr. Consulta".)

Commit → Render redeploya sozinho → marcadores criados automaticamente
na próxima execução, em todas as contas.

---

## Monitoramento

- **Logs em tempo real:** Render → seu serviço → aba **Logs**
- **Histórico de execuções:** cron-job.org → seu job → **Execution history**
- **Alertas de falha:** cron-job.org → Settings → Notifications (email automático)

---

## Solução de problemas

| Sintoma | Causa provável | Solução |
|---|---|---|
| 401 no /run | Token errado | Confira X-Run-Token = RUN_TOKEN |
| `unauthorized_client` nos logs | Delegação não propagou | Aguarde até 24h ou confira Client ID/escopos no Admin |
| `RateLimitReached` do GitHub Models | Limite diário do plano free | Reduza MAX_EMAILS_PER_RUN ou aumente o intervalo do cron |
| Primeira execução do dia demora | Hibernação do plano Free | Normal — timeout de 300s no cron resolve |
| Muitos usuários = execução lenta | Volume alto de emails | Reduza HOURS_LOOKBACK para 1 |
