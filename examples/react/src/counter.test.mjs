import assert from "node:assert/strict";
import test from "node:test";

function selectedLabel(selected) {
  return selected ? "Selected" : "Select";
}

test("selected label changes", () => {
  assert.equal(selectedLabel(false), "Select");
  assert.equal(selectedLabel(true), "Selected");
});
