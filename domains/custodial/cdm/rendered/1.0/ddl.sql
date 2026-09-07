-- custodial CDM 1.0: DDL for Snowflake managed Iceberg tables in schema SILVER.
-- Rendered by astra-spec cdm render from custodial/cdm/1.0.yaml. Do not edit; change the model and re-render.
-- {{ DATABASE }} is filled at deploy time. Tables inherit the database's external volume and catalog (ADR 0002).

-- Firm: key (FIRM_ID)
CREATE ICEBERG TABLE IF NOT EXISTS {{ DATABASE }}."SILVER"."FIRM" (
  "FIRM_ID"        STRING NOT NULL COMMENT 'Platform identifier of the firm.',
  "FIRM_NAME"      STRING NOT NULL COMMENT 'Legal or trading name of the firm.',
  "FIRM_TYPE"      STRING COMMENT 'What kind of firm it is. Codes: RIA = registered investment adviser; BROKER_DEALER = broker-dealer; BANK = bank or trust company; OTHER = any other type.',
  "STATUS"         STRING NOT NULL COMMENT 'Whether the platform still manages accounts for the firm. Codes: ACTIVE = accounts are managed; INACTIVE = no accounts are managed.',
  "SOURCE_SYSTEM"  STRING NOT NULL COMMENT 'Custodian id or reference feed that produced the row.',
  "SOURCE_FILE"    STRING COMMENT 'Name of the file the row was loaded from; null for rows produced by resolution or reference data.',
  "SOURCE_LINE"    NUMBER(18,0) COMMENT 'Line of the source file the row came from.',
  "CONFIG_VERSION" STRING COMMENT 'Version of the config whose pipeline produced the row.',
  "LOADED_AT"      TIMESTAMP_NTZ(6) NOT NULL COMMENT 'When the row was written.',
  "UPDATED_AT"     TIMESTAMP_NTZ(6) COMMENT 'When the row last changed; null until it does.'
)
BASE_LOCATION = 'silver/firm/'
COMMENT = 'Firm: The advisory firm or broker-dealer that owns the relationship with an account''s holder and under which the platform manages the account. [custodial CDM 1.0]';

-- Account: key (CUSTODIAN_ID, ACCOUNT_NUMBER)
-- references Firm through (FIRM_ID)
CREATE ICEBERG TABLE IF NOT EXISTS {{ DATABASE }}."SILVER"."ACCOUNT" (
  "CUSTODIAN_ID"      STRING NOT NULL COMMENT 'Custodian that holds the account.',
  "ACCOUNT_NUMBER"    STRING NOT NULL COMMENT 'Account number as the custodian assigns it. PII: account_number.',
  "FIRM_ID"           STRING NOT NULL COMMENT 'Firm the account belongs to.',
  "ACCOUNT_ID"        STRING COMMENT 'Platform account id from the account cross-reference; null until resolved.',
  "ACCOUNT_NAME"      STRING COMMENT 'Registration name of the account. PII: name.',
  "ACCOUNT_TYPE"      STRING COMMENT 'Custodian''s account type, as received.',
  "REGISTRATION_TYPE" STRING COMMENT 'Canonical registration of the account. Codes: INDIVIDUAL = individual; JOINT = joint; TRUST = trust; RETIREMENT = IRA or other retirement account; CORPORATE = corporate or entity; OTHER = any other registration.',
  "TAX_STATUS"        STRING COMMENT 'Whether the account is taxable. Codes: TAXABLE = taxable; TAX_DEFERRED = tax deferred; TAX_EXEMPT = tax exempt.',
  "BASE_CURRENCY"     STRING NOT NULL COMMENT 'ISO 4217 currency the account is valued in.',
  "OPENED_ON"         DATE COMMENT 'Date the custodian opened the account.',
  "CLOSED_ON"         DATE COMMENT 'Date the custodian closed the account; null while open.',
  "STATUS"            STRING NOT NULL COMMENT 'Whether the account is open. Codes: OPEN = open; CLOSED = closed; PENDING = opened at the custodian but not yet resolved to a platform account.',
  "SOURCE_SYSTEM"     STRING NOT NULL COMMENT 'Custodian id or reference feed that produced the row.',
  "SOURCE_FILE"       STRING COMMENT 'Name of the file the row was loaded from; null for rows produced by resolution or reference data.',
  "SOURCE_LINE"       NUMBER(18,0) COMMENT 'Line of the source file the row came from.',
  "CONFIG_VERSION"    STRING COMMENT 'Version of the config whose pipeline produced the row.',
  "LOADED_AT"         TIMESTAMP_NTZ(6) NOT NULL COMMENT 'When the row was written.',
  "UPDATED_AT"        TIMESTAMP_NTZ(6) COMMENT 'When the row last changed; null until it does.'
)
BASE_LOCATION = 'silver/account/'
COMMENT = 'Account: A custodial account: the container in which a custodian holds a client''s securities and cash, identified by the custodian and the account number the custodian assigns. One account belongs to one firm. [custodial CDM 1.0]';

-- Security: key (SECURITY_ID)
CREATE ICEBERG TABLE IF NOT EXISTS {{ DATABASE }}."SILVER"."SECURITY" (
  "SECURITY_ID"    STRING NOT NULL COMMENT 'Security master identifier.',
  "CUSIP"          STRING COMMENT 'CUSIP, nine characters.',
  "ISIN"           STRING COMMENT 'ISIN, twelve characters.',
  "SEDOL"          STRING COMMENT 'SEDOL, seven characters.',
  "TICKER"         STRING COMMENT 'Exchange ticker.',
  "DESCRIPTION"    STRING NOT NULL COMMENT 'Name of the security as the security master states it.',
  "ASSET_CLASS"    STRING NOT NULL COMMENT 'Broad class of the security. Codes: EQUITY = equity; FIXED_INCOME = fixed income; FUND = mutual fund, ETF or other pooled vehicle; OPTION = option; CASH = cash or cash equivalent; OTHER = any other class.',
  "SECURITY_TYPE"  STRING COMMENT 'Security master''s finer type within the asset class.',
  "ISSUER"         STRING COMMENT 'Issuer of the security.',
  "CURRENCY"       STRING NOT NULL COMMENT 'ISO 4217 currency the security trades in.',
  "PRICE_FACTOR"   NUMBER(18,8) COMMENT 'Multiplier from quoted price to value per unit; 1 unless the security quotes per 100 or per contract.',
  "MATURITY_DATE"  DATE COMMENT 'Maturity date of a fixed-income security.',
  "STATUS"         STRING NOT NULL COMMENT 'Whether the security is current in the security master. Codes: ACTIVE = active; INACTIVE = matured, delisted or retired.',
  "SOURCE_SYSTEM"  STRING NOT NULL COMMENT 'Custodian id or reference feed that produced the row.',
  "SOURCE_FILE"    STRING COMMENT 'Name of the file the row was loaded from; null for rows produced by resolution or reference data.',
  "SOURCE_LINE"    NUMBER(18,0) COMMENT 'Line of the source file the row came from.',
  "CONFIG_VERSION" STRING COMMENT 'Version of the config whose pipeline produced the row.',
  "LOADED_AT"      TIMESTAMP_NTZ(6) NOT NULL COMMENT 'When the row was written.',
  "UPDATED_AT"     TIMESTAMP_NTZ(6) COMMENT 'When the row last changed; null until it does.'
)
BASE_LOCATION = 'silver/security/'
COMMENT = 'Security: A financial instrument that can be held or traded: an equity, fund, bond, option or cash equivalent, identified by the security master''s security id and carrying the market identifiers (CUSIP, ISIN, SEDOL, ticker) used to resolve custodian records. [custodial CDM 1.0]';

-- Position: key (CUSTODIAN_ID, ACCOUNT_NUMBER, SECURITY_ID, AS_OF_DATE)
-- references Account through (CUSTODIAN_ID, ACCOUNT_NUMBER)
-- references Security through (SECURITY_ID)
CREATE ICEBERG TABLE IF NOT EXISTS {{ DATABASE }}."SILVER"."POSITION" (
  "CUSTODIAN_ID"          STRING NOT NULL COMMENT 'Custodian that reported the position.',
  "ACCOUNT_NUMBER"        STRING NOT NULL COMMENT 'Account the position is held in. PII: account_number.',
  "SECURITY_ID"           STRING NOT NULL COMMENT 'Security held, resolved through the security master.',
  "AS_OF_DATE"            DATE NOT NULL COMMENT 'Business date the snapshot describes.',
  "CUSTODIAN_SECURITY_ID" STRING COMMENT 'Identifier the custodian sent for the security, before resolution.',
  "POSITION_TYPE"         STRING NOT NULL COMMENT 'Long or short. Codes: LONG = long; SHORT = short.',
  "QUANTITY"              NUMBER(28,8) NOT NULL COMMENT 'Units held; negative for short positions.',
  "PRICE"                 NUMBER(28,10) COMMENT 'Price the custodian valued the position at.',
  "MARKET_VALUE"          NUMBER(28,4) COMMENT 'Market value the custodian reported, in the position currency.',
  "COST_BASIS"            NUMBER(28,4) COMMENT 'Total cost basis the custodian reported.',
  "ACCRUED_INTEREST"      NUMBER(28,4) COMMENT 'Accrued interest included in the valuation, for fixed income.',
  "CURRENCY"              STRING NOT NULL COMMENT 'ISO 4217 currency of the price and values.',
  "SOURCE_SYSTEM"         STRING NOT NULL COMMENT 'Custodian id or reference feed that produced the row.',
  "SOURCE_FILE"           STRING COMMENT 'Name of the file the row was loaded from; null for rows produced by resolution or reference data.',
  "SOURCE_LINE"           NUMBER(18,0) COMMENT 'Line of the source file the row came from.',
  "CONFIG_VERSION"        STRING COMMENT 'Version of the config whose pipeline produced the row.',
  "LOADED_AT"             TIMESTAMP_NTZ(6) NOT NULL COMMENT 'When the row was written.',
  "UPDATED_AT"            TIMESTAMP_NTZ(6) COMMENT 'When the row last changed; null until it does.'
)
BASE_LOCATION = 'silver/position/'
COMMENT = 'Position: The quantity of one security held in one account as of one business date, with the price and market value the custodian reported. A position is a daily snapshot, not a running balance. [custodial CDM 1.0]';

-- Lot: key (CUSTODIAN_ID, ACCOUNT_NUMBER, SECURITY_ID, LOT_ID, AS_OF_DATE)
-- references Position through (CUSTODIAN_ID, ACCOUNT_NUMBER, SECURITY_ID, AS_OF_DATE)
CREATE ICEBERG TABLE IF NOT EXISTS {{ DATABASE }}."SILVER"."LOT" (
  "CUSTODIAN_ID"   STRING NOT NULL COMMENT 'Custodian that reported the lot.',
  "ACCOUNT_NUMBER" STRING NOT NULL COMMENT 'Account the lot is held in. PII: account_number.',
  "SECURITY_ID"    STRING NOT NULL COMMENT 'Security the lot holds.',
  "LOT_ID"         STRING NOT NULL COMMENT 'Custodian''s identifier of the lot; the acquisition date and sequence when the custodian sends none.',
  "AS_OF_DATE"     DATE NOT NULL COMMENT 'Business date the snapshot describes.',
  "ACQUIRED_ON"    DATE NOT NULL COMMENT 'Date the lot was acquired.',
  "QUANTITY"       NUMBER(28,8) NOT NULL COMMENT 'Units remaining in the lot.',
  "UNIT_COST"      NUMBER(28,10) COMMENT 'Cost per unit at acquisition.',
  "COST_BASIS"     NUMBER(28,4) COMMENT 'Total cost basis of the remaining units.',
  "HOLDING_PERIOD" STRING COMMENT 'Short or long term for tax purposes as of the business date. Codes: SHORT = held one year or less; LONG = held more than one year.',
  "CURRENCY"       STRING NOT NULL COMMENT 'ISO 4217 currency of the cost figures.',
  "SOURCE_SYSTEM"  STRING NOT NULL COMMENT 'Custodian id or reference feed that produced the row.',
  "SOURCE_FILE"    STRING COMMENT 'Name of the file the row was loaded from; null for rows produced by resolution or reference data.',
  "SOURCE_LINE"    NUMBER(18,0) COMMENT 'Line of the source file the row came from.',
  "CONFIG_VERSION" STRING COMMENT 'Version of the config whose pipeline produced the row.',
  "LOADED_AT"      TIMESTAMP_NTZ(6) NOT NULL COMMENT 'When the row was written.',
  "UPDATED_AT"     TIMESTAMP_NTZ(6) COMMENT 'When the row last changed; null until it does.'
)
BASE_LOCATION = 'silver/lot/'
COMMENT = 'Lot: A tax lot: the part of a position acquired on one date at one cost, kept separately so gains, losses and holding periods can be computed when it is sold. [custodial CDM 1.0]';

-- Transaction: key (CUSTODIAN_ID, TRANSACTION_ID)
-- references Account through (CUSTODIAN_ID, ACCOUNT_NUMBER)
-- references Security through (SECURITY_ID) when present
CREATE ICEBERG TABLE IF NOT EXISTS {{ DATABASE }}."SILVER"."TRANSACTION" (
  "CUSTODIAN_ID"                 STRING NOT NULL COMMENT 'Custodian that reported the transaction.',
  "TRANSACTION_ID"               STRING NOT NULL COMMENT 'Canonical transaction id; the custodian''s id, suffixed with the part name when a record was split.',
  "SOURCE_TRANSACTION_ID"        STRING NOT NULL COMMENT 'Transaction id as the custodian sent it.',
  "SPLIT_RULE"                   STRING COMMENT 'Name of the split rule that produced this row from the custodian record; null when the record was not split.',
  "SPLIT_PART"                   NUMBER(18,0) COMMENT 'Position of this row among the parts of a split record, from 1.',
  "ACCOUNT_NUMBER"               STRING NOT NULL COMMENT 'Account the transaction belongs to. PII: account_number.',
  "SECURITY_ID"                  STRING COMMENT 'Security involved; null for cash-only transactions.',
  "CUSTODIAN_SECURITY_ID"        STRING COMMENT 'Identifier the custodian sent for the security, before resolution.',
  "TRANSACTION_TYPE"             STRING NOT NULL COMMENT 'Canonical type, resolved from the custodian''s transaction code. Codes: BUY = purchase; SELL = sale; DIVIDEND = cash dividend; INTEREST = interest; CAPITAL_GAIN = capital gain distribution; FEE = fee or expense; DEPOSIT = cash in; WITHDRAWAL = cash out; TRANSFER_IN = securities in; TRANSFER_OUT = securities out; CORPORATE_ACTION = split, merger, spin-off or similar; ADJUSTMENT = custodian adjustment; OTHER = any other type.',
  "CUSTODIAN_TRANSACTION_CODE"   STRING NOT NULL COMMENT 'Transaction code as the custodian sent it.',
  "TRADE_DATE"                   DATE NOT NULL COMMENT 'Date the transaction took place.',
  "SETTLE_DATE"                  DATE COMMENT 'Date the transaction settled or is due to settle.',
  "QUANTITY"                     NUMBER(28,8) COMMENT 'Units transacted; null for cash-only transactions.',
  "PRICE"                        NUMBER(28,10) COMMENT 'Price per unit.',
  "GROSS_AMOUNT"                 NUMBER(28,4) COMMENT 'Amount before fees; positive for cash in, negative for cash out.',
  "FEES"                         NUMBER(28,4) COMMENT 'Commissions and fees charged.',
  "NET_AMOUNT"                   NUMBER(28,4) NOT NULL COMMENT 'Amount that moved cash; positive for cash in, negative for cash out.',
  "CURRENCY"                     STRING NOT NULL COMMENT 'ISO 4217 currency of the amounts.',
  "DESCRIPTION"                  STRING COMMENT 'Narrative the custodian sent.',
  "STATUS"                       STRING NOT NULL COMMENT 'Lifecycle status of the transaction. Codes: ACTIVE = counts towards holdings and cash; CANCELLED = cancelled by a later record; SUPERSEDED = replaced by a correction; CANCEL = the record that cancels another; never counts.',
  "CANCELS_TRANSACTION_ID"       STRING COMMENT 'For a cancel record, the transaction it cancels.',
  "CORRECTS_TRANSACTION_ID"      STRING COMMENT 'For a correction, the transaction it replaces.',
  "CANCELLED_BY_TRANSACTION_ID"  STRING COMMENT 'For a cancelled transaction, the cancel record.',
  "SUPERSEDED_BY_TRANSACTION_ID" STRING COMMENT 'For a superseded transaction, the correction that replaced it.',
  "SOURCE_SYSTEM"                STRING NOT NULL COMMENT 'Custodian id or reference feed that produced the row.',
  "SOURCE_FILE"                  STRING COMMENT 'Name of the file the row was loaded from; null for rows produced by resolution or reference data.',
  "SOURCE_LINE"                  NUMBER(18,0) COMMENT 'Line of the source file the row came from.',
  "CONFIG_VERSION"               STRING COMMENT 'Version of the config whose pipeline produced the row.',
  "LOADED_AT"                    TIMESTAMP_NTZ(6) NOT NULL COMMENT 'When the row was written.',
  "UPDATED_AT"                   TIMESTAMP_NTZ(6) COMMENT 'When the row last changed; null until it does.'
)
BASE_LOCATION = 'silver/transaction/'
COMMENT = 'Transaction: One event that changes an account''s holdings or cash: a buy, sell, dividend, fee, transfer or adjustment. A custodian record that represents several events, such as a dividend reinvestment, becomes several transactions, each traceable to the record. A cancel or correction links to the transaction it acts on. [custodial CDM 1.0]';

-- Price: key (SECURITY_ID, PRICE_DATE, PRICE_SOURCE)
-- references Security through (SECURITY_ID)
CREATE ICEBERG TABLE IF NOT EXISTS {{ DATABASE }}."SILVER"."PRICE" (
  "SECURITY_ID"    STRING NOT NULL COMMENT 'Security priced.',
  "PRICE_DATE"     DATE NOT NULL COMMENT 'Date the price is for.',
  "PRICE_SOURCE"   STRING NOT NULL COMMENT 'Custodian id or pricing vendor that supplied the price.',
  "PRICE_TYPE"     STRING NOT NULL COMMENT 'Which price this is. Codes: CLOSE = closing price; BID = bid; ASK = ask; EVALUATED = evaluated price for an instrument that did not trade; NAV = net asset value of a fund.',
  "PRICE"          NUMBER(28,10) NOT NULL COMMENT 'Price per unit, before the security''s price factor.',
  "CURRENCY"       STRING NOT NULL COMMENT 'ISO 4217 currency of the price.',
  "SOURCE_SYSTEM"  STRING NOT NULL COMMENT 'Custodian id or reference feed that produced the row.',
  "SOURCE_FILE"    STRING COMMENT 'Name of the file the row was loaded from; null for rows produced by resolution or reference data.',
  "SOURCE_LINE"    NUMBER(18,0) COMMENT 'Line of the source file the row came from.',
  "CONFIG_VERSION" STRING COMMENT 'Version of the config whose pipeline produced the row.',
  "LOADED_AT"      TIMESTAMP_NTZ(6) NOT NULL COMMENT 'When the row was written.',
  "UPDATED_AT"     TIMESTAMP_NTZ(6) COMMENT 'When the row last changed; null until it does.'
)
BASE_LOCATION = 'silver/price/'
COMMENT = 'Price: The value of one security on one date from one source, in the security''s currency, used to value positions and to check the market values custodians report. [custodial CDM 1.0]';

-- Cash Balance: key (CUSTODIAN_ID, ACCOUNT_NUMBER, CURRENCY, BALANCE_TYPE, AS_OF_DATE)
-- references Account through (CUSTODIAN_ID, ACCOUNT_NUMBER)
CREATE ICEBERG TABLE IF NOT EXISTS {{ DATABASE }}."SILVER"."CASH_BALANCE" (
  "CUSTODIAN_ID"   STRING NOT NULL COMMENT 'Custodian that reported the balance.',
  "ACCOUNT_NUMBER" STRING NOT NULL COMMENT 'Account the cash is held in. PII: account_number.',
  "CURRENCY"       STRING NOT NULL COMMENT 'ISO 4217 currency of the balance.',
  "BALANCE_TYPE"   STRING NOT NULL COMMENT 'Which balance this is. Codes: SETTLED = settled cash; TRADE_DATE = cash including unsettled trades; MONEY_MARKET = cash swept to a money market fund; MARGIN = margin balance.',
  "AS_OF_DATE"     DATE NOT NULL COMMENT 'Business date the balance describes.',
  "AMOUNT"         NUMBER(28,4) NOT NULL COMMENT 'Balance; negative when the account owes cash.',
  "SOURCE_SYSTEM"  STRING NOT NULL COMMENT 'Custodian id or reference feed that produced the row.',
  "SOURCE_FILE"    STRING COMMENT 'Name of the file the row was loaded from; null for rows produced by resolution or reference data.',
  "SOURCE_LINE"    NUMBER(18,0) COMMENT 'Line of the source file the row came from.',
  "CONFIG_VERSION" STRING COMMENT 'Version of the config whose pipeline produced the row.',
  "LOADED_AT"      TIMESTAMP_NTZ(6) NOT NULL COMMENT 'When the row was written.',
  "UPDATED_AT"     TIMESTAMP_NTZ(6) COMMENT 'When the row last changed; null until it does.'
)
BASE_LOCATION = 'silver/cash_balance/'
COMMENT = 'Cash Balance: The cash held in one account in one currency as of one business date, by balance type: settled, trade date, money market or margin. [custodial CDM 1.0]';

-- Exception: key (EXCEPTION_ID)
CREATE ICEBERG TABLE IF NOT EXISTS {{ DATABASE }}."SILVER"."EXCEPTION" (
  "EXCEPTION_ID"   STRING NOT NULL COMMENT 'Platform identifier of the exception.',
  "REJECTION_CODE" STRING NOT NULL COMMENT 'Code from the rejection taxonomy (rejections.yaml) that classifies the exception. Lookup: CONTROL.REJECTION_CODES.CODE.',
  "LEVEL"          STRING NOT NULL COMMENT 'How far the rejection reaches. Codes: FILE = the whole file was rejected; RECORD = one record or pair was rejected; FIELD = one value was rejected; the record may still have loaded.',
  "ENTITY"         STRING COMMENT 'Canonical entity the rejected record was meant for; null for file-level exceptions.',
  "CUSTODIAN_ID"   STRING COMMENT 'Custodian whose data raised the exception.',
  "FIELD_NAME"     STRING COMMENT 'Source field at fault, for field-level exceptions.',
  "RAW_VALUE"      STRING COMMENT 'Value as received, for field-level exceptions. PII: raw_record.',
  "MESSAGE"        STRING NOT NULL COMMENT 'What was wrong, as the rule that rejected it states it.',
  "RECORD_KEY"     STRING COMMENT 'Reconciliation identity of the rejected record, as text, when it could be read.',
  "RAISED_AT"      TIMESTAMP_NTZ(6) NOT NULL COMMENT 'When the exception was raised.',
  "STATUS"         STRING NOT NULL COMMENT 'Where the exception is in its resolution. Codes: NEW = written by a pipeline stage, waiting for triage; RESOLVED = resolved by a person; AUTO_RESOLVED = resolved by a whitelisted rule, with audit; DISMISSED = closed without a change.',
  "RESOLUTION"     STRING COMMENT 'What was done to resolve it.',
  "RESOLVED_BY"    STRING COMMENT 'Person or rule that resolved it.',
  "RESOLVED_AT"    TIMESTAMP_NTZ(6) COMMENT 'When it was resolved.',
  "SOURCE_SYSTEM"  STRING NOT NULL COMMENT 'Custodian id or reference feed that produced the row.',
  "SOURCE_FILE"    STRING COMMENT 'Name of the file the row was loaded from; null for rows produced by resolution or reference data.',
  "SOURCE_LINE"    NUMBER(18,0) COMMENT 'Line of the source file the row came from.',
  "CONFIG_VERSION" STRING COMMENT 'Version of the config whose pipeline produced the row.',
  "LOADED_AT"      TIMESTAMP_NTZ(6) NOT NULL COMMENT 'When the row was written.',
  "UPDATED_AT"     TIMESTAMP_NTZ(6) COMMENT 'When the row last changed; null until it does.'
)
BASE_LOCATION = 'silver/exception/'
COMMENT = 'Exception: A record the factory could not accept or resolve, with the rejection code that classifies it, where it came from and its resolution status. Every rejected file, record, pair or value is an exception. [custodial CDM 1.0]';
