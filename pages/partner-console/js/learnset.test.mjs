import assert from "node:assert/strict";

globalThis.window = {};
const { normalizeLearnset, filterLearnset } = await import("./learnset.js");
const moves = normalizeLearnset({
  level_up: [["电击", 1], ["十万伏特", 42], ["旧招式", null], ["十万伏特", 42]],
  machine: ["十万伏特", ["守住", null], null],
  egg: [["伏特攻击", null]],
}, {
  十万伏特: { type: "电", name_en: "Thunderbolt", category: "特殊", power: "90" },
  守住: { type: "一般", category: "变化" },
});
assert.equal(moves.length, 5);
assert.equal(moves.find((move) => move.name === "十万伏特").sources.length, 2);
assert.equal(moves.find((move) => move.name === "旧招式").sources[0].level, null);
assert.equal(filterLearnset(moves, "machine").length, 2);
assert.equal(filterLearnset(moves, "all", "  THUNDERBOLT ")[0].name, "十万伏特");
assert.equal(filterLearnset(moves, "all", "电").length, 2);
assert.equal(filterLearnset(moves, "egg", "特殊").length, 0);
assert.deepEqual(normalizeLearnset(), []);
console.log("8 learnset normalization/filter checks passed");
