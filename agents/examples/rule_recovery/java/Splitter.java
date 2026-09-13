package com.envestnet.loader.pershing;

import java.io.BufferedReader;
import java.util.ArrayList;
import java.util.List;

/**
 * Illustrative legacy Splitter, written for the Rule Recovery agent's own example (S5.4.1,
 * ADR 0044) -- not real Envestnet source. The real Splitter/Loader has not been characterized
 * in this repository yet (docs/backlog-v0.2.md's own Risks section says as much); this stands
 * in for it until real source is available, the same way the illustrative specs stand in for
 * a real custodian layout.
 *
 * Reads a raw Pershing GCUS file and splits it into header, detail and trailer lines before the
 * Loader validates and stores them.
 */
public class Splitter {

    /** No line of the file started with a record type this Splitter recognizes. */
    public static final String REJ_UNKNOWN_RECORD_TYPE = "L001";

    private final List<String> headers = new ArrayList<>();
    private final List<String> details = new ArrayList<>();
    private final List<String> trailers = new ArrayList<>();

    public void split(BufferedReader in) throws java.io.IOException {
        String line;
        while ((line = in.readLine()) != null) {
            if (line.isEmpty()) {
                continue;
            }
            String recordType = line.substring(0, 3);
            switch (recordType) {
                case "HDR":
                    headers.add(line);
                    break;
                case "DTL":
                    details.add(line);
                    break;
                case "TRL":
                    trailers.add(line);
                    break;
                default:
                    // A line whose first three characters match none of HDR/DTL/TRL cannot be
                    // routed to any downstream handler; the Loader never sees it.
                    reject(line, REJ_UNKNOWN_RECORD_TYPE);
            }
        }
    }

    private void reject(String line, String code) {
        System.err.println("rejected [" + code + "]: " + line);
    }

    public List<String> getDetails() {
        return details;
    }
}
