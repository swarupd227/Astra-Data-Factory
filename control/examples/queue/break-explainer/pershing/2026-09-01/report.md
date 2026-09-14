# Break Explainer: pershing 2026-09-01

3 difference(s). 3 explained (100%).

| Key | Field | Legacy | Lakehouse | Cause | Rule | Explanation |
|---|---|---|---|---|---|---|
| ['ACC0000002', '594918104', '2026-09-01'] | QUANTITY | 100.00000 | 100.00200 | transform | pershing_gcus.quantity_sign | QUANTITY is produced by signed_implied_decimal(13, 5); the two pipelines' values differ within what a rounding or scale difference in this transform could explain. |
| ['ACC0000003', '912828U40', '2026-09-01'] | PRICE | 50.1234 | 50.2000 | transform | - | PRICE is produced by implied_decimal(6); the two pipelines' values differ within what a rounding or scale difference in this transform could explain. |
| ['ACC0000004', '922908363', '2026-09-01'] | MARKET_VALUE | 5012.34 | 5999.99 | unmapped | - | MARKET_VALUE has no direct mapping in this config; it is likely computed downstream, which this agent does not trace. |

## By cause

| Cause | Count |
|---|---|
| transform | 2 |
| resolution | 0 |
| unmapped | 1 |
| unexplained | 0 |
