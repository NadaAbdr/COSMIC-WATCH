# Metric definitions

How every number on the Cosmic Watch dashboard is calculated. All come from the dbt tables
`analytics.dim_asteroids` (one row per asteroid) and `analytics.fact_close_approaches`
(one row per close approach). Source data: NASA NeoWs API.

| Metric | Definition |
|---|---|
| **Close approach** | One asteroid passing a body (usually Earth) at one exact moment. Identified by `approach_id` = md5(asteroid id + exact time). The same approach seen in several API responses is counted once. |
| **Close approaches** (KPI) | Number of close approaches with a date inside the selected dates, for the selected bodies. |
| **Asteroids** (KPI) | Count of distinct `asteroid_id` among those approaches. |
| **Potentially hazardous** | Distinct asteroids among those approaches flagged `is_hazardous` by NASA (`is_potentially_hazardous_asteroid`). NASA sets this for asteroids larger than about 140 m that can come within about 0.05 AU of Earth's orbit. It does **not** mean an impact is expected. |
| **Closest pass** | Smallest `miss_distance_lunar` among the approaches. 1 lunar distance (LD) is about 384,400 km. |
| **Relative speed** | `velocity_kmh`: speed of the asteroid relative to the body, in km/h. |
| **Diameter** | `diameter_min_m` / `diameter_max_m`: NASA's estimated range in metres. The scatter chart uses the maximum, on a log scale because sizes range from a few metres to tens of kilometres. |
| **Close approaches per month** | Count of approaches grouped by calendar month (first day of the month on the x axis). |
| **Fastest** | Largest `velocity_kmh` among the approaches. |
| **Orbit class** | NASA orbit family from `orbital_data.orbit_class_type` (AMO Amor, APO Apollo, ATE Aten, IEO Atira). |

## Notes
- The default date window is one year back to one year ahead of today, limited to the dates present in the data. Future dates are NASA's
  computed predictions, not observations.
- `orbiting_body` uses NASA's short codes for some planets (`Merc`, `Juptr`). The dashboard shows
  full names; the tables keep NASA's codes.
- Asteroids that appear in both the per-asteroid lookup and the date-range feed use the lookup values.
