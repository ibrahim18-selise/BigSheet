"""Create a large test CSV:  python tools/make_sample_data.py [rows] [output.csv]"""
import sys
import duckdb

rows = int(sys.argv[1]) if len(sys.argv) > 1 else 10_000_000
out = sys.argv[2] if len(sys.argv) > 2 else "sample_sales.csv"
duckdb.sql(f"""
COPY (
  SELECT range + 1 AS order_id,
         'Customer ' || (range % 250000) AS customer,
         ['Dhaka', 'Chattogram', 'Sylhet', 'Khulna', 'Rajshahi', 'Barishal'][(range % 6) + 1] AS city,
         ['Electronics', 'Grocery', 'Clothing', 'Books', 'Home'][(hash(range) % 5) + 1] AS category,
         round(random() * 5000, 2) AS amount,
         (random() * 20)::INT + 1 AS quantity,
         DATE '2021-01-01' + (range % 1800)::INT AS order_date
  FROM range({rows})
) TO '{out}' (HEADER)
""")
print(f"Wrote {rows:,} rows to {out}")
