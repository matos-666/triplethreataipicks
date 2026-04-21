# 🏀 NBA Props Bot

Bot de picks de NBA Player Props baseado em EV (expected value).  
Corre no GitHub Actions (grátis), envia tips para Telegram, guarda histórico em SQLite e publica página web pública com os resultados.

- **Custo total:** 0 € (GitHub free + Odds API free tier).
- **O teu PC pode estar desligado.** Tudo corre na cloud da GitHub.

---

## O que faz

1. **Todos os dias às 17:00 Lisboa** fetch de odds (The Odds API) + stats (nba.com) → calcula EV para cada prop → filtra pelos teus critérios → envia picks para Telegram.
2. **Dia seguinte às 15:00 Lisboa** — grada as picks do dia anterior contra os box scores reais.
3. **A cada 5 minutos** — polla comandos do Telegram (mudar EV mínimo, odds, mercados, etc).
4. **Página web** com histórico completo, win rate e ROI, actualizada automaticamente.

---

## Setup (15 minutos, 5 passos)

### 1. Criar bot Telegram

1. No Telegram, procura **@BotFather** e abre conversa.
2. `/newbot` → escolhe nome (ex: "O Meu NBA Props") → escolhe username terminado em `bot` (ex: `meu_nba_props_bot`).
3. Copia o **token** que te aparece (parecido com `7431234567:AAFo...`). **Guarda.**

### 2. Criar conta The Odds API

1. Vai a https://the-odds-api.com e clica **Get API Key**.
2. Regista-te (plano free = 500 créditos/mês, sem cartão).
3. Copia a **API key** do dashboard. **Guarda.**

### 3. Criar repositório no GitHub

1. Cria conta em https://github.com se não tiveres.
2. Clica **New repository** (canto superior direito, "+").
3. Nome: `nba-props-bot` (ou o que quiseres).
4. Marca **Public** (importante — privado gasta minutos Actions, público é ilimitado).
5. NÃO marques "Initialize with README".
6. Clica **Create repository**.

### 4. Enviar este código para o repo

Opção **A — drag & drop (mais fácil):**

1. No teu Mac, abre a pasta `nba-props-bot` (dentro de `Desktop/Previews.pt`).
2. No repo do GitHub acabado de criar, clica o link **"uploading an existing file"**.
3. Arrasta **todo o conteúdo da pasta** `nba-props-bot` (não a pasta em si — os ficheiros e subpastas).
4. Scroll para baixo → **Commit changes**.

Opção **B — terminal (se preferires):**

```bash
cd ~/Desktop/Previews.pt/nba-props-bot
git init
git add .
git commit -m "initial"
git branch -M main
git remote add origin https://github.com/O_TEU_USER/nba-props-bot.git
git push -u origin main
```

### 5. Configurar secrets e activar

No teu repo GitHub:

**a) Adicionar os 2 secrets:**

1. **Settings** (topo direito do repo) → **Secrets and variables** → **Actions**.
2. Clica **New repository secret**.
3. Nome: `TELEGRAM_BOT_TOKEN` → Valor: o token do BotFather → **Add secret**.
4. Clica **New repository secret** outra vez.
5. Nome: `ODDS_API_KEY` → Valor: a key do The Odds API → **Add secret**.

**b) Dar permissão de escrita aos Actions:**

1. **Settings** → **Actions** → **General** → scroll até **Workflow permissions**.
2. Marca **Read and write permissions** → **Save**.

**c) Activar GitHub Pages:**

1. **Settings** → **Pages**.
2. Source: **GitHub Actions** → Save.

**d) Registar no bot:**

1. Abre o Telegram, procura o teu bot pelo username.
2. Envia `/start`. Deves receber mensagem de boas-vindas.
3. Envia `/config` para ver os filtros padrão.

**Done.** A primeira run automática é às 16:00 UTC (17:00 Lisboa). Para testar já:

1. Repo → aba **Actions** → **Daily NBA Picks** → **Run workflow** (botão direita) → Run.
2. Aguarda ~2 min. Deves receber picks no Telegram (se houver jogos hoje e picks acima do teu EV).

---

## Comandos do bot

| Comando | Efeito |
|---|---|
| `/start` | Regista este chat para receber picks |
| `/config` | Mostra configuração actual |
| `/setev 0.05` | EV mínimo = 5% |
| `/setoddsmin 1.5` | Odd decimal mínima |
| `/setoddsmax 3.0` | Odd decimal máxima |
| `/setmaxevents 5` | Máx jogos analisados/dia (⚠️ mais = mais API credits gastos) |
| `/setkelly 0.25` | Fracção Kelly para sugestão de stake |
| `/markets` | Listar mercados suportados |
| `/addmarket player_threes` | Adicionar mercado |
| `/rmmarket player_turnovers` | Remover mercado |
| `/stats` | Win rate e unidades |
| `/stop` | Parar de receber picks |

Comandos demoram até 5 min a aplicar (é o intervalo do polling).

---

## Onde vejo o histórico?

1. **Telegram** — `/stats` resumo rápido.
2. **Web** — URL do teu GitHub Pages: `https://O_TEU_USER.github.io/nba-props-bot/` (aparece em Settings → Pages depois do primeiro deploy). Tabela completa com filtros.
3. **Ficheiro** — `data/history.db` (SQLite) no repo. Abres com DB Browser for SQLite (grátis) se quiseres queries custom.

---

## Budget da Odds API

Free tier = **500 créditos/mês**.

Consumo estimado (config padrão):
- 1 credit para listar eventos
- 5 jogos × 4 mercados = 20 credits/dia
- **Total ~21 credits/dia × 30 dias ≈ 630 créditos**

⚠️ **Acima do limite free.** Solução:
- Baixa `/setmaxevents 3` → ~13 credits/dia = 390/mês ✓
- Ou reduz mercados (`/rmmarket player_threes`)
- Ou paga $30/mês pelo plano básico (não recomendado inicialmente)

Ajusta conforme os teus créditos restantes (dashboard do The Odds API mostra).

---

## Problemas comuns

**"Não recebo picks nenhum":**
- Só corre em dias com jogos NBA.
- Verifica na aba Actions se o workflow correu com sucesso.
- Modelo é conservador — se não há picks acima do teu EV, não manda nada. Experimenta `/setev 0.02`.

**"nba_api falhou" nos logs:**
- Às vezes o nba.com bloqueia requests da cloud. Normalmente passa na run seguinte.
- Se persistir, abre issue.

**"Odds API 401":**
- Secret `ODDS_API_KEY` mal configurado. Refaz passo 5a.

**Página web não actualiza:**
- Pode demorar 2-3 min após cada run. Dá hard-refresh (Cmd+Shift+R).

---

## Aposta responsavelmente

Este modelo usa estatística simples (distribuição Normal/Poisson sobre jogos recentes). Não tem em conta lesões, rotações, back-to-backs, matchup defensivo. **EV positivo não garante lucro a curto prazo.** Usa bankroll fixo, stakes pequenas, e trata qualquer resultado ≥1 mês como indicativo.
