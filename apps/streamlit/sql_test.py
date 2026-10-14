import os
import pandas as pd
import psycopg
import streamlit as st

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# Page configuration for a wide dashboard layout
st.set_page_config(page_title="Cosmic Watch Dashboard", layout="wide")

# دالة مساعدة لتنفيذ الاستعلامات
def run_query(sql_query):
    try:
        with psycopg.connect(
            host=os.environ.get("POSTGRES_HOST", "localhost"),
            port=int(os.environ.get("POSTGRES_PORT", "5432")),
            dbname=os.environ.get("POSTGRES_DB", "cosmic_watch"),
            user=os.environ.get("POSTGRES_USER", "cosmic"),
            password=os.environ["POSTGRES_PASSWORD"],
        ) as conn:
            cur = conn.execute(sql_query)
            cols = [c.name for c in cur.description]
            df = pd.DataFrame(cur.fetchall(), columns=cols)
        return df
    except Exception as e:
        st.error(f"An error occurred: {e}")
        return pd.DataFrame()


# 🎛️ SIDEBAR: Filters (الشريط الجانبي للفلاتر مثل الصورة)

st.sidebar.title("Filters")
st.sidebar.markdown("---")

st.sidebar.subheader("Close-approach dates")
# محاكاة نطاق التاريخ مثل الصورة
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


# 🔭 MAIN DASHBOARD AREA

st.title("🔭 Cosmic Watch: Asteroid Analytics Dashboard")
st.markdown("---")

# شرط الخطورة إذا تم تفعيله
hazard_filter_sql = "AND is_potentially_hazardous = true" if hazardous_only else ""

# ------------------------------------------
# 1. Metric / Top Summary
# ------------------------------------------
sql_period_count = f"""
SELECT COUNT(*) AS total_approaches
FROM analytics.fact_close_approaches
WHERE close_approach_date >= '{start_date}' 
  AND close_approach_date <= '{end_date}'
  {hazard_filter_sql};
"""
df_period = run_query(sql_period_count)
if not df_period.empty:
    total_count = df_period.iloc[0]['total_approaches']
    st.metric(label="Total Approaches (Filtered)", value=total_count)

st.markdown("---")

# ------------------------------------------
# 2. Layout split: Table (Left) & Chart (Right) - 
# ------------------------------------------
col_left, col_right = st.columns([1.5, 1])

with col_left:
    st.markdown("### 15 Closest Approaches")
    
    # استعلام أقرب 15 اقتراباً (بناءً على مسافة الاقتراب miss_distance_km إذا كانت متوفرة أو تعديلها حسب أعمدتك)
    sql_closest = f"""
    SELECT asteroid_id, close_approach_date, miss_distance_km
    FROM analytics.fact_close_approaches
    WHERE close_approach_date >= '{start_date}' 
      AND close_approach_date <= '{end_date}'
      {hazard_filter_sql}
    ORDER BY miss_distance_km ASC
    LIMIT 15;
    """
    df_closest = run_query(sql_closest)
    if not df_closest.empty:
        st.dataframe(df_closest, use_container_width=True, hide_index=True)
    else:
        st.info("No data available for the selected filters.")

with col_right:
    st.markdown("### Last 6 Months Trend ")
    
    # استعلام يجلب آخر 6 شهور فقط بناءً على التواريخ المتوفرة في الجدول
    sql_last_6_months = """
    SELECT TO_CHAR(close_approach_date, 'YYYY-MM') AS approach_month, COUNT(*) AS approaches_count
    FROM analytics.fact_close_approaches
    WHERE close_approach_date >= (SELECT MAX(close_approach_date) - INTERVAL '6 months' FROM analytics.fact_close_approaches)
    GROUP BY approach_month
    ORDER BY approach_month DESC
    LIMIT 6;
    """
    df_months = run_query(sql_last_6_months)
    if not df_months.empty:
        # ترتيب التصاعدي للرسم البياني الزمني
        df_months = df_months.sort_values("approach_month")
        st.bar_chart(df_months.set_index("approach_month"))
    else:
        st.info("No recent data for the last 6 months.")



# 5. Most Dangerous Asteroids (Frequent & Large)

st.markdown("### ⚠️ 5. Most Dangerous Asteroids (Frequent)⚠️" )

sql_dangerous = """
SELECT 
    asteroid_id, 
    COUNT(*) AS approach_count
FROM analytics.fact_close_approaches
GROUP BY asteroid_id
ORDER BY approach_count DESC
LIMIT 5;
"""
df_dangerous = run_query(sql_dangerous)
if not df_dangerous.empty:
    st.dataframe(df_dangerous, use_container_width=True, hide_index=True)        