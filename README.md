# Autonomous BI & Data Analysis Agent (Text-to-SQL + Insights)

[![CI Pipeline](https://img.shields.io/badge/CI-GitHub_Actions-2088FF?logo=github-actions&logoColor=white)](.github/workflows/ci.yml)
[![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white)](pyproject.toml)
[![FastAPI](https://img.shields.io/badge/Backend-FastAPI-009688?logo=fastapi&logoColor=white)](backend/main.py)
[![Streamlit](https://img.shields.io/badge/Frontend-Streamlit-FF4B4B?logo=streamlit&logoColor=white)](frontend/app.py)
[![Google GenAI SDK](https://img.shields.io/badge/AI-google--genai-4285F4?logo=google&logoColor=white)](backend/agent.py)
[![Ruff](https://img.shields.io/badge/Code_Style-Ruff-D7FF64?logo=ruff&logoColor=black)](pyproject.toml)

A production-ready, full-stack **Autonomous Business Intelligence & Data Analysis Agent** that translates natural-language business questions into validated read-only ANSI/SQLite queries, self-heals execution errors automatically, and delivers executive insights paired with automated **Plotly** visualizations.

---

## Key Capabilities

- **Autonomous Agentic Text-to-SQL Loop (`google-genai` Interactions API):**
  - Uses structured Function Calling with two deterministic tools: `get_database_schema()` and `run_analytical_query(query: str)`.
  - Always inspects table definitions, column types, and foreign keys before generating SQL.
  - Automatically catches SQLite compilation or column errors, feeds diagnostic hints back into the interaction loop, and rewrites the SQL query until execution succeeds.
- **Defense-in-Depth Read-Only SQL Guardrails:**
  - **Layer 1 (Lexical & Token Validator):** Strips comments (`--`, `/* */`), blocks stacked multi-statement queries (`;`), enforces `SELECT` / `WITH` / `EXPLAIN` entry points, and rejects all DDL/DML/administrative tokens (`DROP`, `DELETE`, `UPDATE`, `INSERT`, `ALTER`, `TRUNCATE`, `CREATE`, `REPLACE`, `ATTACH`, `DETACH`, `PRAGMA`, `VACUUM`).
  - **Layer 2 (SQLite URI Read-Only Mode):** Opens database connections via `file:...?mode=ro` and sets `PRAGMA query_only = ON`.
  - **Layer 3 (SQLite C-Engine Authorizer):** Registers `conn.set_authorizer(...)` to deny any operation outside `SQLITE_SELECT`, `SQLITE_READ`, `SQLITE_FUNCTION`, and `SQLITE_RECURSIVE`.
- **Automated Chart Advisor:**
  - Inspects result cardinality and column types (temporal, categorical, quantitative) to automatically recommend and render **Line**, **Bar**, **Donut/Pie**, **Scatter**, or **KPI Metric** visualizations in Plotly.
- **Interactive Dark/Light Streamlit Dashboard:**
  - Displays the natural-language prompt, formatted SQL block, execution telemetry (`ms`), interactive dataframe, automated Plotly chart, tool execution trace, and a live **Warehouse Schema Explorer**.

---

## System Architecture

### End-to-End Component & Agentic Workflow

```mermaid
flowchart LR
    User["Business User / Analyst"] --> UI["Streamlit BI Dashboard\n(frontend/app.py)"]
    UI -- "POST /api/v1/agent/ask" --> API["FastAPI Backend Engine\n(backend/main.py)"]
    API --> Agent["AutonomousBIAgent\n(backend/agent.py)"]
    Agent <-->|"Structured Function Calling\n(interactions.create)"| Gemini["Google Gemini API\n(gemini-3.8-flash)"]
    Agent -- "1. get_database_schema()" --> DBModule["Read-Only Data Layer\n(backend/database.py)"]
    Agent -- "2. run_analytical_query(sql)" --> Guard["3-Layer SQL Guardrail\n(Token + URI RO + Authorizer)"]
    Guard --> Warehouse[("SQLite E-Commerce Warehouse\n(data/warehouse.db)")]
    Agent --> ChartAdvisor["Chart Advisor Engine\n(backend/chart_advisor.py)"]
    ChartAdvisor --> API
    API -- "AgentQueryResponse (Pydantic v2)" --> UI
```

### Self-Healing Text-to-SQL Sequence

```mermaid
sequenceDiagram
    actor User
    participant UI as Streamlit UI
    participant API as FastAPI (/api/v1/agent/ask)
    participant Agent as AutonomousBIAgent
    participant LLM as Gemini (google-genai)
    participant DB as SQLite Warehouse (Read-Only)

    User->>UI: Ask natural language question
    UI->>API: POST /api/v1/agent/ask
    API->>Agent: agent.ask(question)
    Agent->>LLM: interactions.create(input=question, tools=[...])
    LLM-->>Agent: function_call: get_database_schema()
    Agent->>DB: Reflect tables, columns, FKs, row counts
    DB-->>Agent: DatabaseSchemaResponse
    Agent->>LLM: function_result (schema metadata)
    LLM-->>Agent: function_call: run_analytical_query(query)
    Agent->>DB: Validate & execute read-only SQL
    alt Query Execution Error (e.g. bad column/syntax)
        DB-->>Agent: SQLExecutionError
        Agent->>LLM: function_result (status="error", hint="Rewrite SQL")
        LLM-->>Agent: function_call: run_analytical_query(corrected_query)
        Agent->>DB: Execute corrected read-only SQL
    end
    DB-->>Agent: QueryExecutionResult (rows, columns, execution_time_ms)
    Agent->>LLM: function_result (preview rows & metrics)
    LLM-->>Agent: Synthesized Executive Summary
    Agent-->>API: AgentQueryResponse + ChartRecommendation
    API-->>UI: JSON Response
    UI-->>User: Render Summary, SQL Code Block, Metrics, Table & Plotly Chart
```

---

## Data Warehouse Schema (`data/warehouse.db`)

The deterministic seed script (`scripts/seed_db.py`) populates a normalized e-commerce data warehouse spanning **8 quarters (2025 Q1 – 2026 Q4)** with realistic analytical edge cases (`NULL` customer segments/phones/discounts, `cancelled`/`returned`/`refunded` orders, and discontinued SKUs).

```mermaid
erDiagram
    CUSTOMERS ||--o{ ORDERS : places
    ORDERS ||--|{ ORDER_ITEMS : contains
    PRODUCTS ||--o{ ORDER_ITEMS : "sold in"

    CUSTOMERS {
        INTEGER customer_id PK
        TEXT first_name
        TEXT last_name
        TEXT email UK
        TEXT phone "Nullable"
        TEXT country
        TEXT city "Nullable"
        TEXT customer_segment "Nullable (Enterprise, Mid-Market, SMB, Consumer)"
        TEXT marketing_channel "Nullable"
        DATE signup_date
        INTEGER is_active
    }

    PRODUCTS {
        INTEGER product_id PK
        TEXT sku UK
        TEXT product_name
        TEXT category
        TEXT subcategory "Nullable"
        REAL unit_price
        REAL unit_cost
        INTEGER stock_quantity
        INTEGER discontinued
        DATE launch_date
    }

    ORDERS {
        INTEGER order_id PK
        INTEGER customer_id FK
        TIMESTAMP order_date
        TIMESTAMP shipped_date "Nullable (NULL for cancelled/processing)"
        TEXT status "completed, shipped, processing, cancelled, returned, refunded"
        TEXT shipping_country
        REAL shipping_cost
        TEXT payment_method "Nullable"
        REAL total_amount
    }

    ORDER_ITEMS {
        INTEGER order_item_id PK
        INTEGER order_id FK
        INTEGER product_id FK
        INTEGER quantity
        REAL unit_price
        REAL discount_amount "Nullable"
        REAL line_total
    }
```

---

## Project Structure

```text
.
├── .github/workflows/
│   └── ci.yml                          # GitHub Actions CI (Ruff + Pytest + Coverage)
├── backend/
│   ├── __init__.py
│   ├── agent.py                        # Gemini Interactions API function-calling BI agent
│   ├── chart_advisor.py                # Automated Plotly chart recommendation heuristics
│   ├── config.py                       # Pydantic Settings configuration
│   ├── database.py                     # 3-layer read-only SQLite guardrails & schema reflection
│   ├── main.py                         # FastAPI application and REST endpoints
│   └── schemas.py                      # Strict Pydantic v2 request/response models
├── data/
│   └── warehouse.db                    # Seeded SQLite E-Commerce data warehouse
├── frontend/
│   ├── __init__.py
│   └── app.py                          # Streamlit dark/light executive BI dashboard
├── scripts/
│   ├── __init__.py
│   └── seed_db.py                      # Deterministic warehouse generator with edge cases
├── tests/
│   ├── conftest.py                     # Session-scoped DB & FastAPI TestClient fixtures
│   ├── test_agent_and_charts.py        # Unit tests for agent self-healing & chart advisor
│   ├── test_api_routes.py              # Integration tests for all FastAPI endpoints
│   └── test_database_and_security.py   # Parameterized SQL injection/DDL/DML security tests
├── .dockerignore
├── .env.example
├── .gitignore
├── Dockerfile                          # Multi-stage production Dockerfile
├── docker-compose.yml                  # Orchestrates backend (:8000) and frontend (:8501)
├── pyproject.toml                      # Project metadata, Ruff, and Pytest configuration
└── requirements.txt
```

---

## Quickstart & Setup Instructions

### Option 1: Local Python Environment

1. **Clone the repository and install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

2. **Configure environment variables:**
   ```bash
   cp .env.example .env
   # Edit .env and set GEMINI_API_KEY=your_actual_key
   ```
   *(Note: If `GEMINI_API_KEY` is omitted, the agent automatically operates in deterministic local analytical mode so the UI and test suite work out-of-the-box.)*

3. **Seed the SQLite Data Warehouse:**
   ```bash
   python -m scripts.seed_db
   ```

4. **Start the FastAPI Backend Server (Terminal 1):**
   ```bash
   uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload
   ```
   Open `http://localhost:8000/docs` for interactive Swagger OpenAPI documentation.

5. **Launch the Streamlit Dashboard (Terminal 2):**
   ```bash
   streamlit run frontend/app.py
   ```
   Open `http://localhost:8501` in your browser.

---

### Option 2: Docker Compose (Full Stack)

```bash
export GEMINI_API_KEY="your_gemini_api_key"
docker compose up --build
```

- **FastAPI Backend & OpenAPI Docs:** `http://localhost:8000/docs`
- **Streamlit BI Dashboard:** `http://localhost:8501`

---

## REST API Reference

| Method | Endpoint | Description |
| :--- | :--- | :--- |
| `GET` | `/api/v1/health` | Service readiness, SQLite table count, and Gemini configuration status |
| `GET` | `/api/v1/schema` | Full warehouse schema (`customers`, `orders`, `order_items`, `products`) |
| `POST` | `/api/v1/query/validate` | Validates whether a candidate SQL string is strictly read-only |
| `POST` | `/api/v1/query/sql` | Executes a validated read-only SQL query and returns rows + chart suggestion |
| `POST` | `/api/v1/agent/ask` | Runs the autonomous Text-to-SQL + Insights agent loop |

### Example Request: `POST /api/v1/agent/ask`

```bash
curl -X POST "http://localhost:8000/api/v1/agent/ask" \
  -H "Content-Type: application/json" \
  -d '{
    "question": "What is our net revenue and gross profit breakdown by product category?"
  }'
```

---

## Sample Analytical Queries

1. **Quarterly Revenue & Order Volume Trend (`LINE` chart):**
   > *"Show quarterly revenue and total orders trend across all quarters."*
2. **Product Category Profitability (`BAR` chart):**
   > *"What is our net revenue and gross profit breakdown by product category?"*
3. **Order Status & Cancellation Rate Analysis (`PIE` chart):**
   > *"Analyze order status distribution including cancellation and return rates."*
4. **Customer Segment Performance (`BAR` chart, handling `NULL` segments via `COALESCE`):**
   > *"Compare customer count, net revenue, and average order value by customer segment."*
5. **Top 10 Best-Selling SKUs by Net Sales & Gross Profit (`BAR` chart):**
   > *"Which are the top 10 products by net sales and gross profit?"*

---

## Testing & Code Quality

Run the linter (`ruff`) and the full unit/integration test suite (`pytest` with coverage):

```bash
# Lint and check formatting
python -m ruff check .

# Execute unit & integration tests with coverage report
python -m pytest --cov=backend --cov=scripts --cov-report=term-missing
```
