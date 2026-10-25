-- Staging: one row per invoice line, with clear names and types. A view, so it stores nothing.
select
    line_key,
    invoice_id,
    ordered_on                          as sale_date,
    date_trunc('month', ordered_on)     as sale_month,
    store_no                            as store_id,
    store_name,
    store_city,
    store_zip_code,
    county_name,
    category_code,
    category_name,
    vendor_number                       as vendor_id,
    vendor_name,
    item_no                             as item_id,
    im_desc                             as item_description,
    pack,
    bottle_volume_ml,
    state_bottle_cost,
    state_bottle_retail,
    sales_bottles                       as bottles_sold,
    sales_dollars,
    sales_liters,
    loaded_at
from {{ source('raw', 'LIQUOR_SALES') }}
where ordered_on is not null
  and store_no is not null
