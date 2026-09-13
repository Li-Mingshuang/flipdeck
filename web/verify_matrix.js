// 校验：把 viewer 里的矩阵数学单独跑一遍，和 python 的 poses.pose 对答案
// 用法：node verify_matrix.js   （在 flipdeck-cad 目录下）
"use strict";
const HINGE = [0, 52.0, 27.0];
const PIVOT = [0, -51.0, 5.5];
const DTOR = Math.PI / 180;

function mat4() { return new Float32Array([1,0,0,0, 0,1,0,0, 0,0,1,0, 0,0,0,1]); }
function mul(a, b) {
  const o = new Float32Array(16);
  for (let c = 0; c < 4; c++) for (let r = 0; r < 4; r++) {
    let s = 0; for (let k = 0; k < 4; k++) s += a[k * 4 + r] * b[c * 4 + k];
    o[c * 4 + r] = s;
  }
  return o;
}
function rotX(deg) {
  const a = deg * DTOR, c = Math.cos(a), s = Math.sin(a), m = mat4();
  m[5] = c; m[6] = s; m[9] = -s; m[10] = c; return m;
}
function trans(x, y, z) { const m = mat4(); m[12] = x; m[13] = y; m[14] = z; return m; }
function xformPoint(m, p) {
  return [ m[0]*p[0] + m[4]*p[1] + m[8]*p[2] + m[12],
           m[1]*p[0] + m[5]*p[1] + m[9]*p[2] + m[13],
           m[2]*p[0] + m[6]*p[1] + m[10]*p[2] + m[14] ];
}
function modelMatrices(thetaDeg, phiDeg) {
  const lid = mul(trans(HINGE[0], HINGE[1], HINGE[2]), rotX(-thetaDeg));
  const around = mul(mul(trans(PIVOT[0], PIVOT[1], PIVOT[2]), rotX(-phiDeg)),
                     trans(-PIVOT[0], -PIVOT[1], -PIVOT[2]));
  return { deck: mat4(), lid: lid, cradle: mul(lid, around), phone: mul(lid, around) };
}

const local = [0.0, -51.0, -1.0];              // 手机中心（cradle 局部坐标）
const expect = {                               // 来自 python poses.pose('phone', θ, φ)
  "0/0":     [0.0, 1.0, 26.0],
  "110/0":   [0.0, 68.503, 75.266],
  "0/180":   [0.0, 1.0, 39.0],
  "110/90":  [0.0, 76.834, 79.151],
};
let bad = 0;
for (const key of Object.keys(expect)) {
  const [th, ph] = key.split("/").map(Number);
  const mv = modelMatrices(th, ph);
  const got = xformPoint(mv.phone, local);
  const err = Math.max(...got.map((v, i) => Math.abs(v - expect[key][i])));
  const ok = err < 0.01;
  if (!ok) bad++;
  console.log(`${ok ? "OK  " : "FAIL"} θ/φ=${key.padEnd(7)} js=[${got.map(v => v.toFixed(3)).join(", ")}]  ` +
              `python=[${expect[key].join(", ")}]  最大偏差 ${err.toFixed(4)}mm`);
}
console.log(bad === 0 ? "\n矩阵数学与 python 完全一致 ✔" : `\n有 ${bad} 组不一致 ✘`);
process.exit(bad === 0 ? 0 : 1);
