// A* sobre la cuadrícula de la oficina (4 direcciones, coste uniforme).

export interface Point {
  x: number;
  y: number;
}

class MinHeap<T> {
  private items: { key: number; value: T }[] = [];
  get size() {
    return this.items.length;
  }
  push(key: number, value: T) {
    const a = this.items;
    a.push({ key, value });
    let i = a.length - 1;
    while (i > 0) {
      const p = (i - 1) >> 1;
      if (a[p].key <= a[i].key) break;
      [a[p], a[i]] = [a[i], a[p]];
      i = p;
    }
  }
  pop(): T | undefined {
    const a = this.items;
    if (!a.length) return undefined;
    const top = a[0].value;
    const last = a.pop()!;
    if (a.length) {
      a[0] = last;
      let i = 0;
      for (;;) {
        const l = 2 * i + 1;
        const r = l + 1;
        let m = i;
        if (l < a.length && a[l].key < a[m].key) m = l;
        if (r < a.length && a[r].key < a[m].key) m = r;
        if (m === i) break;
        [a[m], a[i]] = [a[i], a[m]];
        i = m;
      }
    }
    return top;
  }
}

export function findPath(blocked: boolean[][], start: Point, goal: Point): Point[] | null {
  const rows = blocked.length;
  const cols = blocked[0].length;
  const inside = (p: Point) => p.x >= 0 && p.y >= 0 && p.x < cols && p.y < rows;
  if (!inside(start) || !inside(goal) || blocked[goal.y][goal.x]) return null;
  const key = (p: Point) => p.y * cols + p.x;
  const g = new Map<number, number>([[key(start), 0]]);
  const came = new Map<number, number>();
  const open = new MinHeap<Point>();
  const h = (p: Point) => Math.abs(p.x - goal.x) + Math.abs(p.y - goal.y);
  open.push(h(start), start);
  const closed = new Set<number>();
  while (open.size) {
    const cur = open.pop()!;
    const ck = key(cur);
    if (closed.has(ck)) continue;
    if (cur.x === goal.x && cur.y === goal.y) {
      const path: Point[] = [cur];
      let k = ck;
      while (came.has(k)) {
        k = came.get(k)!;
        path.push({ x: k % cols, y: Math.floor(k / cols) });
      }
      return path.reverse();
    }
    closed.add(ck);
    for (const [dx, dy] of [
      [1, 0],
      [-1, 0],
      [0, 1],
      [0, -1],
    ]) {
      const n = { x: cur.x + dx, y: cur.y + dy };
      if (!inside(n) || blocked[n.y][n.x]) continue;
      const nk = key(n);
      const tentative = g.get(ck)! + 1;
      if (tentative < (g.get(nk) ?? Infinity)) {
        g.set(nk, tentative);
        came.set(nk, ck);
        open.push(tentative + h(n), n);
      }
    }
  }
  return null;
}
