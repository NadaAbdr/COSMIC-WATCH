-- Singular test: returns the rows that break basic physical rules. Zero rows = pass.
select 'fact' as tbl, approach_id::text as id, 'non-positive velocity or negative distance' as problem
from {{ ref('fact_close_approaches') }}
where velocity_kmh <= 0 or miss_distance_km < 0 or miss_distance_lunar < 0

union all

select 'dim', asteroid_id, 'diameter_max < diameter_min'
from {{ ref('dim_asteroids') }}
where diameter_max_m < diameter_min_m
