// 校验自动取景：把 5 个姿态下所有零件的包围盒角点投影到屏幕，检查是否全部落在画面内。
// 用法： node verify_framing.js
"use strict";
const fs = require("fs"), path = require("path");
const ROOT = __dirname;
const D = JSON.parse(fs.readFileSync(path.join(ROOT, "params.json"), "utf8"));
const HINGE = D.hinge, PIVOT = D.pivot, DTOR = Math.PI / 180;
const FOV = 34 * DTOR;

const DEF = {
  deck: "deck", lid: "lid", cradle: "cradle", phone: "phone",
  cap_dpad: "deck", cap_a: "deck", cap_b: "deck", cap_x: "deck", cap_y: "deck",
  cap_start: "deck", cap_select: "deck", cap_home: "deck",
  lever_l: "deck", lever_r: "deck",
  sticks_base: "deck", stick_l_cap: "deck", stick_r_cap: "deck",
};
function readSTLBBox(file) {
  const buf = fs.readFileSync(file);
  const n = buf.readUInt32LE(80);
  const lo = [1e9, 1e9, 1e9], hi = [-1e9, -1e9, -1e9];
  for (let i = 0; i < n; i++) {
    const o = 84 + i * 50 + 12;
    for (let v = 0; v < 3; v++) for (let k = 0; k < 3; k++) {
      const x = buf.readFloatLE(o + v * 12 + k * 4);
      if (x < lo[k]) lo[k] = x;
      if (x > hi[k]) hi[k] = x;
    }
  }
  return { lo: lo, hi: hi };
}
function mul(a, b) {
  const o = new Float32Array(16);
  for (let c = 0; c < 4; c++) for (let r = 0; r < 4; r++) {
    let s = 0; for (let k = 0; k < 4; k++) s += a[k * 4 + r] * b[c * 4 + k];
    o[c * 4 + r] = s;
  }
  return o;
}
function rotX(d) { const a = d * DTOR, c = Math.cos(a), s = Math.sin(a), m = new Float32Array([1,0,0,0, 0,1,0,0, 0,0,1,0, 0,0,0,1]);
  m[5] = c; m[6] = s; m[9] = -s; m[10] = c; return m; }
function trans(x, y, z) { const m = new Float32Array([1,0,0,0, 0,1,0,0, 0,0,1,0, 0,0,0,1]); m[12]=x; m[13]=y; m[14]=z; return m; }
function around(px, py, pz, R) { return mul(mul(trans(px, py, pz), R), trans(-px, -py, -pz)); }
function modelMatrices(th, ph) {
  const lid = mul(trans(HINGE[0], HINGE[1], HINGE[2]), rotX(-th));
  const c = mul(lid, around(PIVOT[0], PIVOT[1], PIVOT[2], rotX(-ph)));
  return { deck: new Float32Array([1,0,0,0, 0,1,0,0, 0,0,1,0, 0,0,0,1]), lid: lid, cradle: c, phone: c };
}
function basis(yawDeg, pitchDeg) {
  const yaw = yawDeg * DTOR, pitch = pitchDeg * DTOR;
  const t = [0,0,0];  // 方向只依赖 yaw/pitch
  const e = [Math.cos(pitch)*Math.cos(yaw), Math.cos(pitch)*Math.sin(yaw), Math.sin(pitch)];
  let z = [e[0], e[1], e[2]];
  const l = Math.hypot(z[0], z[1], z[2]); z = [z[0]/l, z[1]/l, z[2]/l];
  let x = [-z[1], z[0], 0];
  const l2 = Math.hypot(x[0], x[1], x[2]) || 1; x = [x[0]/l2, x[1]/l2, x[2]/l2];
  const y = [z[1]*x[2]-z[2]*x[1], z[2]*x[0]-z[0]*x[2], z[0]*x[1]-z[1]*x[0]];
  return { r: x, u: y, f: [-z[0], -z[1], -z[2]] };
}
const boxes = {};
for (const name of Object.keys(DEF)) boxes[name] = readSTLBBox(path.join(ROOT, "stl", name + ".stl"));

const ASPECT = 1.75, YAW = -82, PITCH = 20;       // 与页面默认一致
const b = basis(YAW, PITCH);
const tanV = Math.tan(FOV / 2), tanH = tanV * ASPECT;

function check(th, ph, label) {
  const mv = modelMatrices(th, ph), pts = [];
  for (const name of Object.keys(DEF)) {
    const bb = boxes[name], M = mv[DEF[name]];
    for (let i = 0; i < 8; i++) {
      const lp = [(i&1)?bb.hi[0]:bb.lo[0], (i&2)?bb.hi[1]:bb.lo[1], (i&4)?bb.hi[2]:bb.lo[2]];
      pts.push([M[0]*lp[0]+M[4]*lp[1]+M[8]*lp[2]+M[12],
                M[1]*lp[0]+M[5]*lp[1]+M[9]*lp[2]+M[13],
                M[2]*lp[0]+M[6]*lp[1]+M[10]*lp[2]+M[14]]);
    }
  }
  const lo = [1e9,1e9,1e9], hi = [-1e9,-1e9,-1e9];
  pts.forEach(p => { for (let k = 0; k < 3; k++) { if (p[k] < lo[k]) lo[k] = p[k]; if (p[k] > hi[k]) hi[k] = p[k]; } });
  const c = [(lo[0]+hi[0])/2, (lo[1]+hi[1])/2, (lo[2]+hi[2])/2];
  let Dd = 60;
  pts.forEach(p => {
    const d = [p[0]-c[0], p[1]-c[1], p[2]-c[2]];
    const a = d[0]*b.f[0]+d[1]*b.f[1]+d[2]*b.f[2];
    const x = Math.abs(d[0]*b.r[0]+d[1]*b.r[1]+d[2]*b.r[2]);
    const y = Math.abs(d[0]*b.u[0]+d[1]*b.u[1]+d[2]*b.u[2]);
    Dd = Math.max(Dd, x/tanH - a, y/tanV - a);
  });
  const dist = Math.min(2400, Math.max(160, Dd * 1.06));
  // 用该距离反算每个角点的 NDC，检查是否都 < 1（在画面内）
  let maxX = 0, maxY = 0;
  pts.forEach(p => {
    const d = [p[0]-c[0], p[1]-c[1], p[2]-c[2]];
    const zc = dist + (d[0]*b.f[0]+d[1]*b.f[1]+d[2]*b.f[2]);
    const xc = d[0]*b.r[0]+d[1]*b.r[1]+d[2]*b.r[2];
    const yc = d[0]*b.u[0]+d[1]*b.u[1]+d[2]*b.u[2];
    maxX = Math.max(maxX, Math.abs(xc)/(zc*tanH));
    maxY = Math.max(maxY, Math.abs(yc)/(zc*tanV));
  });
  const ok = maxX <= 1 && maxY <= 1;
  console.log(`${ok ? "OK  " : "FAIL"} ${label.padEnd(14)} dist=${dist.toFixed(0)}mm  ` +
              `画面占用 X ${(maxX*100).toFixed(0)}% / Y ${(maxY*100).toFixed(0)}%  ` +
              `-> ${ok ? "全部在画面内" : "有切出"}`);
  return ok;
}
let all = true;
all &= check(110, 0, "展开游戏 110/0");
all &= check(0, 0, "合盖收纳 0/0");
all &= check(0, 180, "手机模式 0/180");
all &= check(110, 90, "换形态中 110/90");
all &= check(60, 30, "中间姿态 60/30");
console.log(all ? "\n自动取景在全部姿态下都把整机包完整 ✔" : "\n有姿态被切掉 ✘");
process.exit(all ? 0 : 1);
