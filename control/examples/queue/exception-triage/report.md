# Exception Triage draft

9 root cause(s) across 12 exception(s). 1 eligible to auto-apply.

## Suggestions, by root cause

| Code | Sample value | Count | Resolution | Confidence | Whitelisted | Auto-apply |
|---|---|---|---|---|---|---|
| ACCOUNT_NOT_FOUND | ACC9999999 | 2 | Add the account to the cross-reference (a new account) or confirm it is not managed; records wait until then. | 50% (0/0) | no | no |
| PRICE_STALE | - | 2 | Obtain a current price; the stale price is used and flagged until then. | 82% (8/9) | yes | **yes** |
| TRANSACTION_CODE_UNMAPPED | ZZ | 2 | Map the code in the config's transaction code table; records with the code wait until then. | 50% (0/0) | no | no |
| ACCOUNT_NOT_FOUND | ACC8888888 | 1 | Add the account to the cross-reference (a new account) or confirm it is not managed; records wait until then. | 50% (0/0) | no | no |
| PRICE_MISSING | - | 1 | Carry the last available price forward when the pricing policy allows it; otherwise obtain a price. | 29% (1/5) | yes | no |
| SECURITY_NOT_FOUND | 000000000 | 1 | Add the security to the security master or map the custodian's identifier; records wait until then. | 50% (0/0) | no | no |
| SECURITY_NOT_FOUND | 111111111 | 1 | Add the security to the security master or map the custodian's identifier; records wait until then. | 50% (0/0) | no | no |
| SECURITY_NOT_FOUND | 222222222 | 1 | Add the security to the security master or map the custodian's identifier; records wait until then. | 50% (0/0) | no | no |
| UNKNOWN_LOCAL_CODE | - | 1 | *(no taxonomy entry — no suggestion)* | 50% (0/0) | no | no |

## Codes with no taxonomy entry

Not guessed at — these need a person, or a new code added to the taxonomy first:

- UNKNOWN_LOCAL_CODE

## Acceptance rate, per code

| Code | Confidence | Accepted | Decisions recorded |
|---|---|---|---|
| ACCOUNT_NOT_FOUND | 50% | 0 | 0 |
| PRICE_MISSING | 29% | 1 | 5 |
| PRICE_STALE | 82% | 8 | 9 |
| SECURITY_NOT_FOUND | 50% | 0 | 0 |
| TRANSACTION_CODE_UNMAPPED | 50% | 0 | 0 |
| UNKNOWN_LOCAL_CODE | 50% | 0 | 0 |
