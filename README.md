# robot-to-pg-listener

**Idiomas / Languages:** [Português (Brasil)](README.md) | [English](README.en.md)

**Pacote no PyPI:** [https://pypi.org/project/robot-to-pg-listener/](https://pypi.org/project/robot-to-pg-listener/)

Biblioteca Python que fornece um **listener do Robot Framework** genérico: durante a execução dos testes ele **coleta resultados** e, ao final, **grava uma linha por teste** em **PostgreSQL** (driver `pg8000`). Cada linha recebe um **`id`** (hash automático) e um instante **`run_at`** definido na **primeira** `start_suite` da execução (UTC). A suite é identificada pelo **`longname`** do Robot; as tags vão em uma coluna **JSON**.

Se você nunca usou um listener: o Robot chama métodos como `start_suite`, `end_test` e `close` na sua classe. Este pacote monta um `INSERT` em lote na tabela configurada.

---

## O que o listener faz (fluxo)

1. **Conexão** — Na inicialização, lê as variáveis de ambiente do PostgreSQL e abre um **pool de conexões**.

2. **Suites (`start_suite` / `end_suite`)** — Na **primeira** `start_suite`, grava o instante **`run_at`** (data/hora UTC, ISO 8601). Em toda `start_suite`, empilha o **`longname`** da suite (suites aninhadas). Opcionalmente aplica **`RTPG_GLOBAL_VARS`**.

3. **Durante os testes** —
   - Em **`start_test`**, associa ao teste o **`run_at`** da execução e o nome da suite corrente.
   - Em **`end_test`**, gera um **`id`** único (32 caracteres hex, derivado de UUID), grava nome do teste, documentação, tags em JSON, status e mensagem de falha sanitizada.

4. **Fim (`close`)** — **`INSERT`** em lote na tabela principal (por padrão `robot_runs`). Se falhar, grava **JSON de fallback**.

**`id`:** um valor novo por teste, adequado como chave primária `text` (ou conversível para `uuid` no banco).

**`run_at`:** o mesmo valor para **todas** as linhas daquela execução do Robot (momento da primeira `start_suite`), em UTC.

---

## Pré-requisitos

- Python **3.9+** no mesmo ambiente em que você executa o comando `robot`
- **PostgreSQL** acessível, com a tabela **`robot_runs`** (ou o nome em `RTPG_TABLE_RUNS`) criada conforme o schema deste documento

O pacote PyPI **já traz** `robotframework` e `pg8000` como dependências; não é obrigatório instalar o Robot separadamente, desde que você use o `pip` desse mesmo interpretador.

---

## Instalação com `pip`

Crie e ative um ambiente virtual (recomendado), depois instale o pacote **`robot-to-pg-listener`**:

```bash
python3 -m venv .venv
source .venv/bin/activate   # Linux/macOS
# .venv\Scripts\activate    # Windows (cmd/PowerShell)

python -m pip install --upgrade pip
python -m pip install robot-to-pg-listener
```

Instalação direta sem venv:

```bash
python3 -m pip install robot-to-pg-listener
```

Para conferir se o listener está importável no mesmo Python que você usará para o Robot:

```bash
python -c "from robot_to_pg_listener import Listener; print('OK')"
```

**A partir do código-fonte deste repositório** (modo editável):

```bash
cd RobotToPGListener
python -m pip install -e .
```

---

## Como usar (Robot + listener)

1. **Crie a tabela no PostgreSQL** (veja a seção *Tabelas e colunas* mais abaixo, ou copie o `CREATE TABLE` de exemplo).

2. **Exporte as credenciais do banco** (obrigatório para o listener conectar):

```bash
export RTPG_DB_HOST=db.example.com
export RTPG_DB_PORT=5432
export RTPG_DB_NAME=qatests
export RTPG_DB_USER=qa
export RTPG_DB_PASSWORD=secret
```

3. **Execute o Robot** com o listener (forma curta recomendada — **`Listener`** é alias da mesma classe; sem nada após `:`):

```bash
robot --listener robot_to_pg_listener.Listener path/para/suites/
```

A classe no código chama-se **`Listener`**. Quem ainda usar `robot_to_pg_listener.RobotToPGListener` na CLI ou `from robot_to_pg_listener import RobotToPGListener` em Python continua funcionando (alias de compatibilidade).

Exemplos comuns:

```bash
# Um arquivo
robot --listener robot_to_pg_listener.Listener tests/login.robot

# Suite com tag
robot --listener robot_to_pg_listener.Listener --include smoke tests/

# Vários listeners
robot --listener robot_to_pg_listener.Listener --listener OutroListener tests/
```

**Evitar repetir `--listener`** em todo comando: defina opções fixas do Robot (shell ou CI):

```bash
export ROBOT_OPTIONS="--listener robot_to_pg_listener.Listener"
robot $ROBOT_OPTIONS tests/
```

**Variáveis globais extras nos testes** (opcional), em JSON:

```bash
export RTPG_GLOBAL_VARS='{"MINHA_VAR":"valor"}'
robot --listener robot_to_pg_listener.Listener tests/
```

**Outra tabela destino** (opcional):

```bash
export RTPG_TABLE_RUNS=minha_tabela_runs
robot --listener robot_to_pg_listener.Listener tests/
```

Em **CI/CD** (Docker, GitHub Actions, etc.), use o **mesmo** interpretador onde o `pip install` foi feito e defina as mesmas variáveis `RUN`/`ENV` do job, por exemplo:

```bash
robot --listener robot_to_pg_listener.Listener tests/
```

Ou monte a linha de comando a partir de `ROBOT_OPTIONS` / `ROBOT_OPTS` com `--listener robot_to_pg_listener.Listener`.

---

## Variáveis de ambiente

### Banco de dados (obrigatórias para conectar)

| Variável | Descrição |
|----------|-----------|
| `RTPG_DB_HOST` | Host PostgreSQL |
| `RTPG_DB_NAME` | Nome do banco |
| `RTPG_DB_USER` | Usuário |
| `RTPG_DB_PASSWORD` | Senha |
| `RTPG_DB_PORT` | Porta (padrão `5432`) |

**Compatibilidade:** se `RTPG_DB_*` não estiver definido, o listener tenta `DB_QA_*` e depois `DB_USER` / `DB_PASSWORD` com `DB_HOST` ou `DB_QA_HOST`.

### Pool

| Variável | Padrão |
|----------|--------|
| `RTPG_POOL_MIN` | `2` |
| `RTPG_POOL_MAX` | `10` |

### Tabela de destino

| Variável | Padrão |
|----------|--------|
| `RTPG_TABLE_RUNS` | `robot_runs` |

### Variáveis globais para todos os testes

```bash
export RTPG_GLOBAL_VARS='{"SUITE_OWNER":"bi","EXTRA_TAGS":"smoke"}'
```

### Fallback em disco

| Variável | Padrão |
|----------|--------|
| `RTPG_FALLBACK_DIR` | `FALLBACK_DATA_DIR` se existir, senão `/tmp/robot_fallback` |

---

## Tabelas e colunas no PostgreSQL

O listener **não cria** tabelas. Os nomes abaixo são os usados no `INSERT` (minúsculas sem aspas).

### Tabela `robot_runs` (ou `RTPG_TABLE_RUNS`)

| Coluna | Conteúdo | Tipo sugerido |
|--------|----------|----------------|
| `id` | Identificador único do teste (hex 32 caracteres) | `text` **PRIMARY KEY**, ou `uuid` com cast na leitura |
| `run_at` | Momento da primeira `start_suite` da execução (UTC, ISO 8601) | `timestamptz` ou `text` |
| `suite_name` | `longname` da suite do teste | `text` |
| `test_name` | Nome do caso de teste | `text` |
| `test_tags` | Tags em JSON (array de strings), ex.: `[]`, `["smoke"]` | `text` ou `jsonb` |
| `status` | Ex.: `PASS`, `FAIL` | `text` |
| `log_fail` | Mensagem de falha (vazia se passou) | `text` |
| `test_doc` | Documentação do teste no Robot | `text` |

O listener envia `run_at` como texto ISO 8601 (UTC) e aplica **`::timestamptz`** no SQL de insert, compatível com a coluna `timestamptz` do exemplo abaixo.

### Exemplo de `CREATE TABLE`

```sql
CREATE TABLE robot_runs (
  id          text PRIMARY KEY,
  run_at      timestamptz NOT NULL,
  suite_name  text NOT NULL DEFAULT '',
  test_name   text NOT NULL,
  test_tags   text NOT NULL DEFAULT '[]',
  status      text NOT NULL,
  log_fail    text NOT NULL DEFAULT '',
  test_doc    text NOT NULL DEFAULT ''
);
```

---

## Docker / GitHub Actions

No job, instale o pacote no mesmo ambiente onde o `robot` roda e exporte `RTPG_DB_*` (por exemplo como *secrets*). Exemplo genérico:

```yaml
- name: Instalar listener e rodar testes
  env:
    RTPG_DB_HOST: ${{ secrets.PGHOST }}
    RTPG_DB_NAME: ${{ secrets.PGDATABASE }}
    RTPG_DB_USER: ${{ secrets.PGUSER }}
    RTPG_DB_PASSWORD: ${{ secrets.PGPASSWORD }}
    RTPG_DB_PORT: "5432"
  run: |
    python -m pip install robot-to-pg-listener
    robot --listener robot_to_pg_listener.Listener tests/
```

Se o seu pipeline já monta opções do Robot a partir de uma variável (ex.: `ROBOT_OPTS`), inclua `--listener robot_to_pg_listener.Listener` nessa string.

---

## Related projects / Projetos relacionados

| Project | Description |
|---------|-------------|
| [robotframework-gemini](https://github.com/carlosnizolli/robotframework-gemini) | Gemini oracles for RF assertions (PyPI) |
| [robotframework-gemini_exemplos](https://github.com/carlosnizolli/robotframework-gemini_exemplos) | Example suites |
| [docker-robotframework](https://github.com/carlosnizolli/docker-robotframework) | Ubuntu image for RF + Browser (includes pg8000) |
| [RoboCop](https://github.com/carlosnizolli/RoboCop) | Robocop as a GitHub Action |

---

## Licença

MIT (veja `pyproject.toml` / arquivo de licença do repositório).
