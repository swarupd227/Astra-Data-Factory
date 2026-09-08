"""Normalizer transformer: DRIP transactions split into a dividend and a purchase, on Snowpark Connect.

Same result as original.py with DataFrame expressions: the per-line parse is
substrings and casts, and the split is an explode over an array of the parts
a row yields, so no Python runs on the executor.

Layout: illustrative transaction file, spec drip_transaction_example 2026-01-01.
"""

from pyspark.sql import DataFrame, SparkSession
from pyspark.sql import functions as F

NAME = "drip_split"
INPUTS = {"transactions": "drip_transactions.dat"}
OUTPUT_COLUMNS = ["transaction_id", "account_number", "cusip", "transaction_type", "trade_date", "quantity", "amount"]


def run(spark: SparkSession, inputs: dict[str, DataFrame]) -> DataFrame:
    lines = inputs["transactions"]
    detail = lines.filter((F.substring("line", 1, 1) == "D") & (F.length("line") >= 84))
    parsed = detail.select(
        F.trim(F.substring("line", 2, 12)).alias("transaction_id"),
        F.trim(F.substring("line", 27, 10)).alias("account_number"),
        F.trim(F.substring("line", 37, 9)).alias("cusip"),
        F.trim(F.substring("line", 46, 4)).alias("transaction_type"),
        F.to_date(F.substring("line", 50, 8), "yyyyMMdd").alias("trade_date"),
        (F.substring("line", 58, 13).cast("decimal(13,0)") / F.lit(10000)).cast("decimal(13,4)").alias("quantity"),
        (F.substring("line", 71, 13).cast("decimal(13,0)") / F.lit(100)).cast("decimal(13,2)").alias("unsigned_amount"),
        F.substring("line", 84, 1).alias("amount_sign"),
    ).withColumn("amount", F.when(F.col("amount_sign") == "-", -F.col("unsigned_amount")).otherwise(F.col("unsigned_amount")))

    part = lambda suffix, kind, quantity, amount: F.struct(  # noqa: E731
        F.concat(F.col("transaction_id"), F.lit(suffix)).alias("transaction_id"),
        F.lit(kind).alias("transaction_type"),
        quantity.alias("quantity"),
        amount.alias("amount"),
    )
    parts = F.when(
        F.col("transaction_type") == "DRIP",
        F.array(
            part("-1", "DIV", F.lit(None).cast("decimal(13,4)"), F.col("amount")),
            part("-2", "BUY", F.col("quantity"), -F.col("amount")),
        ),
    ).otherwise(F.array(F.struct(F.col("transaction_id"), F.col("transaction_type"), F.col("quantity"), F.col("amount"))))

    exploded = parsed.withColumn("part", F.explode(parts))
    return exploded.select(
        F.col("part.transaction_id").alias("transaction_id"),
        "account_number",
        "cusip",
        F.col("part.transaction_type").alias("transaction_type"),
        "trade_date",
        F.col("part.quantity").cast("decimal(13,4)").alias("quantity"),
        F.col("part.amount").cast("decimal(13,2)").alias("amount"),
    )
