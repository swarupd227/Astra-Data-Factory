"""Normalizer transformer: GCUS position detail lines to normalized positions, on Snowpark Connect.

Same result as original.py, expressed as DataFrame operations: Snowpark
Connect runs the DataFrame API on Snowflake and has no RDD, so the text
read and the per-line Python parse are replaced by a DataFrame of lines
and substring expressions. The harness (astra-verify snowpark assess)
hands the lines in as a one-column DataFrame, which is how the file
arrives on both engines.

Layout: Pershing GCUS position file, spec pershing_gcus 2017-07-25 (specs/pershing_gcus/2017-07-25.yaml).
"""

from pyspark.sql import DataFrame, SparkSession
from pyspark.sql import functions as F

NAME = "position_normalizer"
INPUTS = {"positions": "gcus_positions.dat"}
OUTPUT_COLUMNS = ["account_number", "cusip", "quantity", "price", "as_of_date"]


def run(spark: SparkSession, inputs: dict[str, DataFrame]) -> DataFrame:
    lines = inputs["positions"]
    detail = lines.filter((F.substring("line", 1, 3) == "DTL") & (F.length("line") >= 64))
    parsed = detail.select(
        F.trim(F.substring("line", 4, 10)).alias("account_number"),
        F.trim(F.substring("line", 14, 9)).alias("cusip"),
        F.substring("line", 23, 18).alias("quantity_digits"),
        F.substring("line", 41, 1).alias("quantity_sign"),
        F.substring("line", 42, 15).alias("price_digits"),
        F.substring("line", 57, 8).alias("as_of_date_text"),
    )
    unsigned = (F.col("quantity_digits").cast("decimal(18,0)") / F.lit(100000)).cast("decimal(18,5)")
    quantity = (
        F.when(F.col("quantity_sign") == "-", -unsigned)
        .when(F.col("quantity_sign") == "+", unsigned)
        .otherwise(F.lit(None).cast("decimal(18,5)"))
    )
    price = (F.col("price_digits").cast("decimal(15,0)") / F.lit(1000000)).cast("decimal(15,6)")
    return (
        parsed.withColumn("quantity", quantity)
        .withColumn("price", price)
        .withColumn("as_of_date", F.to_date("as_of_date_text", "yyyyMMdd"))
        .filter(F.col("cusip") != "")
        .select(*OUTPUT_COLUMNS)
    )
