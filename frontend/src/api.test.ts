import { afterEach, describe, expect, it, vi } from "vitest";
import { caseReportUrl, listTransactions, overrideReview } from "./api";

function mockFetch(response: Partial<Response> & { json?: () => Promise<unknown> }) {
  const fetchMock = vi.fn().mockResolvedValue({
    ok: true,
    status: 200,
    statusText: "OK",
    json: async () => ({}),
    ...response,
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

afterEach(() => vi.unstubAllGlobals());

describe("api client", () => {
  it("omits the verdict filter when none is selected", async () => {
    const fetchMock = mockFetch({ json: async () => [] });
    await listTransactions("", 25, 50);

    const url = fetchMock.mock.calls[0][0] as string;
    expect(url).toContain("limit=25");
    expect(url).toContain("offset=50");
    expect(url).not.toContain("verdict=");
  });

  it("passes the verdict filter through when one is selected", async () => {
    const fetchMock = mockFetch({ json: async () => [] });
    await listTransactions("escalate");

    expect(fetchMock.mock.calls[0][0]).toContain("verdict=escalate");
  });

  it("surfaces FastAPI's detail message rather than a bare status code", async () => {
    mockFetch({
      ok: false,
      status: 400,
      statusText: "Bad Request",
      json: async () => ({ detail: "Only escalated cases can be overridden" }),
    });

    await expect(overrideReview("rr-1", "approve", "n", "tok")).rejects.toThrow(
      "Only escalated cases can be overridden"
    );
  });

  it("falls back to statusText when the error body is not JSON", async () => {
    mockFetch({
      ok: false,
      status: 502,
      statusText: "Bad Gateway",
      json: async () => {
        throw new Error("not json");
      },
    });

    await expect(listTransactions()).rejects.toThrow("Bad Gateway");
  });

  it("sends the reviewer's bearer token on an override", async () => {
    const fetchMock = mockFetch({ json: async () => ({}) });
    await overrideReview("rr-1", "reject", "confirmed fraud", "token-abc");

    const init = fetchMock.mock.calls[0][1] as RequestInit;
    expect(init.method).toBe("POST");
    expect((init.headers as Record<string, string>).Authorization).toBe("Bearer token-abc");
    expect(JSON.parse(init.body as string)).toEqual({ decision: "reject", note: "confirmed fraud" });
  });

  it("url-encodes the case id in the report link", () => {
    expect(caseReportUrl("a b/c")).toContain("/transactions/a%20b%2Fc/report.pdf");
  });
});
