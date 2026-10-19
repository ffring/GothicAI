import assert from 'node:assert/strict';
import test from 'node:test';
import {Plan} from '../src/schema.ts';
import {inCone, interpolatePath} from '../src/parts/diagramMath.ts';

const source = 'reports/example.md';
const line = () => ({
  type: 'line', title: 'Победы', source,
  axisX: {min: 0, max: 40, ticks: [0, 20, 40], label: 'Итерация'},
  axisY: {min: 0, max: 100, ticks: [0, 50, 100], label: '%'},
  series: [{name: 'P30', color: '#d9b36c', points: [[0, 25], [20, 50], [40, 95]], draw: [1, 3]}],
  marks: [{x: 30, text: 'P30', at: 4}],
  labels: [{x: 40, y: 95, text: '95%', at: 5}],
});
const big = () => ({
  type: 'big', title: 'Размер модели', source,
  items: [{value: '476 556', label: 'чисел', at: 1, color: '#d9b36c',
    count: {from: 0, to: 476556, dur: 2, format: '{v}'}},
  {value: 1, label: 'мс на решение', at: 3}],
});
const table = () => ({
  type: 'table', title: 'Оценка', source,
  columns: ['Показатель', 'BC', 'P30'], highlight: 2,
  rows: [{cells: ['HP', 218.2, 294.5], at: 1}, {cells: ['Без урона', 78, 1814], at: 2}],
});
const cells = () => ({
  type: 'cells', title: 'Экзамен', source,
  cells: [{label: 'v1', state: 'fail', at: 0}, {label: 'v2', state: 'skip', at: 1},
    {label: 'v3', state: 'pass', at: 2}],
  spans: [{from: 0, to: 1, text: '14.10'}, {from: 2, to: 2, text: '15.10'}],
  notes: [{text: 'Экзамен сдан', at: 3, color: '#d9b36c'}],
});
const bars = () => ({
  type: 'bars', title: 'Решения в секунду', source,
  series: [{name: 'Скорость', color: '#aaa'}],
  groups: [{label: 'BC', values: [2.5], at: 0}, {label: 'P30', values: [90], at: 1}],
  max: 100,
});
const rays = () => ({
  type: 'rays', title: '184 числа', source, maxCm: 500,
  rays: Array.from({length: 16}, (_, i) => ({cm: i % 2 ? 500 : 200, hit: i % 2 === 0})),
  target: {x: 100, y: 200, label: 'противник', at: 1}, hideAt: 5, raysAt: [0.5, 2],
  panels: [{title: 'Я', lines: ['HP: 100'], color: '#d9b36c', at: 0},
    {title: 'Противник', lines: ['HP: 80'], color: '#78a8cf', at: 1}],
});
const cone = () => ({
  type: 'cone', title: 'Конус зрения', source, halfDeg: 45, rangeCm: 600,
  path: [{t: 0, x: 0, y: 300}, {t: 2, x: 400, y: 300}, {t: 5, x: 400, y: 300}],
  targetLabel: 'противник', turn: {at: 3, dur: 0.4, deg: 55, label: '≤ 400 мс'},
  texts: [{text: 'Цель потеряна', at: 2, color: '#e67868'}],
});
const ticks = () => ({
  type: 'ticks', title: 'Один тик', source, dur: 2,
  rows: [{label: 'игра', cells: 'DBBBDBBB', offset: 0, at: 0},
    {label: 'стенд', cells: 'DBBBDBBB', offset: 0.5, at: 1}],
  legend: {D: 'решение', B: 'тело занято'}, align: {at: 4, dur: 1},
});
const grid = () => ({
  type: 'grid', title: 'Действия', source, columns: 2,
  tiles: [{label: 'подойти', at: 0}, {label: 'повернуться', at: 1}],
});
const flow = () => ({
  type: 'flow', title: 'Самоимитация', source,
  nodes: [{id: 'play', label: 'играть', x: 0.2, y: 0.4, w: 0.2, color: '#d9b36c', at: 0},
    {id: 'learn', label: 'доучить', x: 0.8, y: 0.4, at: 1}],
  edges: [{from: 'play', to: 'learn', label: 'отобрать', at: 2, packets: {every: 0.4, dur: 1}},
    {from: 'learn', to: 'play', label: 'снова', at: 3, curve: -0.2}],
});

function plan(chart, format = 'wide') {
  const input = {lang: 'ru', hook: {text: '', until: 0}, beats: [{id: 'chart', kind: 'card', dur: 10, chart}]};
  if (format !== undefined) input.format = format;
  return input;
}

function expectIssue(chart, path, message) {
  const result = Plan.safeParse(plan(chart));
  assert.equal(result.success, false);
  assert.ok(result.error.issues.some((issue) =>
    issue.path.join('.') === `beats.0.chart.${path}` && message.test(issue.message)),
  JSON.stringify(result.error.issues));
}

for (const makeChart of [line, big, table, cells, rays, cone, ticks, grid, flow]) {
  const type = makeChart().type;
  test(`${type}: wide сохраняет данные и моменты появления`, () => {
    const chart = makeChart();
    assert.deepEqual(Plan.parse(plan(chart)).beats[0].chart, chart);
  });

  for (const format of ['tall', undefined]) {
    test(`${type}: ${format ?? 'формат по умолчанию'} отклонён с путём и «только wide»`, () => {
      const input = plan(makeChart());
      if (format === undefined) delete input.format;
      else input.format = format;
      const result = Plan.safeParse(input);
      assert.equal(result.success, false);
      assert.deepEqual(result.error.issues.map(({path, message}) => ({path, message})), [
        {path: ['beats', 0, 'chart', 'type'], message: `${type}: только wide`},
      ]);
    });
  }
}

test('bars: прежний tall-план остаётся валидным без новых полей', () => {
  const chart = bars();
  const parsed = Plan.parse(plan(chart, 'tall')).beats[0].chart;
  assert.deepEqual(parsed, {...chart, log: false, format: '×{v}', decimal: ','});
});

test('bars: log, цвет и примечание группы сохраняются', () => {
  const chart = bars();
  chart.log = true;
  chart.groups[0].color = '#d9b36c';
  chart.groups[1].note = 'без модели';
  chart.groups.push({label: 'Ноль', values: [0], at: 2});
  const parsed = Plan.parse(plan(chart)).beats[0].chart;
  assert.equal(parsed.log, true);
  assert.deepEqual(parsed.groups, chart.groups);
});

for (const [name, change, path, message] of [
  ['нулевой диапазон оси', (c) => { c.axisX.max = c.axisX.min; }, 'axisX.max', /больше min/],
  ['обратный диапазон оси', (c) => { c.axisY.min = 101; }, 'axisY.max', /больше min/],
  ['деление вне оси', (c) => { c.axisY.ticks[1] = 101; }, 'axisY.ticks.1', /вне диапазона/],
  ['повтор X', (c) => { c.series[0].points[1][0] = 0; }, 'series.0.points.1.0', /строго возрастать/],
  ['убывание X', (c) => { c.series[0].points[2][0] = 10; }, 'series.0.points.2.0', /строго возрастать/],
  ['точка вне X', (c) => { c.series[0].points[2][0] = 41; }, 'series.0.points.2.0', /вне диапазона X/],
  ['точка вне Y', (c) => { c.series[0].points[1][1] = -1; }, 'series.0.points.1.1', /вне диапазона Y/],
]) {
  test(`line: ${name}`, () => {
    const chart = line();
    change(chart);
    expectIssue(chart, path, message);
  });
}

for (const count of [2, 4]) {
  test(`table: ${count} ячейки при трёх колонках отклонены`, () => {
    const chart = table();
    chart.rows[1].cells = Array(count).fill('ячейка');
    expectIssue(chart, 'rows.1.cells', /число ячеек/);
  });
}

test('table: highlight за последней колонкой отклонён', () => {
  const chart = table();
  chart.highlight = chart.columns.length;
  expectIssue(chart, 'highlight', /индекс вне columns/);
});

for (const [name, span] of [
  ['обратный диапазон', {from: 2, to: 1, text: 'дата'}],
  ['конец за последней клеткой', {from: 0, to: 3, text: 'дата'}],
  ['весь диапазон за лентой', {from: 3, to: 4, text: 'дата'}],
]) {
  test(`cells: ${name} отклонён`, () => {
    const chart = cells();
    chart.spans = [span];
    expectIssue(chart, 'spans.0.to', /диапазон вне cells/);
  });
}

for (const max of [0, -1, Infinity]) {
  test(`bars log: max=${max} отклонён`, () => {
    const chart = bars();
    chart.log = true;
    chart.max = max;
    expectIssue(chart, 'max', /max > 0/);
  });
}

for (const value of [-1, 101, Infinity]) {
  test(`bars log: value=${value} вне 0…max отклонён`, () => {
    const chart = bars();
    chart.log = true;
    chart.groups[1].values[0] = value;
    expectIssue(chart, 'groups.1.values.0', /0 ≤ value ≤ max/);
  });
}

test('rays: target.at по умолчанию равен нулю', () => {
  const chart = rays();
  delete chart.target.at;
  assert.equal(Plan.parse(plan(chart)).beats[0].chart.target.at, 0);
});

test('cone: подпись цели и texts имеют значения по умолчанию', () => {
  const chart = cone();
  delete chart.targetLabel;
  delete chart.texts;
  const parsed = Plan.parse(plan(chart)).beats[0].chart;
  assert.equal(parsed.targetLabel, 'противник');
  assert.deepEqual(parsed.texts, []);
});

test('ticks: offset по умолчанию равен нулю', () => {
  const chart = ticks();
  delete chart.rows[0].offset;
  assert.equal(Plan.parse(plan(chart)).beats[0].chart.rows[0].offset, 0);
});

for (const [makeChart, name, change, path, message = /./] of [
  [rays, 'нужно ровно 16 лучей', (c) => { c.rays.pop(); }, 'rays'],
  [rays, 'длина луча неотрицательна', (c) => { c.rays[0].cm = -1; }, 'rays.0.cm'],
  [rays, 'длина луча ограничена maxCm', (c) => { c.rays[0].cm = 501; }, 'rays.0.cm', /≤ maxCm/],
  [rays, 'maxCm положителен', (c) => { c.maxCm = 0; }, 'maxCm'],
  [rays, 'длительность появления положительна', (c) => { c.raysAt[1] = 0; }, 'raysAt.1'],
  [rays, 'hideAt неотрицателен', (c) => { c.hideAt = -1; }, 'hideAt'],
  [cone, 'halfDeg положителен', (c) => { c.halfDeg = 0; }, 'halfDeg'],
  [cone, 'halfDeg меньше 180', (c) => { c.halfDeg = 180; }, 'halfDeg'],
  [cone, 'rangeCm положителен', (c) => { c.rangeCm = 0; }, 'rangeCm'],
  [cone, 'path не пуст', (c) => { c.path = []; }, 'path'],
  [cone, 'время path неотрицательно', (c) => { c.path[0].t = -1; }, 'path.0.t'],
  [cone, 'времена path не повторяются', (c) => { c.path[1].t = 0; }, 'path.1.t', /строго возрастать/],
  [cone, 'времена path возрастают', (c) => { c.path[2].t = 1; }, 'path.2.t', /строго возрастать/],
  [cone, 'координаты конечны', (c) => { c.path[0].x = Infinity; }, 'path.0.x'],
  [cone, 'доворот имеет положительную длительность', (c) => { c.turn.dur = 0; }, 'turn.dur'],
  [cone, 'угол доворота конечен', (c) => { c.turn.deg = Infinity; }, 'turn.deg'],
  [ticks, 'есть хотя бы один ряд', (c) => { c.rows = []; }, 'rows'],
  [ticks, 'есть хотя бы одна клетка', (c) => { c.rows[0].cells = ''; }, 'rows.0.cells', /только D и B/],
  [ticks, 'клетки только D или B', (c) => { c.rows[0].cells = 'DBX'; }, 'rows.0.cells', /только D и B/],
  [ticks, 'offset неотрицателен', (c) => { c.rows[1].offset = -0.5; }, 'rows.1.offset'],
  [ticks, 'dur положителен', (c) => { c.dur = 0; }, 'dur'],
  [ticks, 'выравнивание не мгновенно', (c) => { c.align.dur = 0; }, 'align.dur'],
  [grid, 'есть хотя бы одна плитка', (c) => { c.tiles = []; }, 'tiles'],
  [grid, 'columns положителен', (c) => { c.columns = 0; }, 'columns'],
  [grid, 'columns целый', (c) => { c.columns = 1.5; }, 'columns'],
  [grid, 'at неотрицателен', (c) => { c.tiles[0].at = -1; }, 'tiles.0.at'],
  [flow, 'есть хотя бы один узел', (c) => { c.nodes = []; c.edges = []; }, 'nodes'],
  [flow, 'x в долях кадра', (c) => { c.nodes[0].x = 1.1; }, 'nodes.0.x'],
  [flow, 'y в долях кадра', (c) => { c.nodes[0].y = -0.1; }, 'nodes.0.y'],
  [flow, 'ширина положительна', (c) => { c.nodes[0].w = 0; }, 'nodes.0.w'],
  [flow, 'ширина не больше кадра', (c) => { c.nodes[0].w = 1.1; }, 'nodes.0.w'],
  [flow, 'id уникальны', (c) => { c.nodes[1].id = 'play'; }, 'nodes.1.id', /уникальными/],
  [flow, 'from существует', (c) => { c.edges[0].from = 'missing'; }, 'edges.0.from', /неизвестный узел/],
  [flow, 'to существует', (c) => { c.edges[0].to = 'missing'; }, 'edges.0.to', /неизвестный узел/],
  [flow, 'изгиб конечен', (c) => { c.edges[1].curve = Infinity; }, 'edges.1.curve'],
  [flow, 'период пакетов положителен', (c) => { c.edges[0].packets.every = 0; }, 'edges.0.packets.every'],
  [flow, 'длительность пакетов положительна', (c) => { c.edges[0].packets.dur = 0; }, 'edges.0.packets.dur'],
]) {
  test(`${makeChart().type}: ${name}`, () => {
    const chart = makeChart();
    change(chart);
    expectIssue(chart, path, message);
  });
}

const motionPath = [{t: 1, x: 10, y: 40}, {t: 3, x: 30, y: 0}, {t: 7, x: -10, y: 80}];

test('cone.path: координаты ограничены первым и последним ключом', () => {
  assert.deepEqual(interpolatePath(motionPath, -5), {x: 10, y: 40});
  assert.deepEqual(interpolatePath(motionPath, 10), {x: -10, y: 80});
});

test('cone.path: в ключах возвращаются их координаты', () => {
  for (const {t, x, y} of motionPath) assert.deepEqual(interpolatePath(motionPath, t), {x, y});
});

test('cone.path: линейная интерполяция между ключами', () => {
  assert.deepEqual(interpolatePath(motionPath, 2), {x: 20, y: 20});
});

test('cone.path: интервалы разной длины интерполируются по своему времени', () => {
  assert.deepEqual(interpolatePath(motionPath, 4), {x: 20, y: 20});
  assert.deepEqual(interpolatePath(motionPath, 6), {x: 0, y: 60});
});

test('cone.path: единственная точка остаётся неподвижной', () => {
  assert.deepEqual(interpolatePath([{t: 2, x: -7, y: 12}], 5), {x: -7, y: 12});
});

test('cone: взгляд направлен вверх, граница конуса включена', () => {
  assert.equal(inCone({x: 0, y: 100}, 45, 500), true);
  assert.equal(inCone({x: 100, y: 100}, 45, 500), true);
  assert.equal(inCone({x: -100, y: 100}, 45, 500), true);
  assert.equal(inCone({x: 101, y: 100}, 45, 500), false);
  assert.equal(inCone({x: 0, y: -100}, 45, 500), false);
});

test('cone: дальность ограничивает видимость независимо от угла', () => {
  assert.equal(inCone({x: 0, y: 500}, 45, 500), true);
  assert.equal(inCone({x: 0, y: 501}, 45, 500), false);
});

test('cone: положительный доворот направляет взгляд вправо', () => {
  assert.equal(inCone({x: 100, y: 0}, 20, 500, 90), true);
  assert.equal(inCone({x: 0, y: 100}, 20, 500, 90), false);
});

test('cone: видимость не разрывается при переходе через ±180°', () => {
  const point = (deg) => ({x: Math.sin(deg * Math.PI / 180) * 100, y: Math.cos(deg * Math.PI / 180) * 100});
  assert.equal(inCone(point(-179), 5, 500, 179), true);
  assert.equal(inCone(point(179), 5, 500, -179), true);
  assert.equal(inCone(point(170), 5, 500, -179), false);
});
