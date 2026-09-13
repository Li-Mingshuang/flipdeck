// 用桩（stub）在 Node 里真跑一遍 viewer 的 JS：能抓出"打开就黑屏"这类加载/运行期异常，
// 并统计 WebGL 绘制调用次数（>=4 说明真的有东西被画出来，不是黑屏）。
// 用法： node verify_runtime.js
"use strict";
const fs = require("fs"), path = require("path"), vm = require("vm");
const ROOT = __dirname;
const html = fs.readFileSync(path.join(ROOT, "flipdeck_viewer.html"), "utf8");
const m = html.match(/<script>([\s\S]*?)<\/script>/);
if (!m) { console.error("找不到 <script>"); process.exit(1); }
const js = m[1];

// ---------- 假 WebGL ----------
let drawCalls = 0, uniformCalls = 0, errors = [];
function glStub() {
  const gl = {
    drawingBufferWidth: 1600, drawingBufferHeight: 900,
    canvas: null,
    createShader: () => ({}), createProgram: () => ({}),
    shaderSource: () => {}, compileShader: () => {}, attachShader: () => {},
    linkProgram: () => {}, useProgram: () => {},
    getShaderParameter: () => true, getProgramParameter: () => true,
    getShaderInfoLog: () => "", getProgramInfoLog: () => "",
    getAttribLocation: () => 0, getUniformLocation: () => ({}),
    uniformMatrix4fv: () => { uniformCalls++; }, uniform3fv: () => {}, uniform1f: () => {}, uniform1i: () => {},
    createBuffer: () => ({}), bindBuffer: () => {}, bufferData: () => {},
    vertexAttribPointer: () => {}, enableVertexAttribArray: () => {},
    drawArrays: () => { drawCalls++; },
    createTexture: () => ({}), bindTexture: () => {}, texImage2D: () => {}, texParameteri: () => {},
    activeTexture: () => {}, enable: () => {}, disable: () => {}, clear: () => {},
    clearColor: () => {}, viewport: () => {}, depthFunc: () => {}, polygonOffset: () => {},
    enableVertexAttribArray_: () => {},
  };
  return gl;
}
function ctx2dStub() {
  const grad = { addColorStop: () => {} };
  return new Proxy({}, {
    get(t, k) {
      if (k === "createLinearGradient" || k === "createRadialGradient") return () => grad;
      if (k === "measureText") return () => ({ width: 10 });
      if (k === "getImageData") return () => ({ data: new Uint8ClampedArray(4) });
      if (k === "canvas") return { width: 1200, height: 520 };
      return () => {};
    },
    set() { return true; },
  });
}
function elementStub(tag) {
  const el = {
    tagName: (tag || "div").toUpperCase(), style: {}, dataset: {}, children: [],
    textContent: "", innerHTML: "", value: "0", files: null,
    handlers: {},
    classList: { add: () => {}, remove: () => {}, toggle: () => {} },
    addEventListener: function (t, fn) { (el.handlers[t] = el.handlers[t] || []).push(fn); },
    removeEventListener: () => {},
    appendChild: () => {}, getContext: (t) => (t === "2d" ? ctx2dStub() : glStub()),
    width: 1200, height: 520, clientWidth: 1600, clientHeight: 900,
    getBoundingClientRect: () => ({ left: 0, top: 0, width: 1600, height: 900 }),
    dispatchEvent: () => {}, querySelectorAll: () => [],
  };
  return el;
}
function fire(el, type, extra) {
  if (!el) return;
  const ev = Object.assign({ target: el, preventDefault: () => {} }, extra || {});
  (el.handlers[type] || []).forEach((fn) => fn.call(el, ev));   // 浏览器里 this 指向元素
}

// ---------- 最小 STL（1 个三角形）与响应 ----------
function tinySTL() {
  const buf = Buffer.alloc(84 + 50);
  buf.writeUInt32LE(1, 80);
  let o = 84;
  const coords = [0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 1, 0];
  for (const v of coords) { buf.writeFloatLE(v, o); o += 4; }
  return buf.buffer.slice(buf.byteOffset, buf.byteOffset + buf.byteLength);
}
const paramsJson = JSON.parse(fs.readFileSync(path.join(ROOT, "params.json"), "utf8"));
const boardJson = { updated: "test", games: { run: [], tetris: [], break: [], snake: [], flappy: [] } };
let fetched = [];

function makeSandbox() {
  const canvas = elementStub("canvas");
  const els = {};
  const document_ = {
    getElementById: (id) => (els[id] = els[id] || elementStub(id === "gl" ? "canvas" : "div")),
    querySelectorAll: () => [],
    createElement: (t) => elementStub(t),
    addEventListener: () => {},
    body: elementStub("body"),
  };
  let rafCount = 0, rafCb = null;
  const sandbox = {
    console: console, document: document_, window: null,
    localStorage: { store: {}, getItem(k) { return this.store[k] || null; }, setItem(k, v) { this.store[k] = v; }, removeItem(k) { delete this.store[k]; } },
    performance: { now: () => Date.now() },
    requestAnimationFrame: (cb) => { rafCb = cb; return ++rafCount; },
    alert: () => {}, fetch: null, Image: function () { this.onload = null; },
    URL: { createObjectURL: () => "blob:x", revokeObjectURL: () => {} },
    Math: Math, JSON: JSON, Object: Object, Array: Array, String: String, Number: Number,
    Float32Array: Float32Array, Uint8ClampedArray: Uint8ClampedArray, parseInt: parseInt,
    isNaN: isNaN, Date: Date, Proxy: Proxy, Set: Set, Promise: Promise,
    Event: function (t) { this.type = t; },
  };
  sandbox.window = sandbox;
  sandbox.globalThis = sandbox;
  sandbox.self = sandbox;
  sandbox.addEventListener = () => {};
  sandbox.removeEventListener = () => {};
  sandbox.dispatchEvent = () => {};
  sandbox.devicePixelRatio = 1;
  sandbox.innerWidth = 1600;
  sandbox.innerHeight = 900;
  sandbox.fetch = function (url) {
    fetched.push(String(url));
    const u = String(url);
    let body;
    if (u.endsWith("params.json")) body = { ok: true, status: 200, json: () => Promise.resolve(paramsJson) };
    else if (u.endsWith("leaderboard.json")) body = { ok: true, status: 200, json: () => Promise.resolve(boardJson) };
    else body = { ok: true, status: 200, arrayBuffer: () => Promise.resolve(tinySTL()) };
    return Promise.resolve(body);
  };
  sandbox.__rafStep = function (n) {
    for (let i = 0; i < (n || 1); i++) { if (rafCb) { const cb = rafCb; rafCb = null; cb(Date.now() + i * 16); } }
  };
  sandbox.__els = els;
  sandbox.window.open = () => {};
  sandbox.__stats = () => ({ draws: drawCalls, uniforms: uniformCalls, fetched: fetched.slice() });
  return sandbox;
}

const sandbox = makeSandbox();
const ctx = vm.createContext(sandbox);
vm.runInContext('(function(){ "use strict";\n' + js + '\n})()', ctx, { filename: "viewer.js" });

// 等异步加载（fetch → parseSTL → fitCamera）跑完，再逐项跑一遍各条路径
setTimeout(function () {
  const fail = [];
  function trial(label, fn, minDraws) {
    const before = sandbox.__stats().draws;
    try {
      fn();
      const d = sandbox.__stats().draws - before;
      if (d < (minDraws === undefined ? 1 : minDraws)) { fail.push(`${label}: 只画了 ${d} 次`); console.log(`FAIL ${label}  绘制 ${d} 次`); }
      else console.log(`OK   ${label.padEnd(22)} 绘制 ${d} 次`);
    } catch (e) {
      fail.push(label + ": " + (e && e.message));
      console.log(`FAIL ${label}  异常：${e && e.message}`);
    }
  }
  const els = sandbox.__els;
  trial("初始帧 ×6", () => sandbox.__rafStep(6), 12);
  ["run", "tetris", "break", "snake", "flappy"].forEach(function (g) {
    trial("游戏 " + g, function () {
      els["game"].value = g; fire(els["game"], "change");
      sandbox.__rafStep(5);
    }, 5);
  });
  trial("屏幕=排行榜", function () {
    els["screen"].value = "board"; fire(els["screen"], "change");
    sandbox.__rafStep(3);
  }, 3);
  trial("屏幕=灭屏/主屏/自定义", function () {
    ["off", "home", "user", "game"].forEach(function (v) {
      els["screen"].value = v; fire(els["screen"], "change"); sandbox.__rafStep(2);
    });
  }, 6);
  trial("三个姿态滑块", function () {
    [["110", "0"], ["0", "0"], ["0", "180"], ["110", "90"]].forEach(function (p) {
      els["theta"].value = p[0]; fire(els["theta"], "input");
      els["phi"].value = p[1]; fire(els["phi"], "input");
      sandbox.__rafStep(2);
    });
  }, 8);
  trial("提交成绩按钮", function () { fire(els["submit"], "click"); }, 0);
  trial("Top10 面板", function () { fire(els["boardToggle"], "click"); }, 0);
  trial("滤镜/屏幕旋转", function () {
    els["crt"].value = "off"; fire(els["crt"], "change");
    els["rot"].value = "90"; fire(els["rot"], "change"); sandbox.__rafStep(2);
    els["rot"].value = "270"; fire(els["rot"], "change"); sandbox.__rafStep(2);
  }, 4);

  const st = sandbox.__stats();
  console.log(`\n合计：fetch ${st.fetched.length} 次，WebGL drawArrays ${st.draws} 次`);
  if (fail.length) { console.log("未通过：" + fail.join(" / ")); process.exit(1); }
  console.log("运行期冒烟全部通过 ✔（无异常，且每条路径都在真的画东西）");
}, 300);
