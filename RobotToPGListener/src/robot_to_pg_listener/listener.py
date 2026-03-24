"""Robot Framework listener: PostgreSQL persistence via pg8000 (native API)."""

from __future__ import annotations

import json
import logging
import os
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

import pg8000.native

logger = logging.getLogger(__name__)


def escape_sql_string(value: Any) -> str:
    if value is None:
        return "NULL"
    str_value = str(value)
    escaped = str_value.replace("'", "''")
    return f"'{escaped}'"


def escape_sql_identifier(value: Any) -> str:
    if value is None:
        return "NULL"
    str_value = str(value)
    escaped = str_value.replace('"', '""')
    return f'"{escaped}"'


def list_to_pg_array(
    values: Optional[List[Any]],
    array_type: str = "text",
) -> str:
    if not values:
        return f"ARRAY[]::{array_type}[]"

    if array_type in ("integer", "bigint"):
        escaped_values: List[str] = []
        for v in values:
            if v is None:
                escaped_values.append("NULL")
            elif isinstance(v, str) and not str(v).strip():
                escaped_values.append("NULL")
            else:
                escaped_values.append(str(v))
        return f"ARRAY[{','.join(escaped_values)}]::{array_type}[]"

    parts: List[str] = []
    for v in values:
        if v is None:
            parts.append("NULL")
        else:
            parts.append(escape_sql_string(v))
    return f"ARRAY[{','.join(parts)}]::{array_type}[]"


def new_row_id() -> str:
    """Identificador único por linha (32 caracteres hex, derivado de UUID)."""
    return uuid.uuid4().hex


class ConnectionPool:
    """Pool simples de conexões PostgreSQL (pg8000.native)."""

    _pools: Dict[str, "ConnectionPool"] = {}
    _lock = threading.Lock()

    def __init__(
        self,
        min_size: int = 2,
        max_size: int = 10,
        **connection_params: Any,
    ):
        self.min_size = min_size
        self.max_size = max_size
        self.connection_params = connection_params
        self.connections: List[Any] = []
        self.in_use: set = set()
        self.lock = threading.Lock()
        self._initialize_pool()

    def _initialize_pool(self) -> None:
        for _ in range(self.min_size):
            try:
                conn = pg8000.native.Connection(**self.connection_params)
                self.connections.append(conn)
            except Exception as e:
                logger.warning("Erro ao criar conexão inicial no pool: %s", e)

    def get_connection(self) -> Any:
        with self.lock:
            for conn in self.connections:
                if conn not in self.in_use:
                    try:
                        conn.run("SELECT 1")
                        self.in_use.add(conn)
                        return conn
                    except Exception:
                        try:
                            conn.close()
                        except Exception:
                            pass
                        self.connections.remove(conn)

            if len(self.connections) < self.max_size:
                try:
                    conn = pg8000.native.Connection(**self.connection_params)
                    self.connections.append(conn)
                    self.in_use.add(conn)
                    return conn
                except Exception as e:
                    logger.error("Erro ao criar nova conexão: %s", e)
                    raise

            logger.warning("Pool cheio, aguardando conexão disponível...")
            raise RuntimeError("Pool de conexões esgotado")

    def return_connection(self, conn: Any) -> None:
        with self.lock:
            if conn in self.in_use:
                self.in_use.remove(conn)

    def close_all(self) -> None:
        with self.lock:
            for conn in self.connections:
                try:
                    conn.close()
                except Exception:
                    pass
            self.connections.clear()
            self.in_use.clear()

    @classmethod
    def get_pool(cls, pool_key: str, **connection_params: Any) -> "ConnectionPool":
        with cls._lock:
            if pool_key not in cls._pools:
                cls._pools[pool_key] = cls(**connection_params)
            return cls._pools[pool_key]


def _parse_global_vars_from_env() -> Dict[str, str]:
    raw = os.environ.get("RTPG_GLOBAL_VARS", "").strip()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(
            "RTPG_GLOBAL_VARS deve ser JSON objeto, ex. "
            '{"MY_TAG":"smoke","BUILD":"123"}'
        ) from e
    if not isinstance(data, dict):
        raise ValueError(
            "RTPG_GLOBAL_VARS deve ser um objeto JSON (dicionário)."
        )
    out: Dict[str, str] = {}
    for k, v in data.items():
        out[str(k)] = "" if v is None else str(v)
    return out


def _load_db_config() -> Dict[str, Any]:
    """Carrega parâmetros pg8000.native.Connection.

    Ordem: RTPG_DB_*; depois DB_QA_*; depois DB_USER/DB_PASSWORD com host
    (RTPG_DB_HOST, DB_QA_HOST ou DB_HOST).
    """
    host = (
        os.environ.get("RTPG_DB_HOST")
        or os.environ.get("DB_QA_HOST")
        or os.environ.get("DB_HOST")
    )
    port_s = (
        os.environ.get("RTPG_DB_PORT")
        or os.environ.get("DB_QA_PORT")
        or "5432"
    )
    try:
        port = int(port_s)
    except ValueError as e:
        raise ValueError(f"Porta de banco inválida: {port_s!r}") from e

    database = (
        os.environ.get("RTPG_DB_NAME")
        or os.environ.get("DB_QA_NAME")
        or os.environ.get("PGDATABASE")
    )
    user = (
        os.environ.get("RTPG_DB_USER")
        or os.environ.get("DB_QA_USER")
        or os.environ.get("DB_USER")
    )
    password = (
        os.environ.get("RTPG_DB_PASSWORD")
        or os.environ.get("DB_QA_PASSWORD")
        or os.environ.get("DB_PASSWORD")
    )

    if not all([host, database, user, password]):
        raise ValueError(
            "PostgreSQL incompleta: use RTPG_DB_HOST, RTPG_DB_NAME, "
            "RTPG_DB_USER, RTPG_DB_PASSWORD (ou DB_QA_* / DB_USER com host). "
            "Não há host embutido."
        )

    cfg: Dict[str, Any] = {
        "host": host,
        "port": port,
        "database": database,
        "user": user,
        "password": password,
    }

    return cfg


class Listener:
    """
    Listener RF: grava execução dos testes em PostgreSQL (tabela robot_runs).

    Uso:
        robot --listener robot_to_pg_listener.Listener tests/

    Banco: RTPG_DB_* (ver README). Cada teste recebe id (hash) único; run_at na 1ª start_suite.
    Variáveis extras nos testes: RTPG_GLOBAL_VARS (JSON objeto)
    """

    ROBOT_LIBRARY_SCOPE = "GLOBAL"
    ROBOT_LISTENER_API_VERSION = 3

    def __init__(self, *_args: str):
        self._suite_stack: List[str] = []
        self._run_at_iso: Optional[str] = None

        self.id_list: List[str] = []
        self.run_at_list: List[str] = []
        self.suite_name_list: List[str] = []
        self.test_name_list: List[str] = []
        self.test_tags_list: List[str] = []
        self.status_list: List[str] = []
        self.test_message_list: List[str] = []
        self.test_doc_list: List[str] = []
        self.fallback_data: Optional[str] = None

        self._extra_global_vars = _parse_global_vars_from_env()
        self._table_runs = os.environ.get("RTPG_TABLE_RUNS", "robot_runs")

        db_cfg = _load_db_config()
        min_pool = int(os.environ.get("RTPG_POOL_MIN", "2"))
        max_pool = int(os.environ.get("RTPG_POOL_MAX", "10"))
        pool_key = f"{db_cfg['host']}:{db_cfg['port']}:{db_cfg['database']}"
        self.pool = ConnectionPool.get_pool(
            pool_key,
            min_size=min_pool,
            max_size=max_pool,
            **db_cfg,
        )
        self.conn: Any = None

    def _ensure_run_at(self) -> str:
        if self._run_at_iso is None:
            self._run_at_iso = datetime.now(timezone.utc).isoformat()
        return self._run_at_iso

    def _get_connection(self) -> Any:
        if not self.conn:
            self.conn = self.pool.get_connection()
        return self.conn

    def _execute_with_retry(
        self,
        query_func: Callable[[], Any],
        max_retries: int = 3,
        operation_name: str = "operação",
    ) -> Any:
        last_exception: Optional[Exception] = None
        for attempt in range(max_retries):
            try:
                return query_func()
            except Exception as e:
                last_exception = e
                logger.warning(
                    "Tentativa %s/%s de %s falhou: %s",
                    attempt + 1,
                    max_retries,
                    operation_name,
                    str(e)[:200],
                )
                if attempt < max_retries - 1:
                    wait_time = 2**attempt
                    time.sleep(wait_time)
                    try:
                        if self.conn:
                            self.pool.return_connection(self.conn)
                            self.conn = None
                        self._get_connection()
                    except Exception as conn_error:
                        logger.warning("Erro ao obter nova conexão: %s", conn_error)
        logger.error(
            "Todas as %s tentativas de %s falharam",
            max_retries,
            operation_name,
        )
        assert last_exception is not None
        raise last_exception

    def _apply_global_variables(self) -> None:
        if not self._extra_global_vars:
            return
        try:
            from robot.libraries.BuiltIn import BuiltIn

            bi = BuiltIn()
            for name, value in self._extra_global_vars.items():
                bi.set_global_variable(f"${{{name}}}", value)
        except Exception as e:
            logger.warning(
                "RTPG_GLOBAL_VARS não aplicado (%s); só em execução Robot.",
                e,
            )

    def start_suite(self, suite: Any, result: Any) -> None:
        self._apply_global_variables()
        print("Nome completo da Suite: " + suite.longname)
        if self._run_at_iso is None:
            self._run_at_iso = datetime.now(timezone.utc).isoformat()
        self._suite_stack.append(suite.longname)

    def end_suite(self, suite: Any, result: Any) -> None:
        if self._suite_stack:
            self._suite_stack.pop()

    def _current_suite_name(self) -> str:
        return self._suite_stack[-1] if self._suite_stack else ""

    def start_test(self, test: Any, result: Any) -> None:
        self.run_at_list.append(self._ensure_run_at())
        self.suite_name_list.append(self._current_suite_name())

    def end_test(self, data: Any, result: Any) -> None:
        self.id_list.append(new_row_id())
        self.test_name_list.append(result.name)
        self.test_doc_list.append(result.doc or "")
        self.test_tags_list.append(json.dumps(list(result.tags), ensure_ascii=False))
        self.status_list.append(result.status)
        message = (result.message or "").replace("'", "").replace('"', "")
        self.test_message_list.append(message)

    def _save_fallback_data(self) -> Optional[str]:
        try:
            fallback_dir = os.environ.get(
                "RTPG_FALLBACK_DIR",
                os.environ.get("FALLBACK_DATA_DIR", "/tmp/robot_fallback"),
            )
            os.makedirs(fallback_dir, exist_ok=True)

            timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            filename = f"{fallback_dir}/robot_data_{timestamp}.json"

            fallback_data = {
                "run_at": self._run_at_iso,
                "id_list": self.id_list,
                "run_at_list": self.run_at_list,
                "suite_name_list": self.suite_name_list,
                "test_name_list": self.test_name_list,
                "test_tags_list": self.test_tags_list,
                "status_list": self.status_list,
                "test_message_list": self.test_message_list,
                "test_doc_list": self.test_doc_list,
                "timestamp": timestamp,
            }

            with open(filename, "w", encoding="utf-8") as f:
                json.dump(fallback_data, f, ensure_ascii=False, indent=2)

            logger.warning("Dados salvos em arquivo de fallback: %s", filename)
            self.fallback_data = filename
            return filename
        except Exception as e:
            logger.error("Erro ao salvar dados de fallback: %s", e)
            return None

    def close(self) -> None:
        print("\n\nLISTENER robot_to_pg_listener.Listener")
        logging.basicConfig(stream=sys.stderr, level=logging.DEBUG)
        logging.info("Enviando dados para PostgreSQL...")

        if not self.test_name_list:
            logging.info("Nenhum dado de teste para inserir")
            if self.conn:
                self.pool.return_connection(self.conn)
            return

        truns = escape_sql_identifier(self._table_runs)

        id_arr = list_to_pg_array(self.id_list, "text")
        run_at_arr = list_to_pg_array(self.run_at_list, "text")
        suite_name_arr = list_to_pg_array(self.suite_name_list, "text")
        test_name_arr = list_to_pg_array(self.test_name_list, "text")
        test_tags_arr = list_to_pg_array(self.test_tags_list, "text")
        status_arr = list_to_pg_array(self.status_list, "text")
        msg_arr = list_to_pg_array(self.test_message_list, "text")
        doc_arr = list_to_pg_array(self.test_doc_list, "text")

        query = f"""INSERT INTO {truns}(id,
                                         run_at,
                                         suite_name,
                                         test_name,
                                         test_tags,
                                         status,
                                         log_fail,
                                         test_doc)
                   SELECT t.id,
                          t.run_at::timestamptz,
                          t.suite_name,
                          t.test_name,
                          t.test_tags,
                          t.status,
                          t.log_fail,
                          t.test_doc
                   FROM unnest({id_arr},
                               {run_at_arr},
                               {suite_name_arr},
                               {test_name_arr},
                               {test_tags_arr},
                               {status_arr},
                               {msg_arr},
                               {doc_arr})
                        AS t(id,
                             run_at,
                             suite_name,
                             test_name,
                             test_tags,
                             status,
                             log_fail,
                             test_doc)"""

        def execute_main_insert() -> None:
            conn = self._get_connection()
            conn.run(query)

        try:
            self._execute_with_retry(execute_main_insert, operation_name="inserir dados")
            logging.info("Resultado inserido na base")
        except Exception as e:
            logging.error("Erro ao inserir dados principais: %s", e)
            self._save_fallback_data()

        if self.conn:
            try:
                self.pool.return_connection(self.conn)
            except Exception as e:
                logging.warning("Erro ao retornar conexão ao pool: %s", e)
            finally:
                self.conn = None

        logging.info("Conexão retornada ao pool")


# Nomes antigos em código que importava direto de listener
RobotToPGListener = Listener
RobotToPG = Listener
