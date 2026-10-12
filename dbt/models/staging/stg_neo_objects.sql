-- One row per asteroid object per source response, with the raw JSON object kept in `obj`.
-- Two sources: the per-asteroid lookup (complete history) and the date-range feed (recent window).
-- Marts pick the best row per asteroid, so the same asteroid appearing in many responses is fine here.

with lookup as (

    select
        asteroid_id,
        payload      as obj,
        'lookup'     as data_source,
        1            as source_priority,   -- lookup wins when an asteroid is in both
        fetched_at
    from {{ source('raw', 'neo_lookup_responses') }}

),

feed as (

    select
        e ->> 'id'   as asteroid_id,
        e            as obj,
        'feed'       as data_source,
        2            as source_priority,
        f.fetched_at
    from {{ source('raw', 'neo_feed_responses') }} as f
    cross join lateral jsonb_each(f.payload -> 'near_earth_objects') as d(day, objs)
    cross join lateral jsonb_array_elements(d.objs) as e

)

select * from lookup
union all
select * from feed
