"""Normalizer transformer: DRIP transactions split into a dividend and a purchase, as the legacy Spark job has it.

The legacy job parses detail lines with a Python UDF, then splits each DRIP
row into two rows with an RDD flatMap. The Snowpark Connect version
(snowpark.py) does the same with DataFrame expressions; changes.yaml
records what had to change and why.

Layout: illustrative transaction file, spec drip_transaction_example 2026-01-01
(specs/drip_transaction_example/2026-01-01.yaml). The split rule is the spec's: the
dividend keeps the amount and drops the quantity, the purchase negates the amount.
"""

from decimal import Decimal

from pyspark.sql import Row, SparkSession
from pyspark.sql.types import DateType, DecimalType, StringType, StructField, StructType

NAME = "drip_split"
INPUTS = {"transactions": "drip_transactions.dat"}

SCHEMA = StructType(
    [
        StructField("transaction_id", StringType(), False),
        StructField("account_number", StringType(), False),
        StructField("cusip", StringType(), False),
        StructField("transaction_type", StringType(), False),
        StructField("trade_date", DateType(), True),
        StructField("quantity", DecimalType(13, 4), True),
        StructField("amount", DecimalType(13, 2), True),
    ]
)


def _parse(line: str) -> Row | None:
    from datetime import datetime

    if not line.startswith("D") or len(line) < 84:
        return None
    quantity = Decimal(line[57:70]) / Decimal(10000) if line[57:70].strip().isdigit() else None
    amount = Decimal(line[70:83]) / Decimal(100) if line[70:83].strip().isdigit() else None
    if amount is not None and line[83:84] == "-":
        amount = -amount
    try:
        trade_date = datetime.strptime(line[49:57], "%Y%m%d").date()
    except ValueError:
        trade_date = None
    return Row(
        transaction_id=line[1:13].strip(),
        account_number=line[26:36].strip(),
        cusip=line[36:45].strip(),
        transaction_type=line[45:49].strip(),
        trade_date=trade_date,
        quantity=quantity,
        amount=amount,
    )


def _split(row: Row) -> list[Row]:
    if row.transaction_type != "DRIP":
        return [row]
    dividend = Row(transaction_id=row.transaction_id + "-1", account_number=row.account_number, cusip=row.cusip, transaction_type="DIV", trade_date=row.trade_date, quantity=None, amount=row.amount)
    purchase = Row(transaction_id=row.transaction_id + "-2", account_number=row.account_number, cusip=row.cusip, transaction_type="BUY", trade_date=row.trade_date, quantity=row.quantity, amount=-row.amount if row.amount is not None else None)
    return [dividend, purchase]


def run(spark: SparkSession, paths: dict[str, str]):
    lines = spark.sparkContext.textFile(paths["transactions"])
    rows = lines.map(_parse).filter(lambda r: r is not None).flatMap(_split)
    return spark.createDataFrame(rows, SCHEMA)
