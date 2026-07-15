# robot-to-pg-listener

**Languages:** [English](README.en.md) | [Português (Brasil)](README.md)

**PyPI package:** [https://pypi.org/project/robot-to-pg-listener/](https://pypi.org/project/robot-to-pg-listener/)

Generic **Robot Framework listener** that **collects test results** during a run and, at the end, **writes one row per test** to **PostgreSQL** (via `pg8000`). Each row gets an auto-generated **`id`** (hash) and a **`run_at`** timestamp set on the **first** `start_suite` of the run (UTC). The suite is identified by Robot’s **`longname`**; tags are stored in a single **JSON** column.

If you have never used a listener: Robot calls hooks such as `start_suite`, `end_test`, and `close` on your class. This package builds a batch `INSERT` into the configured table.

---

## How the listener works

1. **Connection** — On startup, reads PostgreSQL settings from the environment and opens a **connection pool**.

2. **Suites (`start_suite` / `end_suite`)** — On the **first** `start_suite`, records **`run_at`** (UTC datetime, ISO 8601). On every `start_suite`, pushes the suite **`longname`** onto a stack (nested suites). Optionally applies **`RTPG_GLOBAL_VARS`**.

3. **During tests** —
   - On **`start_test`**, attaches the run’s **`run_at`** and the current suite name.
   - On **`end_test`**, generates a unique **`id`** (32 hex characters from a UUID), and records test name, documentation, tags as JSON, status, and a sanitized failure message.

4. **End (`close`)** — Batch **`INSERT`** into the main table (default `robot_runs`). On failure, writes a **fallback JSON** file.

**`id`:** new value per test; suitable as a `text` primary key (or convertible to `uuid` in the database).

**`run_at`:** the **same** value for **all** rows of that Robot execution (time of the first `start_suite`), in UTC.

---

## Prerequisites

- Python **3.9+** in the **same** environment where you run the `robot` command
- Reachable **PostgreSQL** with the **`robot_runs`** table (or the name in `RTPG_TABLE_RUNS`) created as described below

The PyPI package **already depends** on `robotframework` and `pg8000`; you do not need a separate Robot install if you use `pip` for that same interpreter.

---

## Install with `pip`

Create and activate a virtual environment (recommended), then install **`robot-to-pg-listener`**:

```bash
python3 -m venv .venv
source .venv/bin/activate   # Linux/macOS
# .venv\Scripts\activate    # Windows (cmd/PowerShell)

python -m pip install --upgrade pip
python -m pip install robot-to-pg-listener
```

Install without a venv:

```bash
python3 -m pip install robot-to-pg-listener
```

Check that the listener imports on the Python you will use with Robot:

```bash
python -c "from robot_to_pg_listener import Listener; print('OK')"
```

**From this repository’s source** (editable install):

```bash
cd RobotToPGListener
python -m pip install -e .
```

---

## Usage (Robot + listener)

1. **Create the PostgreSQL table** (see *Tables and columns* below, or copy the sample `CREATE TABLE`).

2. **Export database credentials** (required for the listener to connect):

```bash
export RTPG_DB_HOST=db.example.com
export RTPG_DB_PORT=5432
export RTPG_DB_NAME=qatests
export RTPG_DB_USER=qa
export RTPG_DB_PASSWORD=secret
```

3. **Run Robot** with the listener (recommended short form — class name is **`Listener`**; no arguments after `:`):

```bash
robot --listener robot_to_pg_listener.Listener path/to/suites/
```

The class is named **`Listener`**. `robot_to_pg_listener.RobotToPGListener` (CLI) and `from robot_to_pg_listener import RobotToPGListener` (Python) still work as compatibility aliases.

Common examples:

```bash
# Single file
robot --listener robot_to_pg_listener.Listener tests/login.robot

# Suite with tag
robot --listener robot_to_pg_listener.Listener --include smoke tests/

# Multiple listeners
robot --listener robot_to_pg_listener.Listener --listener OtherListener tests/
```

**Avoid repeating `--listener`** on every command: set fixed Robot options (shell or CI):

```bash
export ROBOT_OPTIONS="--listener robot_to_pg_listener.Listener"
robot $ROBOT_OPTIONS tests/
```

**Extra global variables for all tests** (optional), as JSON:

```bash
export RTPG_GLOBAL_VARS='{"MY_VAR":"value"}'
robot --listener robot_to_pg_listener.Listener tests/
```

**Custom destination table** (optional):

```bash
export RTPG_TABLE_RUNS=my_runs_table
robot --listener robot_to_pg_listener.Listener tests/
```

In **CI/CD** (Docker, GitHub Actions, etc.), use the **same** interpreter where `pip install` ran and set `RTPG_DB_*` for the job, for example:

```bash
robot --listener robot_to_pg_listener.Listener tests/
```

Or build the command from `ROBOT_OPTIONS` / `ROBOT_OPTS` including `--listener robot_to_pg_listener.Listener`.

---

## Environment variables

### Database (required to connect)

| Variable | Description |
|----------|-------------|
| `RTPG_DB_HOST` | PostgreSQL host |
| `RTPG_DB_NAME` | Database name |
| `RTPG_DB_USER` | User |
| `RTPG_DB_PASSWORD` | Password |
| `RTPG_DB_PORT` | Port (default `5432`) |

**Compatibility:** if `RTPG_DB_*` is unset, the listener tries `DB_QA_*`, then `DB_USER` / `DB_PASSWORD` with `DB_HOST` or `DB_QA_HOST`.

### Pool

| Variable | Default |
|----------|---------|
| `RTPG_POOL_MIN` | `2` |
| `RTPG_POOL_MAX` | `10` |

### Destination table

| Variable | Default |
|----------|---------|
| `RTPG_TABLE_RUNS` | `robot_runs` |

### Global variables for all tests

```bash
export RTPG_GLOBAL_VARS='{"SUITE_OWNER":"bi","EXTRA_TAGS":"smoke"}'
```

### On-disk fallback

| Variable | Default |
|----------|---------|
| `RTPG_FALLBACK_DIR` | `FALLBACK_DATA_DIR` if set, otherwise `/tmp/robot_fallback` |

---

## Tables and columns in PostgreSQL

The listener **does not create** tables. Column names below are exactly those used in the `INSERT` (unquoted → **lowercase** in PostgreSQL).

### Table `robot_runs` (or `RTPG_TABLE_RUNS`)

| Column | Content | Suggested type |
|--------|---------|----------------|
| `id` | Unique test id (32 hex characters) | `text` **PRIMARY KEY**, or `uuid` with a cast when reading |
| `run_at` | Time of the first `start_suite` of the run (UTC, ISO 8601) | `timestamptz` or `text` |
| `suite_name` | Suite `longname` for the test | `text` |
| `test_name` | Test case name | `text` |
| `test_tags` | Tags as JSON (string array), e.g. `[]`, `["smoke"]` | `text` or `jsonb` |
| `status` | e.g. `PASS`, `FAIL` | `text` |
| `log_fail` | Failure message (empty if passed) | `text` |
| `test_doc` | Robot test documentation | `text` |

The listener sends `run_at` as ISO 8601 text (UTC) and applies **`::timestamptz`** in the insert SQL, matching the `timestamptz` column in the sample below.

### Sample `CREATE TABLE`

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

In the job, install the package in the same environment where `robot` runs and export `RTPG_DB_*` (e.g. as secrets). Generic example:

```yaml
- name: Install listener and run tests
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

If your pipeline already builds Robot options from a variable (e.g. `ROBOT_OPTS`), include `--listener robot_to_pg_listener.Listener` in that string.

---

## Related projects / Projetos relacionados

| Project | Description |
|---------|-------------|
| [robotframework-gemini](https://github.com/carlosnizolli/robotframework-gemini) | Gemini oracles for RF assertions (PyPI) |
| [robotframework-gemini_exemplos](https://github.com/carlosnizolli/robotframework-gemini_exemplos) | Example suites |
| [docker-robotframework](https://github.com/carlosnizolli/docker-robotframework) | Ubuntu image for RF + Browser (includes pg8000) |
| [RoboCop](https://github.com/carlosnizolli/RoboCop) | Robocop as a GitHub Action |

---

## License

MIT (see `pyproject.toml` / the repository license file).
