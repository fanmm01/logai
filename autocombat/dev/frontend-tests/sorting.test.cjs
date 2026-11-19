const { test } = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const { loadTs } = require('./load-ts.cjs');
const { compareSerials, sortBySerial } = loadTs(path.resolve(__dirname, '../../frontend/src/types.ts'));

test('character serials sort by natural identifier order without mutating the source array', () => {
  const input = ['Y10', 'Y2', 'X1', 'Y60', 'Y1', 'X2'].map(serial => ({ serial }));
  const sorted = sortBySerial(input);
  assert.deepEqual(sorted.map(item => item.serial), ['X1', 'X2', 'Y1', 'Y2', 'Y10', 'Y60']);
  assert.deepEqual(input.map(item => item.serial), ['Y10', 'Y2', 'X1', 'Y60', 'Y1', 'X2']);
  assert.ok(compareSerials('Y2', 'Y10') < 0);
});
