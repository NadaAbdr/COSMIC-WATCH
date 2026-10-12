-- One row per close approach (an asteroid passing a body at a moment in time).
-- Built from both sources, then de-duplicated: the same approach can appear in the
-- lookup history AND in overlapping feed date ranges (e.g. 09-01..09-07 and 09-02..09-08).

with approaches as (

    select
        o.asteroid_id,
        o.data_source,
        o.source_priority,
        o.fetched_at,
        ca
    from {{ ref('stg_neo_objects') }} as o
    cross join lateral jsonb_array_elements(coalesce(o.obj -> 'close_approach_data', '[]'::jsonb)) as ca

),

keyed as (

    select
        asteroid_id,
        -- the exact moment is the natural key; fall back to the date if the epoch is missing
        md5(asteroid_id || '|' || coalesce(ca ->> 'epoch_date_close_approach', ca ->> 'close_approach_date')) as approach_id,
        (ca ->> 'close_approach_date')::date                                                       as close_approach_date,
        ca ->> 'close_approach_date_full'                                                          as close_approach_date_full,
        (to_timestamp((ca ->> 'epoch_date_close_approach')::bigint / 1000.0) at time zone 'UTC')   as close_approach_ts_utc,
        ca ->> 'orbiting_body'                                                                     as orbiting_body,
        nullif(ca -> 'relative_velocity' ->> 'kilometers_per_hour', '')::double precision          as velocity_kmh,
        nullif(ca -> 'miss_distance' ->> 'kilometers', '')::double precision                       as miss_distance_km,
        nullif(ca -> 'miss_distance' ->> 'lunar', '')::double precision                            as miss_distance_lunar,
        data_source,
        row_number() over (
            partition by asteroid_id,
                         coalesce(ca ->> 'epoch_date_close_approach', ca ->> 'close_approach_date')
            order by source_priority, fetched_at desc
        ) as rn
    from approaches

)

select
    approach_id,
    asteroid_id,
    close_approach_date,
    close_approach_date_full,
    close_approach_ts_utc,
    orbiting_body,
    velocity_kmh,
    miss_distance_km,
    miss_distance_lunar,
    data_source
from keyed
where rn = 1
