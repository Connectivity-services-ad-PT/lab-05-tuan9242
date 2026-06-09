import os
import http.client
import uuid
import time
import psycopg2
from psycopg2.extras import RealDictCursor
import json
from datetime import datetime, timezone
from enum import Enum
from typing import Dict, List, Optional, Any

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field


SERVICE_NAME = os.getenv("SERVICE_NAME", "analytics-service")
SERVICE_VERSION = os.getenv("SERVICE_VERSION", "0.5.0")
AUTH_TOKEN = os.getenv("AUTH_TOKEN", "local-dev-token")

DB_HOST = os.getenv("DB_HOST", "db")
DB_PORT = os.getenv("DB_PORT", "5432")
DB_NAME = os.getenv("POSTGRES_DB", "analyticsdb")
DB_USER = os.getenv("POSTGRES_USER", "lab05")
DB_PASSWORD = os.getenv("POSTGRES_PASSWORD", "lab05pass")


app = FastAPI(
    title="FIT4110 Lab 05 - Analytics Service (A5)",
    version=SERVICE_VERSION,
    description="Dockerized Analytics API with TimescaleDB integration under Docker Compose.",
)


class AlertSeverity(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class AlertStatus(str, Enum):
    OPEN = "OPEN"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    RESOLVED = "RESOLVED"


class ProblemDetails(BaseModel):
    type: str = "about:blank"
    title: str
    status: int = Field(..., ge=400, le=599)
    detail: str
    instance: Optional[str] = None
    errors: Optional[List[Dict]] = []


class HealthStatus(BaseModel):
    status: str
    service: str
    time: str


class CreateAlertRequest(BaseModel):
    sourceService: str = Field(..., min_length=2, max_length=80, pattern="^[a-z0-9-]+$")
    alertType: str = Field(..., description="UNAUTHORIZED_ACCESS, SENSOR_THRESHOLD_EXCEEDED, etc.")
    severity: AlertSeverity
    message: str = Field(..., min_length=5, max_length=500)
    relatedEventId: Optional[str] = None


class Alert(BaseModel):
    id: str
    sourceService: str
    alertType: str
    severity: AlertSeverity
    message: str
    relatedEventId: Optional[str] = None
    status: AlertStatus
    createdAt: str
    resolvedAt: Optional[str] = None


class AlertPage(BaseModel):
    items: List[Alert]
    nextCursor: Optional[str] = None
    hasMore: bool


class EventAccepted(BaseModel):
    eventId: str
    acceptedAt: str


def get_db_connection():
    return psycopg2.connect(
        host=DB_HOST,
        port=DB_PORT,
        database=DB_NAME,
        user=DB_USER,
        password=DB_PASSWORD
    )


def init_db():
    print(f"Connecting to database {DB_NAME} at {DB_HOST}:{DB_PORT}...")
    for i in range(15):
        try:
            conn = get_db_connection()
            cur = conn.cursor()
            # Create events table
            cur.execute("""
                CREATE TABLE IF NOT EXISTS events (
                    eventId UUID PRIMARY KEY,
                    eventType VARCHAR(100) NOT NULL,
                    occurredAt VARCHAR(50),
                    correlationId UUID,
                    source VARCHAR(100),
                    payload JSONB
                );
            """)
            # Create alerts table
            cur.execute("""
                CREATE TABLE IF NOT EXISTS alerts (
                    id UUID PRIMARY KEY,
                    "sourceService" VARCHAR(100) NOT NULL,
                    "alertType" VARCHAR(100) NOT NULL,
                    severity VARCHAR(50) NOT NULL,
                    message TEXT NOT NULL,
                    "relatedEventId" UUID,
                    status VARCHAR(50) NOT NULL,
                    "createdAt" VARCHAR(50) NOT NULL,
                    "resolvedAt" VARCHAR(50)
                );
            """)
            conn.commit()
            cur.close()
            conn.close()
            print("Database initialized successfully.")
            return
        except Exception as e:
            print(f"Database connection failed: {e}. Retrying in 2 seconds...")
            time.sleep(2)
    raise Exception("Could not connect to database after 15 retries.")


@app.on_event("startup")
def startup_event():
    init_db()


def build_problem(
    *,
    status_code: int,
    title: str,
    detail: str,
    instance: Optional[str] = None,
    problem_type: str = "about:blank",
    errors: List[Dict] = None,
) -> Dict:
    problem = {
        "type": problem_type,
        "title": title,
        "status": status_code,
        "detail": detail,
    }
    if instance:
        problem["instance"] = instance
    if errors is not None:
        problem["errors"] = errors
    return problem


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    if isinstance(exc.detail, dict):
        problem = exc.detail
    else:
        problem = build_problem(
            status_code=exc.status_code,
            title=http.client.responses.get(exc.status_code, "HTTP Error"),
            detail=str(exc.detail),
            instance=str(request.url.path),
        )

    problem.setdefault("status", exc.status_code)
    problem.setdefault("title", http.client.responses.get(exc.status_code, "HTTP Error"))
    problem.setdefault("type", "about:blank")
    problem.setdefault("detail", "Request failed")
    problem.setdefault("instance", str(request.url.path))
    problem.setdefault("errors", [])

    return JSONResponse(
        status_code=exc.status_code,
        content=problem,
        media_type="application/problem+json",
        headers=getattr(exc, "headers", None),
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    errors = []
    for error in exc.errors():
        location = ".".join(str(item) for item in error.get("loc", []))
        errors.append({
            "field": location,
            "code": error.get("type", "validation_error"),
            "message": error.get("msg", "Invalid value")
        })

    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content=build_problem(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            title="Validation error",
            detail="Payload validation failed",
            instance=str(request.url.path),
            problem_type="https://campus.local/errors/validation",
            errors=errors,
        ),
        media_type="application/problem+json",
    )


def verify_bearer_token(authorization: Optional[str] = Header(default=None)) -> None:
    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=build_problem(
                status_code=status.HTTP_401_UNAUTHORIZED,
                title="Unauthorized",
                detail="Missing Authorization header",
                problem_type="https://campus.local/errors/unauthorized",
                errors=[],
            ),
        )

    expected = f"Bearer {AUTH_TOKEN}"
    if authorization != expected:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=build_problem(
                status_code=status.HTTP_401_UNAUTHORIZED,
                title="Unauthorized",
                detail="Invalid bearer token",
                problem_type="https://campus.local/errors/unauthorized",
                errors=[],
            ),
        )


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@app.head("/health", response_class=Response)
@app.get("/health", response_model=HealthStatus)
def health(request: Request):
    if request.method == "HEAD":
        return Response(status_code=200)
    
    # Check database health
    db_ok = False
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT 1;")
        cur.fetchone()
        cur.close()
        conn.close()
        db_ok = True
    except Exception as e:
        print(f"Healthcheck database check failed: {e}")

    if not db_ok:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database connection is unhealthy"
        )

    return HealthStatus(
        status="ok",
        service=SERVICE_NAME,
        time=now_iso(),
    )


@app.post(
    "/events",
    response_model=EventAccepted,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(verify_bearer_token)],
)
def create_event(payload: Dict[Any, Any]) -> EventAccepted:
    event_type = payload.get("eventType")
    if not event_type:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=build_problem(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                title="Validation error",
                detail="eventType is required",
                problem_type="https://campus.local/errors/validation",
            )
        )
    
    allowed_event_types = ["telemetry.ingested", "camera.motion.detected", "alert.resolved", "policy.decision.created", "access.log.created"]
    if event_type not in allowed_event_types:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=build_problem(
                status_code=status.HTTP_400_BAD_REQUEST,
                title="Invalid Event Type",
                detail="eventType is not supported",
                problem_type="https://campus.local/errors/validation",
            )
        )

    value = payload.get("value")
    if value is not None and (value < -100 or value > 1000):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=build_problem(
                status_code=status.HTTP_400_BAD_REQUEST,
                title="Invalid Value",
                detail="Value out of range",
                problem_type="https://campus.local/errors/validation",
            )
        )

    event_id = payload.get("eventId", str(uuid.uuid4()))
    accepted_at = now_iso()
    occurred_at = payload.get("occurredAt", accepted_at)
    correlation_id = payload.get("correlationId")
    source = payload.get("source")

    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO events (eventId, eventType, occurredAt, correlationId, source, payload)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (eventId) DO NOTHING;
            """,
            (
                event_id,
                event_type,
                occurred_at,
                correlation_id if correlation_id else None,
                source,
                json.dumps(payload)
            )
        )
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        print(f"Error inserting event into database: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Database error: {e}"
        )

    return EventAccepted(
        eventId=event_id,
        acceptedAt=accepted_at,
    )


@app.post(
    "/alerts",
    response_model=Alert,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(verify_bearer_token)],
)
def create_alert(payload: CreateAlertRequest, response: Response) -> Alert:
    allowed_alerts = ["UNAUTHORIZED_ACCESS", "SENSOR_THRESHOLD_EXCEEDED", "UNKNOWN_PERSON", "SYSTEM_ERROR"]
    if payload.alertType not in allowed_alerts:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=build_problem(
                status_code=status.HTTP_400_BAD_REQUEST,
                title="Invalid Alert Type",
                detail="alertType must be one of allowed values",
                problem_type="https://campus.local/errors/validation",
            )
        )

    alert_id = str(uuid.uuid4())
    created_at = now_iso()
    
    alert = {
        "id": alert_id,
        "sourceService": payload.sourceService,
        "alertType": payload.alertType,
        "severity": payload.severity,
        "message": payload.message,
        "relatedEventId": payload.relatedEventId,
        "status": AlertStatus.OPEN.value,
        "createdAt": created_at,
        "resolvedAt": None,
    }
    
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO alerts (id, "sourceService", "alertType", severity, message, "relatedEventId", status, "createdAt", "resolvedAt")
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s);
            """,
            (
                alert["id"],
                alert["sourceService"],
                alert["alertType"],
                alert["severity"],
                alert["message"],
                alert["relatedEventId"],
                alert["status"],
                alert["createdAt"],
                alert["resolvedAt"]
            )
        )
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        print(f"Error inserting alert into database: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Database error: {e}"
        )

    response.headers["Location"] = f"/alerts/{alert_id}"

    return Alert(**alert)


@app.get(
    "/alerts/recent",
    dependencies=[Depends(verify_bearer_token)],
)
def get_recent_alerts(limit: int = Query(default=20, ge=1, le=100)) -> Dict[str, List[Dict]]:
    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute(
            'SELECT id, "sourceService", "alertType", severity, message, "relatedEventId", status, "createdAt", "resolvedAt" FROM alerts ORDER BY "createdAt" DESC LIMIT %s;',
            (limit,)
        )
        rows = cur.fetchall()
        cur.close()
        conn.close()
        
        items = []
        for row in rows:
            items.append({
                "id": str(row["id"]),
                "sourceService": row["sourceService"],
                "alertType": row["alertType"],
                "severity": row["severity"],
                "message": row["message"],
                "relatedEventId": str(row["relatedEventId"]) if row["relatedEventId"] else None,
                "status": row["status"],
                "createdAt": row["createdAt"],
                "resolvedAt": row["resolvedAt"]
            })
        return {"items": items[::-1]}  # Return in chronological order
    except Exception as e:
        print(f"Error fetching recent alerts: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Database error: {e}"
        )


@app.get(
    "/alerts",
    response_model=AlertPage,
    dependencies=[Depends(verify_bearer_token)],
)
def list_alerts(
    cursor: Optional[str] = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
) -> AlertPage:
    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute(
            'SELECT id, "sourceService", "alertType", severity, message, "relatedEventId", status, "createdAt", "resolvedAt" FROM alerts ORDER BY "createdAt" DESC LIMIT %s;',
            (limit,)
        )
        rows = cur.fetchall()
        cur.close()
        conn.close()
        
        items = []
        for row in rows:
            items.append(Alert(
                id=str(row["id"]),
                sourceService=row["sourceService"],
                alertType=row["alertType"],
                severity=row["severity"],
                message=row["message"],
                relatedEventId=str(row["relatedEventId"]) if row["relatedEventId"] else None,
                status=row["status"],
                createdAt=row["createdAt"],
                resolvedAt=row["resolvedAt"]
            ))
        return AlertPage(
            items=items[::-1],
            nextCursor=None,
            hasMore=False,
        )
    except Exception as e:
        print(f"Error listing alerts: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Database error: {e}"
        )


@app.get(
    "/alerts/{alert_id}",
    response_model=Alert,
    dependencies=[Depends(verify_bearer_token)],
)
def get_alert(alert_id: str) -> Alert:
    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute(
            'SELECT id, "sourceService", "alertType", severity, message, "relatedEventId", status, "createdAt", "resolvedAt" FROM alerts WHERE id = %s;',
            (alert_id,)
        )
        row = cur.fetchone()
        cur.close()
        conn.close()
        
        if row:
            return Alert(
                id=str(row["id"]),
                sourceService=row["sourceService"],
                alertType=row["alertType"],
                severity=row["severity"],
                message=row["message"],
                relatedEventId=str(row["relatedEventId"]) if row["relatedEventId"] else None,
                status=row["status"],
                createdAt=row["createdAt"],
                resolvedAt=row["resolvedAt"]
            )
    except Exception as e:
        print(f"Error getting alert {alert_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Database error: {e}"
        )

    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=build_problem(
            status_code=status.HTTP_404_NOT_FOUND,
            title="Not Found",
            detail=f"Alert {alert_id} not found",
            instance=f"/alerts/{alert_id}",
            problem_type="https://campus.local/errors/not-found",
        ),
    )
