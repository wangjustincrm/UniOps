// Generic task bucketer. Groups items by a key, emits groups in a fixed order.
// Keys not listed in `order` are appended after the ordered ones in first-seen
// order. Empty buckets are omitted. Pure — no React, no side effects.

export interface TaskGroup<T> {
  key: string
  label: string
  items: T[]
}

export function groupTasks<T>(
  items: T[],
  keyOf: (item: T) => string,
  labelOf: (key: string, sample: T) => string,
  order: readonly string[],
): TaskGroup<T>[] {
  const buckets = new Map<string, T[]>()
  for (const item of items) {
    const k = keyOf(item)
    const arr = buckets.get(k)
    if (arr) arr.push(item)
    else buckets.set(k, [item])
  }
  const ordered = order.filter((k) => buckets.has(k))
  const rest = [...buckets.keys()].filter((k) => !order.includes(k))
  return [...ordered, ...rest].map((key) => {
    const bucket = buckets.get(key)!
    return { key, label: labelOf(key, bucket[0]), items: bucket }
  })
}
