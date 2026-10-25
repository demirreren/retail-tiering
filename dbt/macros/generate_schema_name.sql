{#- dbt's default would name the schemas RAW_STAGING and RAW_MARTS (target schema + suffix).
    This override uses the plain names STAGING and MARTS instead. -#}
{% macro generate_schema_name(custom_schema_name, node) -%}
    {%- if custom_schema_name is none -%}
        {{ target.schema }}
    {%- else -%}
        {{ custom_schema_name | trim }}
    {%- endif -%}
{%- endmacro %}
