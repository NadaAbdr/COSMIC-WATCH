import os
import pandas as pd
from google.cloud import bigquery
import streamlit as st
import altair as alt

# Page configuration for a wide dashboard layout
st.set_page_config(page_title="Cosmic Watch Dashboard", layout="wide")


def kpi_card(label, value, icon="☄️"):
    """بطاقة رقم: العنوان فوق، الرقم يسار، الأيقونة يمين."""
    st.markdown(
        f"""
        <div style="
            background: rgba(128,128,128,0.12);
            border-radius: 12px;
            padding: 14px 18px;
            margin-bottom: 8px;">
            <div style="font-size:14px; opacity:0.75; margin-bottom:8px;">{label}</div>
            <div style="display:flex; justify-content:space-between; align-items:center;">
                <span style="font-size:28px; font-weight:600;">{value}</span>
                <span style="font-size:28px;">{icon}</span>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


# تهيئة عميل BigQuery مع تحديد مشروعك بدقة
@st.cache_resource
def get_bigquery_client():
    return bigquery.Client(project="cosmic-watch-de")

# دالة مساعدة لتنفيذ الاستعلامات من BigQuery
def run_query(sql_query):
    try:
        client = get_bigquery_client()
        df = client.query(sql_query).to_dataframe()
        return df
    except Exception as e:
        st.error(f"An error occurred with BigQuery: {e}")
        return pd.DataFrame()

# ==========================================
# 🎛️ SIDEBAR: Filters (الشريط الجانبي للفلاتر)
# ==========================================
st.sidebar.title("Filters")
st.sidebar.markdown("---")

st.sidebar.subheader("Close-approach dates")
date_range = st.sidebar.date_input(
    "Select Date Range",
    value=(pd.to_datetime("2025-10-12"), pd.to_datetime("2027-10-12"))
)

hazardous_only = st.sidebar.checkbox("Potentially hazardous asteroids only")

st.sidebar.markdown("---")
if st.sidebar.button("Reload data"):
    st.rerun()

# استخراج تواريخ البداية والنهاية من الفلتر الجانبي
if isinstance(date_range, tuple) and len(date_range) == 2:
    start_date, end_date = date_range
else:
    start_date, end_date = "2025-10-12", "2027-10-12"

# ==========================================
# 🔭 MAIN DASHBOARD AREA
# ==========================================
st.title("🔭 Cosmic Watch: Asteroid Analytics Dashboard")
st.markdown("---")

# شرط الخطورة إذا تم تفعيله
hazard_filter_sql = "AND d.is_hazardous = true" if hazardous_only else ""
# ==========================================
# 1. Metric / Top Summary (الكروت الأربعة)
# ==========================================
sql_kpis = f"""
SELECT
    COUNT(*)                    AS total_approaches,
    COUNT(DISTINCT f.asteroid_id) AS distinct_asteroids,
    MIN(f.miss_distance_km)       AS closest_km,
    MAX(f.velocity_kmh)           AS fastest_kmh
FROM `cosmic-watch-de.raw.fact_close_approaches` f
LEFT JOIN `cosmic-watch-de.raw.dim_asteroids` d ON f.asteroid_id = d.asteroid_id
WHERE f.close_approach_date >= '{start_date}'
  AND f.close_approach_date <= '{end_date}'
  {hazard_filter_sql};
"""
df_kpi = run_query(sql_kpis)
if not df_kpi.empty and int(df_kpi.iloc[0]["total_approaches"]) > 0:
    row = df_kpi.iloc[0]
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        kpi_card("Total Approaches", f"{int(row['total_approaches']):,}", "☄️")
    with c2:
        kpi_card("Distinct Asteroids", f"{int(row['distinct_asteroids']):,}", "🪐")
    with c3:
        kpi_card("Closest Approach (km)", f"{float(row['closest_km']):,.0f}", "🎯")
    with c4:
        kpi_card("Fastest (km/h)", f"{float(row['fastest_kmh']):,.0f}", "🚀")
else:
    st.info("No data for the selected filters.")

st.markdown("---")

# ------------------------------------------
# 2. Layout split: Table (Left) & Chart (Right)
# ------------------------------------------
col_left, col_right = st.columns([1.5, 1])

with col_left:
    st.markdown("### 🎯 5 Closest Approaches")
    
    # استعلام أقرب 15 اقتراباً
    sql_closest = f"""
    SELECT f.asteroid_id, f.close_approach_date, f.miss_distance_km
    FROM `cosmic-watch-de.raw.fact_close_approaches` f
    LEFT JOIN `cosmic-watch-de.raw.dim_asteroids` d ON f.asteroid_id = d.asteroid_id
    WHERE f.close_approach_date >= '{start_date}' 
      AND f.close_approach_date <= '{end_date}'
      {hazard_filter_sql}
    ORDER BY f.miss_distance_km ASC
    LIMIT 5;
    """
    df_closest = run_query(sql_closest)
    if not df_closest.empty:
        st.dataframe(df_closest, use_container_width=True, hide_index=True)
    else:
        st.info("No data available for the selected filters.")



with col_right:
    st.markdown("### 📆 Close Approaches per Year")
    sql_year = """
    SELECT EXTRACT(YEAR FROM close_approach_date) AS year,
           COUNT(*)                              AS approaches,
           ROUND(AVG(velocity_kmh))              AS avg_speed_kmh
    FROM `cosmic-watch-de.raw.fact_close_approaches`
    WHERE orbiting_body = 'Earth'
    GROUP BY year
    ORDER BY approaches DESC
    LIMIT 10;
    """
    df_y = run_query(sql_year)
    if not df_y.empty:
        df_y["year"] = df_y["year"].astype(int).astype(str)
        chart_y = (
            alt.Chart(df_y).mark_bar().encode(
                y=alt.Y("year:N", title="Year", sort="-x"),
                x=alt.X("approaches:Q", title="Number of approaches"),
                tooltip=["year", "approaches", "avg_speed_kmh"],
            ).properties(height=280)
        )
        st.altair_chart(chart_y, use_container_width=True)
    else:
        st.info("No data found.")
# ==========================================
# 3 & 6. Layout split: الجدولين جنباً إلى جنب
# ==========================================
col_table1, col_table2 = st.columns(2)

with col_table1:
    st.markdown("### ⚠️ Most Dangerous Asteroids (Frequent)")
    sql_dangerous = """
    SELECT 
        f.asteroid_id, 
        COUNT(*) AS approach_count
    FROM `cosmic-watch-de.raw.fact_close_approaches` f
    GROUP BY f.asteroid_id
    ORDER BY approach_count DESC
    LIMIT 5;
    """
    df_dangerous = run_query(sql_dangerous)
    if not df_dangerous.empty:
        st.dataframe(df_dangerous, use_container_width=True, hide_index=True)
    else:
        st.info("No data found.")

with col_table2:
    st.markdown("### 🪐 Hazardous Asteroids by Orbit Class")
    sql_orbit = """
    SELECT orbit_class, 
           COUNT(*) AS asteroids, 
           COUNTIF(is_hazardous) AS hazardous, 
           ROUND(100 * COUNTIF(is_hazardous) / COUNT(*), 1) AS pct_hazardous 
    FROM `cosmic-watch-de.raw.dim_asteroids` 
    GROUP BY orbit_class 
    ORDER BY pct_hazardous DESC;
    """
    df_o = run_query(sql_orbit)
    if not df_o.empty:
        st.dataframe(df_o, use_container_width=True, hide_index=True)
    else:
        st.info("No data found.")

st.markdown("---")
# ==========================================
# 4. Upcoming Approaches (Potentially Hazardous Asteroids)
# ==========================================
st.markdown("### 🔮 Upcoming Approaches (Potentially Hazardous Asteroids)")

sql_upcoming = """
WITH upcoming AS (  
    SELECT DISTINCT  
        d.asteroid_id,  
        d.name,  
        f.close_approach_date,  
        DATE_DIFF(  
            f.close_approach_date,  
            CURRENT_DATE('Asia/Riyadh'),  
            DAY  
        ) AS days_until_approach,  
        f.miss_distance_km,  
        f.miss_distance_lunar,  
        d.diameter_min_m,  
        d.diameter_max_m  
    FROM `cosmic-watch-de.raw.fact_close_approaches` AS f  
    JOIN `cosmic-watch-de.raw.dim_asteroids` AS d  
        ON f.asteroid_id = d.asteroid_id  
    WHERE d.is_hazardous = TRUE  
        AND LOWER(TRIM(f.orbiting_body)) = 'earth'  
        AND f.close_approach_date >= CURRENT_DATE('Asia/Riyadh')  
),  
next_approach AS (  
    SELECT *  
    FROM upcoming  
    QUALIFY ROW_NUMBER() OVER (  
        PARTITION BY asteroid_id  
        ORDER BY  
            close_approach_date,  
            miss_distance_km ASC NULLS LAST  
    ) = 1  
)  
SELECT  
    asteroid_id,  
    name,  
    close_approach_date,  
    days_until_approach,  
    ROUND(miss_distance_km, 2) AS miss_distance_km,  
    ROUND(miss_distance_lunar, 2) AS miss_distance_lunar,  
    ROUND(diameter_min_m, 2) AS diameter_min_m,  
    ROUND(diameter_max_m, 2) AS diameter_max_m  
FROM next_approach  
ORDER BY  
    close_approach_date,  
    asteroid_id;
"""
df_up = run_query(sql_upcoming)
if not df_up.empty:
    st.dataframe(df_up, use_container_width=True, hide_index=True)
else:
    st.info("No upcoming approaches found.")
