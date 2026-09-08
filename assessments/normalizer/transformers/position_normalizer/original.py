"""Normalizer transformer: GCUS position detail lines to normalized positions, as the legacy Spark job has it.

This is the transformer as it runs in the legacy Normalizer: the file is read
as text through the RDD API, each line is parsed by a Python function on the
executor, and the result is turned into a DataFrame at the end. The Snowpark
Connect version (snowpark.py) does the same work with DataFrame expressions;
changes.yaml records what had to change and why.

Layout: Pershing GCUS position file, spec pershing_gcus 2017-07-25 (specs/pershing_gcus/2017-07-25.yaml).
"""

from decimal import Decimal

from pyspark.sql import Row, SparkSession
from pyspark.sql.types import DateType, DecimalType, StringType, StructField, StructType

NAME = "position_normalizer"
INPUTS = {"positions": "gcus_positions.dat"}

SCHEMA = StructType(
    [
        StructField("account_number", StringType(), False),
        StructField("cusip", StringType(), False),
        StructField("quantity", DecimalType(18, 5), True),
        StructField("price", DecimalType(15, 6), True),
        StructField("as_of_date", DateType(), True),
    ]
)


def _parse(line: str) -> Row | None:
    """One detail line to one row; header, trailer and short lines are dropped."""
    from datetime import datetime

    if not line.startswith("DTL") or len(line) < 64:
        return None
    digits = line[22:40]
    sign = line[40:41]
    quantity = Decimal(digits) / Decimal(100000) if digits.strip().isdigit() else None
    if quantity is not None:
        quantity = -quantity if sign == "-" else quantity if sign == "+" else None
    price_digits = line[41:56]
    price = Decimal(price_digits) / Decimal(1000000) if price_digits.strip().isdigit() else None
    try:
        as_of = datetime.strptime(line[56:64], "%Y%m%d").date()
    except ValueError:
        as_of = None
    cusip = line[13:22].strip()
    if not cusip:
        return None
    return Row(account_number=line[3:13].strip(), cusip=cusip, quantity=quantity, price=price, as_of_date=as_of)


def run(spark: SparkSession, paths: dict[str, str]):
    lines = spark.sparkContext.textFile(paths["positions"])
    rows = lines.map(_parse).filter(lambda r: r is not None)
    return spark.createDataFrame(rows, SCHEMA)
