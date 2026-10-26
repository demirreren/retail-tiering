-- Mart: one row per store per quarter. This is the direct input to tiering (Phase 2).
-- Besides totals it carries the three things a tier score needs that totals do not show:
-- active_months (did the store trade all quarter), monthly_sales_cv (how steady its months were)
-- and distinct_categories (how broad its product mix is).
with lines as (
    select *, date_trunc('quarter', sale_date) as quarter_start
    from {{ ref('stg_liquor_sales') }}
),

monthly as (
    select store_id, quarter_start, sale_month, sum(sales_dollars) as month_sales
    from lines
    group by store_id, quarter_start, sale_month
),

consistency as (
    -- coefficient of variation = std / mean of the monthly totals. 0 = perfectly even months.
    select
        store_id,
        quarter_start,
        count(*)                                                 as active_months,
        stddev_pop(month_sales) / nullif(avg(month_sales), 0)    as monthly_sales_cv
    from monthly
    group by store_id, quarter_start
),

totals as (
    select
        store_id,
        quarter_start,
        max_by(store_name, sale_date)       as store_name,
        max_by(store_city, sale_date)       as store_city,
        max_by(county_name, sale_date)      as county_name,
        sum(sales_dollars)                  as sales_dollars,
        sum(bottles_sold)                   as bottles_sold,
        sum(sales_liters)                   as sales_liters,
        count(distinct invoice_id)          as invoices,
        count(distinct item_id)             as distinct_items,
        count(distinct category_name)       as distinct_categories,
        count(*)                            as line_items
    from lines
    group by store_id, quarter_start
)

select
    t.store_id || '-' || year(t.quarter_start) || 'Q' || quarter(t.quarter_start)   as store_quarter_key,
    t.store_id,
    t.quarter_start,
    year(t.quarter_start)                                                           as year,
    quarter(t.quarter_start)                                                        as quarter,
    t.store_name,
    t.store_city,
    t.county_name,
    t.sales_dollars,
    t.bottles_sold,
    t.sales_liters,
    t.invoices,
    t.distinct_items,
    t.distinct_categories,
    t.line_items,
    c.active_months,
    c.monthly_sales_cv
from totals t
join consistency c
  on c.store_id = t.store_id and c.quarter_start = t.quarter_start
