-- Mart: one row per store per month. This is the input to tiering.
with lines as (
    select * from {{ ref('stg_liquor_sales') }}
)
select
    store_id || '-' || to_char(sale_month, 'YYYY-MM')   as store_month_key,
    store_id,
    max_by(store_name, sale_date)                       as store_name,     -- latest name on record
    max_by(store_city, sale_date)                       as store_city,
    max_by(county_name, sale_date)                      as county_name,
    sale_month,
    sum(sales_dollars)                                  as sales_dollars,
    sum(bottles_sold)                                   as bottles_sold,
    sum(sales_liters)                                   as sales_liters,
    count(distinct invoice_id)                          as invoices,
    count(distinct item_id)                             as distinct_items,
    count(distinct category_name)                       as distinct_categories,
    count(*)                                            as line_items
from lines
group by store_id, sale_month
