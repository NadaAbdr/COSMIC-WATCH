


SELECT asteroid_id, COUNT(*) AS approach_count
FROM analytics.fact_close_approaches
GROUP BY asteroid_id
ORDER BY approach_count DESC
LIMIT 5;  

SELECT column_name, data_type 
FROM information_schema.columns 
WHERE table_schema = 'analytics' 
  AND table_name = 'fact_close_approaches';

SELECT 
    asteroid_id, 
    COUNT(*) AS approach_count,
    AVG(estimated_diameter_max_m) AS avg_diameter_m -- أو اسم عمود القطر المتوفر عندكم
FROM analytics.fact_close_approaches
GROUP BY asteroid_id
ORDER BY approach_count DESC, avg_diameter_m DESC
LIMIT 5;  