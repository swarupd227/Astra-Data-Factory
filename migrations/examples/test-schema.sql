-- A small SQL Server schema in the shape of the Loader's tables, for the end-to-end run of
-- `astra-data migrate` before the real database is pointed at (docs/runbooks/historical-migration.md).
-- Apply it to an empty test database, then run the migration file with its connection string.
CREATE SCHEMA dbo;
GO

CREATE TABLE dbo.LoaderAccount (
    AccountId        INT            NOT NULL PRIMARY KEY,
    CustodianCode    VARCHAR(10)    NOT NULL,
    AccountNumber    VARCHAR(20)    NOT NULL,
    AccountName      NVARCHAR(120)  NULL,
    OpenedOn         DATE           NULL,
    ClosedOn         DATE           NULL,
    LoadedAt         DATETIME2(3)   NOT NULL DEFAULT SYSUTCDATETIME()
);
GO

CREATE TABLE dbo.LoaderPosition (
    PositionId       BIGINT         NOT NULL PRIMARY KEY,
    AccountId        INT            NOT NULL REFERENCES dbo.LoaderAccount (AccountId),
    Cusip            CHAR(9)        NULL,
    AsOfDate         DATE           NOT NULL,
    Quantity         DECIMAL(18, 5) NOT NULL,
    Price            DECIMAL(15, 6) NULL,
    MarketValue      MONEY          NULL,
    SourceFile       VARCHAR(200)   NOT NULL,
    SourceLine       INT            NOT NULL
);
GO

CREATE TABLE dbo.LoaderRejection (
    RejectionId      BIGINT         NOT NULL PRIMARY KEY,
    RejectCode       VARCHAR(30)    NOT NULL,
    SourceFile       VARCHAR(200)   NOT NULL,
    SourceLine       INT            NOT NULL,
    RawRecord        VARCHAR(MAX)   NOT NULL,
    RaisedAt         DATETIME2(3)   NOT NULL
);
GO

INSERT INTO dbo.LoaderAccount (AccountId, CustodianCode, AccountNumber, AccountName, OpenedOn)
VALUES (1, 'PERSHING', '12345678', N'Test account one', '2019-03-04'),
       (2, 'PERSHING', '87654321', N'Test account two', '2020-11-30');
GO

INSERT INTO dbo.LoaderPosition (PositionId, AccountId, Cusip, AsOfDate, Quantity, Price, MarketValue, SourceFile, SourceLine)
VALUES (1, 1, '037833100', '2026-08-29', 100.00000, 227.120000, 22712.00, 'GCUS_20260829_POS_001.dat', 2),
       (2, 2, '594918104', '2026-08-29', 25.00000, 410.500000, 10262.50, 'GCUS_20260829_POS_001.dat', 3);
GO

INSERT INTO dbo.LoaderRejection (RejectionId, RejectCode, SourceFile, SourceLine, RawRecord, RaisedAt)
VALUES (1, 'ACCOUNT_NOT_FOUND', 'GCUS_20260829_POS_001.dat', 4, 'DTL99999999...', '2026-08-29T06:12:00');
GO
