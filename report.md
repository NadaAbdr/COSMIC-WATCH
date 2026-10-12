Work Files Report (00_preview.sql and sql_test.py)
1. SQL Preview File (00_preview.sql)
Objective: Provide a dedicated, clean, and organized workspace to write and test SQL data-preview queries, avoiding the clutter of random open tabs.

What it typically includes:

Basic query statements to preview sample data (e.g., SELECT * ... LIMIT 5).

Testing actual table names (such as verifying the fact_close_approaches table name and its corresponding dataset).

Writing initial conditions and filters (such as defining date ranges for close approaches or filtering hazardous asteroids) to ensure data accuracy before integrating them into the Python codebase.

2. Python Test File (sql_test.py)
Objective: Test database connectivity and dynamically run queries within the development environment (VS Code) before finally merging them into the main dashboard script.

What it typically includes:

Importing necessary functions or libraries required to execute queries (such as the run_query function).

Writing a small, independent script to test fetching and displaying data to ensure the connection runs efficiently and that there are no programming errors in table paths or returned values.

💻 Essential PowerShell Commands
1. Command to Create the SQL Preview File (00_preview.sql)
PowerShell
Set-Content sql\analysis\00_preview.sql "select * from analytics.fact_close_approaches limit 20;"
2. Command to Create the Python Test File (sql_test.py)
PowerShell
Set-Content apps\streamlit\sql_test.py 'import os
from pathlib import Path
import pandas as pd
import psycopg
import streamlit as st

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

SQL_DIR = Path(__file__).resolve().parents[2] / "sql" / "analysis"

st.title("SQL test page")

files = sorted(SQL_DIR.glob("*.sql"))
if not files:
    st.warning(f"No .sql files in {SQL_DIR}")
    st.stop()

chosen = st.selectbox("Query file", files, format_func=lambda p: p.name)
sql = chosen.read_text(encoding="utf-8")
st.code(sql, language="sql")

if st.button("Run"):
    body = "\n".join(
        l for l in sql.splitlines() if not l.strip().startswith("--")
    ).strip().lower()
    if not body.startswith(("select", "with")):'
3. Command to Run the Streamlit App on Port 8502
PowerShell
streamlit run apps/streamlit/sql_test.py --server.port 8502