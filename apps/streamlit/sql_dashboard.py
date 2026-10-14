"""COSMIC-WATCH: SQL dashboard (PostgreSQL).

Same sections as the team's SQL page (four number cards, closest approaches, busiest
years, most frequent asteroids, hazardous asteroids by orbit class, upcoming hazardous
approaches), with a new look. It reads the dbt tables analytics.dim_asteroids and
analytics.fact_close_approaches from the local PostgreSQL.

Run:  streamlit run apps/streamlit/sql_dashboard.py --server.port 8505
"""
import os
from datetime import date, timedelta
from pathlib import Path

import altair as alt
import pandas as pd
import psycopg
import streamlit as st

# Colours shared with the architecture diagrams
BLUE, GREEN, ORANGE, PURPLE = "#1f5fbf", "#2f8f5b", "#d9772b", "#7b4bb0"

STYLE = """
<style>
.hero {
    background: linear-gradient(120deg, #0b1633 0%, #1f5fbf 100%);
    border-radius: 16px; padding: 26px 30px; margin-bottom: 22px; color: #ffffff;
}
.hero h1 { margin: 0; font-size: 34px; color: #ffffff; }
.hero p  { margin: 6px 0 0 0; font-size: 15px; opacity: 0.85; color: #ffffff; }
.kpi {
    background: rgba(128,128,128,0.10); border-radius: 14px; padding: 16px 18px;
    border-left: 6px solid var(--accent); margin-bottom: 10px;
}
.kpi .label { font-size: 13px; opacity: 0.75; margin-bottom: 6px; }
.kpi .row   { display: flex; justify-content: space-between; align-items: center; }
.kpi .value { font-size: 30px; font-weight: 700; }
.kpi .icon  { font-size: 30px; }
.section {
    font-size: 20px; font-weight: 700; margin: 26px 0 10px 0;
    padding-left: 12px; border-left: 6px solid var(--accent);
}
.note { font-size: 13px; opacity: 0.7; margin-top: 4px; }
</style>
"""

FROM_JOIN = """
from analytics.fact_close_approaches as f
left join analytics.dim_asteroids as d on d.asteroid_id = f.asteroid_id
"""

SQL_BODIES = "select distinct orbiting_body from analytics.fact_close_approaches order by 1"

SQL_KPIS = """
select count(*)                       as total_approaches,
       count(distinct f.asteroid_id)  as distinct_asteroids,
       min(f.miss_distance_km)        as closest_km,
       max(f.velocity_kmh)            as fastest_kmh
""" + FROM_JOIN + "where {where}"

SQL_CLOSEST = """
select d.name                                     as "Asteroid",
       f.close_approach_date                      as "Date",
       f.orbiting_body                            as "Body",
       round(f.miss_distance_km::numeric)         as "Miss distance (km)",
       round(f.miss_distance_lunar::numeric, 2)   as "Miss distance (lunar)",
       coalesce(d.is_hazardous, false)            as "Hazardous"
""" + FROM_JOIN + """where {where}
order by f.miss_distance_km asc
limit 5
"""

SQL_YEARS = """
select extract(year from f.close_approach_date)::int as year,
       count(*)                                      as approaches,
       round(avg(f.velocity_kmh)::numeric)           as avg_speed_kmh
from analytics.fact_close_approaches as f
where {where}
group by 1
order by approaches desc
limit 10
"""

SQL_FREQUENT = """
select d.name                              as "Asteroid",
       f.asteroid_id                       as "ID",
       count(*)                            as "Approaches",
       coalesce(bool_or(d.is_hazardous), false) as "Hazardous"
""" + FROM_JOIN + """where {where}
group by d.name, f.asteroid_id
order by "Approaches" desc
limit 5
"""

SQL_ORBIT = """
select coalesce(orbit_class, 'Unknown')                                        as "Orbit class",
       count(*)                                                                as "Asteroids",
       count(*) filter (where is_hazardous)                                    as "Hazardous",
       round(100.0 * count(*) filter (where is_hazardous) / count(*), 1)       as "Hazardous share"
from analytics.dim_asteroids
group by 1
order by "Hazardous share" desc, "Asteroids" desc
"""

SQL_UPCOMING = """
select * from (
    select distinct on (d.asteroid_id)
           d.asteroid_id                                              as "ID",
           d.name                                                     as "Asteroid",
           f.close_approach_date                                      as "Date",
           (f.close_approach_date - (now() at time zone 'Asia/Riyadh')::date) as "Days until",
           round(f.miss_distance_km::numeric)                         as "Miss distance (km)",
           round(f.miss_distance_lunar::numeric, 2)                   as "Miss distance (lunar)",
           round(d.diameter_min_m::numeric)                           as "Min diameter (m)",
           round(d.diameter_max_m::numeric)                           as "Max diameter (m)"
    from analytics.fact_close_approaches as f
    join analytics.dim_asteroids as d on d.asteroid_id = f.asteroid_id
    where d.is_hazardous
      and f.orbiting_body = 'Earth'
      and f.close_approach_date >= (now() at time zone 'Asia/Riyadh')::date
    order by d.asteroid_id, f.close_approach_date, f.miss_distance_km
) as next_approach
order by "Date", "ID"
"""


# ------------------------------ data access ---------------------------------
def load_dotenv():
    """Tiny .env reader. Real environment variables win."""
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


@st.cache_data(ttl=600, show_spinner=False)
def fetch(sql, params):
    load_dotenv()
    with connect() as conn:
        cur = conn.execute(sql, params)
        columns = [c.name for c in cur.description]
        return pd.DataFrame(cur.fetchall(), columns=columns)


def run_query(sql, params=()):
    """Run a query; show a readable error instead of crashing the page."""
    try:
        return fetch(sql, tuple(params))
    except Exception as exc:
        st.error(f"Could not read from PostgreSQL: {exc}")
        return pd.DataFrame()


def build_where(start, end, bodies, hazardous_only):
    """WHERE text and its parameters. Values are passed as parameters, never pasted in."""
    parts = ["f.close_approach_date between %s and %s"]
    params = [start, end]
    if bodies:
        parts.append("f.orbiting_body = any(%s)")
        params.append(list(bodies))
    if hazardous_only:
        parts.append("d.is_hazardous")
    return " and ".join(parts), params


def body_where(bodies):
    """Same, but only the body filter (for the all-dates sections)."""
    if bodies:
        return "f.orbiting_body = any(%s)", [list(bodies)]
    return "true", []


# ------------------------------ page pieces ----------------------------------
def kpi_card(label, value, icon, accent):
    st.markdown(
        f'<div class="kpi" style="--accent:{accent}"><div class="label">{label}</div>'
        f'<div class="row"><span class="value">{value}</span><span class="icon">{icon}</span></div></div>',
        unsafe_allow_html=True,
    )


def section(title, accent):
    st.markdown(f'<div class="section" style="--accent:{accent}">{title}</div>', unsafe_allow_html=True)


def show_table(df, empty_text="No data for these filters."):
    if df.empty:
        st.info(empty_text)
    else:
        st.dataframe(df, use_container_width=True, hide_index=True)


def main():
    st.set_page_config(page_title="Cosmic Watch SQL dashboard", page_icon="☄️", layout="wide")
    st.markdown(STYLE, unsafe_allow_html=True)
    st.markdown(
        '<div class="hero"><h1>☄️ Cosmic Watch</h1>'
        "<p>Near-Earth asteroids from NASA NeoWs, loaded into PostgreSQL and modelled with dbt</p></div>",
        unsafe_allow_html=True,
    )

    # ---- filters ----
    st.sidebar.title("Filters")
    today = date.today()
    picked = st.sidebar.date_input(
        "Close-approach dates", value=(today - timedelta(days=365), today + timedelta(days=365))
    )
    body_options = run_query(SQL_BODIES)
    body_list = list(body_options["orbiting_body"]) if not body_options.empty else []
    bodies = st.sidebar.multiselect(
        "Orbiting body", body_list, default=["Earth"] if "Earth" in body_list else []
    )
    hazardous_only = st.sidebar.checkbox("Potentially hazardous asteroids only")
    if st.sidebar.button("Reload data"):
        st.cache_data.clear()
        st.rerun()

    if not (isinstance(picked, (tuple, list)) and len(picked) == 2):
        st.info("Pick both a start and an end date in the sidebar.")
        st.stop()
    start, end = picked
    where, params = build_where(start, end, bodies, hazardous_only)

    # ---- number cards ----
    kpis = run_query(SQL_KPIS.format(where=where), params)
    if kpis.empty or int(kpis.iloc[0]["total_approaches"]) == 0:
        st.info("No close approaches match these filters. Try a wider date range.")
        st.stop()
    row = kpis.iloc[0]
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        kpi_card("Total approaches", f"{int(row['total_approaches']):,}", "☄️", BLUE)
    with c2:
        kpi_card("Different asteroids", f"{int(row['distinct_asteroids']):,}", "🪐", GREEN)
    with c3:
        kpi_card("Closest approach (km)", f"{float(row['closest_km']):,.0f}", "🎯", ORANGE)
    with c4:
        kpi_card("Fastest (km/h)", f"{float(row['fastest_kmh']):,.0f}", "🚀", PURPLE)

    # ---- closest approaches and busiest years ----
    left, right = st.columns([1.5, 1])
    with left:
        section("🎯 5 closest approaches", BLUE)
        show_table(run_query(SQL_CLOSEST.format(where=where), params))
    with right:
        section("📆 Busiest years", ORANGE)
        year_where, year_params = body_where(bodies)
        years = run_query(SQL_YEARS.format(where=year_where), year_params)
        if years.empty:
            st.info("No data found.")
        else:
            years["year"] = years["year"].astype(str)
            chart = (
                alt.Chart(years)
                .mark_bar(color=BLUE, cornerRadiusEnd=4)
                .encode(
                    y=alt.Y("year:N", title="Year", sort="-x"),
                    x=alt.X("approaches:Q", title="Close approaches"),
                    tooltip=["year", "approaches", "avg_speed_kmh"],
                )
                .properties(height=260)
            )
            st.altair_chart(chart, use_container_width=True)
        st.markdown(
            '<div class="note">All dates in the data, including NASA predictions up to the year 2200.</div>',
            unsafe_allow_html=True,
        )

    # ---- frequent asteroids and orbit classes ----
    left, right = st.columns(2)
    with left:
        section("🔁 Most frequent asteroids", GREEN)
        show_table(run_query(SQL_FREQUENT.format(where=year_where), year_params))
        st.markdown(
            '<div class="note">Counts how often each asteroid passes. '
            "It does not measure danger on its own, see the Hazardous column.</div>",
            unsafe_allow_html=True,
        )
    with right:
        section("🪐 Hazardous asteroids by orbit class", PURPLE)
        show_table(run_query(SQL_ORBIT))

    # ---- upcoming hazardous approaches ----
    section("🔮 Upcoming approaches of potentially hazardous asteroids", ORANGE)
    show_table(run_query(SQL_UPCOMING), "No upcoming approaches found.")
    st.markdown(
        '<div class="note">One row per asteroid: its next approach to Earth from today. '
        '"Potentially hazardous" is NASA\'s label for large asteroids that can pass close to Earth. '
        "It does not mean an impact is expected.</div>",
        unsafe_allow_html=True,
    )


if __name__ == "__main__":
    main()