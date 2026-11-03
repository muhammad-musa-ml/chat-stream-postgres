/** Server data on its way to the page: still coming, failed, or here. */
export type Load<T> = { status: "loading" } | { status: "error"; error: string } | { status: "ready"; data: T };

export function messageOf(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}
