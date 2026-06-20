import { z } from "zod";

export const TickerReportSchema = z.object({
  symbol: z.string().describe("The ticker symbol, e.g. AAPL."),
  companyName: z.string().describe("The full company name."),
  summary: z
    .string()
    .describe("A neutral 2-4 sentence overview of the company and its current situation."),
  thesis: z
    .object({
      bull: z
        .array(z.string())
        .describe("Concise arguments supporting an investment (the bull case)."),
      bear: z
        .array(z.string())
        .describe("Concise arguments against an investment (the bear case)."),
    })
    .describe("Opposing investment cases derived only from the provided data."),
  keyMetrics: z
    .array(
      z.object({
        label: z.string().describe("Name of the metric, e.g. 'P/E ratio'."),
        value: z
          .string()
          .describe(
            "The figure RESTATED verbatim from the provided data, as a string. Never invent or estimate a number.",
          ),
        interpretation: z
          .string()
          .describe("Why this metric matters and how to read it in context."),
      }),
    )
    .describe("Metrics restated from the provided data with plain-language interpretation."),
  valuationContext: z
    .string()
    .describe("How the stock appears valued given the provided figures, without inventing numbers."),
  risks: z.array(z.string()).describe("Key risks to the investment thesis."),
  thingsToInvestigate: z
    .array(z.string())
    .describe("Open questions or data points a reader should research further."),
  confidence: z
    .enum(["low", "medium", "high"])
    .describe("Confidence in this report given the completeness of the provided data."),
});

export type TickerReport = z.infer<typeof TickerReportSchema>;
