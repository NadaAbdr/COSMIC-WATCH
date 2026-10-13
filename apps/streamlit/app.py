"""COSMIC-WATCH dashboard: near-Earth asteroids from the dbt `analytics` tables.

Run from the repo root (PostgreSQL from docker-compose must be up):
    pip install streamlit
    streamlit run apps/streamlit/app.py

Connection settings come from environment variables or the .env file:
    POSTGRES_HOST (default localhost)  POSTGRES_PORT (5432)  POSTGRES_DB
    POSTGRES_USER  POSTGRES_PASSWORD
"""
import os
from datetime import date, timedelta
from pathlib import Path

import altair as alt
import numpy as np
import pandas as pd
import streamlit as st

QUERY = """
select
    f.approach_id,
    f.asteroid_id,
    d.name,
    f.close_approach_date,
    f.orbiting_body,
    f.velocity_kmh,
    f.miss_distance_km,
    f.miss_distance_lunar,
    d.diameter_min_m,
    d.diameter_max_m,
    d.is_hazardous,
    d.orbit_class
from analytics.fact_close_approaches as f
join analytics.dim_asteroids as d on d.asteroid_id = f.asteroid_id
"""


# --------------------------- data access ---------------------------------
def load_dotenv():
    """Tiny .env reader (no extra package). Real environment variables win."""
    env_file = Path(__file__).resolve().parents[2] / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def connect():
    import psycopg

    password = os.environ.get("POSTGRES_PASSWORD")
    if not password:
        raise RuntimeError("POSTGRES_PASSWORD is not set (put it in .env).")
    return psycopg.connect(
        host=os.environ.get("POSTGRES_HOST", "localhost"),
        port=int(os.environ.get("POSTGRES_PORT", "5432")),
        dbname=os.environ.get("POSTGRES_DB", "cosmic_watch"),
        user=os.environ.get("POSTGRES_USER", "cosmic"),
        password=password,
        connect_timeout=5,
    )


def prepare(df: pd.DataFrame) -> pd.DataFrame:
    """Fix types and add the average diameter."""
    df = df.copy()
    df["close_approach_date"] = pd.to_datetime(df["close_approach_date"])
    df["is_hazardous"] = df["is_hazardous"].fillna(False).astype(bool)
    df["diameter_m"] = (df["diameter_min_m"] + df["diameter_max_m"]) / 2
    return df


@st.cache_data(ttl=600, show_spinner="Loading from PostgreSQL...")
def load_data() -> pd.DataFrame:
    load_dotenv()
    with connect() as conn, conn.cursor() as cur:
        cur.execute(QUERY)
        columns = [c.name for c in cur.description]
        rows = cur.fetchall()
    return prepare(pd.DataFrame(rows, columns=columns))


COVERAGE_QUERY = "select start_date, end_date from raw.neo_feed_responses"


@st.cache_data(ttl=600, show_spinner=False)
def load_coverage() -> pd.DataFrame:
    """Date ranges for which a full NASA feed was downloaded."""
    load_dotenv()
    with connect() as conn, conn.cursor() as cur:
        cur.execute(COVERAGE_QUERY)
        rows = cur.fetchall()
    return pd.DataFrame(rows, columns=["start_date", "end_date"])


# --------------------------- calculations --------------------------------
def apply_filters(df, start, end, bodies, hazardous_only):
    mask = df["close_approach_date"].between(pd.Timestamp(start), pd.Timestamp(end))
    if bodies:
        mask &= df["orbiting_body"].isin(bodies)
    if hazardous_only:
        mask &= df["is_hazardous"]
    return df[mask]


def kpis(df):
    return {
        "asteroids": int(df["asteroid_id"].nunique()),
        "approaches": int(len(df)),
        "hazardous": int(df.loc[df["is_hazardous"], "asteroid_id"].nunique()),
        "closest_lunar": df["miss_distance_lunar"].min(),
        "fastest_kmh": df["velocity_kmh"].max(),
    }


def covered_days(ranges):
    """Every calendar day that falls inside at least one downloaded feed range."""
    if ranges.empty:
        return pd.DatetimeIndex([])
    spans = [
        pd.date_range(pd.Timestamp(start), pd.Timestamp(end), freq="D")
        for start, end in zip(ranges["start_date"], ranges["end_date"])
    ]
    return spans[0].append(spans[1:]).unique().sort_values()


def monthly_counts(df, covered=None):
    """Close approaches per month. With `covered`, only days with a full feed are counted,
    so every month is measured the same way."""
    if covered is not None:
        df = df[df["close_approach_date"].dt.normalize().isin(covered)]
    month = df["close_approach_date"].dt.to_period("M").dt.to_timestamp()
    out = month.value_counts().sort_index().rename_axis("month").reset_index(name="approaches")
    return out


def speed_histogram(df, bins=40):
    values = df["velocity_kmh"].dropna()
    if values.empty:
        return pd.DataFrame(columns=["start", "end", "count"])
    counts, edges = np.histogram(values, bins=bins)
    return pd.DataFrame({"start": edges[:-1], "end": edges[1:], "count": counts})


def closest_table(df, n=15):
    t = df.nsmallest(n, "miss_distance_lunar")[
        ["name", "close_approach_date", "orbiting_body", "miss_distance_lunar",
         "miss_distance_km", "velocity_kmh", "diameter_m", "is_hazardous"]
    ].copy()
    t["close_approach_date"] = t["close_approach_date"].dt.date
    return t.rename(columns={
        "name": "Asteroid", "close_approach_date": "Date", "orbiting_body": "Body",
        "miss_distance_lunar": "Miss distance (lunar)", "miss_distance_km": "Miss distance (km)",
        "velocity_kmh": "Speed (km/h)", "diameter_m": "Diameter (m)", "is_hazardous": "Hazardous",
    }).round(2)


def size_vs_distance(df, n=3000):
    """Points for the scatter plot. Both axes are log scale, so values must be > 0."""
    pts = df[(df["diameter_m"] > 0) & (df["miss_distance_lunar"] > 0)]
    pts = pts[["name", "close_approach_date", "diameter_m", "miss_distance_lunar", "is_hazardous"]]
    if len(pts) > n:
        pts = pts.sample(n, random_state=1)
    pts = pts.copy()
    pts["close_approach_date"] = pts["close_approach_date"].dt.strftime("%Y-%m-%d")
    pts["Hazardous"] = np.where(pts["is_hazardous"], "Yes", "No")
    return pts


def orbit_counts(df):
    asteroids = df.drop_duplicates("asteroid_id")
    counts = asteroids["orbit_class"].fillna("Unknown").value_counts()
    return counts.rename_axis("orbit_class").reset_index(name="asteroids")


# ------------------------------- page ------------------------------------
def main():
    st.set_page_config(page_title="COSMIC-WATCH", page_icon="☄️", layout="wide")
    st.title("COSMIC-WATCH: near-Earth asteroids")
    st.caption(
        "NASA NeoWs data, loaded into PostgreSQL and modelled with dbt "
        "(analytics.dim_asteroids and analytics.fact_close_approaches)."
    )

    try:
        df = load_data()
    except Exception as exc:  # connection, password, missing tables...
        st.error(
            "Could not load data from PostgreSQL. Check that the database is running "
            "(docker compose up -d postgres), that dbt has built the analytics tables, "
            "and that .env has POSTGRES_PASSWORD."
        )
        st.exception(exc)
        st.stop()

    if df.empty:
        st.warning("The analytics tables are empty. Run the pipeline (Airflow DAG or `make run`) first.")
        st.stop()

    # ---- sidebar filters ----
    st.sidebar.header("Filters")
    data_min = df["close_approach_date"].min().date()
    data_max = df["close_approach_date"].max().date()
    default_start = max(data_min, date.today() - timedelta(days=365))
    default_end = min(data_max, date.today() + timedelta(days=365))
    if default_start > default_end:
        default_start, default_end = data_min, data_max

    picked = st.sidebar.date_input(
        "Close-approach dates",
        value=(default_start, default_end),
        min_value=data_min,
        max_value=data_max,
    )
    if not (isinstance(picked, (tuple, list)) and len(picked) == 2):
        st.info("Pick an end date in the sidebar.")
        st.stop()
    start, end = picked

    all_bodies = sorted(df["orbiting_body"].dropna().unique())
    bodies = st.sidebar.multiselect(
        "Orbiting body", all_bodies, default=["Earth"] if "Earth" in all_bodies else all_bodies
    )
    hazardous_only = st.sidebar.checkbox("Potentially hazardous asteroids only")
    if st.sidebar.button("Reload data"):
        load_data.clear()
        st.rerun()

    view = apply_filters(df, start, end, bodies, hazardous_only)
    if view.empty:
        st.info("No close approaches match these filters. Try a wider date range.")
        st.stop()

    try:
        covered = covered_days(load_coverage())
    except Exception:  # raw table unreadable: fall back to counting every day
        covered = None
    if covered is not None and len(covered) == 0:
        covered = None

    # ---- headline numbers ----
    k = kpis(view)
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Asteroids", f"{k['asteroids']:,}")
    c2.metric("Close approaches", f"{k['approaches']:,}")
    c3.metric("Potentially hazardous", f"{k['hazardous']:,}")
    c4.metric("Closest pass", f"{k['closest_lunar']:.2f} lunar distances")
    c5.metric("Fastest", f"{k['fastest_kmh']:,.0f} km/h")

    # ---- over time + speed ----
    left, right = st.columns(2)
    with left:
        st.subheader("Close approaches per month")
        if covered is not None:
            st.caption(
                f"Counts only days fully covered by the NASA feed "
                f"({covered.min():%d %b %Y} to {covered.max():%d %b %Y}). Other days would look "
                "empty, because they only include asteroids whose orbits were looked up."
            )
        else:
            st.caption("Coverage information is unavailable, so every day is counted.")
        monthly = monthly_counts(view, covered)
        if monthly.empty:
            st.info("No fully covered days fall inside the selected dates.")
        st.altair_chart(
            alt.Chart(monthly).mark_bar().encode(
                x=alt.X("month:T", title="Month"),
                y=alt.Y("approaches:Q", title="Close approaches"),
                tooltip=[alt.Tooltip("month:T", title="Month", format="%b %Y"),
                         alt.Tooltip("approaches:Q", title="Approaches")],
            ).properties(width="container", height=300)
        )
    with right:
        st.subheader("How fast do they travel?")
        hist = speed_histogram(view)
        st.altair_chart(
            alt.Chart(hist).mark_bar().encode(
                x=alt.X("start:Q", title="Relative velocity (km/h)"),
                x2="end:Q",
                y=alt.Y("count:Q", title="Close approaches"),
                tooltip=[alt.Tooltip("start:Q", title="From km/h", format=",.0f"),
                         alt.Tooltip("end:Q", title="To km/h", format=",.0f"),
                         alt.Tooltip("count:Q", title="Approaches")],
            ).properties(width="container", height=300)
        )

    # ---- size vs distance ----
    st.subheader("Size versus miss distance")
    st.caption("Each dot is one close approach (up to 3,000 shown). Both axes use a log scale.")
    pts = size_vs_distance(view)
    st.altair_chart(
        alt.Chart(pts).mark_circle(opacity=0.5, size=40).encode(
            x=alt.X("miss_distance_lunar:Q", scale=alt.Scale(type="log"),
                    title="Miss distance (lunar distances)"),
            y=alt.Y("diameter_m:Q", scale=alt.Scale(type="log"), title="Estimated diameter (m)"),
            color=alt.Color("Hazardous:N", scale=alt.Scale(domain=["No", "Yes"], range=["#4c78a8", "#e45756"])),
            tooltip=["name:N", "close_approach_date:N",
                     alt.Tooltip("miss_distance_lunar:Q", format=".2f"),
                     alt.Tooltip("diameter_m:Q", format=".1f"), "Hazardous:N"],
        ).properties(width="container", height=380).interactive()
    )

    # ---- closest + orbits ----
    left, right = st.columns([3, 2])
    with left:
        st.subheader("15 closest approaches")
        st.dataframe(closest_table(view), hide_index=True)
    with right:
        st.subheader("Asteroids by orbit class")
        orbits = orbit_counts(view)
        st.altair_chart(
            alt.Chart(orbits).mark_bar().encode(
                x=alt.X("asteroids:Q", title="Asteroids"),
                y=alt.Y("orbit_class:N", sort="-x", title=None),
                tooltip=["orbit_class:N", "asteroids:Q"],
            ).properties(width="container", height=300)
        )


if __name__ == "__main__":
    main()