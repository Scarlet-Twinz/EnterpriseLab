"""Enterprise Lab's fictional business API."""
from __future__ import annotations

import logging
import threading
import time
import uuid
from collections import Counter, defaultdict
from typing import Annotated, Any

import psycopg
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.db import connect_as
from app.security import Principal, authenticate

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("enterprise_lab")
_METRIC_LOCK = threading.Lock()
_REQUEST_COUNTS: Counter[tuple[str, str, int]] = Counter()
_REQUEST_DURATION: dict[tuple[str, str, int], list[float]] = defaultdict(lambda: [0.0, 0.0])

app = FastAPI(
    title="Enterprise Lab Customer API",
    version="0.1.0",
    description=(
        "A fictional multi-tenant company API used to exercise a real Agata Proxima deployment. "
        "All business database traffic is routed through the configured Proxima Engine."
    ),
)

PrincipalDep = Annotated[Principal, Depends(authenticate)]
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=160)]
Email = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=3,
        max_length=254,
        pattern=r"^[^@\\s]+@[^@\\s]+\\.[^@\\s]+$",
    ),
]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CustomerCreate(StrictModel):
    name: Name
    email: Email
    status: str = Field(default="active", pattern="^(active|prospect|suspended)$")


class InvoiceCreate(StrictModel):
    customer_id: int = Field(gt=0)
    amount_cents: int = Field(gt=0, le=100_000_000)
    currency: str = Field(default="USD", pattern="^[A-Z]{3}$")
    description: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=300)]


class DocumentCreate(StrictModel):
    title: Name
    classification: str = Field(default="confidential", pattern="^(internal|confidential|restricted)$")
    body: Annotated[str, StringConstraints(min_length=1, max_length=20000)]


class EmployeeCreate(StrictModel):
    name: Name
    email: Email
    department: Name
    job_title: Name
    status: str = Field(default="active", pattern="^(active|inactive|leave)$")


class ProjectCreate(StrictModel):
    name: Name
    description: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]
    budget_cents: int = Field(ge=0, le=10_000_000_000)
    status: str = Field(default="planned", pattern="^(planned|active|paused|completed)$")


def _run(principal: Principal, sql: str, params: tuple[Any, ...] = (), *, one: bool = False) -> Any:
    try:
        with connect_as(principal) as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                result = cur.fetchone() if one else cur.fetchall()
            conn.commit()
            return result
    except (psycopg.Error, RuntimeError) as exc:
        # Do not log DSNs, tokens, API keys, or raw request bodies.
        logger.warning("database_request_failed tenant=%s error_type=%s", principal.tenant_id, type(exc).__name__)
        raise HTTPException(status_code=503, detail="Protected database request failed") from exc


@app.middleware("http")
async def request_evidence(request: Request, call_next: Any) -> Response:
    request_id = request.headers.get("x-request-id") or str(uuid.uuid4())
    started = time.perf_counter()
    response: Response
    try:
        response = await call_next(request)
    except Exception:
        logger.exception("request_failed request_id=%s method=%s path=%s", request_id, request.method, request.url.path)
        raise
    duration_ms = round((time.perf_counter() - started) * 1000, 2)
    response.headers["x-request-id"] = request_id
    route = request.scope.get("route")
    route_template = getattr(route, "path", "unmatched")
    metric_key = (request.method, route_template, response.status_code)
    with _METRIC_LOCK:
        _REQUEST_COUNTS[metric_key] += 1
        _REQUEST_DURATION[metric_key][0] += duration_ms
        _REQUEST_DURATION[metric_key][1] += 1
    logger.info(
        "request_complete request_id=%s method=%s path=%s status=%s duration_ms=%s",
        request_id, request.method, request.url.path, response.status_code, duration_ms,
    )
    return response


@app.get("/metrics", include_in_schema=False)
def metrics() -> Response:
    """Prometheus text exposition; labels intentionally exclude tenant IDs and secrets."""
    lines = [
        "# HELP enterprise_lab_process_up Whether the Enterprise Lab API process is responding.",
        "# TYPE enterprise_lab_process_up gauge",
        "enterprise_lab_process_up 1",
        "# HELP enterprise_lab_http_requests_total Total HTTP requests handled by route and status.",
        "# TYPE enterprise_lab_http_requests_total counter",
        "# HELP enterprise_lab_http_request_duration_ms_sum Total request duration in milliseconds.",
        "# TYPE enterprise_lab_http_request_duration_ms_sum counter",
        "# HELP enterprise_lab_http_request_duration_ms_count Number of request duration observations.",
        "# TYPE enterprise_lab_http_request_duration_ms_count counter",
    ]
    with _METRIC_LOCK:
        counts = list(_REQUEST_COUNTS.items())
        durations = {key: value[:] for key, value in _REQUEST_DURATION.items()}
    for (method, route, status), count in sorted(counts):
        labels = f'method="{method}",route="{route}",status="{status}"'
        total_ms, observations = durations[(method, route, status)]
        lines.append(f"enterprise_lab_http_requests_total{{{labels}}} {count}")
        lines.append(f"enterprise_lab_http_request_duration_ms_sum{{{labels}}} {total_ms:.2f}")
        lines.append(f"enterprise_lab_http_request_duration_ms_count{{{labels}}} {int(observations)}")
    return Response(content="\n".join(lines) + "\n", media_type="text/plain; version=0.0.4; charset=utf-8")


@app.get("/healthz")
def healthz() -> dict[str, str]:
    """Process liveness only; this endpoint does not claim Proxima/database health."""
    return {"status": "ok", "service": "enterprise-lab"}


@app.get("/api/v1/me")
def me(principal: PrincipalDep) -> dict[str, str]:
    return {"tenant_id": principal.tenant_id, "role": principal.role}


@app.get("/api/v1/customers")
def list_customers(principal: PrincipalDep) -> list[dict[str, Any]]:
    return _run(
        principal,
        "SELECT id, name, email, status, created_at FROM customers WHERE tenant_id = %s ORDER BY id",
        (principal.tenant_id,),
    )


@app.post("/api/v1/customers", status_code=201)
def create_customer(payload: CustomerCreate, principal: PrincipalDep) -> dict[str, Any]:
    _require_write(principal)
    return _run(
        principal,
        """INSERT INTO customers (tenant_id, name, email, status)
           VALUES (%s, %s, %s, %s)
           RETURNING id, name, email, status, created_at""",
        (principal.tenant_id, payload.name, payload.email, payload.status),
        one=True,
    )


@app.get("/api/v1/customers/{customer_id}")
def get_customer(customer_id: int, principal: PrincipalDep) -> dict[str, Any]:
    row = _run(
        principal,
        """SELECT id, name, email, status, created_at FROM customers
           WHERE tenant_id = %s AND id = %s""",
        (principal.tenant_id, customer_id),
        one=True,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Customer not found")
    return row


@app.get("/api/v1/invoices")
def list_invoices(principal: PrincipalDep) -> list[dict[str, Any]]:
    return _run(
        principal,
        """SELECT id, customer_id, amount_cents, currency, description, status, created_at
           FROM invoices WHERE tenant_id = %s ORDER BY id""",
        (principal.tenant_id,),
    )


@app.post("/api/v1/invoices", status_code=201)
def create_invoice(payload: InvoiceCreate, principal: PrincipalDep) -> dict[str, Any]:
    _require_write(principal)
    customer = _run(
        principal,
        "SELECT id FROM customers WHERE tenant_id = %s AND id = %s",
        (principal.tenant_id, payload.customer_id),
        one=True,
    )
    if customer is None:
        raise HTTPException(status_code=404, detail="Customer not found")
    return _run(
        principal,
        """INSERT INTO invoices (tenant_id, customer_id, amount_cents, currency, description)
           VALUES (%s, %s, %s, %s, %s)
           RETURNING id, customer_id, amount_cents, currency, description, status, created_at""",
        (principal.tenant_id, payload.customer_id, payload.amount_cents, payload.currency, payload.description),
        one=True,
    )


@app.get("/api/v1/employees")
def list_employees(principal: PrincipalDep) -> list[dict[str, Any]]:
    return _run(
        principal,
        """SELECT id, name, email, department, job_title, status, created_at
           FROM employees WHERE tenant_id = %s ORDER BY id""",
        (principal.tenant_id,),
    )


@app.post("/api/v1/employees", status_code=201)
def create_employee(payload: EmployeeCreate, principal: PrincipalDep) -> dict[str, Any]:
    _require_write(principal)
    return _run(
        principal,
        """INSERT INTO employees (tenant_id, name, email, department, job_title, status)
           VALUES (%s, %s, %s, %s, %s, %s)
           RETURNING id, name, email, department, job_title, status, created_at""",
        (principal.tenant_id, payload.name, payload.email, payload.department, payload.job_title, payload.status),
        one=True,
    )


@app.get("/api/v1/projects")
def list_projects(principal: PrincipalDep) -> list[dict[str, Any]]:
    return _run(
        principal,
        """SELECT id, name, description, budget_cents, status, created_at
           FROM projects WHERE tenant_id = %s ORDER BY id""",
        (principal.tenant_id,),
    )


@app.post("/api/v1/projects", status_code=201)
def create_project(payload: ProjectCreate, principal: PrincipalDep) -> dict[str, Any]:
    _require_write(principal)
    return _run(
        principal,
        """INSERT INTO projects (tenant_id, name, description, budget_cents, status)
           VALUES (%s, %s, %s, %s, %s)
           RETURNING id, name, description, budget_cents, status, created_at""",
        (principal.tenant_id, payload.name, payload.description, payload.budget_cents, payload.status),
        one=True,
    )


@app.get("/api/v1/documents")
def list_documents(principal: PrincipalDep) -> list[dict[str, Any]]:
    return _run(
        principal,
        """SELECT id, title, classification, body, created_at FROM documents
           WHERE tenant_id = %s ORDER BY id""",
        (principal.tenant_id,),
    )


@app.post("/api/v1/documents", status_code=201)
def create_document(payload: DocumentCreate, principal: PrincipalDep) -> dict[str, Any]:
    _require_write(principal)
    return _run(
        principal,
        """INSERT INTO documents (tenant_id, title, classification, body)
           VALUES (%s, %s, %s, %s)
           RETURNING id, title, classification, body, created_at""",
        (principal.tenant_id, payload.title, payload.classification, payload.body),
        one=True,
    )


def _require_write(principal: Principal) -> None:
    if principal.role not in {"admin", "operator"}:
        raise HTTPException(status_code=403, detail="Write permission required")
