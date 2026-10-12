-- One row per asteroid. Static attributes only (size, hazard flags, orbit).
-- If an asteroid is in several responses, prefer the lookup source, then the newest fetch.

with ranked as (

    select
        *,
        row_number() over (
            partition by asteroid_id
            order by source_priority, fetched_at desc
        ) as rn
    from {{ ref('stg_neo_objects') }}

)

select
    asteroid_id,
    obj ->> 'name'                                                                       as name,
    nullif(obj ->> 'absolute_magnitude_h', '')::double precision                         as absolute_magnitude,
    nullif(obj -> 'estimated_diameter' -> 'meters' ->> 'estimated_diameter_min', '')::double precision as diameter_min_m,
    nullif(obj -> 'estimated_diameter' -> 'meters' ->> 'estimated_diameter_max', '')::double precision as diameter_max_m,
    (obj ->> 'is_potentially_hazardous_asteroid')::boolean                               as is_hazardous,
    (obj ->> 'is_sentry_object')::boolean                                                as is_sentry,
    obj -> 'orbital_data' -> 'orbit_class' ->> 'orbit_class_type'                        as orbit_class,
    nullif(obj -> 'orbital_data' ->> 'eccentricity', '')::double precision               as eccentricity,
    nullif(obj -> 'orbital_data' ->> 'semi_major_axis', '')::double precision            as semi_major_axis_au,
    data_source
from ranked
where rn = 1
