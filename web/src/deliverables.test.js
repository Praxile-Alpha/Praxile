import test from "node:test";
import assert from "node:assert/strict";
import { parseContract } from "./deliverables.js";

const base = { schema_version: "praxile.deliverable_contract.v3", source: "user_request",
  outputs: [], state_equals: {}, unchanged_files: { "memo.docx": "a".repeat(64) } };

test("V3 supports preservation-only tasks", () => {
  assert.deepEqual(parseContract(JSON.stringify(base)), base);
});
test("V3 rejects malformed preservation input", () => {
  for (const unchanged_files of [null, [], {}, { "memo.docx": true }, { "memo.docx": "A".repeat(64) }]) {
    assert.throws(() => parseContract(JSON.stringify({ ...base, unchanged_files })), /V3 requires/);
  }
});
test("existing V1 and V2 remain supported", () => {
  for (const version of ["v1", "v2"]) {
    const contract = { schema_version: `praxile.deliverable_contract.${version}`, source: "user_request",
      outputs: [{ path: "report.json", media_type: "application/json" }] };
    if (version === "v2") contract.state_equals = { "browser.session_active": true };
    assert.deepEqual(parseContract(JSON.stringify(contract)), contract);
  }
});
