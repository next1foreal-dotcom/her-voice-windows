import test from 'node:test';
import assert from 'node:assert/strict';
import '../web/ui.js';

function renderer(hasMaxBlend = true) {
  const bound = new Map();
  const uploaded = new Map();
  const frame = { bytes: 0, draws: [] };
  const gl = {
    ARRAY_BUFFER: 34962, ELEMENT_ARRAY_BUFFER: 34963, FLOAT: 5126,
    UNSIGNED_SHORT: 5123, TRIANGLES: 4, DYNAMIC_DRAW: 35048,
    COLOR_BUFFER_BIT: 16384, VERTEX_SHADER: 35633, FRAGMENT_SHADER: 35632,
    COMPILE_STATUS: 35713, LINK_STATUS: 35714, BLEND: 3042,
    ONE: 1, ONE_MINUS_SRC_ALPHA: 771, FUNC_ADD: 32774,
    getExtension: () => hasMaxBlend ? { MAX_EXT: 32776 } : null,
    createShader: () => ({}), createProgram: () => ({}), createBuffer: () => ({}),
    getShaderParameter: () => true, getProgramParameter: () => true,
    getAttribLocation: () => 0, getUniformLocation: (_, name) => name,
    isContextLost: () => false,
    bindBuffer(target, buffer) { bound.set(target, buffer); },
    bufferData(target, data) {
      frame.bytes += data.byteLength;
      uploaded.set(bound.get(target), data.slice());
    },
    drawArrays(_, first, count) {
      const vertices = uploaded.get(bound.get(gl.ARRAY_BUFFER));
      frame.draws.push(vertices.slice(first * 7, (first + count) * 7));
    },
    drawElements(_, count, type, offset) {
      assert.equal(type, gl.UNSIGNED_SHORT);
      const vertices = uploaded.get(bound.get(gl.ARRAY_BUFFER));
      const indices = uploaded.get(bound.get(gl.ELEMENT_ARRAY_BUFFER));
      const expanded = new Float32Array(count * 7);
      for (let i = 0; i < count; i++) {
        const at = indices[offset / 2 + i] * 7;
        expanded.set(vertices.subarray(at, at + 7), i * 7);
      }
      frame.draws.push(expanded);
    },
  };
  for (const name of ['shaderSource', 'compileShader', 'attachShader', 'linkProgram',
    'viewport', 'clearColor', 'clear', 'useProgram', 'enableVertexAttribArray',
    'vertexAttribPointer', 'uniform2f', 'uniform3f', 'uniform1f', 'enable',
    'blendFunc', 'blendEquation', 'deleteBuffer', 'deleteProgram']) gl[name] = () => {};
  const canvas = {
    width: 1440, height: 900,
    getBoundingClientRect: () => ({ width: 1440, height: 900 }),
    getContext: type => type === 'webgl' ? gl : null,
    addEventListener() {},
  };
  return { ribbon: new globalThis.OS1Ribbon(canvas), frame };
}

test('full ribbon animation stays below 300 KB of GPU upload per frame', () => {
  const { ribbon, frame } = renderer();
  for (const state of globalThis.OS1Ribbon.STATES) {
    frame.bytes = 0;
    frame.draws = [];
    ribbon.simulate(1, [[0, state]], 60);
    assert.ok(frame.bytes <= 300_000, `${state}: uploaded ${frame.bytes} bytes`);
    assert.equal(frame.draws.length, 2);
    assert.equal(frame.draws[0].length, 640 * 8 * 6 * 7);
    assert.deepEqual(frame.draws[0], frame.draws[1], 'both passes retain the full same ordered surface');
  }
  ribbon.destroy();
});

test('WebGL without minmax still draws the entire ribbon', () => {
  const { ribbon, frame } = renderer(false);
  frame.draws = [];
  ribbon.simulate(1, [[0, 'interrupted']], 60);
  assert.equal(frame.draws.length, 1);
  assert.equal(frame.draws[0].length, 640 * 8 * 6 * 7);
  assert.ok(frame.draws[0].every(Number.isFinite));
  ribbon.destroy();
});