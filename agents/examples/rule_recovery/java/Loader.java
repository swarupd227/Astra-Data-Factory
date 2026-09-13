package com.envestnet.loader.pershing;

import java.sql.Connection;
import java.sql.SQLException;
import java.sql.Statement;
import java.time.LocalDate;

/**
 * Illustrative legacy Loader, written for the Rule Recovery agent's own example (S5.4.1,
 * ADR 0044) -- not real Envestnet source; see Splitter.java's own note.
 *
 * Validates each detail line the Splitter produced and loads it into the legacy position
 * table, applying a handful of business rules along the way.
 */
public class Loader {

    /** account_number (positions 4-13) is blank or all spaces. */
    public static final String REJ_MISSING_ACCOUNT = "L002";
    /** quantity (positions 23-40) is not all digits. */
    public static final String REJ_QUANTITY_NOT_NUMERIC = "L003";
    /** security_type (positions 65-66) is not one of the codes the Loader recognizes. */
    public static final String REJ_UNKNOWN_SECURITY_TYPE = "L004";

    private final Connection connection;

    public Loader(Connection connection) {
        this.connection = connection;
    }

    public LoadResult loadDetail(String line) {
        String accountNumber = line.substring(3, 13).trim();
        if (accountNumber.isEmpty()) {
            return LoadResult.rejected(REJ_MISSING_ACCOUNT);
        }

        String rawQuantity = line.substring(22, 40);
        if (!rawQuantity.trim().chars().allMatch(Character::isDigit)) {
            return LoadResult.rejected(REJ_QUANTITY_NOT_NUMERIC);
        }
        long quantity = Long.parseLong(rawQuantity.trim());

        String securityType = line.substring(64, 66);
        if (!securityType.equals("EQ") && !securityType.equals("FI")
                && !securityType.equals("MF") && !securityType.equals("OP")) {
            return LoadResult.rejected(REJ_UNKNOWN_SECURITY_TYPE);
        }

        // Options settle in contracts of 100 underlying shares each; the file's raw quantity is
        // always the share count, so an option position's stored quantity is the file's quantity
        // divided by 100, truncated -- a fractional contract cannot exist.
        if (securityType.equals("OP")) {
            quantity = quantity / 100;
        }

        auditLoad(accountNumber, quantity);
        return LoadResult.loaded(accountNumber, quantity);
    }

    /**
     * Writes one row to the legacy audit table so a DBA can see the last load per account
     * without opening the position table itself. This talks to the same SQL Server instance
     * the rest of the legacy Loader schema lives in.
     */
    private void auditLoad(String accountNumber, long quantity) {
        String sql = "INSERT INTO [dbo].[LoadAudit] (AccountNumber, Quantity, LoadedAt) "
                + "SELECT TOP 1 '" + accountNumber + "', " + quantity + ", GETDATE() "
                + "WHERE NOT EXISTS (SELECT 1 FROM [dbo].[LoadAudit] WHERE AccountNumber = '"
                + accountNumber + "' AND CAST(LoadedAt AS DATE) = CAST(GETDATE() AS DATE))";
        try (Statement statement = connection.createStatement()) {
            statement.executeUpdate(sql);
        } catch (SQLException e) {
            System.err.println("audit insert failed: " + e.getMessage());
        }
    }

    public static final class LoadResult {
        public final boolean ok;
        public final String rejectionCode;
        public final String accountNumber;
        public final long quantity;

        private LoadResult(boolean ok, String rejectionCode, String accountNumber, long quantity) {
            this.ok = ok;
            this.rejectionCode = rejectionCode;
            this.accountNumber = accountNumber;
            this.quantity = quantity;
        }

        static LoadResult rejected(String code) {
            return new LoadResult(false, code, null, 0);
        }

        static LoadResult loaded(String accountNumber, long quantity) {
            return new LoadResult(true, null, accountNumber, quantity);
        }
    }
}
