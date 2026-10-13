-- Cosmic Watch: three questions answered with SQL (plus two bonus ones).
-- Tables: analytics.dim_asteroids (one row per asteroid)
--         analytics.fact_close_approaches (one row per close approach)
-- Run one query at a time (see docs/SQL_QUESTIONS.md).

-- Q1. Which 10 asteroids came closest to Earth, and how big are they?
-- 1 lunar distance (LD) is about 384,400 km.
SELECT d.name,
       f.close_approach_date,
       ROUND(f.miss_distance_lunar::numeric, 3)  AS miss_distance_ld,
       ROUND(f.miss_distance_km::numeric)        AS miss_distance_km,
       ROUND(d.diameter_max_m::numeric)          AS max_diameter_m,
       d.is_hazardous
FROM analytics.fact_close_approaches AS f
JOIN analytics.dim_asteroids         AS d ON d.asteroid_id = f.asteroid_id
WHERE f.orbiting_body = 'Earth'
ORDER BY f.miss_distance_km ASC
LIMIT 10;

-- Q2. Are "potentially hazardous" asteroids more common in some orbit classes?
SELECT orbit_class,
       COUNT(*)                                                    AS asteroids,
       COUNT(*) FILTER (WHERE is_hazardous)                        AS hazardous,
       ROUND(100.0 * COUNT(*) FILTER (WHERE is_hazardous) / COUNT(*), 1) AS pct_hazardous,
       ROUND(AVG(diameter_max_m)::numeric)                         AS avg_max_diameter_m
FROM analytics.dim_asteroids
GROUP BY orbit_class
ORDER BY pct_hazardous DESC;

-- Q3. How many close approaches happen each year, and how fast are they?
-- Note: the data holds NASA's predictions up to 2200, so later years are calculated, not observed.
SELECT EXTRACT(YEAR FROM close_approach_date)::int AS year,
       COUNT(*)                                    AS approaches,
       COUNT(DISTINCT asteroid_id)                 AS different_asteroids,
       ROUND(AVG(velocity_kmh)::numeric)           AS avg_speed_kmh
FROM analytics.fact_close_approaches
WHERE orbiting_body = 'Earth'
GROUP BY 1
ORDER BY approaches DESC
LIMIT 10;

-- Bonus A. Which asteroids pass Earth most often?
SELECT d.name,
       COUNT(*)                          AS approaches,
       ROUND(MIN(f.miss_distance_lunar)::numeric, 2) AS closest_ld
FROM analytics.fact_close_approaches AS f
JOIN analytics.dim_asteroids         AS d ON d.asteroid_id = f.asteroid_id
WHERE f.orbiting_body = 'Earth'
GROUP BY d.name
ORDER BY approaches DESC, closest_ld
LIMIT 10;

-- Bonus B. Do bigger asteroids pass faster? (bucketed by size)
SELECT CASE WHEN d.diameter_max_m <  50  THEN '1: under 50 m'
            WHEN d.diameter_max_m < 140  THEN '2: 50-140 m'
            WHEN d.diameter_max_m < 1000 THEN '3: 140 m - 1 km'
            ELSE                              '4: over 1 km' END AS size_class,
       COUNT(*)                              AS approaches,
       ROUND(AVG(f.velocity_kmh)::numeric)   AS avg_speed_kmh
FROM analytics.fact_close_approaches AS f
JOIN analytics.dim_asteroids         AS d ON d.asteroid_id = f.asteroid_id
GROUP BY 1
ORDER BY 1;
